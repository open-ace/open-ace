"""
Open ACE - AI Computing Explorer - Workspace API Routes

API endpoints for workspace functionality including:
- Prompt templates
- Session management
- Tool connections
- State synchronization
- Collaboration features
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from flask import Blueprint, abort, g, jsonify, request

from app.auth.decorators import (
    _extract_token,
    _load_user_from_token,
    enforce_password_change_requirement,
)
from app.modules.workspace.api_key_proxy import get_api_key_proxy_service
from app.modules.workspace.collaboration import SharePermission, get_collaboration_manager
from app.modules.workspace.llm_proxy_handler import handle_llm_proxy_request
from app.modules.workspace.prompt_library import PromptCategory, PromptTemplate, get_prompt_library
from app.modules.workspace.session_manager import (
    SessionType,
    get_session_manager,
    visible_session_clause,
)
from app.modules.workspace.state_sync import get_state_sync_manager
from app.modules.workspace.tool_connector import get_tool_connector
from app.routes.fs import is_valid_path
from app.utils.tool_names import TOOL_NAME_ALIASES, normalize_tool_name
from app.utils.workspace import get_workspace_base_dir, get_workspace_base_dirs

logger = logging.getLogger(__name__)

# Token quotas are stored in M (millions) units
# Convert to actual tokens when comparing with usage
TOKEN_QUOTA_MULTIPLIER = 1_000_000

# Only refresh session when it has less than this many minutes remaining
_SESSION_REFRESH_THRESHOLD_MINUTES = 10


def format_datetime(dt):
    """Convert datetime to ISO 8601 string for proper timezone handling in frontend.

    Args:
        dt: datetime object or None

    Returns:
        ISO 8601 string with timezone info, or None
    """
    if dt is None:
        return None
    # If datetime has no timezone, assume it's UTC
    if hasattr(dt, "isoformat"):
        iso_str = dt.isoformat()
        # Add timezone if not present
        if "+" not in iso_str and "Z" not in iso_str and "-" not in iso_str[-6:]:
            iso_str += "+00:00"
        return iso_str
    return dt


def _current_tenant_id() -> Optional[int]:
    """Return the current authenticated tenant id, if any."""
    if not hasattr(g, "user") or not g.user:
        return None
    raw_tenant_id = g.user.get("tenant_id")
    if raw_tenant_id in (None, ""):
        return None
    try:
        tenant_id = int(raw_tenant_id)
    except (TypeError, ValueError):
        return None
    return tenant_id if tenant_id > 0 else None


def _tenant_scope_required() -> bool:
    """Whether workspace data should be tenant-scoped for this request."""
    current_role = g.user.get("role") if hasattr(g, "user") and g.user else None
    return bool(current_role != "admin")


def _session_lookup_tenant_id() -> Optional[int]:
    """Return tenant scope for session lookups; system admins stay global.

    Fail closed for non-admins whose tenant cannot be resolved: returning None
    here previously meant "global scope", which let a null-tenant non-admin read
    or mutate any tenant's session. Now we deny (abort 403) instead. Only system
    admins legitimately keep global scope (None), and callers needing global
    scope must opt in explicitly (GLOBAL_TENANT_SENTINEL at the manager layer).
    """
    if not _tenant_scope_required():
        return None
    tenant_id = _current_tenant_id()
    if tenant_id is None:
        abort(403)
    return tenant_id


workspace_bp = Blueprint("workspace", __name__)


@workspace_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    # Skip auth for CORS preflight requests (browser-initiated, carries no business data)
    if request.method == "OPTIONS":
        return None
    # Skip auth for public APIs (config endpoint needed for workspace UI initialization)
    if request.path.startswith("/api/workspace/llm-proxy"):
        return None
    if request.path == "/api/workspace/config":
        return None

    token = _extract_token()

    if token:
        user = _load_user_from_token(token)
        if user:
            g.user = user
            g.user_id = user.get("id")
            g.user_role = user.get("role")
            g.tenant_id = user.get("tenant_id")
            _refresh_session(token)
            password_change_response = enforce_password_change_requirement(user)
            if password_change_response is not None:
                return password_change_response
            return None

        # Session token failed — try WebUI token validation
        try:
            from app.services.webui_manager import WebUIManager

            webui_manager = WebUIManager()
            is_valid, user_id, error = webui_manager.validate_token(token)
            if is_valid and user_id:
                from app.repositories.user_repo import UserRepository

                user_repo = UserRepository()
                user_data = user_repo.get_user_by_id(user_id)
                if user_data:
                    g.user = {
                        "id": user_id,
                        "username": user_data.get("username"),
                        "email": user_data.get("email"),
                        "role": user_data.get("role"),
                        "tenant_id": user_data.get("tenant_id"),
                        "must_change_password": bool(user_data.get("must_change_password")),
                    }
                    g.user_id = user_id
                    g.user_role = user_data.get("role")
                    g.tenant_id = user_data.get("tenant_id")
                    _refresh_session(token, user_repo=user_repo)
                    password_change_response = enforce_password_change_requirement(g.user)
                    if password_change_response is not None:
                        return password_change_response
                    return None
        except Exception as e:
            logger.warning(f"Failed to validate URL token: {e}")

    return jsonify({"error": "Authentication required"}), 401


def _refresh_session(token: str, user_repo=None):
    """Extend DB session and cookie expiry when close to expiration.

    Only refreshes if the session has less than _SESSION_REFRESH_THRESHOLD_MINUTES
    remaining, avoiding a DB write on every request.
    """
    try:
        from app.repositories.database import get_param_placeholder
        from app.repositories.user_repo import UserRepository
        from app.services.auth_service import _get_session_timeout_hours

        repo = user_repo or UserRepository()

        # Placeholder style: use get_param_placeholder() ({p}) per the file
        # convention; never raw ? / %s. The Database layer adapts internally.
        p = get_param_placeholder()
        # Lightweight query — only need expires_at, no JOIN
        row = repo.db.fetch_one(f"SELECT expires_at FROM sessions WHERE token = {p}", (token,))
        if not row or not row.get("expires_at"):
            return

        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at).replace(tzinfo=None)

        remaining = (expires_at - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()
        threshold = timedelta(minutes=_SESSION_REFRESH_THRESHOLD_MINUTES).total_seconds()
        if remaining > threshold:
            return

        timeout_hours = _get_session_timeout_hours()
        new_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            hours=timeout_hours
        )
        repo.extend_session_expiry(token, new_expires_at)
        g._session_refresh_seconds = int(timeout_hours * 3600)
    except Exception as e:
        logger.warning(f"Failed to refresh session: {e}")


@workspace_bp.after_request
def refresh_session_cookie(response):
    """Refresh session cookie max_age only when session was actually refreshed."""
    timeout_seconds = getattr(g, "_session_refresh_seconds", None)
    if timeout_seconds and request.cookies.get("session_token"):
        response.set_cookie(
            "session_token",
            request.cookies["session_token"],
            httponly=True,
            secure=request.is_secure,
            samesite="Lax",
            max_age=timeout_seconds,
        )
        g._session_refresh_seconds = None
    return response


@workspace_bp.route("/llm-proxy", methods=["POST", "HEAD"])
@workspace_bp.route("/llm-proxy/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "HEAD"])
def local_llm_proxy(path=""):
    """Transparent LLM proxy for local multi-user qwen-code-webui sessions."""
    api_proxy = get_api_key_proxy_service()
    return handle_llm_proxy_request(scope="local", api_proxy=api_proxy, path=path)


@workspace_bp.route("/session-models", methods=["GET"])
def get_session_models():
    """Return integrated-mode qwen-code model options for the current scope context."""
    workspace_type = request.args.get("workspace_type", "local")
    if workspace_type not in {"local", "remote"}:
        return jsonify({"success": False, "error": "workspace_type must be local or remote"}), 400

    api_proxy = get_api_key_proxy_service()

    if workspace_type == "local":
        # Local workspace is single-tenant; tenant_id=1 is the default tenant.
        # This must be updated if multi-tenant local workspaces are introduced.
        pool = api_proxy.get_tool_model_pool(
            tenant_id=1,
            tool_name="qwen-code",
            scope="local",
            provider="openai",
        )
        return jsonify(
            {
                "success": True,
                "models": pool.get("models", []),
                "empty_reason": pool.get("empty_reason"),
            }
        )

    machine_id = request.args.get("machine_id")
    session_id = request.args.get("session_id")
    if not machine_id and not session_id:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "machine_id or session_id is required for remote workspace",
                }
            ),
            400,
        )

    if session_id:
        session = get_session_manager().get_session(session_id)
        if not session:
            return jsonify({"success": False, "error": "Session not found"}), 404
        if session.user_id not in (None, g.user["id"]):
            return jsonify({"success": False, "error": "Forbidden"}), 403
        ha_pool = session.context.get("ha_pool", {}) if session.context else {}
        return jsonify(
            {
                "success": True,
                "models": ha_pool.get("models", []),
                "empty_reason": ha_pool.get("empty_reason"),
            }
        )

    from app.modules.workspace.remote_agent_manager import get_remote_agent_manager

    agent_mgr = get_remote_agent_manager()
    if not machine_id or not agent_mgr.check_user_access(machine_id, g.user["id"]):
        return jsonify({"success": False, "error": "Machine not found or access denied"}), 404

    machine = agent_mgr.get_machine(machine_id)
    if not machine:
        return jsonify({"success": False, "error": "Machine not found"}), 404

    tenant_id = machine.get("tenant_id", 1)
    pool = api_proxy.get_tool_model_pool(
        tenant_id=tenant_id,
        tool_name="qwen-code",
        scope="remote",
        provider="openai",
    )
    api_proxy.revoke_proxy_tokens_for_session(
        f"ha-pool:{machine_id}",
        reason="ha_pool_rotated",
    )
    ha_pool_token = api_proxy.generate_proxy_token(
        user_id=g.user["id"],
        session_id=f"ha-pool:{machine_id}",
        tenant_id=tenant_id,
        provider="openai",
        session_type="ha_pool",
        expires_minutes=15,
        extra_payload={
            "scope": "remote",
            "tool_name": "qwen-code",
            "machine_id": machine_id,
            "ha_candidate_keys": pool.get("candidate_keys", []),
            "ha_model_key_ids": pool.get("model_key_ids", {}),
            "ha_models": pool.get("models", []),
            "ha_settings": pool.get("settings", {}),
            "ha_empty_reason": pool.get("empty_reason"),
        },
    )
    return jsonify(
        {
            "success": True,
            "models": pool.get("models", []),
            "empty_reason": pool.get("empty_reason"),
            "ha_pool_token": ha_pool_token,
        }
    )


# ==================== Prompt Templates ====================


@workspace_bp.route("/prompts", methods=["GET"])
def list_prompts():
    """List prompt templates."""
    try:
        library = get_prompt_library()

        category = request.args.get("category")
        search = request.args.get("search")
        tags = request.args.getlist("tags")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        result = library.list_templates(
            category=category,
            user_id=user_id,
            search=search,
            tags=tags if tags else None,
            page=page,
            limit=limit,
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "templates": [t.to_dict() for t in result["templates"]],
                    "total": result["total"],
                    "page": result["page"],
                    "limit": result["limit"],
                    "total_pages": result["total_pages"],
                },
            }
        )
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts", methods=["POST"])
def create_prompt():
    """Create a new prompt template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        username = g.user.get("username", "") if hasattr(g, "user") and g.user else ""

        template = PromptTemplate(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", PromptCategory.GENERAL.value),
            content=data.get("content", ""),
            variables=data.get("variables", []),
            tags=data.get("tags", []),
            author_id=user_id,
            author_name=username,
            is_public=data.get("is_public", False),
        )

        library = get_prompt_library()
        template_id = library.create_template(template)

        return jsonify({"success": True, "data": {"id": template_id}}), 201
    except Exception as e:
        logger.error(f"Error creating prompt: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["GET"])
def get_prompt(template_id):
    """Get a prompt template."""
    try:
        library = get_prompt_library()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        return jsonify({"success": True, "data": template.to_dict()})
    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["PUT"])
def update_prompt(template_id):
    """Update a prompt template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        library = get_prompt_library()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Update fields
        template.name = data.get("name", template.name)
        template.description = data.get("description", template.description)
        template.category = data.get("category", template.category)
        template.content = data.get("content", template.content)
        template.variables = data.get("variables", template.variables)
        template.tags = data.get("tags", template.tags)
        template.is_public = data.get("is_public", template.is_public)

        success = library.update_template(template)

        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Error updating prompt: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["DELETE"])
def delete_prompt(template_id):
    """Delete a prompt template."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        user_role = g.user.get("role") if hasattr(g, "user") and g.user else None

        library = get_prompt_library()
        # Admin users can delete any template; others can only delete their own
        success = library.delete_template(template_id, None if user_role == "admin" else user_id)

        if not success:
            return jsonify({"success": False, "error": "Template not found or not authorized"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting prompt: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/<int:template_id>/render", methods=["POST"])
def render_prompt(template_id):
    """Render a prompt template with variables."""
    try:
        data = request.get_json() or {}
        variables = data.get("variables", {})

        library = get_prompt_library()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Validate required variables
        missing = template.validate_variables(**variables)
        if missing:
            return (
                jsonify(
                    {"success": False, "error": f"Missing required variables: {', '.join(missing)}"}
                ),
                400,
            )

        # Render the template
        rendered = template.render(**variables)

        # Increment use count
        library.increment_use_count(template_id)

        return jsonify({"success": True, "data": {"rendered": rendered}})
    except Exception as e:
        logger.error(f"Error rendering prompt: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/<int:template_id>/copy", methods=["POST"])
def copy_prompt(template_id):
    """Record a prompt copy action (increments use count)."""
    try:
        library = get_prompt_library()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Increment use count
        library.increment_use_count(template_id)

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error recording prompt copy: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/categories", methods=["GET"])
def get_prompt_categories():
    """Get prompt categories with counts."""
    try:
        library = get_prompt_library()
        # Get current user ID to include their private templates in counts
        current_user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        categories = library.get_categories(user_id=current_user_id)

        return jsonify({"success": True, "data": categories})
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/prompts/featured", methods=["GET"])
def get_featured_prompts():
    """Get featured prompt templates."""
    try:
        limit = int(request.args.get("limit", 10))
        library = get_prompt_library()
        templates = library.get_featured_templates(limit)

        return jsonify({"success": True, "data": [t.to_dict() for t in templates]})
    except Exception as e:
        logger.error(f"Error getting featured prompts: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Sessions ====================


@workspace_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """List agent sessions.

    Priority: agent_sessions table (user-created sessions) with optional
    enrichment from session_stats (historical message data from fetch).
    """
    try:
        from app.repositories.database import (
            Database,
            escape_like,
            get_param_placeholder,
            is_postgresql,
        )

        db = Database()

        tool_name = request.args.get("tool_name")
        status = request.args.get("status")
        session_type = request.args.get("session_type")
        host_name = request.args.get("host_name")
        search = request.args.get("search")
        search_days = request.args.get("search_days")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        # Get user_id from g.user to filter sessions
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        tenant_id = _current_tenant_id()

        # Valid values for status and session_type (whitelist validation)
        VALID_STATUS_VALUES = {"active", "paused", "completed", "error"}
        VALID_SESSION_TYPE_VALUES = {"chat", "agent", "workflow", "terminal"}

        # Validate status and session_type parameters
        if status and status not in VALID_STATUS_VALUES:
            status = None  # Invalid value, ignore filter
        if session_type and session_type not in VALID_SESSION_TYPE_VALUES:
            session_type = None  # Invalid value, ignore filter

        # Placeholder style convention for this file: build parameterized SQL by
        # interpolating {p} from get_param_placeholder(). Do NOT wrap these queries
        # in adapt_sql() (the Database layer adapts internally) and do NOT use raw
        # ? / %s literals. Declare p once per function.
        p = get_param_placeholder()

        # Query agent_sessions table (user-created sessions)
        base_conditions = ["1=1"]
        base_params: list[Any] = []

        # Filter out webui aggregate sessions (session_id LIKE 'webui:%')
        # These are internal containers that mix multiple conversations
        # escape_like not needed: hard-coded pattern, no user input
        base_conditions.append(f"session_id NOT LIKE {p}")
        base_params.append("webui:%")
        visible_clause, visible_params = visible_session_clause()
        base_conditions.append(visible_clause)
        base_params.extend(visible_params)

        if user_id:
            base_conditions.append(f"user_id = {p}")
            base_params.append(user_id)
        if tenant_id is not None and _tenant_scope_required():
            base_conditions.append(f"tenant_id = {p}")
            base_params.append(tenant_id)

        if tool_name:
            aliases = TOOL_NAME_ALIASES.get(tool_name, [tool_name])
            placeholders = ",".join([p for _ in aliases])
            base_conditions.append(f"tool_name IN ({placeholders})")
            base_params.extend(aliases)

        if host_name:
            base_conditions.append(f"host_name = {p}")
            base_params.append(host_name)

        if status:
            base_conditions.append(f"status = {p}")
            base_params.append(status)

        if session_type:
            base_conditions.append(f"session_type = {p}")
            base_params.append(session_type)

        base_where_clause = " AND ".join(base_conditions)

        if search:
            # Build time condition for messages
            if search_days:
                try:
                    days = int(search_days)
                    if is_postgresql():
                        time_cond = f"sm.timestamp >= NOW() - INTERVAL '{days} days'"
                    else:
                        time_cond = f"sm.timestamp >= datetime('now', '-{days} days')"
                except (ValueError, TypeError):
                    time_cond = "1=1"
            else:
                time_cond = "1=1"

            offset = (page - 1) * limit

            # Search pattern: escape_like(search) prevents wildcard injection
            # Use lowercase for case-insensitive search (compatible with PostgreSQL and SQLite)
            safe_search = escape_like(search.lower())
            search_pattern = f"%{safe_search}%"

            count_sql = f"""
                SELECT COUNT(DISTINCT s.session_id) as count
                FROM agent_sessions s
                WHERE {base_where_clause}
                  AND (
                    LOWER(s.title) LIKE {p} ESCAPE '\\'  -- escape_like used
                    OR LOWER(s.session_id) LIKE {p} ESCAPE '\\'  -- escape_like used
                    OR EXISTS (
                      SELECT 1 FROM session_messages sm
                      WHERE sm.session_id = s.session_id
                        AND sm.tenant_id = s.tenant_id
                        AND {time_cond}
                        AND LOWER(sm.content) LIKE {p} ESCAPE '\\'  -- escape_like used
                    )
                  )
            """
            count_params = base_params + [search_pattern, search_pattern, search_pattern]
            result = db.fetch_one(count_sql, tuple(count_params))
            total = result["count"] if result else 0

            sessions_sql = f"""
                SELECT DISTINCT s.*
                FROM agent_sessions s
                WHERE {base_where_clause}
                  AND (
                    LOWER(s.title) LIKE {p} ESCAPE '\\'  -- escape_like used
                    OR LOWER(s.session_id) LIKE {p} ESCAPE '\\'  -- escape_like used
                    OR EXISTS (
                      SELECT 1 FROM session_messages sm
                      WHERE sm.session_id = s.session_id
                        AND sm.tenant_id = s.tenant_id
                        AND {time_cond}
                        AND LOWER(sm.content) LIKE {p} ESCAPE '\\'  -- escape_like used
                    )
                  )
                ORDER BY s.updated_at DESC
                LIMIT {p} OFFSET {p}
            """
            sessions_params = base_params + [
                search_pattern,
                search_pattern,
                search_pattern,
                limit,
                offset,
            ]
            sessions = db.fetch_all(sessions_sql, tuple(sessions_params))

        else:
            where_clause = base_where_clause
            count_sql = f"SELECT COUNT(*) as count FROM agent_sessions WHERE {where_clause}"
            result = db.fetch_one(count_sql, tuple(base_params))
            total = result["count"] if result else 0

            offset = (page - 1) * limit
            sessions_sql = f"""
                SELECT * FROM agent_sessions
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT {p} OFFSET {p}
            """
            sessions = db.fetch_all(sessions_sql, tuple(base_params + [limit, offset]))

        total_pages = (total + limit - 1) // limit if total > 0 else 1

        session_ids = [s["session_id"] for s in sessions]
        first_message_map = {}
        if session_ids:
            try:
                sid_placeholders = ", ".join([p] * len(session_ids))
                # Get first user message for each session (for tooltip preview)
                if is_postgresql():
                    # PostgreSQL: use DISTINCT ON for efficient first-row-per-group
                    first_msg_query = f"""
                        SELECT DISTINCT ON (session_id, tenant_id) session_id, tenant_id, content
                        FROM session_messages
                        WHERE session_id IN ({sid_placeholders})
                          AND role = 'user'
                          AND content IS NOT NULL
                          AND content != ''
                        ORDER BY session_id, tenant_id, timestamp ASC
                    """
                    first_msg_rows = db.fetch_all(first_msg_query, tuple(session_ids))
                else:
                    # SQLite: use subquery with MIN(timestamp)
                    first_msg_query = f"""
                        SELECT sm.session_id, sm.tenant_id, sm.content
                        FROM session_messages sm
                        INNER JOIN (
                            SELECT session_id, tenant_id, MIN(timestamp) as first_ts
                            FROM session_messages
                            WHERE session_id IN ({sid_placeholders})
                              AND role = 'user'
                              AND content IS NOT NULL
                              AND content != ''
                            GROUP BY session_id, tenant_id
                        ) first ON sm.session_id = first.session_id
                            AND sm.tenant_id = first.tenant_id
                            AND sm.timestamp = first.first_ts
                    """
                    first_msg_rows = db.fetch_all(first_msg_query, tuple(session_ids))

                for row in first_msg_rows:
                    # Truncate content to 100 chars for tooltip preview
                    content = row.get("content", "")
                    if content:
                        first_message_map[row["session_id"]] = (
                            content[:100] if len(content) > 100 else content
                        )
            except Exception as e:
                logger.warning(f"Failed to compute session first-message previews: {e}")

        # Format sessions for response
        formatted_sessions = []
        for s in sessions:
            first_message = first_message_map.get(s["session_id"])

            formatted_sessions.append(
                {
                    "id": s.get("id"),
                    "session_id": s["session_id"],
                    "session_type": s.get("session_type") or "chat",
                    "title": s.get("title") or f"{s['tool_name']} - {s['session_id'][:8]}",
                    "first_message": first_message,
                    "tool_name": s["tool_name"],
                    "host_name": s.get("host_name") or "localhost",
                    "user_id": s.get("user_id"),
                    "status": s.get("status") or "active",
                    "context": {},
                    "settings": {},
                    "total_tokens": s.get("total_tokens") or 0,
                    "total_input_tokens": s.get("total_input_tokens") or 0,
                    "total_output_tokens": s.get("total_output_tokens") or 0,
                    "message_count": s.get("message_count") or 0,
                    "request_count": s.get("request_count") or 0,
                    "model": s.get("model"),
                    "tags": [],
                    "created_at": format_datetime(s["created_at"]),
                    "updated_at": format_datetime(s["updated_at"]),
                    "completed_at": format_datetime(s.get("completed_at")),
                    "expires_at": format_datetime(s.get("expires_at")),
                    "project_path": s.get("project_path"),
                    "workspace_type": s.get("workspace_type") or "local",
                    "remote_machine_id": s.get("remote_machine_id"),
                    "cli_session_id": s.get("cli_session_id") or "",
                    "messages": [],
                }
            )

        # Enrich remote sessions with machine names
        remote_machine_ids = list(
            {s["remote_machine_id"] for s in formatted_sessions if s.get("remote_machine_id")}
        )
        if remote_machine_ids:
            try:
                from app.repositories.database import get_param_placeholder

                p = get_param_placeholder()
                machine_name_map = {}
                if is_postgresql():
                    placeholders = ", ".join([p] * len(remote_machine_ids))
                    machine_query = f"SELECT machine_id, machine_name FROM remote_machines WHERE machine_id IN ({placeholders})"
                else:
                    placeholders = ", ".join([p] * len(remote_machine_ids))
                    machine_query = f"SELECT machine_id, machine_name FROM remote_machines WHERE machine_id IN ({placeholders})"
                machine_rows = db.fetch_all(machine_query, tuple(remote_machine_ids))
                for row in machine_rows:
                    machine_name_map[row["machine_id"]] = row["machine_name"]
                for s in formatted_sessions:
                    if s.get("remote_machine_id") and s["remote_machine_id"] in machine_name_map:
                        s["machine_name"] = machine_name_map[s["remote_machine_id"]]
            except Exception as e:
                logger.warning(f"Failed to enrich machine names: {e}")

        return jsonify(
            {
                "success": True,
                "data": {
                    "sessions": formatted_sessions,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/remote-projects", methods=["GET"])
def get_remote_projects():
    """Get user's remote workspace projects list.

    Returns distinct project paths from user's remote sessions,
    sorted by most recent usage. Used to populate 'Your Projects'
    in qwen-code-webui for remote workspace.
    """
    try:
        from app.repositories.database import Database, get_param_placeholder

        db = Database()
        p = get_param_placeholder()

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        tenant_id = _current_tenant_id()
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        # Query distinct remote projects with their latest session info
        # Only include remote workspace sessions (workspace_type = 'remote')
        tenant_clause = ""
        params: list = [user_id]
        if tenant_id is not None and _tenant_scope_required():
            tenant_clause = f" AND tenant_id = {p}"
            params.append(tenant_id)
        query = f"""
            SELECT
                project_path,
                MAX(updated_at) as last_used,
                MAX(remote_machine_id) as machine_id,
                COUNT(*) as session_count
            FROM agent_sessions
            WHERE user_id = {p}
              {tenant_clause}
              AND workspace_type = 'remote'
              AND project_path IS NOT NULL
              AND project_path != ''
              AND status != 'deleted'
            GROUP BY project_path
            ORDER BY last_used DESC
            LIMIT 50
        """
        results = db.fetch_all(query, params)

        # Batch lookup machine names to avoid N+1 queries
        # Use set to deduplicate machine_ids (multiple projects may share same machine)
        machine_ids = list({r.get("machine_id") for r in results if r.get("machine_id")})
        machine_name_map = {}
        if machine_ids:
            try:
                placeholders = ", ".join([p] * len(machine_ids))
                machine_query = f"SELECT machine_id, machine_name FROM remote_machines WHERE machine_id IN ({placeholders})"
                machine_rows = db.fetch_all(machine_query, machine_ids)
                machine_name_map = {row["machine_id"]: row["machine_name"] for row in machine_rows}
            except Exception as e:
                logger.warning(f"Failed to batch lookup machine names: {e}")

        projects = []
        for r in results:
            project_path = r.get("project_path")
            if project_path:
                # Convert path to encoded project name format
                # /home/user/demo-project -> -home-user-demo-project
                encoded_name = (
                    project_path.replace("/", "-") if project_path.startswith("/") else project_path
                )

                machine_id = r.get("machine_id")
                machine_name = machine_name_map.get(machine_id) if machine_id else None

                projects.append(
                    {
                        "project_path": project_path,
                        "encoded_project_name": encoded_name,
                        "last_used": format_datetime(r.get("last_used")),
                        "session_count": r.get("session_count", 0),
                        "machine_id": machine_id,
                        "machine_name": machine_name,
                    }
                )

        return jsonify(
            {
                "success": True,
                "projects": projects,
            }
        )
    except Exception as e:
        logger.error(f"Error getting remote projects: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions", methods=["POST"])
def create_session():
    """Create a new agent session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        tool_name = data.get("tool_name")
        if not tool_name:
            return jsonify({"success": False, "error": "tool_name is required"}), 400
        tool_name = normalize_tool_name(tool_name)

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        tenant_id = _current_tenant_id()

        # Get project info from request or look up by path
        project_id = data.get("project_id")
        project_path = data.get("project_path")

        # Validate project_path before using it (Issue #1813)
        # Prevent authorization bypass by validating path server-side
        if project_path:
            base_dirs = get_workspace_base_dirs()
            if not is_valid_path(project_path, allowed_prefixes=base_dirs):
                logger.warning(f"Invalid project_path rejected: {project_path}")
                return jsonify({"success": False, "error": "Invalid project path"}), 400

        # If project_path is provided but not project_id, look up the project
        if project_path and not project_id:
            try:
                from app.repositories.project_repo import ProjectRepository

                project_repo = ProjectRepository()
                project = project_repo.get_project_by_path(project_path, tenant_id=tenant_id)
                if project:
                    project_id = project.id
                    # Auto-add user to project if not already
                    if user_id and project_id:
                        project_repo.add_user_project(int(user_id), int(project_id))
            except Exception as e:
                logger.warning(f"Failed to look up project by path: {e}")

        manager = get_session_manager()
        session = manager.create_session(
            tool_name=tool_name,
            user_id=user_id,
            tenant_id=tenant_id,
            session_type=data.get("session_type", SessionType.CHAT.value),
            title=data.get("title", ""),
            host_name=data.get("host_name", "localhost"),
            context=data.get("context"),
            settings=data.get("settings"),
            model=data.get("model"),
            expires_in_hours=data.get("expires_in_hours"),
            project_id=project_id,
            project_path=project_path,
            session_id=data.get("session_id"),  # Allow passing session_id from client
        )

        return jsonify({"success": True, "data": session.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


def _check_session_access(session, *, require_owner: bool = True):
    """Shared ownership gate for session endpoints.

    Returns a 403/404 jsonify response (with status code) when access must be
    denied, or ``None`` when access is allowed. Mirrors the inline checks that
    used to live in each route, so every message-loading endpoint enforces
    ownership *before* touching ``session_messages`` (Issue #241 #22 — avoid
    loading heavy data for a session the caller cannot see).
    """
    if session is None:
        return jsonify({"success": False, "error": "Session not found"}), 404
    if not require_owner:
        return None
    current_user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
    current_role = g.user.get("role") if hasattr(g, "user") and g.user else None
    current_tenant_id = _current_tenant_id()
    if current_role != "admin":
        if current_tenant_id is not None and session.tenant_id != current_tenant_id:
            return jsonify({"success": False, "error": "Access denied"}), 403
        if not current_user_id or not session.user_id or session.user_id != current_user_id:
            return jsonify({"success": False, "error": "Access denied"}), 403
    return None


def _messages_total(manager, session, milestone_id: Optional[str]) -> int:
    """Milestone-aware message total for the pagination indicator.

    ``agent_sessions.message_count`` is session-level; when a milestone filter
    is active the total must come from a conditional COUNT so the
    "loaded/total" indicator stays correct (Issue #241 #22 review).
    """
    if milestone_id is not None:
        try:
            return int(
                manager.count_messages(
                    session.session_id,
                    milestone_id=milestone_id,
                    tenant_id=session.tenant_id,
                )
            )
        except TypeError:
            return int(manager.count_messages(session.session_id, milestone_id=milestone_id))
    return int(session.message_count or 0)


def _get_messages_page_for_session(
    manager,
    session,
    *,
    limit: Optional[int] = None,
    before_timestamp: Optional[str] = None,
    before_id: Optional[int] = None,
    milestone_id: Optional[str] = None,
):
    """Call SessionManager.get_messages_page with a tenant-aware fallback."""
    kwargs: dict[str, Any] = {}
    if limit is not None:
        kwargs["limit"] = limit
    if before_timestamp is not None:
        kwargs["before_timestamp"] = before_timestamp
    if before_id is not None:
        kwargs["before_id"] = before_id
    if milestone_id is not None:
        kwargs["milestone_id"] = milestone_id

    try:
        return manager.get_messages_page(session.session_id, tenant_id=session.tenant_id, **kwargs)
    except TypeError:
        return manager.get_messages_page(session.session_id, **kwargs)


@workspace_bp.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    """Get a session by ID.

    ``include_messages=true`` returns only the most recent page of messages
    (default 100, max 500) plus pagination metadata — never the full history —
    so sessions with thousands of messages no longer serialize megabytes of
    JSON (Issue #241 #22). Load older pages via ``GET /sessions/<id>/messages``.
    """
    try:
        include_messages = request.args.get("include_messages", "false").lower() == "true"
        manager = get_session_manager()

        # Load the lightweight session (no messages) for ownership + metadata.
        session = manager.get_session(
            session_id, include_messages=False, tenant_id=_session_lookup_tenant_id()
        )
        denied = _check_session_access(session)
        if denied:
            return denied
        assert session is not None  # _check_session_access returned the 404 above if it was None

        data = session.to_dict()

        if include_messages:
            message_limit = request.args.get("message_limit", default=None, type=int)
            milestone_id = request.args.get("milestone_id")
            page = _get_messages_page_for_session(
                manager,
                session,
                limit=message_limit,
                milestone_id=milestone_id,
            )
            data["messages"] = [m.to_dict() for m in page["messages"]]
            data["messages_total"] = _messages_total(manager, session, milestone_id)
            data["messages_has_more"] = page["has_more"]
            data["messages_next_cursor"] = page["next_cursor"]

        return jsonify({"success": True, "data": data})
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/<session_id>/messages", methods=["GET"])
def get_session_messages(session_id):
    """Page through a session's messages with composite-key keyset pagination.

    Query params:
        limit (int): page size, default 100, clamped to [1, 500].
        before_timestamp (str): cursor timestamp (raw stored ISO form).
        before_id (int): cursor id (tiebreaker, paired with before_timestamp).
        milestone_id (str): optional milestone filter.

    Returns ``{messages, has_more, next_cursor, total}``. ``next_cursor`` is the
    ``(timestamp, id)`` of the oldest retained message, or ``None`` when there
    is no older page. Older pages are loaded by passing the previous response's
    ``next_cursor`` back as ``before_timestamp``/``before_id`` (Issue #241 #22).
    """
    try:
        manager = get_session_manager()
        # Ownership must be checked on the session BEFORE reading messages.
        session = manager.get_session(
            session_id, include_messages=False, tenant_id=_session_lookup_tenant_id()
        )
        denied = _check_session_access(session)
        if denied:
            return denied
        assert session is not None  # _check_session_access returned the 404 above if it was None

        message_limit = request.args.get("limit", default=None, type=int)
        before_timestamp = request.args.get("before_timestamp")
        before_id = request.args.get("before_id", default=None, type=int)
        milestone_id = request.args.get("milestone_id")

        # A cursor is only meaningful when both parts are present; a lone
        # before_timestamp would otherwise be silently ignored.
        if before_timestamp is None or before_id is None:
            before_timestamp = None
            before_id = None

        page = _get_messages_page_for_session(
            manager,
            session,
            limit=message_limit,
            before_timestamp=before_timestamp,
            before_id=before_id,
            milestone_id=milestone_id,
        )
        total = _messages_total(manager, session, milestone_id)

        return jsonify(
            {
                "success": True,
                "data": {
                    "messages": [m.to_dict() for m in page["messages"]],
                    "has_more": page["has_more"],
                    "next_cursor": page["next_cursor"],
                    "total": total,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error getting session messages: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/<session_id>/complete", methods=["POST"])
def complete_session(session_id):
    """Mark a session as completed."""
    try:
        manager = get_session_manager()

        # Ownership check
        session = manager.get_session(
            session_id, include_messages=False, tenant_id=_session_lookup_tenant_id()
        )
        denied = _check_session_access(session)
        if denied:
            return denied

        assert session is not None
        success = manager.complete_session(session_id, tenant_id=session.tenant_id)

        if not success:
            return jsonify({"success": False, "error": "Session not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error completing session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    """Delete a session."""
    try:
        manager = get_session_manager()

        # Ownership check before deletion
        session = manager.get_session(
            session_id, include_messages=False, tenant_id=_session_lookup_tenant_id()
        )
        denied = _check_session_access(session)
        if denied:
            return denied

        assert session is not None
        success = manager.delete_session(session_id, tenant_id=session.tenant_id)

        if not success:
            return jsonify({"success": False, "error": "Session not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/<session_id>/restore", methods=["POST"])
def restore_session(session_id):
    """Restore a historical session from agent_sessions to workspace.

    The JSONL file already exists at the original location.
    We just need to return the correct URL with sessionId and encodedProjectName
    so qwen-code-webui can load the history from its local file.

    Returns:
        - sessionId: The session ID (same as input)
        - encodedProjectName: The encoded project name for qwen-code-webui
        - tool_name: The tool name
        - url: The workspace URL to access this session
    """
    try:
        from app.repositories.database import get_param_placeholder
        from scripts.shared.db import _execute, get_connection

        # Get session info from agent_sessions table (now contains all sessions)
        conn = get_connection()
        cursor = conn.cursor()
        p = get_param_placeholder()
        tenant_id = _current_tenant_id()
        tenant_clause = ""
        params = [session_id]
        if tenant_id is not None and _tenant_scope_required():
            tenant_clause = f" AND tenant_id = {p}"
            params.append(tenant_id)

        session_query = f"""
            SELECT
                session_id,
                tool_name,
                project_path,
                workspace_type,
                remote_machine_id,
                user_id,
                cli_session_id,
                tenant_id
            FROM agent_sessions
            WHERE session_id = {p}
              {tenant_clause}
            LIMIT 1
        """
        _execute(cursor, session_query, params)
        session_data = cursor.fetchone()

        if not session_data:
            conn.close()
            return jsonify({"success": False, "error": "Session not found"}), 404

        # Convert to dict if needed
        if not isinstance(session_data, dict):
            session_data = dict(session_data)

        conn.close()

        # Ownership check: only the session owner or admin can restore
        current_user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        current_role = g.user.get("role") if hasattr(g, "user") and g.user else None
        current_tenant_id = _current_tenant_id()
        session_user_id = session_data.get("user_id")
        if current_role != "admin":
            if current_tenant_id is not None and session_data.get("tenant_id") != current_tenant_id:
                return jsonify({"success": False, "error": "Access denied"}), 403
            if not current_user_id or not session_user_id or session_user_id != current_user_id:
                return jsonify({"success": False, "error": "Access denied"}), 403

        tool_name = session_data["tool_name"]
        project_path = session_data.get("project_path")
        workspace_type = session_data.get("workspace_type") or "local"
        remote_machine_id = session_data.get("remote_machine_id")

        # Terminal sessions don't need project_path - they use terminalId
        # Issue #1080: Check if CLI tool session history exists (cli_session_id)
        cli_session_id = session_data.get("cli_session_id") or ""
        if workspace_type == "terminal":
            # Build workspace URL for terminal session
            machine_name = None
            if remote_machine_id:
                try:
                    from app.repositories.database import Database, get_param_placeholder

                    db = Database()
                    p2 = get_param_placeholder()
                    machine_query = (
                        f"SELECT machine_name FROM remote_machines WHERE machine_id = {p2} LIMIT 1"
                    )
                    machine_row = db.fetch_one(machine_query, [remote_machine_id])
                    if machine_row:
                        machine_name = machine_row["machine_name"]
                except Exception as e:
                    logger.warning(f"Failed to look up machine name for {remote_machine_id}: {e}")

            workspace_url = f"/work?workspaceType=terminal&terminalId={session_id}&machineId={remote_machine_id}"
            if machine_name:
                workspace_url += f"&machineName={machine_name}"

            logger.info(f"Restored terminal session {session_id} (machine={remote_machine_id})")

            return jsonify(
                {
                    "success": True,
                    "data": {
                        "session_id": session_id,
                        "encoded_project_name": "",
                        "tool_name": tool_name,
                        "url": workspace_url,
                        "workspace_type": "terminal",
                        "terminal_id": session_id,
                        "remote_machine_id": remote_machine_id,
                        "machine_name": machine_name,
                        "cli_session_id": cli_session_id,
                    },
                }
            )

        # Issue #669: For CLI sessions (qwen/claude/codex), check remote process status
        # Terminal sessions have their own handling in frontend (not_found check)
        if workspace_type == "remote" and remote_machine_id:
            normalized_tool = normalize_tool_name(tool_name)
            if normalized_tool in ["qwen", "claude", "codex"]:
                from app.modules.workspace.remote_agent_manager import get_remote_agent_manager

                agent_mgr = get_remote_agent_manager()
                info = agent_mgr.send_command_with_response(
                    machine_id=remote_machine_id,
                    command="get_session_info",
                    session_id=session_id,
                    timeout=5.0,
                )

                # Process terminated or query failed
                if info is None or not info.get("is_running"):
                    logger.info(
                        "Session %s process terminated (is_running=%s)",
                        session_id[:8],
                        info.get("is_running") if info else "unknown",
                    )

                    # Return status for frontend to guide user decision
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": "Session process has terminated",
                                "can_recreate": True,
                                "can_resume": (
                                    info.get("cli_session_id") is not None if info else False
                                ),
                                "project_path": project_path,
                                "model": session_data.get("model"),
                                "tool_name": tool_name,
                                "remote_machine_id": remote_machine_id,
                            }
                        ),
                        400,
                    )

        # Generate encodedProjectName based on tool
        if normalize_tool_name(tool_name) in ["qwen", "claude"]:
            # project_path may be actual path or encoded name
            # Need to convert actual path to encoded name if necessary
            # Format: /home/rhuang/open-ace -> -home-rhuang-open-ace
            if project_path and project_path.startswith("/"):
                # Actual path, replace / with - (first / becomes leading -)
                encoded_project_name = project_path.replace("/", "-")
            else:
                # Already encoded or empty
                encoded_project_name = project_path
        elif tool_name == "openclaw":
            # project_path is the agent_name (e.g., "main")
            encoded_project_name = project_path
        else:
            # Unknown tool, use project_path as-is
            encoded_project_name = project_path

        if not encoded_project_name:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Project path not found. Cannot restore session without project information.",
                    }
                ),
                404,
            )

        # Build workspace URL with sessionId, encodedProjectName, and toolName
        workspace_url = f"/work/workspace?sessionId={session_id}&encodedProjectName={encoded_project_name}&toolName={tool_name}"

        # Add remote parameters for remote sessions
        machine_name = None
        if workspace_type == "remote" and remote_machine_id:
            # Look up machine name
            try:
                from app.repositories.database import Database, get_param_placeholder

                db = Database()
                p2 = get_param_placeholder()
                machine_query = (
                    f"SELECT machine_name FROM remote_machines WHERE machine_id = {p2} LIMIT 1"
                )
                machine_row = db.fetch_one(machine_query, [remote_machine_id])
                if machine_row:
                    machine_name = machine_row["machine_name"]
            except Exception as e:
                logger.warning(f"Failed to look up machine name for {remote_machine_id}: {e}")

            workspace_url += f"&workspaceType=remote&machineId={remote_machine_id}"
            if machine_name:
                workspace_url += f"&machineName={machine_name}"

        # Add terminal parameters for terminal sessions
        elif workspace_type == "terminal":
            # Terminal session - use terminalId as sessionId
            workspace_url += f"&workspaceType=terminal&terminalId={session_id}"
            if remote_machine_id:
                workspace_url += f"&machineId={remote_machine_id}"
                # Look up machine name for terminal session too
                try:
                    from app.repositories.database import Database, get_param_placeholder

                    db = Database()
                    p2 = get_param_placeholder()
                    machine_query = (
                        f"SELECT machine_name FROM remote_machines WHERE machine_id = {p2} LIMIT 1"
                    )
                    machine_row = db.fetch_one(machine_query, [remote_machine_id])
                    if machine_row:
                        machine_name = machine_row["machine_name"]
                        workspace_url += f"&machineName={machine_name}"
                except Exception as e:
                    logger.warning(f"Failed to look up machine name for {remote_machine_id}: {e}")

        logger.info(
            f"Restored session {session_id} (tool={tool_name}, project={encoded_project_name}, type={workspace_type})"
        )

        result = {
            "session_id": session_id,
            "encoded_project_name": encoded_project_name,
            "tool_name": tool_name,
            "url": workspace_url,
        }
        if workspace_type == "remote":
            result["workspace_type"] = "remote"
            result["remote_machine_id"] = remote_machine_id
            result["machine_name"] = machine_name
        elif workspace_type == "terminal":
            result["workspace_type"] = "terminal"
            result["terminal_id"] = session_id
            result["remote_machine_id"] = remote_machine_id
            result["machine_name"] = machine_name

        return jsonify(
            {
                "success": True,
                "data": result,
            }
        )
    except Exception as e:
        logger.error(f"Error restoring session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/<session_id>/rename", methods=["POST"])
def rename_session(session_id):
    """Rename a session.

    Request body:
        - name: New session name (required)
    """
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"success": False, "error": "Session name is required"}), 400

        new_name = data["name"].strip()
        if not new_name:
            return jsonify({"success": False, "error": "Session name cannot be empty"}), 400

        manager = get_session_manager()
        session = manager.get_session(session_id, tenant_id=_session_lookup_tenant_id())
        denied = _check_session_access(session)
        if denied:
            return denied
        assert session is not None

        session.title = new_name
        success = manager.update_session(session)

        if not success:
            return jsonify({"success": False, "error": "Failed to update session"}), 500

        return jsonify({"success": True, "data": {"session_id": session_id, "title": new_name}})
    except Exception as e:
        logger.error(f"Error renaming session: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sessions/stats", methods=["GET"])
def get_session_stats():
    """Get session statistics."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        manager = get_session_manager()
        stats = manager.get_session_stats(user_id, tenant_id=_session_lookup_tenant_id())

        return jsonify({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Error getting session stats: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Tools ====================


@workspace_bp.route("/tools", methods=["GET"])
def list_tools():
    """List available AI tools."""
    try:
        connector = get_tool_connector()
        tool_type = request.args.get("type")
        status = request.args.get("status")

        tools = connector.list_tools(tool_type=tool_type, status=status)

        return jsonify({"success": True, "data": [t.to_dict() for t in tools]})
    except Exception as e:
        logger.error(f"Error listing tools: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/tools/<tool_name>", methods=["GET"])
def get_tool(tool_name):
    """Get tool information."""
    try:
        connector = get_tool_connector()
        tool = connector.get_tool(tool_name)

        if not tool:
            return jsonify({"success": False, "error": "Tool not found"}), 404

        return jsonify({"success": True, "data": tool.to_dict()})
    except Exception as e:
        logger.error(f"Error getting tool: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/tools/<tool_name>/models", methods=["GET"])
def get_tool_models(tool_name):
    """Get available models for a tool."""
    try:
        connector = get_tool_connector()
        models = connector.get_available_models(tool_name)

        return jsonify({"success": True, "data": models})
    except Exception as e:
        logger.error(f"Error getting tool models: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/tools/health", methods=["GET"])
def check_tools_health():
    """Check health of all tools."""
    try:
        connector = get_tool_connector()
        # For sync context, return cached status
        stats = connector.get_tool_stats()

        return jsonify({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Error checking tools health: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== State Sync ====================


@workspace_bp.route("/sync/events", methods=["GET"])
def get_sync_events():
    """Get sync events."""
    try:
        manager = get_state_sync_manager()

        event_type = request.args.get("event_type")
        session_id = request.args.get("session_id")
        limit = int(request.args.get("limit", 100))

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        events = manager.get_events(
            event_type=event_type, session_id=session_id, user_id=user_id, limit=limit
        )

        return jsonify({"success": True, "data": [e.to_dict() for e in events]})
    except Exception as e:
        logger.error(f"Error getting sync events: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/sync/stats", methods=["GET"])
def get_sync_stats():
    """Get sync statistics."""
    try:
        manager = get_state_sync_manager()
        stats = manager.get_stats()

        return jsonify({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Error getting sync stats: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Collaboration ====================


@workspace_bp.route("/teams", methods=["GET"])
def list_teams():
    """List user's teams."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        manager = get_collaboration_manager()
        teams = manager.list_user_teams(user_id)

        return jsonify({"success": True, "data": [t.to_dict() for t in teams]})
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/teams", methods=["POST"])
def create_team():
    """Create a new team."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        name = data.get("name")
        if not name:
            return jsonify({"success": False, "error": "name is required"}), 400

        manager = get_collaboration_manager()
        team = manager.create_team(
            name=name,
            owner_id=user_id,
            description=data.get("description", ""),
            settings=data.get("settings"),
        )

        return jsonify({"success": True, "data": team.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/shares", methods=["GET"])
def list_shares():
    """List sessions shared with user."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        manager = get_collaboration_manager()
        shares = manager.get_user_shared_sessions(user_id)

        return jsonify({"success": True, "data": [s.to_dict() for s in shares]})
    except Exception as e:
        logger.error(f"Error listing shares: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/shares", methods=["POST"])
def create_share():
    """Share a session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        username = g.user.get("username", "") if hasattr(g, "user") and g.user else ""

        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        session_id = data.get("session_id")
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required"}), 400

        manager = get_collaboration_manager()
        share = manager.share_session(
            session_id=session_id,
            shared_by=user_id,
            shared_by_name=username,
            permission=data.get("permission", SharePermission.VIEW.value),
            share_type=data.get("share_type", "user"),
            target_id=data.get("target_id"),
            target_name=data.get("target_name", ""),
            expires_in_hours=data.get("expires_in_hours"),
            allow_comments=data.get("allow_comments", True),
            allow_copy=data.get("allow_copy", True),
        )

        return jsonify({"success": True, "data": share.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating share: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/shares/<share_id>", methods=["DELETE"])
def revoke_share(share_id):
    """Revoke a share."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        manager = get_collaboration_manager()
        success = manager.revoke_share(share_id, user_id)

        if not success:
            return jsonify({"success": False, "error": "Share not found or not authorized"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error revoking share: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/annotations", methods=["GET"])
def get_annotations():
    """Get annotations for a session."""
    try:
        session_id = request.args.get("session_id")
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required"}), 400

        manager = get_collaboration_manager()
        annotations = manager.get_session_annotations(session_id)

        return jsonify({"success": True, "data": [a.to_dict() for a in annotations]})
    except Exception as e:
        logger.error(f"Error getting annotations: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/annotations", methods=["POST"])
def create_annotation():
    """Create an annotation."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        username = g.user.get("username", "") if hasattr(g, "user") and g.user else ""

        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        session_id = data.get("session_id")
        content = data.get("content")

        if not session_id or not content:
            return jsonify({"success": False, "error": "session_id and content are required"}), 400

        manager = get_collaboration_manager()
        annotation = manager.add_annotation(
            session_id=session_id,
            user_id=user_id,
            username=username,
            content=content,
            message_id=data.get("message_id"),
            annotation_type=data.get("annotation_type", "comment"),
            position=data.get("position"),
            parent_id=data.get("parent_id"),
        )

        return jsonify({"success": True, "data": annotation.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating annotation: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Knowledge Base ====================


@workspace_bp.route("/knowledge", methods=["GET"])
def list_knowledge():
    """List knowledge base entries."""
    try:
        manager = get_collaboration_manager()

        team_id = request.args.get("team_id")
        category = request.args.get("category")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        result = manager.list_knowledge_entries(
            team_id=team_id, category=category, page=page, limit=limit
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "entries": [e.to_dict() for e in result["entries"]],
                    "total": result["total"],
                    "page": result["page"],
                    "limit": result["limit"],
                    "total_pages": result["total_pages"],
                },
            }
        )
    except Exception as e:
        logger.error(f"Error listing knowledge: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/knowledge", methods=["POST"])
def create_knowledge():
    """Create a knowledge base entry."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        username = g.user.get("username", "") if hasattr(g, "user") and g.user else ""

        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        title = data.get("title")
        content = data.get("content")

        if not title or not content:
            return jsonify({"success": False, "error": "title and content are required"}), 400

        manager = get_collaboration_manager()
        entry = manager.create_knowledge_entry(
            title=title,
            content=content,
            author_id=user_id,
            author_name=username,
            team_id=data.get("team_id"),
            category=data.get("category", "general"),
            tags=data.get("tags"),
            is_published=data.get("is_published", False),
        )

        return jsonify({"success": True, "data": entry.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating knowledge: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@workspace_bp.route("/knowledge/<entry_id>", methods=["GET"])
def get_knowledge(entry_id):
    """Get a knowledge base entry."""
    try:
        manager = get_collaboration_manager()
        entry = manager.get_knowledge_entry(entry_id)

        if not entry:
            return jsonify({"success": False, "error": "Entry not found"}), 404

        return jsonify({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"Error getting knowledge: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Workspace Config ====================


@workspace_bp.route("/config", methods=["GET"])
def get_workspace_config():
    """Get workspace configuration."""
    import json
    import os

    try:
        from app.repositories.database import CONFIG_DIR

        config_path = os.path.join(CONFIG_DIR, "config.json")

        # Get workspace base directory for path validation
        base_dir = get_workspace_base_dir()

        workspace_config = {
            "enabled": False,
            "url": "",
            "multi_user_mode": False,
            "port_range_start": 3100,
            "port_range_end": 3200,
            "max_instances": 30,
            "idle_timeout_minutes": 30,
            "base_dir": base_dir,  # For path validation in frontend
            "autonomous_enabled": True,
        }

        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
                workspace = config.get("workspace", {})
                workspace_config["enabled"] = workspace.get("enabled", False)
                workspace_config["url"] = workspace.get("url", "")
                workspace_config["multi_user_mode"] = workspace.get("multi_user_mode", False)
                workspace_config["port_range_start"] = workspace.get("port_range_start", 3100)
                workspace_config["port_range_end"] = workspace.get("port_range_end", 3200)
                workspace_config["max_instances"] = workspace.get("max_instances", 30)
                workspace_config["idle_timeout_minutes"] = workspace.get("idle_timeout_minutes", 30)

            # Expose autonomous feature status
            autonomous_config = config.get("autonomous", {})
            workspace_config["autonomous_enabled"] = autonomous_config.get("enabled", True)

        return jsonify(workspace_config)
    except Exception as e:
        logger.error(f"Error getting workspace config: {e}")
        return jsonify(
            {
                "enabled": False,
                "url": "",
                "multi_user_mode": False,
                "base_dir": get_workspace_base_dir(),
                "autonomous_enabled": True,
            }
        )


# ==================== Multi-User WebUI ====================


@workspace_bp.route("/user-url", methods=["GET"])
def get_user_webui_url():
    """Get the user-specific webui URL with authentication token.

    In multi-user mode, this will start a new webui instance for the user
    if one doesn't exist. In single-user mode, returns the configured URL.

    Returns:
        JSON with url and token fields.
    """
    from app.repositories.user_repo import UserRepository
    from app.services.webui_manager import get_webui_manager

    # Check if user is logged in
    if not hasattr(g, "user") or not g.user:
        return jsonify({"error": "Not authenticated"}), 401

    user_id = g.user.get("id")

    try:
        manager = get_webui_manager()

        # Get user's system_account
        user_repo = UserRepository()
        user = user_repo.get_user_by_id(user_id)

        if not user:
            return jsonify({"error": "User not found"}), 404

        system_account = user.get("system_account") or user.get("username")

        # Get or create user's webui instance
        url, token = manager.get_user_webui_url(int(user_id), str(system_account))

        # Update activity timestamp
        manager.update_user_activity(user_id)

        # Build Open-ACE API URL for iframe integration
        # This is needed so qwen-code-webui can call Open-ACE APIs
        from flask import request as flask_request

        openace_url = flask_request.host_url.rstrip("/")

        # For HTTPS requests in multi-user mode, convert URL to relative path
        # to avoid mixed content blocking (HTTP iframe in HTTPS page)
        if manager.config.multi_user_mode and flask_request.scheme == "https":
            # Extract port from URL like "http://117.72.38.96:3100"
            import re

            port_match = re.search(r":(\d+)$", url)
            if port_match:
                port = port_match.group(1)
                url = f"/webui/{port}/"

        return jsonify(
            {
                "success": True,
                "url": url,
                "token": token,
                "system_account": system_account,
                "multi_user_mode": manager.config.multi_user_mode,
                "openace_url": openace_url,
            }
        )

    except ValueError as e:
        logger.error(f"Error getting user webui URL: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Internal server error",
                }
            ),
            503,
        )  # Service Unavailable (e.g., max instances reached)

    except Exception as e:
        logger.error(f"Error getting user webui URL: {e}")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Internal server error",
                }
            ),
            500,
        )


@workspace_bp.route("/instances", methods=["GET"])
def list_webui_instances():
    """List all active webui instances (admin only)."""
    from app.services.webui_manager import get_webui_manager

    # Check if user is admin
    if not hasattr(g, "user") or not g.user:
        return jsonify({"error": "Not authenticated"}), 401

    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    try:
        manager = get_webui_manager()
        instances = manager.get_all_instances()

        return jsonify(
            {
                "success": True,
                "instances": instances,
                "active_count": manager.get_instance_count(),
                "max_instances": manager.config.max_instances,
            }
        )

    except Exception as e:
        logger.error(f"Error listing webui instances: {e}")
        return jsonify({"error": "Internal server error"}), 500


@workspace_bp.route("/instances/<int:user_id>/stop", methods=["POST"])
def stop_user_webui_instance(user_id):
    """Stop a specific user's webui instance (admin only)."""
    from app.services.webui_manager import get_webui_manager

    # Check if user is admin
    if not hasattr(g, "user") or not g.user:
        return jsonify({"error": "Not authenticated"}), 401

    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    try:
        manager = get_webui_manager()
        manager.stop_user_webui(user_id)

        return jsonify(
            {
                "success": True,
                "message": f"Stopped webui instance for user {user_id}",
            }
        )

    except Exception as e:
        logger.error(f"Error stopping webui instance: {e}")
        return jsonify({"error": "Internal server error"}), 500


@workspace_bp.route("/instances/stop-all", methods=["POST"])
def stop_all_webui_instances():
    """Stop all webui instances (admin only)."""
    from app.services.webui_manager import get_webui_manager

    # Check if user is admin
    if not hasattr(g, "user") or not g.user:
        return jsonify({"error": "Not authenticated"}), 401

    if g.user.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403

    try:
        manager = get_webui_manager()
        manager.stop_all_instances()

        return jsonify(
            {
                "success": True,
                "message": "All webui instances stopped",
            }
        )

    except Exception as e:
        logger.error(f"Error stopping all webui instances: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ==================== Workspace Status ====================


@workspace_bp.route("/status", methods=["GET"])
def get_workspace_status():
    """Get workspace status including today's token and request usage for current user."""
    from datetime import datetime

    from app.repositories.usage_repo import UsageRepository
    from app.repositories.user_repo import UserRepository

    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        today = datetime.now().strftime("%Y-%m-%d")

        # Default quota limits
        tokens_limit = 100000
        requests_limit = 1000
        tokens_used = 0
        requests_used = 0

        if user_id:
            user_repo = UserRepository()
            user = user_repo.get_user_by_id(user_id)

            if user:
                # Get user's quota settings (stored in M units, convert to actual tokens)
                tokens_limit = (user.get("daily_token_quota") or 1) * TOKEN_QUOTA_MULTIPLIER
                requests_limit = user.get("daily_request_quota") or 1000

                # Get today's usage — session-only (agent_sessions) per #1125:
                # the Work page must not read the analysis fact table.
                usage_repo = UsageRepository()
                combined = usage_repo.get_session_only_usage(
                    user_id=user_id,
                    start_date=today,
                    end_date=today,
                )
                tokens_used = combined["tokens"]
                requests_used = combined["requests"]

        status = {
            "tokens_used": tokens_used,
            "tokens_limit": tokens_limit,
            "requests_used": requests_used,
            "requests_limit": requests_limit,
        }

        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting workspace status: {e}")
        return jsonify(
            {
                "tokens_used": 0,
                "tokens_limit": 100000,
                "requests_used": 0,
                "requests_limit": 1000,
            }
        )

#!/usr/bin/env python3
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

from flask import Blueprint, g, jsonify, request

from app.modules.workspace.collaboration import CollaborationManager, SharePermission
from app.modules.workspace.prompt_library import PromptCategory, PromptLibrary, PromptTemplate
from app.modules.workspace.session_manager import SessionManager, SessionType, _param
from app.modules.workspace.state_sync import get_state_sync_manager
from app.modules.workspace.tool_connector import get_tool_connector
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

# Token quotas are stored in M (millions) units
# Convert to actual tokens when comparing with usage
TOKEN_QUOTA_MULTIPLIER = 1_000_000

workspace_bp = Blueprint("workspace", __name__)
auth_service = AuthService()


@workspace_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if token:
        session = auth_service.get_session(token)
        if session:
            g.user = {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "role": session.get("role"),
            }
        else:
            g.user = None
    else:
        g.user = None


# ==================== Prompt Templates ====================


@workspace_bp.route("/prompts", methods=["GET"])
def list_prompts():
    """List prompt templates."""
    try:
        library = PromptLibrary()

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
        return jsonify({"success": False, "error": str(e)}), 500


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

        library = PromptLibrary()
        template_id = library.create_template(template)

        return jsonify({"success": True, "data": {"id": template_id}}), 201
    except Exception as e:
        logger.error(f"Error creating prompt: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["GET"])
def get_prompt(template_id):
    """Get a prompt template."""
    try:
        library = PromptLibrary()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        return jsonify({"success": True, "data": template.to_dict()})
    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["PUT"])
def update_prompt(template_id):
    """Update a prompt template."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        library = PromptLibrary()
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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/<int:template_id>", methods=["DELETE"])
def delete_prompt(template_id):
    """Delete a prompt template."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        library = PromptLibrary()
        success = library.delete_template(template_id, user_id)

        if not success:
            return jsonify({"success": False, "error": "Template not found or not authorized"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting prompt: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/<int:template_id>/render", methods=["POST"])
def render_prompt(template_id):
    """Render a prompt template with variables."""
    try:
        data = request.get_json() or {}
        variables = data.get("variables", {})

        library = PromptLibrary()
        template = library.get_template(template_id)

        if not template:
            return jsonify({"success": False, "error": "Template not found"}), 404

        # Validate required variables
        missing = template.validate_variables(**variables)
        if missing:
            return (
                jsonify(
                    {"success": False, "error": f'Missing required variables: {", ".join(missing)}'}
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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/categories", methods=["GET"])
def get_prompt_categories():
    """Get prompt categories with counts."""
    try:
        library = PromptLibrary()
        categories = library.get_categories()

        return jsonify({"success": True, "data": categories})
    except Exception as e:
        logger.error(f"Error getting categories: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/prompts/featured", methods=["GET"])
def get_featured_prompts():
    """Get featured prompt templates."""
    try:
        limit = int(request.args.get("limit", 10))
        library = PromptLibrary()
        templates = library.get_featured_templates(limit)

        return jsonify({"success": True, "data": [t.to_dict() for t in templates]})
    except Exception as e:
        logger.error(f"Error getting featured prompts: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Sessions ====================


@workspace_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """List agent sessions from daily_messages table.

    Sessions are grouped by agent_session_id (tool process session).
    For PostgreSQL, uses session_stats materialized view for performance.
    """
    try:
        from app.repositories.database import Database, is_postgresql, adapt_sql

        db = Database()

        tool_name = request.args.get("tool_name")
        host_name = request.args.get("host_name")
        search = request.args.get("search")
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))

        conditions = ["session_id IS NOT NULL"]
        params = []

        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)

        if host_name:
            conditions.append("host_name = ?")
            params.append(host_name)

        if search:
            conditions.append("(sender_name LIKE ? OR session_id LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_clause = " AND ".join(conditions)

        # Use materialized view for PostgreSQL (much faster)
        if is_postgresql():
            # Check if session_stats materialized view exists
            mv_check = db.fetch_one(
                "SELECT EXISTS (SELECT FROM pg_matviews WHERE matviewname = 'session_stats')"
            )
            if mv_check and mv_check.get("exists", False):
                # Get total count from materialized view
                count_query = adapt_sql(f"""
                    SELECT COUNT(*) as count
                    FROM session_stats
                    WHERE {where_clause}
                """)
                result = db.fetch_one(count_query, tuple(params))
                total = result["count"] if result else 0
                total_pages = (total + limit - 1) // limit if total > 0 else 1

                # Get paginated sessions from materialized view
                # Note: session_stats already includes project_path column
                offset = (page - 1) * limit
                sessions_query = adapt_sql(f"""
                    SELECT *
                    FROM session_stats
                    WHERE {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """)
                sessions = db.fetch_all(sessions_query, tuple(params + [limit, offset]))

                # Get model for each session from daily_messages (session_stats doesn't have model)
                if sessions:
                    session_ids = [s["session_id"] for s in sessions]
                    model_query = adapt_sql(f"""
                        SELECT agent_session_id as session_id, MAX(model) as model
                        FROM daily_messages
                        WHERE agent_session_id IN ({', '.join(['?' for _ in session_ids])})
                        GROUP BY agent_session_id
                    """)
                    models_data = db.fetch_all(model_query, tuple(session_ids))
                    models_map = {m["session_id"]: m["model"] for m in models_data}
                    for s in sessions:
                        s["model"] = models_map.get(s["session_id"])

                    # Get request_count for each session (session_stats doesn't have request_count)
                    # Request = API calls = assistant + toolResult messages
                    request_query = adapt_sql(f"""
                        SELECT agent_session_id as session_id,
                               SUM(CASE WHEN role IN ('assistant', 'toolResult') THEN 1 ELSE 0 END) as request_count
                        FROM daily_messages
                        WHERE agent_session_id IN ({', '.join(['?' for _ in session_ids])})
                        GROUP BY agent_session_id
                    """)
                    requests_data = db.fetch_all(request_query, tuple(session_ids))
                    requests_map = {r["session_id"]: r["request_count"] for r in requests_data}
                    for s in sessions:
                        s["request_count"] = requests_map.get(s["session_id"], 0)
            else:
                # Fallback to original query if materialized view doesn't exist
                conditions = ["agent_session_id IS NOT NULL"]
                params = []
                if tool_name:
                    conditions.append("tool_name = ?")
                    params.append(tool_name)
                if host_name:
                    conditions.append("host_name = ?")
                    params.append(host_name)
                if search:
                    conditions.append("(sender_name LIKE ? OR agent_session_id LIKE ?)")
                    params.extend([f"%{search}%", f"%{search}%"])
                where_clause = " AND ".join(conditions)

                count_query = adapt_sql(f"""
                    SELECT COUNT(DISTINCT agent_session_id) as count
                    FROM daily_messages
                    WHERE {where_clause}
                """)
                result = db.fetch_one(count_query, tuple(params))
                total = result["count"] if result else 0
                total_pages = (total + limit - 1) // limit if total > 0 else 1

                offset = (page - 1) * limit
                sessions_query = adapt_sql(f"""
                    SELECT
                        agent_session_id as session_id,
                        tool_name,
                        host_name,
                        sender_name,
                        MAX(sender_id) as sender_id,
                        MAX(date) as date,
                        COUNT(*) as message_count,
                        SUM(CASE WHEN role IN ('assistant', 'toolResult') THEN 1 ELSE 0 END) as request_count,
                        SUM(tokens_used) as total_tokens,
                        SUM(input_tokens) as total_input_tokens,
                        SUM(output_tokens) as total_output_tokens,
                        MIN(timestamp) as created_at,
                        MAX(timestamp) as updated_at,
                        MAX(model) as model
                    FROM daily_messages
                    WHERE {where_clause}
                    GROUP BY agent_session_id, tool_name, host_name, sender_name
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """)
                sessions = db.fetch_all(sessions_query, tuple(params + [limit, offset]))
        else:
            # SQLite: use original query
            conditions = ["agent_session_id IS NOT NULL"]
            params = []
            if tool_name:
                conditions.append("tool_name = ?")
                params.append(tool_name)
            if host_name:
                conditions.append("host_name = ?")
                params.append(host_name)
            if search:
                conditions.append("(sender_name LIKE ? OR agent_session_id LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%"])
            where_clause = " AND ".join(conditions)

            count_query = f"""
                SELECT COUNT(DISTINCT agent_session_id) as count
                FROM daily_messages
                WHERE {where_clause}
            """
            result = db.fetch_one(count_query, tuple(params))
            total = result["count"] if result else 0
            total_pages = (total + limit - 1) // limit if total > 0 else 1

            offset = (page - 1) * limit
            sessions_query = f"""
                SELECT
                    agent_session_id as session_id,
                    tool_name,
                    host_name,
                    sender_name,
                    MAX(sender_id) as sender_id,
                    MAX(date) as date,
                    COUNT(*) as message_count,
                    SUM(CASE WHEN role IN ('assistant', 'toolResult') THEN 1 ELSE 0 END) as request_count,
                    SUM(tokens_used) as total_tokens,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    MIN(timestamp) as created_at,
                    MAX(timestamp) as updated_at,
                    MAX(project_path) as project_path,
                    MAX(model) as model
                FROM daily_messages
                WHERE {where_clause}
                GROUP BY agent_session_id, tool_name, host_name, sender_name
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """
            sessions = db.fetch_all(sessions_query, tuple(params + [limit, offset]))

        # Format sessions for response
        formatted_sessions = []
        for s in sessions:
            formatted_sessions.append(
                {
                    "id": None,
                    "session_id": s["session_id"],
                    "session_type": "chat",
                    "title": f"{s['tool_name']} - {s['session_id'][:8]}",
                    "tool_name": s["tool_name"],
                    "host_name": s["host_name"],
                    "user_id": None,
                    "status": "completed",
                    "context": {},
                    "settings": {},
                    "total_tokens": s["total_tokens"] or 0,
                    "total_input_tokens": s["total_input_tokens"] or 0,
                    "total_output_tokens": s["total_output_tokens"] or 0,
                    "message_count": s["message_count"] or 0,
                    "request_count": s.get("request_count") or 0,
                    "model": s.get("model"),
                    "tags": [],
                    "created_at": s["created_at"],
                    "updated_at": s["updated_at"],
                    "completed_at": s["updated_at"],
                    "expires_at": None,
                    "project_path": s.get("project_path"),
                    "messages": [],
                }
            )

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
        return jsonify({"success": False, "error": str(e)}), 500


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

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        # Get project info from request or look up by path
        project_id = data.get("project_id")
        project_path = data.get("project_path")
        
        # If project_path is provided but not project_id, look up the project
        if project_path and not project_id:
            try:
                from app.repositories.project_repo import ProjectRepository
                project_repo = ProjectRepository()
                project = project_repo.get_project_by_path(project_path)
                if project:
                    project_id = project.id
                    # Auto-add user to project if not already
                    if user_id:
                        project_repo.add_user_project(user_id, project_id)
            except Exception as e:
                logger.warning(f"Failed to look up project by path: {e}")

        manager = SessionManager()
        session = manager.create_session(
            tool_name=tool_name,
            user_id=user_id,
            session_type=data.get("session_type", SessionType.CHAT.value),
            title=data.get("title", ""),
            host_name=data.get("host_name", "localhost"),
            context=data.get("context"),
            settings=data.get("settings"),
            model=data.get("model"),
            expires_in_hours=data.get("expires_in_hours"),
            project_id=project_id,
            project_path=project_path,
        )

        return jsonify({"success": True, "data": session.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    """Get a session by ID."""
    try:
        include_messages = request.args.get("include_messages", "false").lower() == "true"

        # First try to get from SessionManager (agent_sessions table)
        manager = SessionManager()
        session = manager.get_session(session_id, include_messages=include_messages)

        if session:
            # Calculate request_count from messages if available
            if include_messages and session.messages:
                session.request_count = sum(
                    1 for m in session.messages if m.role in ('assistant', 'toolResult')
                )
            else:
                # Query request_count from session_messages table
                conn = manager._get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    SELECT COUNT(*) as request_count
                    FROM session_messages
                    WHERE session_id = {_param()}
                    AND role IN ('assistant', 'toolResult')
                    """,
                    (session_id,)
                )
                row = cursor.fetchone()
                session.request_count = row['request_count'] if row else 0
                conn.close()
            return jsonify({"success": True, "data": session.to_dict()})

        # If not found in agent_sessions, try to get from daily_messages
        from scripts.shared.db import get_connection, _execute, _placeholder

        conn = get_connection()
        cursor = conn.cursor()

        # Get session info from daily_messages
        p = _placeholder()
        session_query = f"""
            SELECT
                agent_session_id as session_id,
                tool_name,
                host_name,
                sender_name,
                MAX(sender_id) as sender_id,
                MAX(date) as date,
                COUNT(*) as message_count,
                SUM(CASE WHEN role IN ('assistant', 'toolResult') THEN 1 ELSE 0 END) as request_count,
                SUM(tokens_used) as total_tokens,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                MIN(timestamp) as created_at,
                MAX(timestamp) as updated_at,
                MAX(model) as model
            FROM daily_messages
            WHERE agent_session_id = {p}
            GROUP BY agent_session_id, tool_name, host_name, sender_name
        """
        _execute(cursor, session_query, [session_id])
        session_data = cursor.fetchone()

        if not session_data:
            conn.close()
            return jsonify({"success": False, "error": "Session not found"}), 404

        # Convert to dict if needed
        if not isinstance(session_data, dict):
            session_data = dict(session_data)

        # Get messages if requested
        messages = []
        if include_messages:
            messages_query = f"""
                SELECT
                    id,
                    agent_session_id as session_id,
                    role,
                    content,
                    tokens_used,
                    model,
                    timestamp
                FROM daily_messages
                WHERE agent_session_id = {p}
                ORDER BY timestamp ASC
            """
            _execute(cursor, messages_query, [session_id])
            messages_data = cursor.fetchall()
            messages = [
                {
                    "id": m["id"] if isinstance(m, dict) else m[0],
                    "session_id": m["session_id"] if isinstance(m, dict) else m[1],
                    "role": m["role"] if isinstance(m, dict) else m[2],
                    "content": m["content"] or "" if isinstance(m, dict) else (m[3] or ""),
                    "tokens_used": m["tokens_used"] or 0 if isinstance(m, dict) else (m[4] or 0),
                    "model": m["model"] if isinstance(m, dict) else m[5],
                    "timestamp": m["timestamp"] if isinstance(m, dict) else m[6],
                    "metadata": {},
                }
                for m in messages_data
            ]

        conn.close()

        # Format session for response
        formatted_session = {
            "id": None,
            "session_id": session_data["session_id"],
            "session_type": "chat",
            "title": f"{session_data['tool_name']} - {session_data['session_id'][:8]}",
            "tool_name": session_data["tool_name"],
            "host_name": session_data["host_name"],
            "user_id": None,
            "status": "completed",
            "context": {},
            "settings": {},
            "total_tokens": session_data["total_tokens"] or 0,
            "total_input_tokens": session_data["total_input_tokens"] or 0,
            "total_output_tokens": session_data["total_output_tokens"] or 0,
            "message_count": session_data["message_count"] or 0,
            "request_count": session_data["request_count"] or 0,
            "model": session_data.get("model"),
            "tags": [],
            "created_at": session_data["created_at"],
            "updated_at": session_data["updated_at"],
            "completed_at": session_data["updated_at"],
            "expires_at": None,
            "messages": messages,
        }

        return jsonify({"success": True, "data": formatted_session})
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/<session_id>/messages", methods=["POST"])
def add_session_message(session_id):
    """Add a message to a session."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        role = data.get("role")
        content = data.get("content")

        if not role or not content:
            return jsonify({"success": False, "error": "role and content are required"}), 400

        manager = SessionManager()
        message_id = manager.add_message(
            session_id=session_id,
            role=role,
            content=content,
            tokens_used=data.get("tokens_used", 0),
            model=data.get("model"),
            metadata=data.get("metadata"),
        )

        return jsonify({"success": True, "data": {"message_id": message_id}}), 201
    except Exception as e:
        logger.error(f"Error adding message: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/<session_id>/complete", methods=["POST"])
def complete_session(session_id):
    """Mark a session as completed."""
    try:
        manager = SessionManager()
        success = manager.complete_session(session_id)

        if not success:
            return jsonify({"success": False, "error": "Session not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error completing session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    """Delete a session."""
    try:
        manager = SessionManager()
        success = manager.delete_session(session_id)

        if not success:
            return jsonify({"success": False, "error": "Session not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/<session_id>/restore", methods=["POST"])
def restore_session(session_id):
    """Restore a historical session from daily_messages to workspace.

    The JSONL file already exists at the original location.
    We just need to return the correct URL with sessionId and encodedProjectName
    so qwen-code-webui can load the history from its local file.

    Returns:
        - sessionId: The session ID (same as input for daily_messages sessions)
        - encodedProjectName: The encoded project name for qwen-code-webui
        - tool_name: The tool name
        - url: The workspace URL to access this session
    """
    try:
        from scripts.shared.db import get_connection, _execute, _placeholder

        # Get session info from daily_messages
        conn = get_connection()
        cursor = conn.cursor()
        p = _placeholder()

        session_query = f"""
            SELECT
                agent_session_id as session_id,
                tool_name,
                project_path
            FROM daily_messages
            WHERE agent_session_id = {p}
            LIMIT 1
        """
        _execute(cursor, session_query, [session_id])
        session_data = cursor.fetchone()

        if not session_data:
            conn.close()
            return jsonify({"success": False, "error": "Session not found"}), 404

        # Convert to dict if needed
        if not isinstance(session_data, dict):
            session_data = dict(session_data)

        conn.close()

        tool_name = session_data["tool_name"]
        project_path = session_data.get("project_path")

        # Generate encodedProjectName based on tool
        if tool_name in ["qwen", "claude"]:
            # project_path is already the encoded project name (e.g., "-Users-rhuang-workspace-open-ace")
            # It was extracted from the JSONL file path during fetch
            encoded_project_name = project_path
        elif tool_name == "openclaw":
            # project_path is the agent_name (e.g., "main")
            encoded_project_name = project_path
        else:
            # Unknown tool, use project_path as-is
            encoded_project_name = project_path

        if not encoded_project_name:
            return jsonify({
                "success": False,
                "error": "Project path not found. Cannot restore session without project information."
            }), 404

        # Build workspace URL with sessionId, encodedProjectName, and toolName
        workspace_url = f"/work/workspace?sessionId={session_id}&encodedProjectName={encoded_project_name}&toolName={tool_name}"

        logger.info(f"Restored session {session_id} (tool={tool_name}, project={encoded_project_name})")

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "encoded_project_name": encoded_project_name,
                "tool_name": tool_name,
                "url": workspace_url,
            }
        })
    except Exception as e:
        logger.error(f"Error restoring session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

        manager = SessionManager()
        session = manager.get_session(session_id)

        if not session:
            return jsonify({"success": False, "error": "Session not found"}), 404

        session.title = new_name
        success = manager.update_session(session)

        if not success:
            return jsonify({"success": False, "error": "Failed to update session"}), 500

        return jsonify({"success": True, "data": {"session_id": session_id, "title": new_name}})
    except Exception as e:
        logger.error(f"Error renaming session: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sessions/stats", methods=["GET"])
def get_session_stats():
    """Get session statistics."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        manager = SessionManager()
        stats = manager.get_session_stats(user_id)

        return jsonify({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Error getting session stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/tools/<tool_name>/models", methods=["GET"])
def get_tool_models(tool_name):
    """Get available models for a tool."""
    try:
        connector = get_tool_connector()
        models = connector.get_available_models(tool_name)

        return jsonify({"success": True, "data": models})
    except Exception as e:
        logger.error(f"Error getting tool models: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/sync/stats", methods=["GET"])
def get_sync_stats():
    """Get sync statistics."""
    try:
        manager = get_state_sync_manager()
        stats = manager.get_stats()

        return jsonify({"success": True, "data": stats})
    except Exception as e:
        logger.error(f"Error getting sync stats: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Collaboration ====================


@workspace_bp.route("/teams", methods=["GET"])
def list_teams():
    """List user's teams."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        manager = CollaborationManager()
        teams = manager.list_user_teams(user_id)

        return jsonify({"success": True, "data": [t.to_dict() for t in teams]})
    except Exception as e:
        logger.error(f"Error listing teams: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

        manager = CollaborationManager()
        team = manager.create_team(
            name=name,
            owner_id=user_id,
            description=data.get("description", ""),
            settings=data.get("settings"),
        )

        return jsonify({"success": True, "data": team.to_dict()}), 201
    except Exception as e:
        logger.error(f"Error creating team: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/shares", methods=["GET"])
def list_shares():
    """List sessions shared with user."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        manager = CollaborationManager()
        shares = manager.get_user_shared_sessions(user_id)

        return jsonify({"success": True, "data": [s.to_dict() for s in shares]})
    except Exception as e:
        logger.error(f"Error listing shares: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

        manager = CollaborationManager()
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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/shares/<share_id>", methods=["DELETE"])
def revoke_share(share_id):
    """Revoke a share."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        manager = CollaborationManager()
        success = manager.revoke_share(share_id, user_id)

        if not success:
            return jsonify({"success": False, "error": "Share not found or not authorized"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error revoking share: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/annotations", methods=["GET"])
def get_annotations():
    """Get annotations for a session."""
    try:
        session_id = request.args.get("session_id")
        if not session_id:
            return jsonify({"success": False, "error": "session_id is required"}), 400

        manager = CollaborationManager()
        annotations = manager.get_session_annotations(session_id)

        return jsonify({"success": True, "data": [a.to_dict() for a in annotations]})
    except Exception as e:
        logger.error(f"Error getting annotations: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

        manager = CollaborationManager()
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
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Knowledge Base ====================


@workspace_bp.route("/knowledge", methods=["GET"])
def list_knowledge():
    """List knowledge base entries."""
    try:
        manager = CollaborationManager()

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
        return jsonify({"success": False, "error": str(e)}), 500


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

        manager = CollaborationManager()
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
        return jsonify({"success": False, "error": str(e)}), 500


@workspace_bp.route("/knowledge/<entry_id>", methods=["GET"])
def get_knowledge(entry_id):
    """Get a knowledge base entry."""
    try:
        manager = CollaborationManager()
        entry = manager.get_knowledge_entry(entry_id)

        if not entry:
            return jsonify({"success": False, "error": "Entry not found"}), 404

        return jsonify({"success": True, "data": entry.to_dict()})
    except Exception as e:
        logger.error(f"Error getting knowledge: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Workspace Config ====================


@workspace_bp.route("/config", methods=["GET"])
def get_workspace_config():
    """Get workspace configuration."""
    import json
    import os

    try:
        from app.repositories.database import CONFIG_DIR

        config_path = os.path.join(CONFIG_DIR, "config.json")

        workspace_config = {
            "enabled": False,
            "url": "",
            "multi_user_mode": False,
            "port_range_start": 9000,
            "port_range_end": 9999,
            "max_instances": 30,
            "idle_timeout_minutes": 30,
        }

        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
                workspace = config.get("workspace", {})
                workspace_config["enabled"] = workspace.get("enabled", False)
                workspace_config["url"] = workspace.get("url", "")
                workspace_config["multi_user_mode"] = workspace.get("multi_user_mode", False)
                workspace_config["port_range_start"] = workspace.get("port_range_start", 9000)
                workspace_config["port_range_end"] = workspace.get("port_range_end", 9999)
                workspace_config["max_instances"] = workspace.get("max_instances", 30)
                workspace_config["idle_timeout_minutes"] = workspace.get("idle_timeout_minutes", 30)

        return jsonify(workspace_config)
    except Exception as e:
        logger.error(f"Error getting workspace config: {e}")
        return jsonify({"enabled": False, "url": "", "multi_user_mode": False})


# ==================== Multi-User WebUI ====================


@workspace_bp.route("/user-url", methods=["GET"])
def get_user_webui_url():
    """Get the user-specific webui URL with authentication token.

    In multi-user mode, this will start a new webui instance for the user
    if one doesn't exist. In single-user mode, returns the configured URL.

    Returns:
        JSON with url and token fields.
    """
    from app.services.webui_manager import get_webui_manager
    from app.repositories.user_repo import UserRepository

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
        url, token = manager.get_user_webui_url(user_id, system_account)

        # Update activity timestamp
        manager.update_user_activity(user_id)

        # Build Open-ACE API URL for iframe integration
        # This is needed so qwen-code-webui can call Open-ACE APIs
        from flask import request as flask_request
        openace_url = flask_request.host_url.rstrip('/')

        return jsonify({
            "success": True,
            "url": url,
            "token": token,
            "system_account": system_account,
            "multi_user_mode": manager.config.multi_user_mode,
            "openace_url": openace_url,
        })

    except ValueError as e:
        logger.error(f"Error getting user webui URL: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
        }), 503  # Service Unavailable (e.g., max instances reached)

    except Exception as e:
        logger.error(f"Error getting user webui URL: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500


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

        return jsonify({
            "success": True,
            "instances": instances,
            "active_count": manager.get_instance_count(),
            "max_instances": manager.config.max_instances,
        })

    except Exception as e:
        logger.error(f"Error listing webui instances: {e}")
        return jsonify({"error": str(e)}), 500


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

        return jsonify({
            "success": True,
            "message": f"Stopped webui instance for user {user_id}",
        })

    except Exception as e:
        logger.error(f"Error stopping webui instance: {e}")
        return jsonify({"error": str(e)}), 500


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

        return jsonify({
            "success": True,
            "message": "All webui instances stopped",
        })

    except Exception as e:
        logger.error(f"Error stopping all webui instances: {e}")
        return jsonify({"error": str(e)}), 500


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

                # Get user's system_account for filtering (sender_name format: {system_account}-{hostname}-{tool})
                username = user.get("username", "")
                system_account = user.get("system_account") or username

                # Get today's usage for this user (same logic as /api/quota/status)
                usage_repo = UsageRepository()
                today_stats = usage_repo.get_request_stats_by_user(date=today, user_name=system_account)

                # Aggregate today's stats for this user
                requests_used = sum(stat.get("requests", 0) for stat in today_stats)
                tokens_used = sum(stat.get("tokens", 0) for stat in today_stats)

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

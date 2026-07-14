# mypy: disable-error-code="arg-type"
"""
Open ACE - Autonomous Development Routes

API routes for AI autonomous development workflow management.
"""

from __future__ import annotations

import json
import logging
import os
import pwd
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.auth.decorators import (
    auth_required,
    check_machine_admin_permission,
    validate_session_token,
)
from app.repositories.autonomous_repo import (
    ALLOWED_CONTENT_LANGUAGES,
    DEFAULT_CONTENT_LANGUAGE,
    AutonomousWorkflowRepository,
)
from app.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Maximum retry count for failed workflows
MAX_RETRY_COUNT = 5

# Lazy repo — avoids creating DB connection at import time
auto_repo: AutonomousWorkflowRepository | None = None
user_repo = UserRepository()


def _get_effective_system_account(system_account: str | None) -> str | None:
    """Check if current user is already the target user.

    When NoNewPrivileges=true is set in systemd, sudo is blocked.
    If the process is already running as the target user, we can skip sudo.

    Returns None if current user matches target user, otherwise returns system_account.
    """
    if not system_account:
        return None

    current_user = pwd.getpwuid(os.getuid()).pw_name
    if current_user == system_account:
        return None

    return system_account


def _run_as_user(system_account: str, command: list) -> subprocess.CompletedProcess:
    """Run a command as a specific user using sudo."""
    sudo_cmd = ["sudo", "-u", system_account] + command
    return subprocess.run(sudo_cmd, capture_output=True, text=True, timeout=10)


def _get_repo() -> AutonomousWorkflowRepository:
    """Lazy-initialize the repository to avoid DB connection at import time."""
    global auto_repo
    if auto_repo is None:
        auto_repo = AutonomousWorkflowRepository()
    return auto_repo


autonomous_bp = Blueprint("autonomous", __name__)


# ── Feature Toggle Guard ─────────────────────────────────────────────


@autonomous_bp.before_request
def check_autonomous_enabled():
    """Reject all requests if autonomous development feature is disabled."""
    from app.utils.config import is_autonomous_enabled

    if not is_autonomous_enabled():
        return (
            jsonify({"error": "Autonomous development feature is disabled", "disabled": True}),
            403,
        )


# ── Rate Limiter ─────────────────────────────────────────────────────


class _RateLimiter:
    """Simple in-memory rate limiter: max *max_count* actions per user per *window* seconds."""

    def __init__(self, max_count: int = 10, window: int = 3600) -> None:
        self._max_count = max_count
        self._window = window
        self._hits: dict[int, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, user_id: int) -> bool:
        """Return True if the user is within the rate limit."""
        now = time.time()
        with self._lock:
            timestamps = self._hits.get(user_id, [])
            # Prune expired entries
            timestamps = [ts for ts in timestamps if now - ts < self._window]
            if len(timestamps) >= self._max_count:
                self._hits[user_id] = timestamps
                return False
            timestamps.append(now)
            self._hits[user_id] = timestamps
            return True


_workflow_rate_limiter = _RateLimiter(max_count=10, window=3600)

# Shared mapping from workflow phase to active status
PHASE_TO_STATUS = {
    "preparation": "preparing",
    "planning": "planning",
    "development": "developing",
    "pr_review": "pr_review",
    "report": "reporting",
    "wait": "waiting",
    "merge": "merging",
}

ISSUE_URL_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/issues/(\d+)(?:[/?#].*)?$", re.I)
ISSUE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def _get_event_emitter():
    """Get or lazily create the event emitter singleton."""
    from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter

    return AutonomousEventEmitter.instance()


def _resolve_milestone_session_id(milestone: dict[str, Any]) -> str:
    """Prefer review session when present because it is the latest LLM round."""
    return milestone.get("review_session_id") or milestone.get("session_id") or ""


def _extract_cli_session_id(session_row: Any) -> str:
    """Return the mapped real CLI session id for a workflow-owned session row."""
    if not session_row:
        return ""

    cli_session_id = getattr(session_row, "cli_session_id", "")
    if not cli_session_id and isinstance(session_row, dict):
        cli_session_id = session_row.get("cli_session_id", "")
    if cli_session_id:
        return str(cli_session_id).strip()

    context = getattr(session_row, "context", None)
    if context is None and isinstance(session_row, dict):
        context = session_row.get("context", {})
    if isinstance(context, dict):
        return str(context.get("cli_session_id", "") or "").strip()
    return ""


def _resolve_actual_session_id(
    session_id: str, session_manager: Any | None, cache: dict[str, str] | None = None
) -> str:
    """Resolve a workflow-owned tracking session id to the real CLI session id."""
    if not session_id:
        return ""
    if cache is not None and session_id in cache:
        return cache[session_id]
    if session_manager is None:
        if cache is not None:
            cache[session_id] = session_id
        return session_id

    actual_session_id = session_id
    try:
        session_row = session_manager.get_session(session_id)
        cli_session_id = _extract_cli_session_id(session_row)
        if cli_session_id:
            actual_session_id = cli_session_id
    except Exception:
        logger.warning("Failed to resolve actual milestone session id", exc_info=True)

    if cache is not None:
        cache[session_id] = actual_session_id
    return actual_session_id


def _enforce_quota_gate(user_id: int) -> Response | None:
    """Fail-closed quota pre-check for workflow creation paths.

    Autonomous dev consumes tokens that bypass the LLM proxy (local agents
    connect to the model API directly), so the proxy's 429 can't bound them —
    enforce here instead. Returns a 429 ``Response`` to short-circuit the
    caller when the user is over quota *or* the quota check itself errors
    (fail-closed); returns ``None`` to proceed. Shared by ``create_workflow``
    and ``fork_milestone`` so the fork path can't bypass the gate.
    """
    try:
        from app.modules.governance.quota_manager import QuotaManager

        quota_result = QuotaManager().check_quota(user_id)
        if not quota_result["allowed"]:
            return jsonify({"error": quota_result["reason"] or "Quota exceeded"}), 429
    except Exception as exc:
        logger.error("Quota check failed, denying workflow creation for safety: %s", exc)
        return jsonify({"error": "Quota check unavailable - request denied for safety"}), 429
    return None


def _enrich_milestones_with_usage(
    workflow_id: str, milestones: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach per-milestone LLM usage/session metadata for the timeline UI."""
    repo = _get_repo()
    usage_by_milestone = repo.get_milestone_usage_summary(workflow_id, milestones)
    session_manager = None
    session_cache: dict[str, str] = {}
    try:
        from app.modules.workspace.session_manager import SessionManager

        session_manager = SessionManager()
    except Exception:
        logger.warning("Failed to initialize SessionManager for milestone enrichment")
    enriched: list[dict[str, Any]] = []
    for milestone in milestones:
        milestone_id = milestone.get("milestone_id", "")
        usage = usage_by_milestone.get(milestone_id, {})
        llm_session_id = usage.get("llm_session_id") or _resolve_milestone_session_id(milestone)
        enriched.append(
            {
                **milestone,
                "llm_session_id": llm_session_id,
                "actual_llm_session_id": _resolve_actual_session_id(
                    llm_session_id, session_manager, session_cache
                ),
                "llm_total_tokens": usage.get("llm_total_tokens", 0),
                "llm_request_count": usage.get("llm_request_count", 0),
            }
        )
    return enriched


def _parse_commit_shas(commit_shas_raw: str) -> list[str]:
    """Parse milestone commit_shas field into a normalized SHA list."""
    if not commit_shas_raw:
        return []
    try:
        shas = (
            json.loads(commit_shas_raw)
            if commit_shas_raw.startswith("[")
            else commit_shas_raw.split(",")
        )
    except (ValueError, TypeError):
        shas = [commit_shas_raw]
    return [str(sha).strip().strip('"').strip("'") for sha in shas if str(sha).strip()]


def _enrich_milestones_with_diff_stats(
    workflow: dict[str, Any], milestones: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Backfill missing diff_stats for milestones that have commits and persist them."""
    project_path = workflow.get("worktree_path") or workflow.get("project_path", "")
    if not project_path:
        return milestones

    repo = _get_repo()
    targets = [
        milestone
        for milestone in milestones
        if milestone.get("commit_shas") and not milestone.get("diff_stats")
    ]
    if not targets:
        return milestones

    from app.modules.workspace.autonomous.github_ops import GitHubOps
    from app.repositories.user_repo import UserRepository

    # Get system_account for sudo execution (Issue #1530)
    system_account = workflow.get("system_account")
    if not system_account:
        user = UserRepository().get_user_by_id(workflow.get("user_id"))
        system_account = user.get("system_account") if user else None

    gh = GitHubOps(project_path, system_account=system_account)
    by_id: dict[str, dict[str, Any]] = {}
    for milestone in targets:
        milestone_id = milestone.get("milestone_id", "")
        shas = _parse_commit_shas(milestone.get("commit_shas", ""))
        if not shas:
            continue

        additions = 0
        deletions = 0
        files = 0
        commits = 0
        had_error = False
        for sha in shas:
            try:
                stats = gh.get_commit_diff_stats(sha)
                additions += int(stats.get("additions", 0) or 0)
                deletions += int(stats.get("deletions", 0) or 0)
                files += int(stats.get("files", 0) or 0)
                commits += int(stats.get("commits", 0) or 0)
            except Exception as exc:
                had_error = True
                logger.warning(
                    "Failed to backfill diff stats for %s (%s): %s",
                    milestone_id[:8],
                    sha[:8],
                    exc,
                )

        if had_error:
            continue

        diff_stats_json = json.dumps(
            {
                "additions": additions,
                "deletions": deletions,
                "files": files,
                "commits": commits,
            }
        )
        try:
            repo.update_milestone(milestone_id, {"diff_stats": diff_stats_json})
        except Exception as exc:
            logger.warning("Failed to persist diff stats for %s: %s", milestone_id[:8], exc)
            continue

        by_id[milestone_id] = {
            **milestone,
            "diff_stats": diff_stats_json,
        }

    if not by_id:
        return milestones
    return [by_id.get(milestone.get("milestone_id", ""), milestone) for milestone in milestones]


def _normalize_issue_url(value: str) -> str:
    """Trim whitespace and trailing slash from an issue URL."""
    return value.strip().rstrip("/")


def _parse_issue_selectors(raw_input: str) -> tuple[list[dict], list[str]]:
    """Parse issue selectors from mixed text/URL/range input."""
    selectors: list[dict] = []
    ignored_tokens: list[str] = []
    seen_numbers: set[int] = set()

    for token in [part.strip() for part in re.split(r"[\s,]+", raw_input or "") if part.strip()]:
        url_match = ISSUE_URL_RE.match(token)
        if url_match:
            issue_number = int(url_match.group(1))
            if issue_number not in seen_numbers:
                selectors.append(
                    {
                        "issue_number": issue_number,
                        "requirements_issue_url": _normalize_issue_url(token),
                    }
                )
                seen_numbers.add(issue_number)
            continue

        range_match = ISSUE_RANGE_RE.match(token)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= 0 or end <= 0 or start > end:
                ignored_tokens.append(token)
                continue
            for issue_number in range(start, end + 1):
                if issue_number in seen_numbers:
                    continue
                selectors.append(
                    {
                        "issue_number": issue_number,
                        "requirements_issue_url": "",
                    }
                )
                seen_numbers.add(issue_number)
            continue

        if token.isdigit():
            issue_number = int(token)
            if issue_number <= 0:
                ignored_tokens.append(token)
                continue
            if issue_number not in seen_numbers:
                selectors.append(
                    {
                        "issue_number": issue_number,
                        "requirements_issue_url": "",
                    }
                )
                seen_numbers.add(issue_number)
            continue

        ignored_tokens.append(token)

    return selectors, ignored_tokens


def _format_issue_title(base_title: str, issue_number: int, is_multi: bool) -> str:
    """Build a workflow title for issue-based batches."""
    clean_title = (base_title or "").strip()
    if clean_title:
        return f"{clean_title} (#{issue_number})" if is_multi else clean_title
    return f"Issue #{issue_number}"


def _serialize_definition_snapshot(snapshot: dict[str, Any]) -> str:
    """Serialize a workflow definition snapshot for storage."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def _build_definition_snapshot(
    data: dict,
    requirements_text: str,
    requirements_issue_input: str,
    requirements_issue_url: str,
    issue_selectors: list[dict] | None = None,
    ignored_issue_tokens: list[str] | None = None,
    batch_id: str | None = None,
    batch_order: int | None = None,
    batch_total: int | None = None,
    resolved_issue_number: int | None = None,
    resolved_issue_url: str | None = None,
) -> dict[str, Any]:
    """Capture the creation-time workflow definition before runtime fields drift."""
    return {
        "title": data.get("title", ""),
        "requirements_mode": "text" if requirements_text else "issue_input",
        "requirements_text": data.get("requirements_text", ""),
        "requirements_issue_input_raw": data.get("requirements_issue_input", ""),
        "requirements_issue_url_raw": data.get("requirements_issue_url", ""),
        "parsed_issue_selectors": issue_selectors or [],
        "ignored_issue_tokens": ignored_issue_tokens or [],
        "project_path": data.get("project_path", ""),
        "project_repo_url": data.get("project_repo_url", ""),
        "is_new_project": data.get("is_new_project", False),
        "is_private": data.get("is_private", True),
        "cli_tool": data.get("cli_tool", ""),
        "model": data.get("model", ""),
        "permission_mode": data.get("permission_mode", "auto-edit"),
        "branch_strategy": data.get("branch_strategy", "new-branch"),
        "branch_name": data.get("branch_name", ""),
        "workspace_type": data.get("workspace_type", "local"),
        "remote_machine_id": data.get("remote_machine_id", ""),
        "max_plan_rounds": data.get("max_plan_rounds", 3),
        "max_pr_review_rounds": data.get("max_pr_review_rounds", 5),
        "require_full_review_rounds": data.get("require_full_review_rounds", False),
        "auto_merge": data.get("auto_merge", True),
        "batch_id": batch_id,
        "batch_order": batch_order,
        "batch_total": batch_total,
        "resolved_issue_number": resolved_issue_number,
        "resolved_issue_url": resolved_issue_url,
    }


def _parse_definition_snapshot(value: Any) -> dict[str, Any] | None:
    """Parse a stored definition snapshot into the API response shape."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _workflow_response(workflow: dict | None) -> dict | None:
    """Normalize workflow records before sending them over the API."""
    if not workflow:
        return workflow
    normalized = dict(workflow)
    normalized["definition_snapshot"] = _parse_definition_snapshot(
        normalized.get("definition_snapshot")
    )
    if normalized.get("require_full_review_rounds") is None:
        normalized["require_full_review_rounds"] = False
    # content_language fallback for legacy workflows created before the column
    # existed (NULL/empty → default). Persisted AI content is unaffected; this
    # only provides a sensible default for downstream consumers.
    if normalized.get("content_language") not in ALLOWED_CONTENT_LANGUAGES:
        normalized["content_language"] = DEFAULT_CONTENT_LANGUAGE
    return normalized


def _parse_int_arg(name: str, default: int) -> int:
    """Parse integer query params without turning bad input into a 500."""
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


# ── Workflow CRUD ───────────────────────────────────────────────────


@autonomous_bp.route("/workflows", methods=["POST"])
@auth_required
def create_workflow():
    """Create a new autonomous development workflow."""
    data = request.get_json(silent=True) or {}
    user_id = g.user_id
    requirements_text = (data.get("requirements_text") or "").strip()
    requirements_issue_input = (data.get("requirements_issue_input") or "").strip()
    requirements_issue_url = (data.get("requirements_issue_url") or "").strip()
    is_issue_mode = not requirements_text and bool(
        requirements_issue_input or requirements_issue_url
    )

    # Rate limit: max 10 workflows per user per hour
    if not _workflow_rate_limiter.is_allowed(user_id):
        return jsonify({"error": "Rate limit exceeded: max 10 workflows per hour"}), 429

    # Quota gate (fail-closed): over-quota users may not create workflows.
    quota_rejection = _enforce_quota_gate(user_id)
    if quota_rejection is not None:
        return quota_rejection

    # Validate remote machine admin permission
    workspace_type = data.get("workspace_type", "local")
    remote_machine_id = data.get("remote_machine_id", "")
    if workspace_type == "remote" and remote_machine_id:
        if g.user_role != "admin":
            if not check_machine_admin_permission(user_id, remote_machine_id):
                return (
                    jsonify({"error": "Machine admin permission required for remote workflows"}),
                    403,
                )

    # Validate local workspace permission
    # Admin users bypass permission validation (they have full system access)
    if workspace_type == "local" and g.user_role != "admin":
        user = user_repo.get_user_by_id(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        system_account = user.get("system_account")
        if not system_account:
            return jsonify({"error": "User does not have a system account configured"}), 400

        project_path = data.get("project_path", "")
        if project_path:
            effective_system_account = _get_effective_system_account(system_account)
            if effective_system_account:
                # Check if user can access the project path
                result = _run_as_user(effective_system_account, ["test", "-e", project_path])
                if result.returncode != 0:
                    return jsonify({"error": f"Cannot access project path: {project_path}"}), 403
                # Check if user has read permission
                result = _run_as_user(effective_system_account, ["test", "-r", project_path])
                if result.returncode != 0:
                    return (
                        jsonify({"error": f"No read permission for project path: {project_path}"}),
                        403,
                    )
                # Check if user has write permission (required for autonomous development)
                result = _run_as_user(effective_system_account, ["test", "-w", project_path])
                if result.returncode != 0:
                    return (
                        jsonify({"error": f"No write permission for project path: {project_path}"}),
                        403,
                    )
            else:
                # Already running as target user, check directly
                if not os.path.exists(project_path):
                    return jsonify({"error": f"Cannot access project path: {project_path}"}), 403
                if not os.access(project_path, os.R_OK):
                    return (
                        jsonify({"error": f"No read permission for project path: {project_path}"}),
                        403,
                    )
                if not os.access(project_path, os.W_OK):
                    return (
                        jsonify({"error": f"No write permission for project path: {project_path}"}),
                        403,
                    )

    # Validate required fields
    if not requirements_text and not requirements_issue_input and not requirements_issue_url:
        return (
            jsonify(
                {
                    "error": "requirements_text, requirements_issue_input, or requirements_issue_url is required"
                }
            ),
            400,
        )

    if not data.get("cli_tool"):
        return jsonify({"error": "cli_tool is required"}), 400

    if not data.get("project_path") and not data.get("is_new_project"):
        return jsonify({"error": "project_path is required for existing projects"}), 400

    # Validate project_path security
    project_path = data.get("project_path", "")
    if project_path:
        # Check original path is absolute (before normalization)
        if not os.path.isabs(project_path):
            return jsonify({"error": "project_path must be an absolute path"}), 400
        if ".." in project_path.split(os.sep):
            return jsonify({"error": "project_path must not contain path traversal"}), 400

    # Get user's system_account for workflow persistence (Issue #1530)
    user = user_repo.get_user_by_id(user_id)
    user_system_account = user.get("system_account", "") if user else ""

    # Pre-generate branch_name and worktree_path for batch workflows (Issue #1573)
    # This ensures scheduler can perform conflict checks even before preparation phase.
    project_path = data.get("project_path", "")
    branch_strategy = data.get("branch_strategy", "new-branch")
    user_branch_name = data.get("branch_name", "")  # User's original input (if any)

    base_workflow_data = {
        "user_id": user_id,
        "title": data.get("title", ""),
        "requirements_text": requirements_text,
        "requirements_issue_url": requirements_issue_url,
        "project_path": project_path,
        "project_repo_url": data.get("project_repo_url", ""),
        "is_new_project": data.get("is_new_project", False),
        "is_private": data.get("is_private", True),
        "cli_tool": data.get("cli_tool", ""),
        "model": data.get("model", ""),
        "permission_mode": data.get("permission_mode", "auto-edit"),
        "branch_strategy": branch_strategy,
        "workspace_type": data.get("workspace_type", "local"),
        "remote_machine_id": data.get("remote_machine_id", ""),
        "max_plan_rounds": data.get("max_plan_rounds", 3),
        "max_pr_review_rounds": data.get("max_pr_review_rounds", 5),
        "require_full_review_rounds": data.get("require_full_review_rounds", False),
        "auto_merge": data.get("auto_merge", True),  # Auto merge PR for batch workflows
        # Persist the workflow's content language at creation time. This is the
        # source of truth for AI-authored content (plan/review/tldr) and does
        # not change per viewer. Validate against the supported set; fall back
        # to the default for anything missing/unknown.
        "content_language": (
            data.get("content_language")
            if data.get("content_language") in ALLOWED_CONTENT_LANGUAGES
            else DEFAULT_CONTENT_LANGUAGE
        ),
        # Persist system_account for sudo execution (Issue #1530)
        "system_account": user_system_account,
    }

    try:
        repo = _get_repo()

        if is_issue_mode:
            raw_issue_input = requirements_issue_input or requirements_issue_url
            issue_selectors, ignored_issue_tokens = _parse_issue_selectors(raw_issue_input)
            if not issue_selectors:
                return jsonify({"error": "No valid GitHub issue selectors found"}), 400

            is_multi_issue = len(issue_selectors) > 1
            batch_id = str(uuid.uuid4()) if is_multi_issue else None
            created_workflows = []

            # Lock base commit SHA for batch workflows to prevent race condition (Issue #1552)
            base_commit_sha = None
            if is_multi_issue:
                project_path = data.get("project_path", "")
                system_account = user_system_account
                if project_path:
                    from app.modules.workspace.autonomous.github_ops import GitHubOps

                    try:
                        gh = GitHubOps(project_path, system_account=system_account)
                        base_commit_sha = gh._run_git(["rev-parse", "origin/main"]).stdout.strip()
                        if base_commit_sha:
                            logger.info(
                                "Locked base_commit_sha %s for batch %s",
                                base_commit_sha[:8],
                                batch_id[:8] if batch_id else "unknown",
                            )
                    except Exception as e:
                        logger.warning(
                            "Failed to lock base_commit_sha for batch %s: %s. Will use dynamic origin/main.",
                            batch_id[:8] if batch_id else "unknown",
                            e,
                        )

            for index, selector in enumerate(issue_selectors, start=1):
                workflow_data = dict(base_workflow_data)

                # Pre-generate workflow_id, branch_name and worktree_path (Issue #1573)
                # This ensures scheduler can perform conflict checks before preparation phase.
                workflow_id = str(uuid.uuid4())
                branch_name = f"auto-dev/{workflow_id[:8]}"
                # Use .worktrees directory for worktree strategy, with full workflow_id for uniqueness
                worktree_path = (
                    os.path.join(project_path, ".worktrees", workflow_id) if project_path else ""
                )

                definition_snapshot = _build_definition_snapshot(
                    data,
                    requirements_text,
                    requirements_issue_input,
                    requirements_issue_url,
                    issue_selectors=issue_selectors,
                    ignored_issue_tokens=ignored_issue_tokens,
                    batch_id=batch_id,
                    batch_order=index if is_multi_issue else None,
                    batch_total=len(issue_selectors) if is_multi_issue else None,
                    resolved_issue_number=selector["issue_number"],
                    resolved_issue_url=selector["requirements_issue_url"],
                )
                workflow_data.update(
                    {
                        "workflow_id": workflow_id,  # Pre-generated for branch_name/worktree_path
                        "title": _format_issue_title(
                            base_workflow_data.get("title", ""),
                            selector["issue_number"],
                            is_multi_issue,
                        ),
                        "requirements_text": "",
                        "requirements_issue_url": selector["requirements_issue_url"],
                        "github_issue_number": selector["issue_number"],
                        "batch_id": batch_id,
                        "batch_order": index if is_multi_issue else None,
                        "batch_total": len(issue_selectors) if is_multi_issue else None,
                        "base_commit_sha": base_commit_sha,  # Locked SHA for batch (Issue #1552)
                        "status": "pending" if index == 1 else "queued",
                        "definition_snapshot": _serialize_definition_snapshot(definition_snapshot),
                        # Pre-generated values for scheduler conflict checks (Issue #1573)
                        "branch_name": branch_name,
                        "worktree_path": worktree_path,
                        "branch_strategy": "worktree",  # Force worktree for batch workflows
                        "original_branch_name": user_branch_name,  # Preserve user's original input
                    }
                )
                workflow = repo.create_workflow(workflow_data)
                if not workflow:
                    return jsonify({"error": "Failed to create workflow"}), 500
                created_workflows.append(workflow)
                _emit_event_safe(
                    workflow["workflow_id"],
                    "workflow_created",
                    {"workflow_id": workflow["workflow_id"], "title": workflow.get("title", "")},
                )

            response_data: dict[str, Any] = {
                "success": True,
                "workflow": _workflow_response(created_workflows[0]),
            }
            if is_multi_issue:
                response_data["workflows"] = [
                    _workflow_response(workflow) for workflow in created_workflows
                ]
            if ignored_issue_tokens:
                response_data["ignored_issue_tokens"] = ignored_issue_tokens
            return jsonify(response_data), 201

        base_workflow_data["definition_snapshot"] = _serialize_definition_snapshot(
            _build_definition_snapshot(
                data,
                requirements_text,
                requirements_issue_input,
                requirements_issue_url,
            )
        )

        # Pre-generate workflow_id, branch_name and worktree_path for worktree strategy (Issue #1573)
        # For worktree strategy, always use pre-generated values to ensure scheduler conflict checks work.
        # For new-branch/same-branch strategy, preserve user's original branch_name input.
        workflow_id = str(uuid.uuid4())
        base_workflow_data["workflow_id"] = workflow_id

        if branch_strategy == "worktree":
            # Force pre-generated branch_name for worktree strategy
            branch_name = f"auto-dev/{workflow_id[:8]}"
            worktree_path = (
                os.path.join(project_path, ".worktrees", workflow_id) if project_path else ""
            )
            base_workflow_data["branch_name"] = branch_name
            base_workflow_data["worktree_path"] = worktree_path
            base_workflow_data["original_branch_name"] = user_branch_name  # Preserve user's input
            logger.info(
                "Pre-generated branch_name=%s, worktree_path=%s for workflow %s",
                branch_name,
                worktree_path,
                workflow_id[:8],
            )
        else:
            # For new-branch/same-branch, use user's input if provided, otherwise pre-generate
            if user_branch_name:
                base_workflow_data["branch_name"] = user_branch_name
            elif branch_strategy == "current":
                # Lock to the actual current branch during preparation; avoid
                # showing a misleading auto-generated branch name up front.
                base_workflow_data["branch_name"] = ""
            else:
                branch_name = f"auto-dev/{workflow_id[:8]}"
                base_workflow_data["branch_name"] = branch_name

        workflow = repo.create_workflow(base_workflow_data)
        if not workflow:
            return jsonify({"error": "Failed to create workflow"}), 500

        _emit_event_safe(
            workflow["workflow_id"],
            "workflow_created",
            {"workflow_id": workflow["workflow_id"], "title": workflow.get("title", "")},
        )

        return jsonify({"success": True, "workflow": _workflow_response(workflow)}), 201
    except Exception as e:
        logger.error("Failed to create workflow: %s", e)
        return jsonify({"error": str(e)}), 500


@autonomous_bp.route("/workflows", methods=["GET"])
@auth_required
def list_workflows():
    """List autonomous development workflows."""
    user_id = g.user_id
    is_admin = g.user_role == "admin"

    # Non-admin users can only see their own workflows
    filter_user_id = None if is_admin else user_id
    status = request.args.get("status")
    search = (request.args.get("search") or "").strip() or None
    limit = max(1, min(_parse_int_arg("limit", 50), 200))
    offset = max(0, _parse_int_arg("offset", 0))

    try:
        repo = _get_repo()
        workflows = repo.list_workflows(
            user_id=filter_user_id,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )
        total = repo.count_workflows(
            user_id=filter_user_id,
            status=status,
            search=search,
        )
        return jsonify(
            {
                "success": True,
                "workflows": [_workflow_response(workflow) for workflow in workflows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as e:
        logger.error("Failed to list workflows: %s", e)
        return jsonify({"error": str(e)}), 500


@autonomous_bp.route("/workflows/<workflow_id>", methods=["GET"])
@auth_required
def get_workflow(workflow_id):
    """Get a workflow by ID."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404

    # Check ownership
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    return jsonify({"success": True, "workflow": _workflow_response(workflow)})


@autonomous_bp.route("/workflows/<workflow_id>", methods=["DELETE"])
@auth_required
def delete_workflow(workflow_id):
    """Cancel and delete a workflow."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404

    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    try:
        _get_repo().delete_workflow(workflow_id)
        return jsonify({"success": True})
    except Exception as e:
        logger.error("Failed to delete workflow: %s", e)
        return jsonify({"error": str(e)}), 500


@autonomous_bp.route("/batches/<batch_id>", methods=["DELETE"])
@auth_required
def delete_batch(batch_id):
    """Delete an entire batch of workflows."""
    workflows = _get_repo().list_batch_workflows(batch_id)
    if not workflows:
        return jsonify({"error": "Batch not found"}), 404

    if g.user_role != "admin":
        for workflow in workflows:
            if workflow.get("user_id") != g.user_id:
                return jsonify({"error": "Access denied"}), 403

    try:
        deleted_count = _get_repo().delete_batch(batch_id)
        return jsonify({"success": True, "deleted_count": deleted_count})
    except Exception as e:
        logger.error("Failed to delete batch %s: %s", batch_id, e)
        return jsonify({"error": str(e)}), 500


# ── Workflow Control ────────────────────────────────────────────────


@autonomous_bp.route("/workflows/<workflow_id>/pause", methods=["POST"])
@auth_required
def pause_workflow(workflow_id):
    """Pause a running workflow."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    if workflow.get("status") == "paused":
        return jsonify({"error": "Workflow already paused"}), 400

    # Suspend the running agent subprocess (SIGSTOP)
    _pause_running_task(workflow_id)

    from datetime import datetime, timezone

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": "paused",
            "paused_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    _emit_event_safe(workflow_id, "status_change", {"status": "paused"})
    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/resume", methods=["POST"])
@auth_required
def resume_workflow(workflow_id):
    """Resume a paused workflow."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    if workflow.get("status") != "paused":
        return jsonify({"error": "Workflow is not paused"}), 400

    # Resume the paused agent subprocess (SIGCONT)
    _resume_running_task(workflow_id)

    phase = workflow.get("current_phase", "preparation")
    status = PHASE_TO_STATUS.get(phase, "pending")

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": status,
            "paused_at": None,
        },
    )

    _emit_event_safe(workflow_id, "status_change", {"status": status})
    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/stop", methods=["POST"])
@auth_required
def stop_workflow(workflow_id):
    """Gracefully stop a workflow."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    # Kill the running agent subprocess (SIGTERM → SIGKILL)
    _stop_running_task(workflow_id)

    from datetime import datetime, timezone

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": "cancelled",
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    _emit_event_safe(workflow_id, "status_change", {"status": "cancelled"})

    batch_id = workflow.get("batch_id")
    if batch_id:
        cancelled_count = _get_repo().cancel_queued_batch_workflows(batch_id, workflow_id)
        if cancelled_count:
            for sibling in _get_repo().list_batch_workflows(batch_id):
                if (
                    sibling.get("workflow_id") == workflow_id
                    or sibling.get("status") != "cancelled"
                ):
                    continue
                _emit_event_safe(sibling["workflow_id"], "status_change", {"status": "cancelled"})

    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/extend-planning-timeout", methods=["POST"])
@auth_required
def extend_planning_timeout(workflow_id):
    """Extend the planning phase timeout for a timed-out workflow."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    if workflow.get("status") != "planning_timeout":
        return jsonify({"error": "Workflow is not in planning_timeout status"}), 400

    data = request.get_json(silent=True) or {}
    additional_seconds = data.get("additional_seconds", 600)
    # Clamp to 60s–3600s
    additional_seconds = max(60, min(additional_seconds, 3600))

    # Accumulate extension in planning_timeout_extension field.
    # Orchestrator computes actual timeout as PLANNING_TIMEOUT + extension.
    current_extension = int(workflow.get("planning_timeout_extension", 0) or 0)
    new_extension = current_extension + additional_seconds
    phase = workflow.get("current_phase", "planning")
    status = PHASE_TO_STATUS.get(phase, "planning")

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": status,
            "planning_timeout_extension": new_extension,
            "error_message": "",
        },
    )

    _emit_event_safe(
        workflow_id,
        "status_change",
        {"status": status, "extended_timeout": additional_seconds},
    )
    return jsonify({"success": True, "new_planning_timeout": new_extension})


@autonomous_bp.route("/workflows/<workflow_id>/retry", methods=["POST"])
@auth_required
def retry_workflow(workflow_id):
    """Retry a failed workflow from its current phase."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    if workflow.get("status") not in ("failed", "planning_timeout"):
        return jsonify({"error": "Only failed or timed-out workflows can be retried"}), 400

    # Check retry count limit
    retry_count = workflow.get("retry_count", 0) or 0
    if retry_count >= MAX_RETRY_COUNT:
        return jsonify({"error": f"Maximum retry count ({MAX_RETRY_COUNT}) exceeded"}), 400

    phase = workflow.get("current_phase", "preparation")
    status = PHASE_TO_STATUS.get(phase, "pending")

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": status,
            "error_message": "",
            "retry_count": retry_count + 1,
        },
    )

    _emit_event_safe(workflow_id, "status_change", {"status": status, "phase": phase})
    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/done", methods=["POST"])
@auth_required
def mark_done(workflow_id):
    """Mark workflow as complete, triggering merge phase."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    data = request.get_json(silent=True) or {}
    updates = {
        "current_phase": "merge",
        "status": "merging",
    }
    if data.get("selected_branch"):
        updates["branch_name"] = data["selected_branch"]

    _get_repo().update_workflow(workflow_id, updates)

    _emit_event_safe(workflow_id, "status_change", {"status": "merging", "phase": "merge"})
    return jsonify({"success": True})


# ── Milestone Operations ────────────────────────────────────────────


@autonomous_bp.route("/workflows/<workflow_id>/timeline", methods=["GET"])
@auth_required
def get_timeline(workflow_id):
    """Get all milestones for a workflow (timeline)."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestones = _enrich_milestones_with_usage(
        workflow_id, _get_repo().list_milestones(workflow_id)
    )
    milestones = _enrich_milestones_with_diff_stats(workflow, milestones)
    return jsonify({"success": True, "milestones": milestones})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/cancel", methods=["POST"])
@auth_required
def cancel_milestone(workflow_id, milestone_id):
    """Cancel a milestone and all subsequent milestones with user feedback."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404

    # Parse user feedback (required)
    data = request.get_json(silent=True) or {}
    user_feedback = data.get("user_feedback", "").strip()
    if not user_feedback:
        return jsonify({"error": "user_feedback is required"}), 400
    if len(user_feedback) > 5000:
        return jsonify({"error": "user_feedback too long (max 5000 characters)"}), 400

    cancelled = _get_repo().cancel_milestones_after(workflow_id, milestone_id)

    # Store user feedback and set workflow to waiting state
    _get_repo().update_workflow(
        workflow_id,
        {
            "current_phase": "wait",
            "status": "waiting",
            "user_feedback": user_feedback,
        },
    )

    # Create a requirement_received milestone to record the feedback
    _get_repo().create_milestone(
        {
            "workflow_id": workflow_id,
            "phase": "wait",
            "milestone_type": "requirement_received",
            "status": "completed",
            "title": "User feedback received",
            "description": user_feedback[:500],
            "result_summary": user_feedback[:200],
        }
    )

    _emit_event_safe(
        workflow_id,
        "user_action",
        {
            "action": "cancel_with_feedback",
            "milestone_id": milestone_id,
            "cancelled_count": len(cancelled),
            "has_feedback": True,
        },
    )
    return jsonify({"success": True, "cancelled": len(cancelled)})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/fork", methods=["POST"])
@auth_required
def fork_milestone(workflow_id, milestone_id):
    """Fork from a milestone, creating a new independent workflow.

    Creates a new workflow row with shared history up to the fork point.
    The new workflow starts at preparation and the orchestrator jumps
    to the next phase based on the fork milestone's phase.
    Uses worktree strategy for parallel execution.
    """
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404
    if milestone.get("status") == "cancelled":
        return jsonify({"error": "Cannot fork from a cancelled milestone"}), 400

    # Quota gate (fail-closed): forking creates a runnable workflow, so the
    # owner must pass the same check as create_workflow. Forks call
    # create_workflow() on the repo directly, bypassing the route handler,
    # so the gate must be applied here explicitly.
    owner_id = workflow.get("user_id")
    if owner_id is not None:
        quota_rejection = _enforce_quota_gate(int(owner_id))
        if quota_rejection is not None:
            return quota_rejection

    data = request.get_json(silent=True) or {}
    user_feedback = data.get("user_feedback", "").strip()
    if len(user_feedback) > 5000:
        return jsonify({"error": "user_feedback too long (max 5000 characters)"}), 400
    pause_original = data.get("pause_original", True)
    fork_branch = data.get("branch_name", "") or f"fork/from-{milestone_id[:8]}"

    # Determine the fork milestone's phase (used for fork marker milestone)
    fork_phase = milestone.get("phase", "planning")

    # Create new workflow (copy settings from original)
    fork_workflow_data = {
        "user_id": workflow.get("user_id"),
        "title": f"{workflow.get('title', '')} [Fork]",
        "requirements_text": workflow.get("requirements_text", ""),
        "requirements_issue_url": workflow.get("requirements_issue_url", ""),
        "project_path": workflow.get("project_path", ""),
        "project_repo_url": workflow.get("project_repo_url", ""),
        "is_new_project": False,
        "is_private": workflow.get("is_private", True),
        "cli_tool": workflow.get("cli_tool", "claude-code"),
        "model": workflow.get("model", ""),
        "permission_mode": workflow.get("permission_mode", "auto-edit"),
        "branch_name": fork_branch,
        "branch_strategy": "worktree",  # Force worktree for parallel execution
        "workspace_type": workflow.get("workspace_type", "local"),
        "remote_machine_id": workflow.get("remote_machine_id", ""),
        "max_plan_rounds": workflow.get("max_plan_rounds", 3),
        "max_pr_review_rounds": workflow.get("max_pr_review_rounds", 5),
        "require_full_review_rounds": workflow.get("require_full_review_rounds", False),
        "github_issue_number": workflow.get("github_issue_number"),
        # Fork-specific fields
        "parent_workflow_id": workflow_id,
        "fork_milestone_id": milestone_id,
        "user_feedback": user_feedback,
        "original_branch_name": workflow.get("branch_name", ""),
        # Inherit the parent's content language so forked AI content stays
        # consistent with the original workflow.
        "content_language": workflow.get("content_language", "en"),
        # Inherit the parent's system_account for sudo execution (Issue #1530)
        "system_account": workflow.get("system_account", ""),
        # Start at the next phase (preparation handles worktree + phase jump)
        "status": "pending",
        "current_phase": "preparation",
        "dev_round": milestone.get("dev_round", 1),
    }
    fork_workflow_data["definition_snapshot"] = _serialize_definition_snapshot(
        _build_definition_snapshot(
            fork_workflow_data,
            fork_workflow_data["requirements_text"],
            "",
            fork_workflow_data["requirements_issue_url"],
            resolved_issue_number=workflow.get("github_issue_number"),
            resolved_issue_url=workflow.get("requirements_issue_url", ""),
        )
    )
    fork_workflow = _get_repo().create_workflow(fork_workflow_data)

    # Copy milestones up to fork point to new workflow
    _get_repo().copy_milestones_to_workflow(workflow_id, fork_workflow["workflow_id"], milestone_id)

    # Create fork marker milestone on parent workflow
    _get_repo().create_milestone(
        {
            "workflow_id": workflow_id,
            "phase": fork_phase,
            "dev_round": milestone.get("dev_round", 1),
            "milestone_type": "workflow_forked",
            "status": "completed",
            "title": "Forked to new workflow",
            "parent_milestone_id": milestone_id,
            "fork_branch": fork_branch,
            "fork_workflow_id": fork_workflow["workflow_id"],
            "result_summary": user_feedback[:200] if user_feedback else "",
        }
    )

    # Optionally pause the original workflow
    if pause_original:
        # Suspend the running agent subprocess (SIGSTOP) BEFORE flipping the
        # DB status. Without this the orchestrator thread currently inside
        # _run_agent() keeps running — advance() only checks status at entry,
        # not mid-phase — so the "paused" workflow would continue dev→test→PR
        # to completion despite being forked. Mirrors pause_workflow()'s order.
        _pause_running_task(workflow_id)
        _get_repo().update_workflow(
            workflow_id,
            {
                "status": "paused",
                "paused_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

    # Emit events on both workflows
    _emit_event_safe(
        workflow_id,
        "user_action",
        {
            "action": "fork_milestone",
            "milestone_id": milestone_id,
            "fork_branch": fork_branch,
            "fork_workflow_id": fork_workflow["workflow_id"],
            "pause_original": pause_original,
        },
    )
    _emit_event_safe(
        fork_workflow["workflow_id"],
        "user_action",
        {
            "action": "workflow_created_as_fork",
            "parent_workflow_id": workflow_id,
            "fork_milestone_id": milestone_id,
        },
    )
    return jsonify({"success": True, "fork_workflow": _workflow_response(fork_workflow)})


@autonomous_bp.route("/workflows/<workflow_id>/forks", methods=["GET"])
@auth_required
def get_workflow_forks(workflow_id):
    """List all child workflows forked from this one."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    forks = _get_repo().list_forks(workflow_id)
    return jsonify({"success": True, "forks": forks})


@autonomous_bp.route("/workflows/<workflow_id>/resume-with-feedback", methods=["POST"])
@auth_required
def resume_with_feedback(workflow_id):
    """Resume a waiting workflow with updated user feedback."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403
    if workflow.get("status") not in ("waiting", "paused"):
        return jsonify({"error": "Workflow is not in a resumable state"}), 400

    data = request.get_json(silent=True) or {}
    user_feedback = data.get("user_feedback", "").strip()
    if not user_feedback:
        return jsonify({"error": "user_feedback is required"}), 400
    if len(user_feedback) > 5000:
        return jsonify({"error": "user_feedback too long (max 5000 characters)"}), 400

    # Store feedback and set to waiting (scheduler will pick up via _do_wait)
    _get_repo().update_workflow(
        workflow_id,
        {
            "user_feedback": user_feedback,
            "current_phase": "wait",
            "status": "waiting",
        },
    )

    _emit_event_safe(
        workflow_id,
        "user_action",
        {"action": "resume_with_feedback", "has_feedback": True},
    )
    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/session", methods=["GET"])
@auth_required
def get_milestone_session(workflow_id, milestone_id):
    """Get the agent session associated with a milestone."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404

    session_id = _resolve_milestone_session_id(milestone)
    if not session_id:
        return jsonify({"success": True, "session": None})

    # Use existing session API to get session detail
    from app.modules.workspace.session_manager import SessionManager

    sm = SessionManager()
    actual_session_id = _resolve_actual_session_id(session_id, sm)

    if actual_session_id and actual_session_id != session_id:
        # The resolved actual session is the real Claude transcript, shared
        # across multiple milestones (e.g. plan_created + plan_refined resume
        # the same CLI session). Its messages are not tagged per-milestone
        # (milestone_id is typically NULL), so filtering by message_milestone_id
        # would drop them all and leave the viewer showing only "Status".
        # Return the full transcript for the actual session.
        session_data = sm.get_session(
            actual_session_id,
            include_messages=True,
        )
        if session_data:
            return jsonify({"success": True, "session": session_data})

    session_data = sm.get_session(
        session_id, include_messages=True, message_milestone_id=milestone_id
    )

    return jsonify({"success": True, "session": session_data})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/diff", methods=["GET"])
@auth_required
def get_milestone_diff(workflow_id, milestone_id):
    """Get the code diff for a milestone's commits."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404

    commit_shas_raw = milestone.get("commit_shas", "")
    if not commit_shas_raw:
        return jsonify({"success": True, "diff": ""})

    # Parse commit SHAs (may be JSON array or comma-separated)
    import json as _json

    try:
        shas = (
            _json.loads(commit_shas_raw)
            if commit_shas_raw.startswith("[")
            else commit_shas_raw.split(",")
        )
    except (ValueError, TypeError):
        shas = [commit_shas_raw]

    shas = [s.strip().strip('"').strip("'") for s in shas if s.strip()]

    if not shas:
        return jsonify({"success": True, "diff": ""})

    # Get diff for each commit
    from app.modules.workspace.autonomous.github_ops import GitHubOps
    from app.repositories.user_repo import UserRepository

    project_path = workflow.get("worktree_path") or workflow.get("project_path", "")

    # Get system_account for sudo execution (Issue #1530)
    system_account = workflow.get("system_account")
    if not system_account:
        user = UserRepository().get_user_by_id(workflow.get("user_id"))
        system_account = user.get("system_account") if user else None

    gh = GitHubOps(project_path, system_account=system_account)
    diff_parts = []
    for sha in shas:
        try:
            diff_text = gh.get_commit_diff(sha)
            if diff_text:
                diff_parts.append(f"--- Commit: {sha[:8]} ---\n{diff_text}")
        except Exception as e:
            logger.warning("Failed to get diff for %s: %s", sha[:8], e)

    return jsonify({"success": True, "diff": "\n\n".join(diff_parts)})


@autonomous_bp.route("/workflows/<workflow_id>/pr-diff", methods=["GET"])
@auth_required
def get_workflow_pr_diff(workflow_id):
    """Get the cumulative PR diff (head vs base) for a workflow.

    Returns ``{success, diff, pr_number}``. When the workflow has no PR yet,
    returns ``pr_number=null, diff=""`` (200) so the caller can render an empty
    state instead of erroring. gh failures degrade to an empty diff + warning.
    """
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    pr_number = workflow.get("github_pr_number")
    if not pr_number:
        return jsonify({"success": True, "diff": "", "pr_number": None})

    from app.modules.workspace.autonomous.github_ops import GitHubOps
    from app.repositories.user_repo import UserRepository

    project_path = workflow.get("worktree_path") or workflow.get("project_path", "")

    # Get system_account for sudo execution (Issue #1530)
    system_account = workflow.get("system_account")
    if not system_account:
        user = UserRepository().get_user_by_id(workflow.get("user_id"))
        system_account = user.get("system_account") if user else None

    gh = GitHubOps(project_path, system_account=system_account)
    try:
        diff = gh.get_pr_diff(int(pr_number))
    except Exception as e:
        logger.warning("Failed to get PR diff for #%s: %s", pr_number, e)
        diff = ""

    return jsonify({"success": True, "diff": diff, "pr_number": int(pr_number)})


# ── Real-Time Events (SSE) ─────────────────────────────────────────


@autonomous_bp.route("/workflows/<workflow_id>/events/stream", methods=["GET"])
@auth_required
def stream_workflow_events(workflow_id):
    """SSE stream for real-time workflow events."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    emitter = _get_event_emitter()
    q = emitter.subscribe(workflow_id)

    def generate():
        # Yield immediately so Flask sends response headers — otherwise
        # the browser's EventSource stays in CONNECTING (readyState 0)
        # until the first q.get() returns (up to 30 s).
        yield ": connected\n\n"
        try:
            while True:
                try:
                    event_data = q.get(timeout=30)
                    emitter.mark_read(workflow_id, q)
                    yield f"data: {json.dumps(event_data)}\n\n"
                except queue.Empty:
                    # Re-validate auth on keepalive to detect revoked tokens
                    token = request.cookies.get("session_token") or request.headers.get(
                        "Authorization", ""
                    ).replace("Bearer ", "")
                    if not token or not validate_session_token(token):
                        break  # Token invalid or revoked — close stream
                    emitter.mark_read(workflow_id, q)
                    # Emit as a `data:` event (not an SSE comment line) so the
                    # browser fires onmessage and the frontend stall detector can
                    # reset on every keepalive. See app/routes/remote.py.
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
        except GeneratorExit:
            pass  # cleanup handled by finally
        finally:
            emitter.unsubscribe(workflow_id, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Auxiliary ───────────────────────────────────────────────────────


@autonomous_bp.route("/tools", methods=["GET"])
@auth_required
def get_available_tools():
    """Get the list of available agent tools."""
    tools = [
        {"id": "claude-code", "name": "Claude Code", "executable": "claude"},
        {"id": "qwen-code-cli", "name": "Qwen Code", "executable": "qwen"},
        {"id": "codex", "name": "Codex CLI", "executable": "codex"},
        {"id": "openclaw", "name": "OpenClaw", "executable": "openclaw"},
        {"id": "zcode", "name": "ZCode", "executable": "zcode"},
    ]
    return jsonify({"success": True, "tools": tools})


@autonomous_bp.route("/models", methods=["GET"])
@auth_required
def get_available_models():
    """Get available models for a given tool and workspace type."""
    tool = request.args.get("tool", "")
    workspace_type = request.args.get("workspace_type", "local")
    machine_id = request.args.get("machine_id", "")

    # Resolve tenant. Local is single-tenant (default 1); remote derives it from
    # the machine. ``g.tenant_id`` is never set in the app, so the previous
    # ``getattr`` here always returned None and short-circuited to []. The
    # remote lookup is outside the try below on purpose: an infrastructure
    # failure here should surface (mirrors workspace.py:241-251), not be masked
    # as a "success, no models" response or silently fall back to tenant 1
    # (which could surface another tenant's local-scope keys).
    tenant_id = 1
    if workspace_type == "remote" and machine_id:
        from app.modules.workspace.remote_agent_manager import get_remote_agent_manager

        agent_mgr = get_remote_agent_manager()
        # Guard the machine before reading it — a caller must not read an
        # arbitrary machine's tenant/model list. Mirrors workspace.py:244.
        if not agent_mgr.check_user_access(machine_id, g.user["id"]):
            return (
                jsonify({"success": False, "error": "Machine not found or access denied"}),
                404,
            )
        machine = agent_mgr.get_machine(machine_id) or {}
        tenant_id = machine.get("tenant_id", 1)

    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        api_proxy = APIKeyProxyService()
        # ``scope`` is a query value: 'local'/'remote' match keys tagged with
        # that scope *or* 'shared'; 'shared' itself would match only shared keys
        # and silently miss every remote key. See _list_tool_key_rows
        # (api_key_proxy.py:770-776) and migration 048.
        scope = "remote" if workspace_type == "remote" else "local"
        # Provider-agnostic: keys are matched by cli_tools membership and models
        # are extracted from wherever the tool stores them (modelProviders for
        # qwen/codex, env for claude, top-level `model` for zcode). The openai-only
        # get_tool_model_pool is reserved for functional HA routing.
        pool = api_proxy.get_tool_models(tenant_id=tenant_id, tool_name=tool, scope=scope)
        # The frontend model dropdown renders ``model.name``; entries are keyed by
        # ``id``. Normalize so a display name is always present (fall back to id),
        # and surface ``empty_reason`` for future UX.
        models = [
            {"name": m.get("name") or m.get("id") or str(m), **m}
            for m in pool.get("models", [])
            if isinstance(m, dict)
        ]
        return jsonify(
            {
                "success": True,
                "models": models,
                "empty_reason": pool.get("empty_reason"),
            }
        )
    except Exception as e:
        logger.error("Failed to get models: %s", e)
        return jsonify({"success": True, "models": []})


# ── Helper ──────────────────────────────────────────────────────────


def _emit_event_safe(workflow_id: str, event_type: str, data: dict):
    """Safely emit an event without failing the request."""
    try:
        _get_event_emitter().emit(workflow_id, event_type, data)
    except Exception:
        pass


def _cancel_running_task(workflow_id: str):
    """Legacy: cancel a running task. Delegates to _stop_running_task."""
    _stop_running_task(workflow_id)


def _pid_matches_expected(pid: int) -> bool:
    """Best-effort check that PID likely belongs to a CLI agent process.

    Helps guard against PID recycling on long-running servers by verifying
    the process command line contains known agent binary names.
    """
    try:
        import subprocess as _sp

        result = _sp.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return False  # process doesn't exist
        comm = result.stdout.strip().lower()
        return any(
            name in comm for name in ("claude", "qwen", "codex", "openclaw", "zcode", "node")
        )
    except Exception:
        # If we can't check, assume it matches (conservative: try to stop it)
        return True


def _kill_pid(pid: int) -> bool:
    """Kill a process by PID with SIGTERM -> SIGKILL escalation.

    Works with process groups created via ``start_new_session=True``.
    Returns True if the process was killed or was already gone.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return True  # already gone

    # Stage 1: SIGTERM
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return True

    # Stage 2: poll for exit, up to 5 seconds
    for _ in range(10):
        time.sleep(0.5)
        try:
            os.killpg(pgid, 0)  # existence check
        except (ProcessLookupError, OSError):
            return True  # died after SIGTERM

    # Stage 3: SIGKILL
    logger.warning("PID %d still alive after SIGTERM, sending SIGKILL", pid)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        return True

    # Final check
    time.sleep(1)
    try:
        os.killpg(pgid, 0)
        return False  # still alive even after SIGKILL
    except (ProcessLookupError, OSError):
        return True


def _suspend_pid(pid: int) -> bool:
    """Suspend a process by PID using SIGSTOP."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return True

    try:
        os.killpg(pgid, signal.SIGSTOP)
        return True
    except (ProcessLookupError, OSError):
        return True


def _resume_pid(pid: int) -> bool:
    """Resume a suspended process by PID using SIGCONT."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return True

    try:
        os.killpg(pgid, signal.SIGCONT)
        return True
    except (ProcessLookupError, OSError):
        return True


def _mark_sessions_paused(workflow_id: str, pids: set[int]) -> None:
    """Flag the in-memory sessions owning ``pids`` as paused.

    The pause fallback paths (``_pause_running_task`` Strategy 2/3) deliver
    SIGSTOP directly to a PID without going through the runner's
    ``pause_session``, so the session's ``_paused`` Event stays clear and
    ``_wait_for_completion`` keeps draining the timeout budget — eventually
    reaping the frozen process and making a paused workflow appear to
    auto-resume. This syncs the flag for every PID we actually suspended so
    the budget freezes correctly regardless of the delivery path.
    """
    if not pids:
        return
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        orchestrator = AutonomousScheduler.instance().get_running_orchestrator(workflow_id)
        if orchestrator:
            for pid in pids:
                orchestrator._runner.mark_session_paused_by_pid(pid)
    except Exception as e:
        logger.warning("Failed to mark sessions paused for %s: %s", workflow_id[:8], e)


def _mark_sessions_resumed(workflow_id: str, pids: set[int]) -> None:
    """Clear the paused flag on the in-memory sessions owning ``pids``.

    Mirror of :func:`_mark_sessions_paused` for the resume fallback paths,
    so ``_wait_for_completion`` unfreezes the deadline.
    """
    if not pids:
        return
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        orchestrator = AutonomousScheduler.instance().get_running_orchestrator(workflow_id)
        if orchestrator:
            for pid in pids:
                orchestrator._runner.mark_session_resumed_by_pid(pid)
    except Exception as e:
        logger.warning("Failed to mark sessions resumed for %s: %s", workflow_id[:8], e)


def _pause_running_task(workflow_id: str):
    """Pause the running agent task using multiple strategies.

    Strategy 1: In-memory orchestrator → orchestrator.pause_current_task()
    Strategy 2: PID from database → direct SIGSTOP
    Strategy 3: Scan runner's in-memory sessions → SIGSTOP matching sessions
    """
    affected_pids: set[int] = set()

    # Strategy 1: in-memory orchestrator (fast path)
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            orchestrator.pause_current_task()
            try:
                with orchestrator._session_lock:
                    sid = orchestrator._current_session_id
                if sid:
                    session = orchestrator._runner._local_sessions.get(sid)
                    if session and session.process and session.process.returncode is None:
                        affected_pids.add(session.process.pid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Pause strategy 1 (in-memory) failed for %s: %s", workflow_id[:8], e)

    # Strategy 2: PID from database (covers scheduler poll gap)
    try:
        repo = _get_repo()
        workflow = repo.get_workflow(workflow_id)
        if workflow:
            pid = workflow.get("agent_pid")
            if (
                pid
                and isinstance(pid, int)
                and pid > 0
                and pid not in affected_pids
                and _pid_matches_expected(pid)
            ):
                logger.info(
                    "Pause strategy 2: suspending DB-tracked PID %d for workflow %s",
                    pid,
                    workflow_id[:8],
                )
                _suspend_pid(pid)
                affected_pids.add(pid)
            # Keep agent_pid in DB (needed for resume)
    except Exception as e:
        logger.warning("Pause strategy 2 (DB PID) failed for %s: %s", workflow_id[:8], e)

    # The fallback strategies above sent SIGSTOP directly, bypassing
    # pause_session(), so the matching in-memory sessions never had their
    # _paused Event set. Without it, _wait_for_completion keeps counting the
    # timeout budget and reaps the frozen process once it elapses — the
    # workflow then "auto-resumes". Sync the flag for every PID we suspended.
    _mark_sessions_paused(workflow_id, affected_pids)

    # Strategy 3: scan runner sessions (last resort)
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            runner = orchestrator._runner
            for _sid, session in list(runner._local_sessions.items()):
                if session.workflow_id == workflow_id:
                    if session.process and session.process.returncode is None:
                        pid = session.process.pid
                        if pid not in affected_pids:
                            try:
                                os.killpg(os.getpgid(pid), signal.SIGSTOP)
                                affected_pids.add(pid)
                            except (ProcessLookupError, OSError):
                                pass
    except Exception as e:
        logger.warning("Pause strategy 3 (session scan) failed for %s: %s", workflow_id[:8], e)

    # The fallback strategies above sent SIGSTOP directly, bypassing
    # pause_session(), so the matching in-memory sessions never had their
    # _paused Event set. Without it, _wait_for_completion keeps counting the
    # timeout budget and reaps the frozen process once it elapses — the
    # workflow then "auto-resumes". Sync the flag for every PID we suspended
    # across all strategies (including the session-scan last resort), so the
    # budget freezes correctly regardless of which path delivered the signal.
    _mark_sessions_paused(workflow_id, affected_pids)


def _stop_running_task(workflow_id: str):
    """Stop (kill) the running agent task using multiple strategies.

    Strategy 1: In-memory orchestrator → orchestrator.cancel_current_task()
    Strategy 2: PID from database → direct SIGTERM/SIGKILL
    Strategy 3: Scan runner's in-memory sessions → kill matching sessions
    """
    killed_pids: set[int] = set()

    # Strategy 1: in-memory orchestrator (fast path)
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            orchestrator.cancel_current_task()
            try:
                with orchestrator._session_lock:
                    sid = orchestrator._current_session_id
                if sid:
                    session = orchestrator._runner._local_sessions.get(sid)
                    if session and session.process and session.process.returncode is None:
                        killed_pids.add(session.process.pid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Stop strategy 1 (in-memory) failed for %s: %s", workflow_id[:8], e)

    # Strategy 2: PID from database (covers scheduler poll gap / server restart)
    try:
        repo = _get_repo()
        workflow = repo.get_workflow(workflow_id)
        if workflow:
            pid = workflow.get("agent_pid")
            if (
                pid
                and isinstance(pid, int)
                and pid > 0
                and pid not in killed_pids
                and _pid_matches_expected(pid)
            ):
                logger.info(
                    "Stop strategy 2: killing DB-tracked PID %d for workflow %s",
                    pid,
                    workflow_id[:8],
                )
                _kill_pid(pid)
                killed_pids.add(pid)
            # Clear PID from DB
            repo.update_workflow(workflow_id, {"agent_pid": None, "agent_session_id": ""})
    except Exception as e:
        logger.warning("Stop strategy 2 (DB PID) failed for %s: %s", workflow_id[:8], e)

    # Strategy 3: scan runner sessions (last resort)
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            runner = orchestrator._runner
            for _sid, session in list(runner._local_sessions.items()):
                if session.workflow_id == workflow_id:
                    if session.process and session.process.returncode is None:
                        pid = session.process.pid
                        if pid not in killed_pids:
                            try:
                                os.killpg(os.getpgid(pid), signal.SIGTERM)
                                killed_pids.add(pid)
                            except (ProcessLookupError, OSError):
                                pass
                    runner.stop_session(_sid)
    except Exception as e:
        logger.warning("Stop strategy 3 (session scan) failed for %s: %s", workflow_id[:8], e)


def _resume_running_task(workflow_id: str):
    """Resume a paused agent task using multiple strategies.

    Strategy 1: In-memory orchestrator → orchestrator.resume_current_task()
    Strategy 2: PID from database → direct SIGCONT
    """
    resumed_pids: set[int] = set()

    # Strategy 1: in-memory orchestrator
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            orchestrator.resume_current_task()
            try:
                with orchestrator._session_lock:
                    sid = orchestrator._current_session_id
                if sid:
                    session = orchestrator._runner._local_sessions.get(sid)
                    if session and session.process and session.process.returncode is None:
                        resumed_pids.add(session.process.pid)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Resume strategy 1 (in-memory) failed for %s: %s", workflow_id[:8], e)

    # Strategy 2: PID from database
    try:
        repo = _get_repo()
        workflow = repo.get_workflow(workflow_id)
        if workflow:
            pid = workflow.get("agent_pid")
            if (
                pid
                and isinstance(pid, int)
                and pid > 0
                and pid not in resumed_pids
                and _pid_matches_expected(pid)
            ):
                logger.info(
                    "Resume strategy 2: resuming DB-tracked PID %d for workflow %s",
                    pid,
                    workflow_id[:8],
                )
                _resume_pid(pid)
                resumed_pids.add(pid)
    except Exception as e:
        logger.warning("Resume strategy 2 (DB PID) failed for %s: %s", workflow_id[:8], e)

    # The fallback strategies above sent SIGCONT directly, bypassing
    # resume_session(), so the matching in-memory sessions still have their
    # _paused Event set. Without clearing it, _wait_for_completion keeps the
    # deadline frozen and the task never times out. Sync the flag.
    _mark_sessions_resumed(workflow_id, resumed_pids)

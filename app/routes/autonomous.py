# mypy: disable-error-code="arg-type"
"""
Open ACE - Autonomous Development Routes

API routes for AI autonomous development workflow management.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time

from flask import Blueprint, Response, g, jsonify, request, stream_with_context

from app.auth.decorators import (
    auth_required,
    check_machine_admin_permission,
    validate_session_token,
)
from app.repositories.autonomous_repo import AutonomousWorkflowRepository

logger = logging.getLogger(__name__)

# Maximum retry count for failed workflows
MAX_RETRY_COUNT = 5

# Lazy repo — avoids creating DB connection at import time
auto_repo: AutonomousWorkflowRepository | None = None


def _get_repo() -> AutonomousWorkflowRepository:
    """Lazy-initialize the repository to avoid DB connection at import time."""
    global auto_repo
    if auto_repo is None:
        auto_repo = AutonomousWorkflowRepository()
    return auto_repo


autonomous_bp = Blueprint("autonomous", __name__)

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


def _get_event_emitter():
    """Get or lazily create the event emitter singleton."""
    from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter

    return AutonomousEventEmitter.instance()


# ── Workflow CRUD ───────────────────────────────────────────────────


@autonomous_bp.route("/workflows", methods=["POST"])
@auth_required
def create_workflow():
    """Create a new autonomous development workflow."""
    data = request.get_json(silent=True) or {}
    user_id = g.user_id

    # Rate limit: max 10 workflows per user per hour
    if not _workflow_rate_limiter.is_allowed(user_id):
        return jsonify({"error": "Rate limit exceeded: max 10 workflows per hour"}), 429

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

    # Validate required fields
    if not data.get("requirements_text") and not data.get("requirements_issue_url"):
        return jsonify({"error": "requirements_text or requirements_issue_url is required"}), 400

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

    workflow_data = {
        "user_id": user_id,
        "title": data.get("title", ""),
        "requirements_text": data.get("requirements_text", ""),
        "requirements_issue_url": data.get("requirements_issue_url", ""),
        "project_path": data.get("project_path", ""),
        "project_repo_url": data.get("project_repo_url", ""),
        "is_new_project": data.get("is_new_project", False),
        "is_private": data.get("is_private", True),
        "cli_tool": data.get("cli_tool", ""),
        "model": data.get("model", ""),
        "permission_mode": data.get("permission_mode", "auto-edit"),
        "branch_name": data.get("branch_name", ""),
        "branch_strategy": data.get("branch_strategy", "new-branch"),
        "workspace_type": data.get("workspace_type", "local"),
        "remote_machine_id": data.get("remote_machine_id", ""),
        "max_plan_rounds": data.get("max_plan_rounds", 3),
        "max_pr_review_rounds": data.get("max_pr_review_rounds", 5),
    }

    try:
        workflow = _get_repo().create_workflow(workflow_data)
        if not workflow:
            return jsonify({"error": "Failed to create workflow"}), 500

        # Emit event
        try:
            _get_event_emitter().emit(
                workflow["workflow_id"],
                "workflow_created",
                {"workflow_id": workflow["workflow_id"], "title": workflow.get("title", "")},
            )
        except Exception:
            pass

        return jsonify({"success": True, "workflow": workflow}), 201
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
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    try:
        workflows = _get_repo().list_workflows(
            user_id=filter_user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return jsonify({"success": True, "workflows": workflows})
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

    return jsonify({"success": True, "workflow": workflow})


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

    # Signal running orchestrator to cancel its active agent task
    _cancel_running_task(workflow_id)

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

    # Signal running orchestrator to cancel its active agent task
    _cancel_running_task(workflow_id)

    from datetime import datetime, timezone

    _get_repo().update_workflow(
        workflow_id,
        {
            "status": "cancelled",
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    _emit_event_safe(workflow_id, "status_change", {"status": "cancelled"})
    return jsonify({"success": True})


@autonomous_bp.route("/workflows/<workflow_id>/retry", methods=["POST"])
@auth_required
def retry_workflow(workflow_id):
    """Retry a failed workflow from its current phase."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    if workflow.get("status") != "failed":
        return jsonify({"error": "Only failed workflows can be retried"}), 400

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

    milestones = _get_repo().list_milestones(workflow_id)
    return jsonify({"success": True, "milestones": milestones})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/cancel", methods=["POST"])
@auth_required
def cancel_milestone(workflow_id, milestone_id):
    """Cancel a milestone and all subsequent milestones."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404

    cancelled = _get_repo().cancel_milestones_after(workflow_id, milestone_id)

    # Set workflow to waiting state for user to provide new requirements
    _get_repo().update_workflow(
        workflow_id,
        {
            "current_phase": "wait",
            "status": "waiting",
        },
    )

    _emit_event_safe(
        workflow_id,
        "user_action",
        {
            "action": "cancel_milestone",
            "milestone_id": milestone_id,
            "cancelled_count": len(cancelled),
        },
    )
    return jsonify({"success": True, "cancelled": len(cancelled)})


@autonomous_bp.route("/workflows/<workflow_id>/milestones/<milestone_id>/fork", methods=["POST"])
@auth_required
def fork_milestone(workflow_id, milestone_id):
    """Fork from a milestone, creating a new branch."""
    workflow = _get_repo().get_workflow(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    if g.user_role != "admin" and workflow.get("user_id") != g.user_id:
        return jsonify({"error": "Access denied"}), 403

    milestone = _get_repo().get_milestone(milestone_id)
    if not milestone or milestone.get("workflow_id") != workflow_id:
        return jsonify({"error": "Milestone not found"}), 404

    data = request.get_json(silent=True) or {}
    fork_branch = data.get("branch_name", f"fork/from-milestone-{milestone_id[:8]}")

    # Create fork milestone
    fork_data = {
        "workflow_id": workflow_id,
        "phase": milestone.get("phase", ""),
        "dev_round": milestone.get("dev_round", 1),
        "milestone_type": "branch_created",
        "status": "completed",
        "title": f"Forked from milestone: {milestone.get('title', '')}",
        "parent_milestone_id": milestone_id,
        "fork_branch": fork_branch,
    }
    fork_milestone = _get_repo().create_milestone(fork_data)

    # Cancel milestones after the fork point
    _get_repo().cancel_milestones_after(workflow_id, milestone_id)

    # Update workflow with new branch and reset to planning
    _get_repo().update_workflow(
        workflow_id,
        {
            "branch_name": fork_branch,
            "current_phase": "planning",
            "status": "planning",
        },
    )

    _emit_event_safe(
        workflow_id,
        "user_action",
        {
            "action": "fork_milestone",
            "milestone_id": milestone_id,
            "fork_branch": fork_branch,
        },
    )
    return jsonify({"success": True, "fork_milestone": fork_milestone})


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

    session_id = milestone.get("session_id", "")
    if not session_id:
        return jsonify({"success": True, "session": None})

    # Use existing session API to get session detail
    from app.modules.workspace.session_manager import SessionManager

    sm = SessionManager()
    session_data = sm.get_session(session_id, include_messages=True)

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

    project_path = workflow.get("worktree_path") or workflow.get("project_path", "")
    gh = GitHubOps(project_path)
    diff_parts = []
    for sha in shas:
        try:
            diff_text = gh.get_commit_diff(sha)
            if diff_text:
                diff_parts.append(f"--- Commit: {sha[:8]} ---\n{diff_text}")
        except Exception as e:
            logger.warning("Failed to get diff for %s: %s", sha[:8], e)

    return jsonify({"success": True, "diff": "\n\n".join(diff_parts)})


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
                    yield ": keepalive\n\n"
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
    ]
    return jsonify({"success": True, "tools": tools})


@autonomous_bp.route("/models", methods=["GET"])
@auth_required
def get_available_models():
    """Get available models for a given tool and workspace type."""
    tool = request.args.get("tool", "")
    workspace_type = request.args.get("workspace_type", "local")
    request.args.get("machine_id", "")

    try:
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        api_proxy = APIKeyProxyService()
        tenant_id = getattr(g, "tenant_id", None)
        scope = "shared" if workspace_type == "remote" else "local"
        if tenant_id is None:
            return jsonify({"success": True, "models": []})
        models = api_proxy.get_tool_model_pool(tenant_id, tool, scope)
        return jsonify({"success": True, "models": models})
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
    """Signal a running orchestrator to cancel its current agent task."""
    try:
        from app.services.autonomous_scheduler import AutonomousScheduler

        scheduler = AutonomousScheduler.instance()
        orchestrator = scheduler.get_running_orchestrator(workflow_id)
        if orchestrator:
            orchestrator.cancel_current_task()
    except Exception as e:
        logger.warning("Failed to cancel running task for %s: %s", workflow_id[:8], e)

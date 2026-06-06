# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Autonomous Workflow Repository

Database operations for the AI autonomous development feature.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.repositories.database import Database, is_postgresql

logger = logging.getLogger(__name__)


class AutonomousWorkflowRepository:
    """Repository for autonomous workflow CRUD operations."""

    # Allowed fields for dynamic UPDATE — prevents SQL injection via dict keys
    ALLOWED_WORKFLOW_FIELDS = {
        "title",
        "status",
        "requirements_text",
        "requirements_issue_url",
        "project_path",
        "project_repo_url",
        "is_new_project",
        "is_private",
        "cli_tool",
        "model",
        "permission_mode",
        "branch_name",
        "branch_strategy",
        "workspace_type",
        "remote_machine_id",
        "worktree_path",
        "github_issue_number",
        "github_pr_number",
        "github_pr_url",
        "current_phase",
        "current_round",
        "dev_round",
        "max_plan_rounds",
        "max_pr_review_rounds",
        "total_tokens",
        "total_input_tokens",
        "total_output_tokens",
        "total_requests",
        "error_message",
        "retry_count",
        "task_timeout",
        "updated_at",
        "completed_at",
        "paused_at",
    }
    ALLOWED_MILESTONE_FIELDS = {
        "phase",
        "dev_round",
        "round_number",
        "milestone_type",
        "status",
        "title",
        "description",
        "session_id",
        "review_session_id",
        "github_issue_number",
        "github_pr_number",
        "github_comment_id",
        "commit_shas",
        "diff_stats",
        "result_summary",
        "plan_content",
        "review_content",
        "error_message",
        "parent_milestone_id",
        "fork_branch",
        "metadata",
        "started_at",
        "completed_at",
        "updated_at",
    }

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    # ── Workflow CRUD ──────────────────────────────────────────────

    @staticmethod
    def _coerce_bool(value, default: bool = False) -> bool:
        """Coerce a value to Python bool for BOOLEAN columns.

        Handles int (0/1), str ('0'/'1'/'true'/'false'), and bool values.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return default

    def create_workflow(self, data: dict) -> dict:
        """Create a new autonomous workflow. Returns the created record."""
        workflow_id = data.get("workflow_id") or str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if is_postgresql():
            result = self.db.fetch_one(
                """
                INSERT INTO autonomous_workflows
                    (workflow_id, user_id, title, status, requirements_text,
                     requirements_issue_url, project_path, project_repo_url,
                     is_new_project, is_private, cli_tool, model, permission_mode,
                     branch_name, branch_strategy, workspace_type,
                     remote_machine_id, current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    workflow_id,
                    data.get("user_id"),
                    data.get("title", ""),
                    data.get("status", "pending"),
                    data.get("requirements_text", ""),
                    data.get("requirements_issue_url", ""),
                    data.get("project_path", ""),
                    data.get("project_repo_url", ""),
                    self._coerce_bool(data.get("is_new_project"), False),
                    self._coerce_bool(data.get("is_private"), True),
                    data.get("cli_tool", ""),
                    data.get("model", ""),
                    data.get("permission_mode", "auto-edit"),
                    data.get("branch_name", ""),
                    data.get("branch_strategy", "new-branch"),
                    data.get("workspace_type", "local"),
                    data.get("remote_machine_id", ""),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    now,
                    now,
                ),
                commit=True,
            )
            return result
        else:
            self.db.execute(
                """
                INSERT INTO autonomous_workflows
                    (workflow_id, user_id, title, status, requirements_text,
                     requirements_issue_url, project_path, project_repo_url,
                     is_new_project, is_private, cli_tool, model, permission_mode,
                     branch_name, branch_strategy, workspace_type,
                     remote_machine_id, current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    data.get("user_id"),
                    data.get("title", ""),
                    data.get("status", "pending"),
                    data.get("requirements_text", ""),
                    data.get("requirements_issue_url", ""),
                    data.get("project_path", ""),
                    data.get("project_repo_url", ""),
                    self._coerce_bool(data.get("is_new_project"), False),
                    self._coerce_bool(data.get("is_private"), True),
                    data.get("cli_tool", ""),
                    data.get("model", ""),
                    data.get("permission_mode", "auto-edit"),
                    data.get("branch_name", ""),
                    data.get("branch_strategy", "new-branch"),
                    data.get("workspace_type", "local"),
                    data.get("remote_machine_id", ""),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    now,
                    now,
                ),
            )
            return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> Optional[dict]:
        """Get a workflow by workflow_id."""
        return self.db.fetch_one(
            "SELECT * FROM autonomous_workflows WHERE workflow_id = ?",
            (workflow_id,),
        )

    def get_workflow_by_session(self, session_id: str) -> Optional[dict]:
        """Get a workflow that has a milestone with the given session_id."""
        return self.db.fetch_one(
            """
            SELECT aw.* FROM autonomous_workflows aw
            JOIN workflow_milestones wm ON aw.workflow_id = wm.workflow_id
            WHERE wm.session_id = ?
            LIMIT 1
            """,
            (session_id,),
        )

    def list_workflows(
        self,
        user_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """List workflows with optional filters."""
        conditions = []
        params = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            # Support comma-separated statuses
            statuses = [s.strip() for s in status.split(",")]
            placeholders = ",".join(["?"] * len(statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend(statuses)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        params.append(offset)

        return self.db.fetch_all(
            f"SELECT * FROM autonomous_workflows {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    def get_active_workflows(self) -> list:
        """Get all workflows that need processing."""
        return self.db.fetch_all(
            """
            SELECT * FROM autonomous_workflows
            WHERE status IN ('pending', 'preparing', 'planning', 'developing',
                             'pr_review', 'reporting', 'waiting', 'merging')
            ORDER BY created_at ASC
            """
        )

    def update_workflow(self, workflow_id: str, updates: dict) -> Optional[dict]:
        """Update a workflow's fields. Returns updated record."""
        if not updates:
            return self.get_workflow(workflow_id)

        updates["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Filter to allowed fields only (prevents SQL injection)
        safe_updates = {k: v for k, v in updates.items() if k in self.ALLOWED_WORKFLOW_FIELDS}
        if not safe_updates:
            return self.get_workflow(workflow_id)

        # Boolean columns — coerce to Python bool for PostgreSQL BOOLEAN type
        _BOOL_COLS = {"is_new_project", "is_private"}

        set_clauses = []
        params = []
        for key, value in safe_updates.items():
            set_clauses.append(f"{key} = ?")
            params.append(self._coerce_bool(value) if key in _BOOL_COLS else value)

        params.append(workflow_id)
        self.db.execute(
            f"UPDATE autonomous_workflows SET {', '.join(set_clauses)} WHERE workflow_id = ?",
            tuple(params),
        )
        return self.get_workflow(workflow_id)

    def update_workflow_tokens(self, workflow_id: str, tokens: dict) -> None:
        """Accumulate token counts for a workflow."""
        self.db.execute(
            """
            UPDATE autonomous_workflows SET
                total_tokens = total_tokens + ?,
                total_input_tokens = total_input_tokens + ?,
                total_output_tokens = total_output_tokens + ?,
                total_requests = total_requests + ?,
                updated_at = ?
            WHERE workflow_id = ?
            """,
            (
                tokens.get("total_tokens", 0),
                tokens.get("total_input_tokens", 0),
                tokens.get("total_output_tokens", 0),
                tokens.get("total_requests", 1),
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                workflow_id,
            ),
        )

    def delete_workflow(self, workflow_id: str) -> None:
        """Delete a workflow and its milestones/events in a single transaction."""
        from app.repositories.database import adapt_sql

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql("DELETE FROM workflow_events WHERE workflow_id = ?"),
                (workflow_id,),
            )
            cursor.execute(
                adapt_sql("DELETE FROM workflow_milestones WHERE workflow_id = ?"),
                (workflow_id,),
            )
            cursor.execute(
                adapt_sql("DELETE FROM autonomous_workflows WHERE workflow_id = ?"),
                (workflow_id,),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Milestone CRUD ─────────────────────────────────────────────

    def create_milestone(self, data: dict) -> dict:
        """Create a workflow milestone."""
        milestone_id = data.get("milestone_id") or str(uuid.uuid4())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if is_postgresql():
            result = self.db.fetch_one(
                """
                INSERT INTO workflow_milestones
                    (workflow_id, milestone_id, phase, dev_round, round_number,
                     milestone_type, status, title, description,
                     session_id, review_session_id,
                     github_issue_number, github_pr_number, github_comment_id,
                     commit_shas, diff_stats, result_summary,
                     plan_content, review_content,
                     parent_milestone_id, fork_branch, metadata,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    data.get("workflow_id", ""),
                    milestone_id,
                    data.get("phase", ""),
                    data.get("dev_round", 1),
                    data.get("round_number", 0),
                    data.get("milestone_type", ""),
                    data.get("status", "pending"),
                    data.get("title", ""),
                    data.get("description", ""),
                    data.get("session_id", ""),
                    data.get("review_session_id", ""),
                    data.get("github_issue_number"),
                    data.get("github_pr_number"),
                    data.get("github_comment_id", ""),
                    data.get("commit_shas", ""),
                    data.get("diff_stats", ""),
                    data.get("result_summary", ""),
                    data.get("plan_content", ""),
                    data.get("review_content", ""),
                    data.get("parent_milestone_id", ""),
                    data.get("fork_branch", ""),
                    data.get("metadata", ""),
                    now if data.get("status") == "in_progress" else None,
                    now,
                    now,
                ),
                commit=True,
            )
            return result
        else:
            self.db.execute(
                """
                INSERT INTO workflow_milestones
                    (workflow_id, milestone_id, phase, dev_round, round_number,
                     milestone_type, status, title, description,
                     session_id, review_session_id,
                     github_issue_number, github_pr_number, github_comment_id,
                     commit_shas, diff_stats, result_summary,
                     plan_content, review_content,
                     parent_milestone_id, fork_branch, metadata,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("workflow_id", ""),
                    milestone_id,
                    data.get("phase", ""),
                    data.get("dev_round", 1),
                    data.get("round_number", 0),
                    data.get("milestone_type", ""),
                    data.get("status", "pending"),
                    data.get("title", ""),
                    data.get("description", ""),
                    data.get("session_id", ""),
                    data.get("review_session_id", ""),
                    data.get("github_issue_number"),
                    data.get("github_pr_number"),
                    data.get("github_comment_id", ""),
                    data.get("commit_shas", ""),
                    data.get("diff_stats", ""),
                    data.get("result_summary", ""),
                    data.get("plan_content", ""),
                    data.get("review_content", ""),
                    data.get("parent_milestone_id", ""),
                    data.get("fork_branch", ""),
                    data.get("metadata", ""),
                    now if data.get("status") == "in_progress" else None,
                    now,
                    now,
                ),
            )
            return self.get_milestone(milestone_id)

    def get_milestone(self, milestone_id: str) -> Optional[dict]:
        """Get a milestone by milestone_id."""
        return self.db.fetch_one(
            "SELECT * FROM workflow_milestones WHERE milestone_id = ?",
            (milestone_id,),
        )

    def list_milestones(
        self,
        workflow_id: str,
        phase: Optional[str] = None,
        dev_round: Optional[int] = None,
        status: Optional[str] = None,
    ) -> list:
        """List milestones for a workflow."""
        conditions = ["workflow_id = ?"]
        params = [workflow_id]

        if phase is not None:
            conditions.append("phase = ?")
            params.append(phase)
        if dev_round is not None:
            conditions.append("dev_round = ?")
            params.append(dev_round)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(conditions)}"
        return self.db.fetch_all(
            f"SELECT * FROM workflow_milestones {where} ORDER BY created_at ASC",
            tuple(params),
        )

    def update_milestone(self, milestone_id: str, updates: dict) -> Optional[dict]:
        """Update a milestone's fields."""
        if not updates:
            return self.get_milestone(milestone_id)

        updates["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Filter to allowed fields only (prevents SQL injection)
        safe_updates = {k: v for k, v in updates.items() if k in self.ALLOWED_MILESTONE_FIELDS}
        if not safe_updates:
            return self.get_milestone(milestone_id)

        set_clauses = []
        params = []
        for key, value in safe_updates.items():
            set_clauses.append(f"{key} = ?")
            params.append(value)

        params.append(milestone_id)
        self.db.execute(
            f"UPDATE workflow_milestones SET {', '.join(set_clauses)} WHERE milestone_id = ?",
            tuple(params),
        )
        return self.get_milestone(milestone_id)

    def cancel_milestones_after(self, workflow_id: str, after_milestone_id: str) -> list:
        """Cancel all milestones after the given one (by creation order)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            """
            UPDATE workflow_milestones SET status = 'cancelled', updated_at = ?
            WHERE workflow_id = ? AND id > (
                SELECT id FROM workflow_milestones WHERE milestone_id = ?
            )
            """,
            (now, workflow_id, after_milestone_id),
        )
        return self.list_milestones(workflow_id, status="cancelled")

    # ── Event CRUD ─────────────────────────────────────────────────

    def create_event(self, data: dict) -> dict:
        """Create a workflow event."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if is_postgresql():
            result = self.db.fetch_one(
                """
                INSERT INTO workflow_events
                    (workflow_id, milestone_id, event_type, event_data, created_at)
                VALUES (?, ?, ?, ?, ?)
                RETURNING *
                """,
                (
                    data.get("workflow_id", ""),
                    data.get("milestone_id", ""),
                    data.get("event_type", ""),
                    data.get("event_data", ""),
                    now,
                ),
                commit=True,
            )
            return result
        else:
            self.db.execute(
                """
                INSERT INTO workflow_events
                    (workflow_id, milestone_id, event_type, event_data, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    data.get("workflow_id", ""),
                    data.get("milestone_id", ""),
                    data.get("event_type", ""),
                    data.get("event_data", ""),
                    now,
                ),
            )
            row = self.db.fetch_one(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY id DESC LIMIT 1",
                (data.get("workflow_id", ""),),
            )
            return row

    def list_events(self, workflow_id: str, limit: int = 200, offset: int = 0) -> list:
        """List events for a workflow."""
        return self.db.fetch_all(
            "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (workflow_id, limit, offset),
        )

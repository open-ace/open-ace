# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Autonomous Workflow Repository

Database operations for the AI autonomous development feature.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.repositories.database import Database, adapt_sql, is_postgresql

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
        "batch_id",
        "batch_order",
        "batch_total",
        "auto_merge",
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
        "planning_timeout_extension",
        "parent_workflow_id",
        "fork_milestone_id",
        "user_feedback",
        "original_branch_name",
        "locked_at",
        "locked_by",
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
        "fork_workflow_id",
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
                     remote_machine_id, github_issue_number, batch_id,
                     batch_order, batch_total, auto_merge, current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds,
                     parent_workflow_id, fork_milestone_id, user_feedback,
                     original_branch_name,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("github_issue_number"),
                    data.get("batch_id"),
                    data.get("batch_order"),
                    data.get("batch_total"),
                    self._coerce_bool(data.get("auto_merge"), True),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    data.get("parent_workflow_id"),
                    data.get("fork_milestone_id"),
                    data.get("user_feedback", ""),
                    data.get("original_branch_name", ""),
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
                     remote_machine_id, github_issue_number, batch_id,
                     batch_order, batch_total, auto_merge, current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds,
                     parent_workflow_id, fork_milestone_id, user_feedback,
                     original_branch_name,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("github_issue_number"),
                    data.get("batch_id"),
                    data.get("batch_order"),
                    data.get("batch_total"),
                    self._coerce_bool(data.get("auto_merge"), True),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    data.get("parent_workflow_id"),
                    data.get("fork_milestone_id"),
                    data.get("user_feedback", ""),
                    data.get("original_branch_name", ""),
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

    def get_queued_workflows(self) -> list:
        """Get workflows that are queued behind another workflow in the same batch."""
        return self.db.fetch_all(
            """
            SELECT * FROM autonomous_workflows
            WHERE status = 'queued' AND batch_id IS NOT NULL AND batch_id != ''
            ORDER BY created_at ASC, batch_order ASC
            """
        )

    def list_batch_workflows(self, batch_id: str) -> list:
        """List all workflows in a batch ordered by configured sequence."""
        return self.db.fetch_all(
            """
            SELECT * FROM autonomous_workflows
            WHERE batch_id = ?
            ORDER BY batch_order ASC, created_at ASC
            """,
            (batch_id,),
        )

    def cancel_queued_batch_workflows(self, batch_id: str, exclude_workflow_id: str) -> int:
        """Cancel queued workflows in the same batch except the current one."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    """
                    UPDATE autonomous_workflows
                    SET status = 'cancelled', completed_at = ?, updated_at = ?
                    WHERE batch_id = ?
                      AND workflow_id != ?
                      AND status = 'queued'
                    """
                ),
                (now, now, batch_id, exclude_workflow_id),
            )
            rowcount = cursor.rowcount
            conn.commit()
            return rowcount
        finally:
            conn.close()

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
        _BOOL_COLS = {"is_new_project", "is_private", "auto_merge"}

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
                updated_at = ?
            WHERE workflow_id = ?
            """,
            (
                tokens.get("total_tokens", 0),
                tokens.get("total_input_tokens", 0),
                tokens.get("total_output_tokens", 0),
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                workflow_id,
            ),
        )

    def recalculate_workflow_requests(self, workflow_id: str) -> None:
        """Recalculate workflow total_requests from actual session message counts.

        Counts assistant messages across all sessions linked to this workflow's
        milestones, matching the request count shown in the session list sidebar.
        """
        self.db.execute(
            adapt_sql(
                """
                UPDATE autonomous_workflows SET
                    total_requests = (
                        SELECT COALESCE(SUM(cnt), 0) FROM (
                            SELECT COUNT(*) as cnt
                            FROM session_messages sm
                            JOIN workflow_milestones wm ON wm.session_id = sm.session_id
                            WHERE wm.workflow_id = ?
                            AND wm.session_id IS NOT NULL AND wm.session_id != ''
                            AND sm.role = 'assistant'
                        ) sub
                    ),
                    updated_at = ?
                WHERE workflow_id = ?
                """
            ),
            (
                workflow_id,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                workflow_id,
            ),
        )

    def list_forks(self, workflow_id: str) -> list:
        """List all child workflows forked from the given parent."""
        return self.db.fetch_all(
            "SELECT * FROM autonomous_workflows WHERE parent_workflow_id = ? ORDER BY created_at ASC",
            (workflow_id,),
        )

    def copy_milestones_to_workflow(
        self, src_workflow_id: str, dst_workflow_id: str, up_to_milestone_id: str
    ) -> list:
        """Copy milestones from src to dst workflow, up to and including the target milestone.

        Used by fork to carry forward shared history to the new workflow.
        Returns the list of newly created milestone records.
        All inserts are committed in a single transaction for atomicity.
        """
        from app.repositories.database import adapt_sql

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Fetch milestones up to and including the fork point
        milestones = self.db.fetch_all(
            """
            SELECT * FROM workflow_milestones
            WHERE workflow_id = ? AND id <= (
                SELECT id FROM workflow_milestones WHERE milestone_id = ?
            )
            ORDER BY id ASC
            """,
            (src_workflow_id, up_to_milestone_id),
        )
        copied = []
        new_ms_ids = []
        fields = [
            "phase",
            "dev_round",
            "round_number",
            "milestone_type",
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
        ]
        col_names = [
            "workflow_id",
            "milestone_id",
            "status",
            "started_at",
            "created_at",
            "updated_at",
        ] + fields
        placeholders = ",".join(["?"] * len(col_names))
        col_str = ",".join(col_names)

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            for ms in milestones:
                new_ms_id = str(uuid.uuid4())
                new_ms_ids.append(new_ms_id)
                col_values = [
                    dst_workflow_id,
                    new_ms_id,
                    ms.get("status", "completed"),
                    now,
                    now,
                    now,
                ]
                for f in fields:
                    col_values.append(ms.get(f, ""))

                cursor.execute(
                    adapt_sql(
                        f"INSERT INTO workflow_milestones ({col_str}) VALUES ({placeholders})"
                    ),
                    tuple(col_values),
                )
            conn.commit()
        finally:
            conn.close()

        # Fetch the newly created milestones for return value
        for ms_id in new_ms_ids:
            result = self.get_milestone(ms_id)
            if result:
                copied.append(result)
        return copied

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
                     error_message,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("error_message", ""),
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
                     error_message,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("error_message", ""),
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

    # ── Distributed Lock ──────────────────────────────────────────────

    # Lock timeout in seconds — stale locks are automatically cleared
    LOCK_TIMEOUT_SECONDS = 1800  # 30 minutes

    def acquire_lock(self, workflow_id: str, owner: str) -> bool:
        """Atomically acquire a processing lock for a workflow.

        Returns True if the lock was acquired, False if already locked.
        Stale locks (older than LOCK_TIMEOUT_SECONDS) are broken automatically.
        """
        import app.repositories.database as _db_mod

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=self.LOCK_TIMEOUT_SECONDS)
        ).strftime("%Y-%m-%d %H:%M:%S")

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                _db_mod.adapt_sql(
                    """
                    UPDATE autonomous_workflows
                    SET locked_at = ?, locked_by = ?
                    WHERE workflow_id = ?
                      AND (locked_at IS NULL OR locked_at < ?)
                    """
                ),
                (now, owner, workflow_id, cutoff),
            )
            rowcount = cursor.rowcount
            conn.commit()
            return rowcount > 0
        finally:
            conn.close()

    def release_lock(self, workflow_id: str, owner: str) -> None:
        """Release the lock, but only if we are the owner."""
        import app.repositories.database as _db_mod

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                _db_mod.adapt_sql(
                    """
                    UPDATE autonomous_workflows
                    SET locked_at = NULL, locked_by = NULL
                    WHERE workflow_id = ? AND locked_by = ?
                    """
                ),
                (workflow_id, owner),
            )
            conn.commit()
        finally:
            conn.close()

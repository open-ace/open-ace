# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Autonomous Workflow Repository

Database operations for the AI autonomous development feature.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.repositories.database import Database, adapt_sql, escape_like, is_postgresql

logger = logging.getLogger(__name__)

# Supported content languages for workflow-authored content (en/zh/ja/ko).
# Shared by the repository (persistence) and the orchestrator (AI prompt
# injection) so the two layers can never drift apart.
ALLOWED_CONTENT_LANGUAGES = ("en", "zh", "ja", "ko")
DEFAULT_CONTENT_LANGUAGE = "en"


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
        "definition_snapshot",
        "current_phase",
        "current_round",
        "dev_round",
        "max_plan_rounds",
        "max_pr_review_rounds",
        "require_full_review_rounds",
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
        "agent_pid",
        "agent_session_id",
        "main_session_id",
        "review_session_id",
        "test_session_id",
        "transient_retry_count",
        "content_language",
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
        "tldr",
        "phase_total_tokens",
        "phase_input_tokens",
        "phase_output_tokens",
        "phase_request_count",
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

    # ── Agent PID helpers ──────────────────────────────────────────

    def get_workflows_with_active_pid(self) -> list[dict]:
        """Find workflows that have an agent PID set and are in an active status.

        Used by the orphan process cleanup at server startup.
        """
        active_statuses = (
            "pending",
            "preparing",
            "planning",
            "developing",
            "pr_review",
            "reporting",
            "waiting",
            "merging",
        )
        placeholders = ", ".join(["?"] * len(active_statuses))
        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                adapt_sql(
                    f"""
                    SELECT workflow_id, agent_pid, status
                    FROM autonomous_workflows
                    WHERE agent_pid IS NOT NULL
                      AND agent_pid > 0
                      AND status IN ({placeholders})
                    """
                ),
                list(active_statuses),
            )
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ── Workflow CRUD ──────────────────────────────────────────────

    @staticmethod
    def _normalize_content_language(value) -> str:
        """Validate content_language against the supported set, falling back.

        Accepts the 4 supported languages; anything else (None, empty, unknown)
        falls back to the default so persisted content always has a language.
        """
        if isinstance(value, str) and value.strip() in ALLOWED_CONTENT_LANGUAGES:
            return value.strip()
        return DEFAULT_CONTENT_LANGUAGE

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
        # Normalize content_language up front so both INSERT branches stay in
        # sync and the value cannot be silently dropped by a missing column.
        data = dict(data)
        data["content_language"] = self._normalize_content_language(data.get("content_language"))

        if is_postgresql():
            result = self.db.fetch_one(
                """
                INSERT INTO autonomous_workflows
                    (workflow_id, user_id, title, status, requirements_text,
                     requirements_issue_url, project_path, project_repo_url,
                     is_new_project, is_private, cli_tool, model, permission_mode,
                     branch_name, branch_strategy, workspace_type,
                     remote_machine_id, github_issue_number, batch_id,
                     batch_order, batch_total, auto_merge, definition_snapshot,
                     current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds, require_full_review_rounds,
                     parent_workflow_id, fork_milestone_id, user_feedback,
                     original_branch_name, content_language,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("definition_snapshot"),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    self._coerce_bool(data.get("require_full_review_rounds"), False),
                    data.get("parent_workflow_id"),
                    data.get("fork_milestone_id"),
                    data.get("user_feedback", ""),
                    data.get("original_branch_name", ""),
                    data.get("content_language", "en"),
                    now,
                    now,
                ),
                commit=True,
            )
            created = result
        else:
            self.db.execute(
                """
                INSERT INTO autonomous_workflows
                    (workflow_id, user_id, title, status, requirements_text,
                     requirements_issue_url, project_path, project_repo_url,
                     is_new_project, is_private, cli_tool, model, permission_mode,
                     branch_name, branch_strategy, workspace_type,
                     remote_machine_id, github_issue_number, batch_id,
                     batch_order, batch_total, auto_merge, definition_snapshot,
                     current_phase, dev_round,
                     max_plan_rounds, max_pr_review_rounds, require_full_review_rounds,
                     parent_workflow_id, fork_milestone_id, user_feedback,
                     original_branch_name, content_language,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("definition_snapshot"),
                    data.get("current_phase", "preparation"),
                    data.get("dev_round", 1),
                    data.get("max_plan_rounds", 3),
                    data.get("max_pr_review_rounds", 5),
                    self._coerce_bool(data.get("require_full_review_rounds"), False),
                    data.get("parent_workflow_id"),
                    data.get("fork_milestone_id"),
                    data.get("user_feedback", ""),
                    data.get("original_branch_name", ""),
                    data.get("content_language", "en"),
                    now,
                    now,
                ),
            )
            created = self.get_workflow(workflow_id)

        # Silent-drop guard: content_language must round-trip. If the INSERT
        # column list ever drifts out of sync with the value tuple, this would
        # catch a missing column instead of persisting a NULL silently.
        if created and not created.get("content_language"):
            logger.error(
                "content_language missing after create_workflow %s — falling back to 'en'",
                workflow_id,
            )
            created["content_language"] = "en"
        return created

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
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """List workflows with optional filters."""
        where, params = self._build_workflow_list_filters(
            user_id=user_id,
            status=status,
            search=search,
        )
        params.append(limit)
        params.append(offset)

        return self.db.fetch_all(
            f"SELECT * FROM autonomous_workflows {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    def count_workflows(
        self,
        user_id: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """Count workflows with the same filters used by list_workflows."""
        where, params = self._build_workflow_list_filters(
            user_id=user_id,
            status=status,
            search=search,
        )
        result = self.db.fetch_one(
            f"SELECT COUNT(*) AS total FROM autonomous_workflows {where}",
            tuple(params),
        )
        return int(result["total"] if result else 0)

    def _build_workflow_list_filters(
        self,
        user_id: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> tuple[str, list[Any]]:
        """Build WHERE conditions for workflow list/count queries."""
        conditions: list[str] = []
        params: list[Any] = []

        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if status:
            # Support comma-separated statuses.
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                placeholders = ",".join(["?"] * len(statuses))
                conditions.append(f"status IN ({placeholders})")
                params.extend(statuses)
        if search:
            search_fields = (
                "title",
                "workflow_id",
                "requirements_text",
                "requirements_issue_url",
                "project_path",
                "cli_tool",
                "model",
                "branch_name",
            )
            search_pattern = f"%{escape_like(search.strip().lower())}%"
            search_clauses = [
                f"LOWER(COALESCE({field}, '')) LIKE ? ESCAPE '\\'" for field in search_fields
            ]
            conditions.append(f"({' OR '.join(search_clauses)})")
            params.extend([search_pattern] * len(search_fields))

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return where, params

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

    def get_paused_workflows(self, quota_prefix: str = "") -> list:
        """Get paused workflows, optionally filtered to a quota-pause reason.

        With ``quota_prefix`` empty this returns every paused workflow. With a
        prefix it filters in SQL (``error_message LIKE 'prefix%'``) so the
        auto-resume scan stays cheap and doesn't grow with the full paused set.
        """
        if quota_prefix:
            return self.db.fetch_all(
                """
                SELECT * FROM autonomous_workflows
                WHERE status = 'paused' AND error_message LIKE ? ESCAPE '\\'
                ORDER BY created_at ASC
                """,
                (f"{escape_like(quota_prefix)}%",),
            )
        return self.db.fetch_all(
            """
            SELECT * FROM autonomous_workflows
            WHERE status = 'paused'
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

    def refresh_workflow_usage_from_sessions(self, workflow_id: str) -> None:
        """Refresh workflow token/request totals from per-milestone phase usage.

        Sums each milestone's phase_* increment so shared sessions are counted
        once per milestone, rather than via cumulative agent_sessions totals
        (which double-count when a session spans multiple milestones).
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            adapt_sql(
                """
                UPDATE autonomous_workflows SET
                    total_tokens = COALESCE((
                        SELECT SUM(COALESCE(phase_total_tokens, 0))
                        FROM workflow_milestones WHERE workflow_id = ?
                    ), 0),
                    total_input_tokens = COALESCE((
                        SELECT SUM(COALESCE(phase_input_tokens, 0))
                        FROM workflow_milestones WHERE workflow_id = ?
                    ), 0),
                    total_output_tokens = COALESCE((
                        SELECT SUM(COALESCE(phase_output_tokens, 0))
                        FROM workflow_milestones WHERE workflow_id = ?
                    ), 0),
                    total_requests = COALESCE((
                        SELECT SUM(COALESCE(phase_request_count, 0))
                        FROM workflow_milestones WHERE workflow_id = ?
                    ), 0),
                    updated_at = ?
                WHERE workflow_id = ?
                """
            ),
            (workflow_id, workflow_id, workflow_id, workflow_id, now, workflow_id),
        )

    def get_milestone_usage_summary(
        self, workflow_id: str, milestones: Optional[list[dict]] = None
    ) -> dict[str, dict[str, Any]]:
        """Return per-milestone usage from each milestone's own phase_* columns.

        Each milestone stores only its own increment (phase_total_tokens etc.),
        so shared sessions (main/review/test lines) no longer duplicate
        cumulative session totals across milestones.
        """
        milestones = milestones or self.list_milestones(workflow_id)
        result: dict[str, dict[str, Any]] = {}
        for milestone in milestones:
            milestone_id = milestone.get("milestone_id", "")
            session_id = (
                milestone.get("review_session_id") or milestone.get("session_id") or ""
            ).strip()
            result[milestone_id] = {
                "llm_session_id": session_id,
                "llm_total_tokens": int(milestone.get("phase_total_tokens", 0) or 0),
                "llm_request_count": int(milestone.get("phase_request_count", 0) or 0),
            }
        return result

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
            "fork_workflow_id",
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
                # Copied milestones become the child workflow's own history. They
                # are not the fork point this child originated from, so the
                # ancestor's fork_workflow_id (which points at a *sibling*
                # workflow, e.g. an earlier fork) must not be carried over —
                # otherwise a later fork from this child misattributes its split
                # to the ancestor's branch point. See PR #1243 review.
                source_milestone = dict(ms)
                source_milestone["fork_workflow_id"] = ""
                for f in fields:
                    col_values.append(source_milestone.get(f, ""))

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

    def delete_batch(self, batch_id: str) -> int:
        """Delete all workflows and related records for a batch."""
        workflows = self.list_batch_workflows(batch_id)
        if not workflows:
            return 0

        conn = self.db.get_connection()
        try:
            cursor = conn.cursor()
            workflow_ids = [
                workflow.get("workflow_id", "")
                for workflow in workflows
                if workflow.get("workflow_id")
            ]
            if not workflow_ids:
                return 0
            placeholders = ",".join(["?"] * len(workflow_ids))
            cursor.execute(
                adapt_sql(f"DELETE FROM workflow_events WHERE workflow_id IN ({placeholders})"),
                tuple(workflow_ids),
            )
            cursor.execute(
                adapt_sql(f"DELETE FROM workflow_milestones WHERE workflow_id IN ({placeholders})"),
                tuple(workflow_ids),
            )
            cursor.execute(
                adapt_sql("DELETE FROM autonomous_workflows WHERE batch_id = ?"),
                (batch_id,),
            )
            conn.commit()
            return len(workflow_ids)
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
                     plan_content, review_content, tldr,
                     parent_milestone_id, fork_branch, fork_workflow_id, metadata,
                     error_message,
                     phase_total_tokens, phase_input_tokens,
                     phase_output_tokens, phase_request_count,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("tldr", ""),
                    data.get("parent_milestone_id", ""),
                    data.get("fork_branch", ""),
                    data.get("fork_workflow_id", ""),
                    data.get("metadata", ""),
                    data.get("error_message", ""),
                    data.get("phase_total_tokens", 0),
                    data.get("phase_input_tokens", 0),
                    data.get("phase_output_tokens", 0),
                    data.get("phase_request_count", 0),
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
                     plan_content, review_content, tldr,
                     parent_milestone_id, fork_branch, fork_workflow_id, metadata,
                     error_message,
                     phase_total_tokens, phase_input_tokens,
                     phase_output_tokens, phase_request_count,
                     started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("tldr", ""),
                    data.get("parent_milestone_id", ""),
                    data.get("fork_branch", ""),
                    data.get("fork_workflow_id", ""),
                    data.get("metadata", ""),
                    data.get("error_message", ""),
                    data.get("phase_total_tokens", 0),
                    data.get("phase_input_tokens", 0),
                    data.get("phase_output_tokens", 0),
                    data.get("phase_request_count", 0),
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

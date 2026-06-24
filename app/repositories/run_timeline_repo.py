# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Run Timeline Repository

Database operations for the persisted remote-session run timeline
(agent_runs / agent_run_events / agent_approvals). Cross-database (SQLite +
PostgreSQL) via adapt_sql(); all queries use ``?`` placeholders.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.modules.workspace.run_timeline.models import AgentApproval, AgentRun, RunEvent
from app.repositories.database import Database, is_postgresql

logger = logging.getLogger(__name__)


class RunTimelineRepository:
    """Repository for the run-timeline tables."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    # ── Runs ───────────────────────────────────────────────────────

    def ensure_run(
        self,
        run_id: str,
        session_id: str,
        user_id: int | None = None,
        tenant_id: int | None = None,
        machine_id: str | None = None,
        tool_name: str | None = None,
        provider: str | None = None,
        cli_tool: str | None = None,
        model: str | None = None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert a run row if one does not already exist for this session.

        Idempotent: a no-op when the run already exists. This removes any
        ordering dependency between run creation and the first recorded event
        (events reference run_id via a plain INDEX, never a FK).
        """
        from app.modules.workspace.run_timeline.models import _dump_json

        now = datetime.utcnow()
        self.db.execute(
            """
            INSERT INTO agent_runs
                (run_id, session_id, user_id, tenant_id, machine_id, tool_name,
                 provider, cli_tool, model, status, started_at, metadata,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (session_id) DO NOTHING
            """,
            (
                run_id,
                session_id,
                user_id,
                tenant_id,
                machine_id,
                tool_name,
                provider,
                cli_tool,
                model,
                status,
                now,
                _dump_json(metadata),
                now,
                now,
            ),
        )

    def update_run_status(
        self,
        session_id: str,
        status: str,
        ended_at: datetime | None = None,
    ) -> None:
        """Update a run's lifecycle status.

        Only terminal statuses (completed/stopped/error) set ``ended_at``; a
        non-terminal transition (paused/resume) clears it. If the caller passes
        an explicit ``ended_at`` for a terminal status it is honoured, otherwise
        ``now`` is used.
        """
        now = datetime.utcnow()
        terminal = status in ("completed", "stopped", "error")
        effective_ended = ended_at if ended_at is not None else (now if terminal else None)
        self.db.execute(
            """
            UPDATE agent_runs
               SET status = ?, ended_at = ?, updated_at = ?
             WHERE session_id = ?
            """,
            (status, effective_ended, now, session_id),
        )

    def update_run_usage(
        self,
        session_id: str,
        total_tokens: int,
        total_input_tokens: int,
        total_output_tokens: int,
        total_requests: int,
    ) -> None:
        """Snapshot cumulative usage onto the run row."""
        self.db.execute(
            """
            UPDATE agent_runs
               SET total_tokens = ?, total_input_tokens = ?,
                   total_output_tokens = ?, total_requests = ?,
                   updated_at = ?
             WHERE session_id = ?
            """,
            (
                total_tokens,
                total_input_tokens,
                total_output_tokens,
                total_requests,
                datetime.utcnow(),
                session_id,
            ),
        )

    def get_run_by_session(self, session_id: str) -> AgentRun | None:
        row = self.db.fetch_one(
            "SELECT * FROM agent_runs WHERE session_id = ?",
            (session_id,),
        )
        return AgentRun.from_row(row) if row else None

    # ── Events ─────────────────────────────────────────────────────

    def append_event(
        self,
        run_id: str,
        session_id: str,
        event_type: str,
        event_subtype: str | None = None,
        role: str | None = None,
        content: str | None = None,
        tool_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        key_id: str | None = None,
        user_id: int | None = None,
        tenant_id: int | None = None,
        machine_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_ts: datetime | None = None,
    ) -> int | None:
        """Append a timeline event and return its new id (stable order key)."""
        from app.modules.workspace.run_timeline.models import _dump_json

        ts = event_ts or datetime.utcnow()
        insert_sql = """
            INSERT INTO agent_run_events
                (run_id, session_id, event_type, event_subtype, role, content,
                 tool_name, provider, model, key_id, user_id, tenant_id,
                 machine_id, metadata, event_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (
            run_id,
            session_id,
            event_type,
            event_subtype,
            role,
            content,
            tool_name,
            provider,
            model,
            key_id,
            user_id,
            tenant_id,
            machine_id,
            _dump_json(metadata),
            ts,
        )
        if is_postgresql():
            row = self.db.fetch_one(insert_sql + " RETURNING id", params, commit=True)
            return row["id"] if row else None
        cursor = self.db.execute(insert_sql, params)
        return getattr(cursor, "lastrowid", None)

    def query_events(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        after_id: int | None = None,
        event_type: str | None = None,
        order: str = "asc",
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RunEvent]:
        """Return events for a session in stable chronological order.

        Order key is the autoincrement ``id`` (global monotonic), so results are
        stable across concurrent inserts. ``after_id`` enables cursor pagination
        for live streaming; ``offset`` is used for filtered/bounded queries.
        """
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]

        if after_id is not None:
            conditions.append("id > ?")
            params.append(after_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("event_ts >= ?")
            params.append(since)
        if until:
            conditions.append("event_ts <= ?")
            params.append(until)

        direction = "DESC" if order.lower() == "desc" else "ASC"
        where = " AND ".join(conditions)
        sql = f"""
            SELECT * FROM agent_run_events
             WHERE {where}
          ORDER BY id {direction}
             LIMIT ? OFFSET ?
            """
        params.extend([limit, offset])
        rows = self.db.fetch_all(sql, tuple(params))
        return [RunEvent.from_row(r) for r in rows]

    def count_events(
        self,
        session_id: str,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("event_ts >= ?")
            params.append(since)
        if until:
            conditions.append("event_ts <= ?")
            params.append(until)
        where = " AND ".join(conditions)
        row = self.db.fetch_one(
            f"SELECT COUNT(*) AS count FROM agent_run_events WHERE {where}",
            tuple(params),
        )
        return int(row["count"]) if row else 0

    # ── Approvals ──────────────────────────────────────────────────

    def upsert_approval_request(
        self,
        request_id: str,
        run_id: str,
        session_id: str,
        tool_name: str | None = None,
        request_subtype: str | None = None,
        request_details: dict[str, Any] | None = None,
    ) -> None:
        """Insert a pending approval, or refresh request details if it exists."""
        from app.modules.workspace.run_timeline.models import _dump_json

        now = datetime.utcnow()
        self.db.execute(
            """
            INSERT INTO agent_approvals
                (request_id, run_id, session_id, tool_name, request_subtype,
                 request_details, status, requested_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            ON CONFLICT (request_id) DO UPDATE SET
                request_subtype = excluded.request_subtype,
                request_details = excluded.request_details,
                status = 'pending',
                decided_by = NULL,
                decided_by_name = NULL,
                decision = NULL,
                decision_metadata = NULL,
                decided_at = NULL,
                updated_at = excluded.updated_at
            """,
            (
                request_id,
                run_id,
                session_id,
                tool_name,
                request_subtype,
                _dump_json(request_details),
                now,
                now,
                now,
            ),
        )

    def update_approval_response(
        self,
        request_id: str,
        decision: str,
        decided_by: int | None = None,
        decided_by_name: str | None = None,
        decision_metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a user's permission response. Returns rows updated."""
        from app.modules.workspace.run_timeline.models import _dump_json

        now = datetime.utcnow()
        status = "approved" if decision == "allow" else "denied"
        cursor = self.db.execute(
            """
            UPDATE agent_approvals
               SET decision = ?, status = ?, decided_by = ?,
                   decided_by_name = ?, decision_metadata = ?,
                   decided_at = ?, updated_at = ?
             WHERE request_id = ? AND status = 'pending'
            """,
            (
                decision,
                status,
                decided_by,
                decided_by_name,
                _dump_json(decision_metadata),
                now,
                now,
                request_id,
            ),
        )
        return getattr(cursor, "rowcount", 0) or 0

    def get_latest_pending_approval(self, session_id: str) -> AgentApproval | None:
        """Fallback join key when an agent omits request_id."""
        row = self.db.fetch_one(
            """
            SELECT * FROM agent_approvals
             WHERE session_id = ? AND status = 'pending'
          ORDER BY id DESC
             LIMIT 1
            """,
            (session_id,),
        )
        return AgentApproval.from_row(row) if row else None

    def get_approval(self, request_id: str) -> AgentApproval | None:
        row = self.db.fetch_one(
            "SELECT * FROM agent_approvals WHERE request_id = ?",
            (request_id,),
        )
        return AgentApproval.from_row(row) if row else None

    def list_approvals(self, session_id: str) -> list[AgentApproval]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM agent_approvals
             WHERE session_id = ?
          ORDER BY id ASC
            """,
            (session_id,),
        )
        return [AgentApproval.from_row(r) for r in rows]

    # ── Retention (phase-6 placeholder interface) ──────────────────

    def prune_events_before(self, cutoff: datetime) -> int:
        """Delete events older than ``cutoff``. Retention job hook (phase 6)."""
        cursor = self.db.execute(
            "DELETE FROM agent_run_events WHERE created_at < ?",
            (cutoff,),
        )
        return getattr(cursor, "rowcount", 0) or 0


__all__ = ["RunTimelineRepository"]

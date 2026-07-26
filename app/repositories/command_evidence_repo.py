# mypy: disable-error-code="return-value,arg-type"
"""Open ACE - Command Execution Evidence Repository (#2046 Phase A).

Cross-database (SQLite + PostgreSQL) persistence for
``command_execution_evidence``. Mirrors the ``run_timeline_repo`` pattern:
``?`` placeholders via ``adapt_sql``, ``RETURNING id`` on Postgres /
``lastrowid`` on SQLite.

The ``(session_id, command_id)`` UNIQUE constraint makes ``upsert``
idempotent: a ``tool_use`` creates a pending row, the paired ``tool_result``
updates the same row with terminal state. Duplicate provider events re-update
in place rather than inserting copies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence
from app.repositories.database import Database, is_postgresql

logger = logging.getLogger(__name__)


class CommandExecutionEvidenceRepository:
    """Repository for the ``command_execution_evidence`` table."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def upsert(self, evidence: CommandExecutionEvidence) -> int | None:
        """Insert or update by ``(session_id, command_id)``; return the row id.

        On conflict the terminal-state fields (``exit_code``, ``signal``,
        ``timed_out``, ``cancelled``, ``terminal_reason``, ``completed_at``,
        output fields) are overwritten, while identity/attribution fields set
        on the initial ``tool_use`` (``workflow_id``, ``tool_name``, ``argv``,
        ``cwd`` ...) are preserved unless the caller supplies new values.
        """
        argv_json = json.dumps(evidence.argv) if evidence.argv else None
        now = datetime.now(timezone.utc)

        if is_postgresql():
            return self._upsert_postgres(evidence, argv_json, now)
        return self._upsert_sqlite(evidence, argv_json, now)

    def _upsert_sqlite(
        self, evidence: CommandExecutionEvidence, argv_json: str | None, now: datetime
    ) -> int | None:
        existing = self._fetch_by_session_command(evidence.session_id, evidence.command_id)
        if existing is None:
            insert_sql = """
                INSERT INTO command_execution_evidence
                    (command_id, workflow_id, session_id, milestone_id, sandbox_id,
                     sandbox_generation, tool_name, argv, shell_command, cwd,
                     execution_profile, started_at, completed_at, exit_code, signal,
                     timed_out, cancelled, terminal_reason, stdout_digest,
                     stderr_digest, stdout_artifact, stderr_artifact, output_excerpt,
                     tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
            params = self._insert_params(evidence, argv_json, now)
            cursor = self.db.execute(insert_sql, params)
            return getattr(cursor, "lastrowid", None)

        self.db.execute(self._update_sql(), self._update_params(evidence, argv_json, now))
        return existing.id

    def _upsert_postgres(
        self, evidence: CommandExecutionEvidence, argv_json: str | None, now: datetime
    ) -> int | None:
        existing = self._fetch_by_session_command(evidence.session_id, evidence.command_id)
        if existing is None:
            insert_sql = """
                INSERT INTO command_execution_evidence
                    (command_id, workflow_id, session_id, milestone_id, sandbox_id,
                     sandbox_generation, tool_name, argv, shell_command, cwd,
                     execution_profile, started_at, completed_at, exit_code, signal,
                     timed_out, cancelled, terminal_reason, stdout_digest,
                     stderr_digest, stdout_artifact, stderr_artifact, output_excerpt,
                     tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """
            params = self._insert_params(evidence, argv_json, now)
            row = self.db.fetch_one(insert_sql, params, commit=True)
            return row["id"] if row else None

        self.db.execute(self._update_sql(), self._update_params(evidence, argv_json, now))
        return existing.id

    def _fetch_by_session_command(
        self, session_id: str, command_id: str
    ) -> CommandExecutionEvidence | None:
        if not session_id or not command_id:
            return None
        row = self.db.fetch_one(
            "SELECT * FROM command_execution_evidence WHERE session_id = ? AND command_id = ?",
            (session_id, command_id),
        )
        return CommandExecutionEvidence.from_row(row) if row else None

    def query_by_session(self, session_id: str) -> list[CommandExecutionEvidence]:
        """Return all evidence rows for a session in insertion (id) order."""
        rows = self.db.fetch_all(
            "SELECT * FROM command_execution_evidence WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [CommandExecutionEvidence.from_row(r) for r in rows]

    def query_by_milestone(
        self, workflow_id: str, milestone_id: str
    ) -> list[CommandExecutionEvidence]:
        """Return all evidence rows for a workflow milestone in id order."""
        rows = self.db.fetch_all(
            "SELECT * FROM command_execution_evidence "
            "WHERE workflow_id = ? AND milestone_id = ? ORDER BY id ASC",
            (workflow_id, milestone_id),
        )
        return [CommandExecutionEvidence.from_row(r) for r in rows]

    @staticmethod
    def _insert_params(
        evidence: CommandExecutionEvidence, argv_json: str | None, now: datetime
    ) -> tuple[Any, ...]:
        return (
            evidence.command_id,
            evidence.workflow_id,
            evidence.session_id,
            evidence.milestone_id,
            evidence.sandbox_id,
            evidence.sandbox_generation,
            evidence.tool_name,
            argv_json,
            evidence.shell_command,
            evidence.cwd,
            evidence.execution_profile,
            evidence.started_at,
            evidence.completed_at,
            evidence.exit_code,
            evidence.signal,
            int(evidence.timed_out),
            int(evidence.cancelled),
            evidence.terminal_reason,
            evidence.stdout_digest,
            evidence.stderr_digest,
            evidence.stdout_artifact,
            evidence.stderr_artifact,
            evidence.output_excerpt,
            evidence.tenant_id,
            now,
        )

    @staticmethod
    def _update_sql() -> str:
        return """
            UPDATE command_execution_evidence
               SET exit_code = ?, signal = ?, timed_out = ?, cancelled = ?,
                   terminal_reason = ?, completed_at = ?,
                   stdout_digest = ?, stderr_digest = ?,
                   stdout_artifact = ?, stderr_artifact = ?,
                   output_excerpt = ?
             WHERE session_id = ? AND command_id = ?
            """

    @staticmethod
    def _update_params(
        evidence: CommandExecutionEvidence, argv_json: str | None, now: datetime
    ) -> tuple[Any, ...]:
        return (
            evidence.exit_code,
            evidence.signal,
            int(evidence.timed_out),
            int(evidence.cancelled),
            evidence.terminal_reason,
            evidence.completed_at,
            evidence.stdout_digest,
            evidence.stderr_digest,
            evidence.stdout_artifact,
            evidence.stderr_artifact,
            evidence.output_excerpt,
            evidence.session_id,
            evidence.command_id,
        )

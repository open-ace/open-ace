# mypy: disable-error-code="return-value,arg-type"
"""Open ACE - Test Execution Evidence Repository (#2046 Phase B).

Cross-database (SQLite + PostgreSQL) persistence for
``test_execution_evidence``. Mirrors the ``command_evidence_repo`` pattern:
``?`` placeholders via ``adapt_sql``, ``RETURNING id`` on Postgres /
``lastrowid`` on SQLite. The ``(session_id, command_id)`` UNIQUE constraint
makes ``upsert`` idempotent — a re-parse of the same command overwrites the
prior verdict in place.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.modules.workspace.autonomous.command_evidence.test_evidence import TestExecutionEvidence
from app.repositories.database import Database, is_postgresql

logger = logging.getLogger(__name__)


class TestExecutionEvidenceRepository:
    """Repository for the ``test_execution_evidence`` table."""

    __test__ = False  # pytest: this is a repository class, not a test class

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    def upsert(self, evidence: TestExecutionEvidence) -> int | None:
        """Insert or update by ``(session_id, command_id)``; return the row id.

        On conflict every parsed field (framework, counts, selectors,
        coverage scope, parser, confidence, verdict) is overwritten, so a
        re-parse after more output arrived replaces the prior verdict.
        ``command_execution_id`` is refreshed to point at the latest matching
        ``command_execution_evidence`` row.
        """
        selectors_json = json.dumps(evidence.selectors) if evidence.selectors else None
        scope_json = json.dumps(evidence.coverage_scope) if evidence.coverage_scope else None
        now = datetime.now(timezone.utc)

        if is_postgresql():
            return self._upsert_postgres(evidence, selectors_json, scope_json, now)
        return self._upsert_sqlite(evidence, selectors_json, scope_json, now)

    def _upsert_sqlite(
        self,
        evidence: TestExecutionEvidence,
        selectors_json: str | None,
        scope_json: str | None,
        now: datetime,
    ) -> int | None:
        existing = self._fetch_by_session_command(evidence.session_id, evidence.command_id)
        if existing is None:
            insert_sql = """
                INSERT INTO test_execution_evidence
                    (command_id, command_execution_id, framework, collected,
                     passed, failed, skipped, errors, selectors, coverage_scope,
                     parser, parser_confidence, verdict, session_id, workflow_id,
                     milestone_id, tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
            params = self._insert_params(evidence, selectors_json, scope_json, now)
            cursor = self.db.execute(insert_sql, params)
            return getattr(cursor, "lastrowid", None)

        self.db.execute(
            self._update_sql(), self._update_params(evidence, selectors_json, scope_json)
        )
        return existing.id

    def _upsert_postgres(
        self,
        evidence: TestExecutionEvidence,
        selectors_json: str | None,
        scope_json: str | None,
        now: datetime,
    ) -> int | None:
        existing = self._fetch_by_session_command(evidence.session_id, evidence.command_id)
        if existing is None:
            insert_sql = """
                INSERT INTO test_execution_evidence
                    (command_id, command_execution_id, framework, collected,
                     passed, failed, skipped, errors, selectors, coverage_scope,
                     parser, parser_confidence, verdict, session_id, workflow_id,
                     milestone_id, tenant_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """
            params = self._insert_params(evidence, selectors_json, scope_json, now)
            row = self.db.fetch_one(insert_sql, params, commit=True)
            return row["id"] if row else None

        self.db.execute(
            self._update_sql(), self._update_params(evidence, selectors_json, scope_json)
        )
        return existing.id

    def _fetch_by_session_command(
        self, session_id: str, command_id: str
    ) -> TestExecutionEvidence | None:
        if not session_id or not command_id:
            return None
        row = self.db.fetch_one(
            "SELECT * FROM test_execution_evidence WHERE session_id = ? AND command_id = ?",
            (session_id, command_id),
        )
        return TestExecutionEvidence.from_row(row) if row else None

    def query_by_session(self, session_id: str) -> list[TestExecutionEvidence]:
        """Return all test evidence rows for a session in insertion (id) order."""
        rows = self.db.fetch_all(
            "SELECT * FROM test_execution_evidence WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [TestExecutionEvidence.from_row(r) for r in rows]

    def query_by_milestone(
        self, workflow_id: str, milestone_id: str
    ) -> list[TestExecutionEvidence]:
        """Return all test evidence rows for a workflow milestone in id order."""
        rows = self.db.fetch_all(
            "SELECT * FROM test_execution_evidence "
            "WHERE workflow_id = ? AND milestone_id = ? ORDER BY id ASC",
            (workflow_id, milestone_id),
        )
        return [TestExecutionEvidence.from_row(r) for r in rows]

    @staticmethod
    def _insert_params(
        evidence: TestExecutionEvidence,
        selectors_json: str | None,
        scope_json: str | None,
        now: datetime,
    ) -> tuple:
        return (
            evidence.command_id,
            evidence.command_execution_id,
            evidence.framework,
            evidence.collected,
            evidence.passed,
            evidence.failed,
            evidence.skipped,
            evidence.errors,
            selectors_json,
            scope_json,
            evidence.parser,
            evidence.parser_confidence,
            evidence.verdict,
            evidence.session_id,
            evidence.workflow_id,
            evidence.milestone_id,
            evidence.tenant_id,
            now,
        )

    @staticmethod
    def _update_sql() -> str:
        return """
            UPDATE test_execution_evidence
               SET command_execution_id = ?, framework = ?, collected = ?,
                   passed = ?, failed = ?, skipped = ?, errors = ?,
                   selectors = ?, coverage_scope = ?, parser = ?,
                   parser_confidence = ?, verdict = ?
             WHERE session_id = ? AND command_id = ?
            """

    @staticmethod
    def _update_params(
        evidence: TestExecutionEvidence,
        selectors_json: str | None,
        scope_json: str | None,
    ) -> tuple:
        return (
            evidence.command_execution_id,
            evidence.framework,
            evidence.collected,
            evidence.passed,
            evidence.failed,
            evidence.skipped,
            evidence.errors,
            selectors_json,
            scope_json,
            evidence.parser,
            evidence.parser_confidence,
            evidence.verdict,
            evidence.session_id,
            evidence.command_id,
        )

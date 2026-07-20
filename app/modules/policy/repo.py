# mypy: disable-error-code="return-value,arg-type"
"""
Open ACE - Policy Repository.

SYNCHRONOUS data access for the policy module. Per the persistence invariant
(plan §2.7): ``policy_decisions`` rows are the authoritative gate object the
executor trusts, so their INSERT and the atomic ``consume_decision`` UPDATE run
SYNCHRONOUSLY here (never via the run-timeline async writer). Only the
timeline/audit *event* is async.

Cross-database (SQLite + PostgreSQL) via ``adapt_sql()``; all queries use ``?``
placeholders. Mirrors :mod:`app.repositories.run_timeline_repo`.
"""

from __future__ import annotations


import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.modules.policy.models import PolicyDecision, PolicyRule, _dump_json, _utcnow_naive
from app.repositories.database import Database, is_postgresql

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)


class PolicyRepository:
    """Synchronous repository for ``policy_rules`` and ``policy_decisions``."""

    def __init__(self, db: Database | None = None):
        self.db = db or Database()

    # ── Rules ──────────────────────────────────────────────────────

    def create_rule(
        self,
        *,
        rule_key: str,
        name: str,
        policy_type: str,
        effect: str,
        pattern_type: str = "glob",
        pattern: str | None = None,
        value_list: list[str] | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        tenant_id: int | None = None,
        project_path: str | None = None,
        machine_id: str | None = None,
        user_id: int | None = None,
        team_id: str | None = None,
        priority: int = 100,
        is_default: bool = False,
        enabled: bool = True,
        approval_ttl_seconds: int | None = None,
        created_by: int | None = None,
        description: str | None = None,
    ) -> PolicyRule:
        """Insert a new immutable version row, superseding any current version.

        Editing = new row with ``version = max+1``, ``is_current = true``; the
        previous current row is marked ``is_current = false``. Decisions already
        pin their own ``(rule_id, version)`` snapshot, so this never retroacts.
        """
        now = _utcnow_naive()
        with self.db.connection() as conn:
            cursor = conn.cursor()
            # Supersede existing current rows for this logical rule.
            cursor.execute(
                adapt_sql(
                    """
                    UPDATE policy_rules
                       SET is_current = ?, superseded_at = ?
                     WHERE rule_key = ? AND is_current = ?
                    """
                ),
                (_bool(False), now, rule_key, _bool(True)),
            )
            conn.commit()

        # Next version under this key (immutable history).
        version = self._next_version(rule_key)
        insert_sql = """
            INSERT INTO policy_rules
                (rule_key, name, version, is_current, enabled, tenant_id,
                 project_path, machine_id, user_id, team_id, policy_type,
                 pattern_type, pattern, value_list, tool_name, action, effect,
                 priority, is_default, approval_ttl_seconds, created_by,
                 created_at, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (
            rule_key,
            name,
            version,
            _bool(True),
            _bool(enabled),
            tenant_id,
            project_path,
            machine_id,
            user_id,
            team_id,
            policy_type,
            pattern_type,
            pattern,
            _dump_json(value_list),
            tool_name,
            action,
            effect,
            priority,
            _bool(is_default),
            approval_ttl_seconds,
            created_by,
            now,
            description,
        )
        if is_postgresql():
            new_row = self.db.fetch_one(insert_sql + " RETURNING *", params, commit=True)
        else:
            cursor = self.db.execute(insert_sql, params)
            new_id = getattr(cursor, "lastrowid", None)
            new_row = self.db.fetch_one("SELECT * FROM policy_rules WHERE id = ?", (new_id,))
        return (
            PolicyRule.from_row(new_row)
            if new_row
            else PolicyRule(
                rule_key=rule_key,
                name=name,
                version=version,
                policy_type=policy_type,
                effect=effect,
            )
        )

    def _next_version(self, rule_key: str) -> int:
        row = self.db.fetch_one(
            "SELECT COALESCE(MAX(version), 0) AS v FROM policy_rules WHERE rule_key = ?",
            (rule_key,),
        )
        return int((row or {}).get("v", 0)) + 1

    def list_current_rules(self, *, include_disabled: bool = False) -> list[PolicyRule]:
        cond = "is_current = ?"
        params: list[Any] = [_bool(True)]
        if not include_disabled:
            cond += " AND enabled = ?"
            params.append(_bool(True))
        rows = self.db.fetch_all(
            f"SELECT * FROM policy_rules WHERE {cond} ORDER BY priority ASC, created_at ASC",
            tuple(params),
        )
        return [PolicyRule.from_row(r) for r in rows]

    def get_rule(self, rule_id: int) -> PolicyRule | None:
        row = self.db.fetch_one("SELECT * FROM policy_rules WHERE id = ?", (rule_id,))
        return PolicyRule.from_row(row) if row else None

    def get_rules_for_evaluation(self) -> list[PolicyRule]:
        """Current + enabled rules, pre-sorted by priority.

        The evaluator re-sorts by specificity. Kept separate from
        list_current_rules so caching is scoped to the evaluation hot path.
        """
        return self.list_current_rules(include_disabled=False)

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> int:
        """Toggle enabled on the current version only. Returns rows updated."""
        cursor = self.db.execute(
            adapt_sql(
                """
                UPDATE policy_rules SET enabled = ?, updated_at = ?
                 WHERE id = ? AND is_current = ?
                """
            ),
            (_bool(enabled), _utcnow_naive(), rule_id, _bool(True)),
        )
        return getattr(cursor, "rowcount", 0) or 0

    # ── Decisions ──────────────────────────────────────────────────

    def insert_decision(
        self,
        *,
        request_id: str | None,
        run_id: str | None,
        session_id: str | None,
        tenant_id: int | None = None,
        workspace_scope: str | None = None,
        machine_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        tool_name: str | None = None,
        action: str | None = None,
        resource_target: str | None = None,
        args_digest: str | None = None,
        normalization_profile_id: str | None = None,
        normalization_profile_version: int | None = None,
        fingerprint_hash: str | None = None,
        policy_rule_id: int | None = None,
        policy_rule_version: int | None = None,
        decision: str,
        reason: str | None = None,
        reviewer_identity: str | None = None,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
        consumed_at: datetime | None = None,
    ) -> str:
        """Synchronously persist a decision row and return its ``decision_id``."""
        decision_id = str(uuid.uuid4())
        now = _utcnow_naive()
        sql = """
            INSERT INTO policy_decisions
                (decision_id, request_id, run_id, session_id, tenant_id,
                 workspace_scope, machine_id, model, provider, tool_name, action,
                 resource_target, args_digest, normalization_profile_id,
                 normalization_profile_version, fingerprint_hash, policy_rule_id,
                 policy_rule_version, decision, reason, reviewer_identity,
                 issued_at, expires_at, consumed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        self.db.execute(
            sql,
            (
                decision_id,
                request_id,
                run_id,
                session_id,
                tenant_id,
                workspace_scope,
                machine_id,
                model,
                provider,
                tool_name,
                action,
                resource_target,
                args_digest,
                normalization_profile_id,
                normalization_profile_version,
                fingerprint_hash,
                policy_rule_id,
                policy_rule_version,
                decision,
                reason,
                reviewer_identity,
                issued_at or now,
                expires_at,
                consumed_at,
                now,
                now,
            ),
        )
        return decision_id

    def get_decision(self, decision_id: str) -> PolicyDecision | None:
        row = self.db.fetch_one(
            "SELECT * FROM policy_decisions WHERE decision_id = ?", (decision_id,)
        )
        return PolicyDecision.from_row(row) if row else None

    def get_decision_by_request(self, request_id: str) -> PolicyDecision | None:
        """Most recent decision for a CLI input event (request_id)."""
        row = self.db.fetch_one(
            """
            SELECT * FROM policy_decisions
             WHERE request_id = ?
          ORDER BY id DESC
             LIMIT 1
            """,
            (request_id,),
        )
        return PolicyDecision.from_row(row) if row else None

    def consume_decision(
        self,
        decision_id: str,
        *,
        resolved_decision: str,
        reviewer_identity: str | None = None,
        remote_response_id: str | None = None,
        fingerprint_hash: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Atomically consume (single-use) a decision.

        Conditional ``UPDATE`` returning affected rowcount:
        - ``consumed_at IS NULL``  → single-use (replays affect 0 rows)
        - ``expires_at`` not past → expiry enforced
        - optional ``fingerprint_hash`` integrity self-check

        Binding verification + marking ``consumed_at`` happen in ONE statement
        so two concurrent resumes cannot both spend the same approval (review A3).
        """
        now = now or _utcnow_naive()
        cond = "decision_id = ? AND consumed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
        params: list[Any] = [resolved_decision, reviewer_identity, now, remote_response_id, now]
        where_params: list[Any] = [decision_id, now]
        if fingerprint_hash is not None:
            cond += " AND fingerprint_hash = ?"
            where_params.append(fingerprint_hash)
        cursor = self.db.execute(
            adapt_sql(
                f"""
                UPDATE policy_decisions
                   SET decision = ?, reviewer_identity = ?, consumed_at = ?,
                       remote_response_id = ?, updated_at = ?
                 WHERE {cond}
                """
            ),
            tuple(params + where_params),
        )
        return getattr(cursor, "rowcount", 0) or 0

    def list_decisions(self, session_id: str, limit: int = 100) -> list[PolicyDecision]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM policy_decisions
             WHERE session_id = ?
          ORDER BY id DESC
             LIMIT ?
            """,
            (session_id, limit),
        )
        return [PolicyDecision.from_row(r) for r in rows]


def _bool(value: bool) -> Any:
    """Encode a boolean for the current backend (1/0 for SQLite, bool for PG)."""
    return value if is_postgresql() else (1 if value else 0)


def adapt_sql(query: str) -> str:
    """Local import to avoid a module-level circular import surprise."""
    from app.repositories.database import adapt_sql as _adapt

    return _adapt(query)


__all__ = ["PolicyRepository"]

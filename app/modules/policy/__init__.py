"""
Open ACE - Central Policy & Approval Module (MVP).

A pluggable, self-contained policy engine for remote-agent actions. It owns the
durable *decision* lifecycle, treating the CLI permission request as merely the
input event that starts it (plan §0). All code lives here so the feature is
easy to remove or re-implement behind an external API later; integration points
in existing code are 1–2-line guarded calls and the blueprint registration.

Feature flag: ``policy.enabled`` in ``~/.open-ace/config.json`` (60s TTL). When
disabled, :func:`get_evaluator` returns a :class:`NullPolicyEvaluator` and the
system behaves exactly as before (real-time manual approval).

The canonical table schema lives in the Alembic migration
``migrations/versions/<ts>_add_policy_tables.py``; the per-module
:func:`get_ddl_statements` is the deprecated runtime mirror kept for tests and
mirrors the run-timeline pattern.
"""

from __future__ import annotations

from typing import Any

from app.modules.policy.evaluator import (
    NullPolicyEvaluator,
    PolicyEvaluator,
    get_evaluator,
    reset_evaluator_for_tests,
)
from app.modules.policy.models import (
    Decision,
    PatternType,
    PolicyDecision,
    PolicyEffect,
    PolicyRule,
    PolicyType,
    RequestFingerprint,
)
from app.repositories.database import is_postgresql


def is_policy_enabled() -> bool:
    """Re-export of the config flag (kept here for a single import surface)."""
    from app.utils.config import is_policy_enabled as _enabled

    return _enabled()


def get_ddl_statements() -> list[str]:
    """Return DDL for the policy tables (deprecated runtime mirror).

    The authoritative schema is the Alembic migration + the generated
    ``schema/schema-{sqlite,postgres}.sql`` snapshots. This mirror is kept so
    unit tests can stand up the tables without Alembic (mirroring run_timeline).
    """
    use_pg = is_postgresql()
    pk_type = "SERIAL PRIMARY KEY" if use_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    def bool_default(value: bool) -> str:
        if use_pg:
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"

    bool_type = "BOOLEAN" if use_pg else "INTEGER"

    return [
        f"""
        CREATE TABLE IF NOT EXISTS policy_rules (
            id {pk_type},
            rule_key TEXT NOT NULL,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            is_current {bool_type} DEFAULT {bool_default(True)},
            enabled {bool_type} DEFAULT {bool_default(True)},
            tenant_id INTEGER,
            project_path TEXT,
            machine_id TEXT,
            user_id INTEGER,
            team_id TEXT,
            policy_type TEXT NOT NULL,
            pattern_type TEXT DEFAULT 'glob',
            pattern TEXT,
            value_list TEXT,
            tool_name TEXT,
            action TEXT,
            effect TEXT NOT NULL,
            priority INTEGER DEFAULT 100,
            is_default {bool_type} DEFAULT {bool_default(False)},
            approval_ttl_seconds INTEGER,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            superseded_at TIMESTAMP,
            description TEXT
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_rules_key_version "
        "ON policy_rules (rule_key, version)",
        "CREATE INDEX IF NOT EXISTS idx_policy_rules_key_current "
        "ON policy_rules (rule_key, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_policy_rules_current_enabled "
        "ON policy_rules (is_current, enabled)",
        f"""
        CREATE TABLE IF NOT EXISTS policy_decisions (
            id {pk_type},
            decision_id TEXT NOT NULL UNIQUE,
            request_id TEXT,
            run_id TEXT,
            session_id TEXT,
            tenant_id INTEGER,
            workspace_scope TEXT,
            machine_id TEXT,
            model TEXT,
            provider TEXT,
            tool_name TEXT,
            action TEXT,
            resource_target TEXT,
            args_digest TEXT,
            normalization_profile_id TEXT,
            normalization_profile_version INTEGER,
            fingerprint_hash TEXT,
            policy_rule_id INTEGER,
            policy_rule_version INTEGER,
            decision TEXT NOT NULL,
            reason TEXT,
            reviewer_identity TEXT,
            issued_at TIMESTAMP,
            expires_at TIMESTAMP,
            consumed_at TIMESTAMP,
            remote_response_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_request_id "
        "ON policy_decisions (request_id)",
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_session_id "
        "ON policy_decisions (session_id)",
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_fingerprint "
        "ON policy_decisions (fingerprint_hash)",
    ]


__all__: list[str | Any] = [
    "is_policy_enabled",
    "get_ddl_statements",
    "get_evaluator",
    "reset_evaluator_for_tests",
    "PolicyEvaluator",
    "NullPolicyEvaluator",
    "PolicyRule",
    "PolicyDecision",
    "RequestFingerprint",
    "PolicyType",
    "PolicyEffect",
    "PatternType",
    "Decision",
]

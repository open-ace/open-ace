"""add central policy & approval tables

Revision ID: 20260626_005_add_policy_tables
Revises: 20260630_001_add_retry_count_column
Create Date: 2026-06-26

Adds the central policy & approval MVP tables (see plan §2.2):
- policy_rules      — immutable versioned rule snapshots (scoped, ordered)
- policy_decisions  — authoritative control-plane decision records

``policy_decisions.request_id`` is a SOFT reference (plain indexed column, not
a foreign key) to ``agent_approvals.request_id``: the input event is persisted
asynchronously by the run-timeline writer, so a hard FK would race with the
synchronous decision insert.

Note: ``policy_rules`` rows are append-only versions; editing a rule inserts a
new ``version`` row and marks the prior ``is_current = false``. Decisions pin
their own ``(policy_rule_id, policy_rule_version)`` snapshot. The runtime DDL
mirror lives in app/modules/policy/__init__.py:get_ddl_statements().
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260626_005_add_policy_tables"
down_revision: str | None = "20260630_001_add_retry_count_column"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the policy tables and indexes."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_rules (
                id SERIAL PRIMARY KEY,
                rule_key TEXT NOT NULL,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                is_current BOOLEAN DEFAULT TRUE,
                enabled BOOLEAN DEFAULT TRUE,
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
                is_default BOOLEAN DEFAULT FALSE,
                approval_ttl_seconds INTEGER,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                superseded_at TIMESTAMP,
                description TEXT
            )
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_decisions (
                id SERIAL PRIMARY KEY,
                decision_id TEXT NOT NULL,
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
            """
        )
    else:
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_key TEXT NOT NULL,
                name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                is_current INTEGER DEFAULT 1,
                enabled INTEGER DEFAULT 1,
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
                is_default INTEGER DEFAULT 0,
                approval_ttl_seconds INTEGER,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                superseded_at TIMESTAMP,
                description TEXT
            )
            """
        )
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL,
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
            """
        )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS policy_rules_rule_key_version_key "
        "ON policy_rules (rule_key, version)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS policy_decisions_decision_id_key "
        "ON policy_decisions (decision_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_rules_key_current "
        "ON policy_rules (rule_key, is_current)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_rules_current_enabled "
        "ON policy_rules (is_current, enabled)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_request_id "
        "ON policy_decisions (request_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_session_id "
        "ON policy_decisions (session_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_policy_decisions_fingerprint "
        "ON policy_decisions (fingerprint_hash)"
    )


def downgrade() -> None:
    """Drop the policy tables (feature is fully removable)."""
    op.execute("DROP TABLE IF EXISTS policy_decisions")
    op.execute("DROP TABLE IF EXISTS policy_rules")

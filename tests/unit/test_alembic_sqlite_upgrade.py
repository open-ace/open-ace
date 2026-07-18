#!/usr/bin/env python3
"""Regression tests for SQLite Alembic upgrades."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from migrations.baseline import BASELINE_REVISION
from scripts.shared import db as shared_db


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Alembic's command.upgrade reconfigures logging; restore handlers/levels
    afterwards so later tests' caplog-based assertions are not affected."""
    root = logging.getLogger()
    saved_root_level = root.level
    saved_root_handlers = root.handlers[:]
    saved_alembic_level = logging.getLogger("alembic").level
    yield
    root.handlers[:] = saved_root_handlers
    root.setLevel(saved_root_level)
    logging.getLogger("alembic").setLevel(saved_alembic_level)
    # Alembic's logging.config.fileConfig(disable_existing_loggers=True) disables
    # every non-alembic logger; re-enable them so later tests' caplog works.
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def test_alembic_upgrade_head_succeeds_for_fresh_sqlite(tmp_path, monkeypatch):
    """A fresh SQLite database should migrate cleanly to head."""
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    shared_db._db_url_cache = None

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    command.upgrade(alembic_cfg, "head")

    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    has_session_messages = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_messages'"
        ).fetchone()
        is not None
    )
    has_mapping_rules = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_account_mapping_rules'"
        ).fetchone()
        is not None
    )
    has_compliance_reports = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='compliance_reports'"
        ).fetchone()
        is not None
    )
    has_run_timeline = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_run_events'"
        ).fetchone()
        is not None
    )
    has_project_categories = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_categories'"
        ).fetchone()
        is not None
    )
    has_model_gateway_config = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_gateway_config'"
        ).fetchone()
        is not None
    )
    has_policy_tables = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_rules'"
        ).fetchone()
        is not None
        and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_decisions'"
        ).fetchone()
        is not None
    )
    has_sso_auth_states = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sso_auth_states'"
        ).fetchone()
        is not None
    )
    columns = set()
    if has_session_messages:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_messages)")}
    agent_session_columns = {row[1] for row in conn.execute("PRAGMA table_info(agent_sessions)")}
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    aw_columns = {row[1] for row in conn.execute("PRAGMA table_info(autonomous_workflows)")}
    project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    usage_columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_usage)")}
    audit_log_columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_logs)")}
    conn.close()

    assert version is not None
    # -> 20260704_001_session_messages_pagination_index
    # -> 20260707_001_add_system_account_to_workflows (Issue #1530)
    # -> 20260709_001_add_readonly_role_to_check_constraint (Issue #1497)
    # -> 20260709_001_add_base_commit_sha (Issue #1552)
    # -> 20260709_003_add_tenant_usage_aggregation (Tenant usage aggregation infrastructure)
    expected_head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert version[0] == expected_head
    if has_session_messages:
        assert "source" in columns
        assert "tenant_id" in columns
    assert "tenant_id" in agent_session_columns
    assert has_mapping_rules is True
    assert has_compliance_reports is True
    assert has_run_timeline is True
    assert has_project_categories is True
    assert has_model_gateway_config is True
    assert has_policy_tables is True
    assert has_sso_auth_states is True
    assert "auto_mapping_enabled" in user_columns
    assert "tenant_id" in project_columns
    assert "tenant_id" in usage_columns
    assert "tenant_id" in audit_log_columns
    # content_language column added by 20260626_002 (#1287)
    assert "content_language" in aw_columns
    assert "preferred_worktree_path" in aw_columns
    assert "ci_repair_context" in aw_columns
    assert "ci_repair_attempts" in aw_columns
    assert "last_ci_failure_signature" in aw_columns
    assert "last_ci_failure_head_sha" in aw_columns


def test_workflow_status_index_created_after_upgrade(tmp_path, monkeypatch):
    """The 20260626_003 migration must add idx_workflows_status_created on SQLite."""
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    shared_db._db_url_cache = None

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")

    conn = sqlite3.connect(db_path)
    has_index = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_workflows_status_created'"
        ).fetchone()
        is not None
    )
    conn.close()
    assert has_index is True


def test_workflow_status_index_downgrade_is_symmetric(tmp_path, monkeypatch):
    """Downgrading past 20260626_003 must drop the index; re-upgrade recreates it."""
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    shared_db._db_url_cache = None

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "20260626_002_workflow_content_language")

    conn = sqlite3.connect(db_path)
    has_index = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_workflows_status_created'"
        ).fetchone()
        is not None
    )
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()

    assert has_index is False
    assert version[0] == "20260626_002_workflow_content_language"

    # Re-upgrade recreates the index (idempotent round-trip)
    command.upgrade(alembic_cfg, "head")
    conn = sqlite3.connect(db_path)
    has_index_again = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_workflows_status_created'"
        ).fetchone()
        is not None
    )
    conn.close()
    assert has_index_again is True


def test_remote_runtime_state_migration_idempotent_when_tables_preexist(tmp_path, monkeypatch):
    """20260718_001 must no-op when remote_runtime_* tables already exist.

    The schema.sql snapshots also define these tables, so freshly-bootstrapped
    databases (and any environment where the runtime created them first) hit the
    migration with the tables already present. A bare op.create_table would raise
    DuplicateTable; the migration guards each create_table/create_index instead.
    """
    db_path = tmp_path / "preexisting.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    shared_db._db_url_cache = None

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # Upgrade to the revision just before remote_runtime_state, then pre-create
    # both tables (and one of their indexes) to mimic a schema.sql bootstrap.
    command.upgrade(alembic_cfg, "20260717_004_scope_usage_and_audit_to_tenant")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE remote_runtime_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT NOT NULL UNIQUE,
            machine_id TEXT NOT NULL,
            session_id TEXT,
            command_type TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            response_payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            delivered_at TIMESTAMP,
            responded_at TIMESTAMP,
            expires_at TIMESTAMP
        );
        CREATE TABLE remote_runtime_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            stream TEXT NOT NULL DEFAULT 'stdout',
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            UNIQUE (session_id, event_index)
        );
        CREATE INDEX idx_remote_runtime_commands_expires
            ON remote_runtime_commands (expires_at);
        """
    )
    conn.commit()
    conn.close()

    # Advancing across 20260718_001 must not raise DuplicateTable.
    command.upgrade(alembic_cfg, "head")

    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    indexes = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name IN ('remote_runtime_commands','remote_runtime_outputs')"
        )
    }
    conn.close()

    assert version[0] == ScriptDirectory.from_config(alembic_cfg).get_current_head()
    # The missing index was created; the pre-existing one was left alone.
    assert "idx_remote_runtime_commands_machine_status" in indexes
    assert "idx_remote_runtime_commands_expires" in indexes
    assert "idx_remote_runtime_outputs_session_index" in indexes
    assert "idx_remote_runtime_outputs_expires" in indexes


def test_alembic_upgrade_head_after_schema_sql_bootstrap_catches_duplicate_tables(
    tmp_path, monkeypatch
):
    """CI guard: a database bootstrapped from schema.sql must still reach head.

    schema.sql defines every current table, so a freshly-bootstrapped database
    already has any table a post-baseline migration might try to create. This
    catches migrations that ``op.create_table`` without an existence check
    (the 20260718_001 DuplicateTable failure mode) for the whole class, not one
    migration at a time. Any future migration whose table is also in schema.sql
    will trip this test unless it guards its create_table/create_index.
    """
    from app.repositories.schema_init import load_schema_from_file

    db_path = tmp_path / "bootstrapped.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    shared_db._db_url_cache = None

    # Bootstrap the full schema the way a fresh install / runtime does: every
    # table from schema-sqlite.sql exists before any migration runs.
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")

    # schema.sql does not create alembic_version (Alembic owns it), but it must
    # exist before we can stamp the baseline. Create it empty, then stamp so
    # the full post-baseline migration chain runs against the populated schema.
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) PRIMARY KEY)")
    conn.execute(f"INSERT INTO alembic_version(version_num) VALUES ('{BASELINE_REVISION}')")
    conn.commit()
    conn.close()

    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    # Must reach head without raising DuplicateTable / DuplicateObject.
    command.upgrade(alembic_cfg, "head")

    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    conn.close()
    assert version[0] == ScriptDirectory.from_config(alembic_cfg).get_current_head()

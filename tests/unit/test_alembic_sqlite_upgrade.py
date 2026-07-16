#!/usr/bin/env python3
"""Regression tests for SQLite Alembic upgrades."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

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
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    aw_columns = {row[1] for row in conn.execute("PRAGMA table_info(autonomous_workflows)")}
    conn.close()

    assert version is not None
    # Migration chain: 001_run_timeline -> 002_content_language -> 003_status_index
    # -> 001_add_project_categories -> 004_fix_tenant_quotas_overflow
    # -> 005_add_policy_tables -> 001_add_model_gateway_config
    # -> 20260703_001_add_require_full_review_rounds
    # -> 20260703_002_add_sso_auth_states
    # -> 20260704_001_add_test_retry_columns
    # -> 20260704_001_session_messages_pagination_index
    # -> 20260707_001_add_system_account_to_workflows (Issue #1530)
    # -> 20260709_001_add_readonly_role_to_check_constraint (Issue #1497)
    # -> 20260709_001_add_base_commit_sha (Issue #1552)
    # -> 20260709_003_add_tenant_usage_aggregation (Tenant usage aggregation infrastructure)
    # -> 20260714_001_add_ci_repair_fields_to_workflows (Issue #1647)
    # -> 20260715_001_add_last_ci_failure_head_sha (Issue #1574)
    # -> 20260714_002_add_users_mapping_indexes (Issue #1574)
    assert version[0] == "20260714_002_add_users_mapping_indexes"
    if has_session_messages:
        assert "source" in columns
    assert has_mapping_rules is True
    assert has_compliance_reports is True
    assert has_run_timeline is True
    assert has_project_categories is True
    assert has_model_gateway_config is True
    assert has_policy_tables is True
    assert has_sso_auth_states is True
    assert "auto_mapping_enabled" in user_columns
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

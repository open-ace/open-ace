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
    columns = set()
    if has_session_messages:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_messages)")}
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    aw_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(autonomous_workflows)")
    }
    conn.close()

    assert version is not None
    # content_language migration chains after run_timeline (single head)
    assert version[0] == "20260626_002_workflow_content_language"
    if has_session_messages:
        assert "source" in columns
    assert has_mapping_rules is True
    assert has_compliance_reports is True
    assert has_run_timeline is True
    assert "auto_mapping_enabled" in user_columns
    # content_language column added by 20260626_002 (#1284)
    assert "content_language" in aw_columns

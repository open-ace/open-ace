#!/usr/bin/env python3
"""
Migration verification for Issue #241 (#22): session_messages pagination index.

Guards the two migration-level properties this change relies on:

  1. **Single head** — the auto-dev fork is prone to producing two heads after
     merge (main advances, the fork's migration parents a stale head). The
     "Single migration head" CI check runs in isolation against the PR's own
     tree, so it can be blind to this; we re-assert it here from the working
     tree.
  2. **The upgrade actually delivers** NOT NULL ``session_messages.timestamp``
     (with backfill) and the composite ``(session_id, timestamp, id)`` index on
     a fresh database.

Run directly or via pytest.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

NEW_REVISION = "20260704_001_session_messages_pagination_index"
PARENT_REVISION = "20260703_002_add_sso_auth_states"


def _alembic_config() -> Config:
    """Build an Alembic Config with absolute paths so resolution does not depend
    on the process CWD (pytest may not run from the project root)."""
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    return cfg


# ============================ Single-head guard ==============================


def test_single_migration_head():
    """There must be exactly one Alembic head (no fork-induced split)."""
    cfg = _alembic_config()
    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    assert len(heads) == 1, (
        f"Expected a single migration head, found {len(heads)}: {heads}. "
        "A forked/stale head usually means main advanced and a migration needs "
        "re-parenting onto the current head."
    )
    assert heads[0] == NEW_REVISION


def test_new_migration_parents_off_current_head():
    """The new migration must chain directly under the prior head, not branch."""
    cfg = _alembic_config()
    script_dir = ScriptDirectory.from_config(cfg)
    revision = script_dir.get_revision(NEW_REVISION)
    assert revision is not None, f"migration {NEW_REVISION} not found in script directory"
    assert revision.down_revision == PARENT_REVISION, (
        f"new migration down_revision={revision.down_revision!r}, " f"expected {PARENT_REVISION!r}"
    )


# =================== Upgrade delivers NOT NULL + index ======================


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Alembic's command.upgrade reconfigures logging; restore afterwards."""
    root = logging.getLogger()
    saved_root_level = root.level
    saved_handlers = root.handlers[:]
    saved_alembic_level = logging.getLogger("alembic").level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_root_level)
    logging.getLogger("alembic").setLevel(saved_alembic_level)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False


def test_upgrade_makes_timestamp_not_null(tmp_path, monkeypatch):
    """After upgrading to head, session_messages.timestamp is NOT NULL and the
    backfill has populated any pre-existing NULL rows."""
    db_path = tmp_path / "pghead.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Build the schema at the PARENT revision (just before this migration),
    # then insert a NULL-timestamp row to prove the backfill runs.
    from scripts.shared import db as shared_db

    shared_db._db_url_cache = None
    cfg = _alembic_config()
    command.upgrade(cfg, PARENT_REVISION)

    conn = sqlite3.connect(db_path)
    # Ensure a session exists for the FK-ish insert.
    conn.execute(
        "INSERT INTO agent_sessions (session_id, tool_name, created_at, updated_at) "
        "VALUES ('s1', 'qwen', '2026-07-04T00:00:00', '2026-07-04T00:00:00')"
    )
    conn.execute(
        "INSERT INTO session_messages (session_id, role, content, timestamp) "
        "VALUES ('s1', 'user', 'pre-migration null ts', NULL)"
    )
    conn.commit()

    # Confirm the column is still nullable at the parent revision.
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(session_messages)")}
    assert cols["timestamp"][3] == 0, "timestamp should be nullable before the migration"
    conn.close()

    # Apply this migration (and anything after, none expected).
    shared_db._db_url_cache = None
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    # The NULL row was backfilled — no longer NULL.
    nulls = conn.execute(
        "SELECT COUNT(*) FROM session_messages WHERE timestamp IS NULL"
    ).fetchone()[0]
    assert nulls == 0, "migration failed to backfill pre-existing NULL timestamps"

    # And the column is now NOT NULL.
    cols = {row[1]: row for row in conn.execute("PRAGMA table_info(session_messages)")}
    assert cols["timestamp"][3] == 1, "timestamp should be NOT NULL after upgrade"
    conn.close()


def test_upgrade_creates_composite_index(tmp_path, monkeypatch):
    """The composite (session_id, timestamp, id) index must exist after upgrade."""
    db_path = tmp_path / "pgidx.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from scripts.shared import db as shared_db

    shared_db._db_url_cache = None
    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    indexes = {row[1]: row[2] for row in conn.execute("PRAGMA index_list('session_messages')")}
    assert (
        "idx_session_messages_session_timestamp" in indexes
    ), "composite (session_id, timestamp, id) index missing after upgrade"
    # The index covers exactly the three keyset columns in order.
    index_cols = [
        row[2]
        for row in conn.execute("PRAGMA index_info('idx_session_messages_session_timestamp')")
    ]
    assert index_cols == ["session_id", "timestamp", "id"]
    conn.close()

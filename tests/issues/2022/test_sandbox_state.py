"""SandboxProvider persistence — sandbox_* columns on autonomous_workflows (#2022 P2).

Phase 2 persists per-workflow sandbox state so a restart can reconcile orphan
sandboxes by generation. These tests pin the round-trip through the repo
(allowlist + columns + dataclass) and the active-sandbox query, against a temp
SQLite DB loaded from schema-sqlite.sql (the same path CI's fresh-DB jobs use).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod
from app.repositories.autonomous_repo import AutonomousWorkflowRepository
from app.repositories.database import Database


@pytest.fixture
def auto_db(tmp_path):
    """Temp SQLite DB with autonomous tables loaded from schema-sqlite.sql."""
    orig_adapt_sql = db_mod.adapt_sql
    db_mod.adapt_sql = lambda q: q
    db_path = str(tmp_path / "test_sandbox.db")
    try:
        with patch.object(db_mod, "is_postgresql", return_value=False):
            db = Database(db_url=f"sqlite:///{db_path}")
            # Load the full schema first (creates users WITH deleted_at + every
            # index). Do NOT pre-create users — a stale hand-written users table
            # would shadow the script's and break the deleted_at index.
            from app.repositories.schema_init import load_schema_from_file

            load_schema_from_file(db_url=db.db_url, dialect="sqlite")
            conn = db.get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("testuser", "test@test.com", "hash123", "user"),
                )
                conn.commit()
            finally:
                conn.close()
            yield db
    finally:
        db_mod.adapt_sql = orig_adapt_sql
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _create_workflow(repo, workflow_id="wf-sandbox-1") -> dict:
    return repo.create_workflow(
        {"workflow_id": workflow_id, "user_id": 1, "title": "sandbox-state-test"}
    )


def test_sandbox_state_columns_round_trip(auto_db):
    repo = AutonomousWorkflowRepository(auto_db)
    wf = _create_workflow(repo)
    wid = wf["workflow_id"]

    repo.update_workflow(
        wid,
        {
            "sandbox_provider": "legacy_posix",
            "sandbox_id": "sb-abc123",
            "sandbox_generation": 2,
            "sandbox_state": "running",
            "sandbox_policy_digest": "sha256:deadbeef",
            "sandbox_last_error": "",
        },
    )

    reloaded = repo.get_workflow(wid)
    assert reloaded["sandbox_provider"] == "legacy_posix"
    assert reloaded["sandbox_id"] == "sb-abc123"
    assert reloaded["sandbox_generation"] == 2
    assert reloaded["sandbox_state"] == "running"
    assert reloaded["sandbox_policy_digest"] == "sha256:deadbeef"
    assert reloaded["sandbox_last_error"] == ""


def test_sandbox_state_defaults_to_null(auto_db):
    # Additive columns: a freshly created workflow has no sandbox state yet.
    repo = AutonomousWorkflowRepository(auto_db)
    wf = _create_workflow(repo)
    reloaded = repo.get_workflow(wf["workflow_id"])
    assert reloaded["sandbox_provider"] is None
    assert reloaded["sandbox_id"] is None
    assert reloaded["sandbox_generation"] is None
    assert reloaded["sandbox_state"] is None


def test_get_workflows_with_active_sandbox_returns_only_active(auto_db):
    repo = AutonomousWorkflowRepository(auto_db)
    active = _create_workflow(repo, "wf-active")
    destroyed = _create_workflow(repo, "wf-destroyed")
    untouched = _create_workflow(repo, "wf-untouched")

    repo.update_workflow(
        active["workflow_id"],
        {"sandbox_state": "running", "sandbox_generation": 1, "sandbox_id": "sb-active"},
    )
    repo.update_workflow(
        destroyed["workflow_id"],
        {"sandbox_state": "destroyed", "sandbox_generation": 1, "sandbox_id": "sb-dead"},
    )
    # untouched: no sandbox_state at all (NULL) — never had a sandbox

    rows = repo.get_workflows_with_active_sandbox()
    ids = {row["workflow_id"] for row in rows}
    assert active["workflow_id"] in ids
    # destroyed + NULL-sandbox rows are NOT active orphans.
    assert destroyed["workflow_id"] not in ids
    assert untouched["workflow_id"] not in ids


# ── generation guard (#2022 P2) ──────────────────────────────────────


def test_is_current_generation_true_when_equal():
    from app.modules.workspace.autonomous.sandbox.provider import is_current_generation

    assert is_current_generation(3, 3) is True


def test_is_current_generation_false_when_handle_stale():
    # Workflow gen bumped to 4 after reconcile; a handle minted at gen 3 is stale.
    from app.modules.workspace.autonomous.sandbox.provider import is_current_generation

    assert is_current_generation(3, 4) is False


def test_is_current_generation_false_when_either_none():
    from app.modules.workspace.autonomous.sandbox.provider import is_current_generation

    # Cannot confirm currency → fail safe (treat as stale, reject the op).
    assert is_current_generation(None, 3) is False
    assert is_current_generation(3, None) is False
    assert is_current_generation(None, None) is False

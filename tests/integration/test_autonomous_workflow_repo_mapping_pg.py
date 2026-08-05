"""PG-only regression: AutonomousWorkflowRepository row mapping (#2259).

On PostgreSQL, ``get_connection()`` defaults to ``RealDictCursor`` (rows are
``RealDictRow`` dict-subclasses). Three repo methods historically built result
dicts with ``dict(zip(cols, row))`` — but iterating a ``RealDictRow`` yields its
KEYS (column names), so every value became the column-name string
(``{"sandbox_generation": "sandbox_generation", ...}``). The startup
``_reconcile_orphan_sandboxes`` sweep then did
``int(wf.get("sandbox_generation") or 0)`` on the literal string
``"sandbox_generation"`` and crashed every server restart (#2259).

This is a **PG-only** bug: SQLite's ``sqlite3.Row`` iterates by index, so
``dict(zip(cols, row))`` happens to be correct there — CI's SQLite matrix
(``tests/issues/2022/test_sandbox_state.py``) stayed green while prod crashed.
These tests must run on a live PostgreSQL to catch it.
"""

from __future__ import annotations

import pytest

# Marks every test in this module as requiring a live PostgreSQL server.
# CI's main matrix runs `pytest -m 'not postgres'` (excluded); the separate
# postgres-test job runs these. Locally they auto-skip via pg_db when no
# server is reachable.
pytestmark = pytest.mark.postgres

from app.repositories.autonomous_repo import AutonomousWorkflowRepository


def _seed_user(db, user_id: int = 1) -> None:
    """Insert the users row that autonomous_workflows.user_id references."""
    db.execute(
        "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, %s)",
        (f"user{user_id}", f"user{user_id}@test.com", "hash", "user"),
    )


def test_active_sandbox_rows_map_values_not_column_names(pg_db):
    """get_workflows_with_active_sandbox must return column values, not names.

    Pre-fix this returned ``{"sandbox_generation": "sandbox_generation", ...}``
    on PG (RealDictRow), so the reconcile sweep's ``int(...)`` crashed (#2259).
    """
    _seed_user(pg_db)
    repo = AutonomousWorkflowRepository(db=pg_db)
    repo.create_workflow({"workflow_id": "wf-orphan", "user_id": 1, "title": "reconcile-mapping"})
    repo.update_workflow(
        "wf-orphan",
        {"sandbox_state": "running", "sandbox_generation": 5, "sandbox_id": "sb-1"},
    )

    rows = repo.get_workflows_with_active_sandbox()
    assert len(rows) == 1
    # The bug: value was the column-name string "sandbox_generation".
    assert rows[0]["sandbox_generation"] == 5
    assert rows[0]["sandbox_state"] == "running"
    assert rows[0]["sandbox_id"] == "sb-1"
    # Nail the crash-frame semantics from autonomous_scheduler.py:1144 — the
    # reconcile sweep does int(wf.get("sandbox_generation") or 0) + 1. Pinning
    # this guards against a future revert to dict(zip(...)) that the equality
    # assertion alone wouldn't catch.
    assert int(rows[0].get("sandbox_generation") or 0) + 1 == 6


def test_active_transition_rows_map_values_not_column_names(pg_db):
    """get_workflows_with_active_transition shares the broken idiom and feeds
    _reconcile_worktree_transitions (scheduler:956) — same PG-only risk."""
    _seed_user(pg_db)
    repo = AutonomousWorkflowRepository(db=pg_db)
    repo.create_workflow({"workflow_id": "wf-tx", "user_id": 1, "title": "transition-mapping"})
    # worktree_transition_state is set out-of-band by the transition journal;
    # mirror that with a direct UPDATE (the mapping bug is independent of how
    # the column was set, and not every field is in update_workflow's allowlist).
    pg_db.execute(
        "UPDATE autonomous_workflows SET worktree_transition_state = %s, "
        "sandbox_generation = %s WHERE workflow_id = %s",
        ("copying", 2, "wf-tx"),
    )

    rows = repo.get_workflows_with_active_transition()
    assert len(rows) == 1
    assert rows[0]["workflow_id"] == "wf-tx"
    assert rows[0]["worktree_transition_state"] == "copying"
    assert rows[0]["sandbox_generation"] == 2

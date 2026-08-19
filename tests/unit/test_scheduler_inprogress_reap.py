"""Scheduler in-progress entry lifecycle: key map, unified teardown, lease-anchored reclaim.

Regression context (2026-08-19, workflow fec4782b): an in-progress entry with
no live worker froze the workflow (and its batch/workspace/branch conflict
keys) for ~50 minutes because nothing but a service restart could drop it.
The DB lease is the liveness authority — a live worker's heartbeat renews it
every 60s regardless of agent duration — so memory entries are reclaimed
against it. See docs/superpowers/plans/2026-08-19-scheduler-inprogress-lease-anchor.md.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.autonomous_scheduler import AutonomousScheduler


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _bare_scheduler() -> AutonomousScheduler:
    sched = AutonomousScheduler.__new__(AutonomousScheduler)
    sched._in_progress_ids = set()
    sched._in_progress_batch_ids = set()
    sched._in_progress_workspaces = set()
    sched._in_progress_branches = set()
    sched._in_progress_by_user = {}
    sched._in_progress_key_map = {}
    sched._in_progress_lock = threading.Lock()
    return sched


def _mark(
    sched: AutonomousScheduler,
    wid: str,
    *,
    batch: str | None = None,
    workspace: str = "",
    branch: str = "",
    user: int | None = 3,
) -> None:
    with sched._in_progress_lock:
        sched._in_progress_ids.add(wid)
        sched._in_progress_by_user.setdefault(user, set()).add(wid)
        sched._in_progress_key_map[wid] = (batch, workspace, branch)
        if batch:
            sched._in_progress_batch_ids.add(batch)
        if workspace:
            sched._in_progress_workspaces.add(workspace)
        if branch:
            sched._in_progress_branches.add(branch)


# ── Task 1: unified teardown helper + key map ────────────────────────────


def test_discard_entry_releases_reserved_keys_exactly_and_is_idempotent():
    sched = _bare_scheduler()
    _mark(sched, "w-1", batch="batch-9", workspace="/ws/open6", branch="auto-dev/w-1")

    sched._discard_in_progress_entry("w-1")

    assert "w-1" not in sched._in_progress_ids
    assert "w-1" not in sched._in_progress_by_user[3]
    assert "batch-9" not in sched._in_progress_batch_ids
    assert "/ws/open6" not in sched._in_progress_workspaces
    assert "auto-dev/w-1" not in sched._in_progress_branches
    assert "w-1" not in sched._in_progress_key_map
    # Idempotent — a second discard (reclaim racing the finally) must not raise.
    sched._discard_in_progress_entry("w-1")


def test_discard_entry_for_keyless_workflow_touches_no_shared_keys():
    """Waiting workflows reserve no conflict keys; their teardown must not
    release keys some other workflow legitimately holds."""
    sched = _bare_scheduler()
    _mark(sched, "w-wait", batch=None, workspace="", branch="", user=3)
    _mark(sched, "w-busy", batch="b-shared", workspace="/ws/shared", branch="", user=4)

    sched._discard_in_progress_entry("w-wait")

    assert "b-shared" in sched._in_progress_batch_ids
    assert "/ws/shared" in sched._in_progress_workspaces
    assert "w-busy" in sched._in_progress_ids


# ── Task 2: lease-anchored reclaim ───────────────────────────────────────


def test_stale_lease_entry_is_reaped_with_keys_and_warning(caplog):
    """THE regression (fec4782b): entry present, row active/eligible, lease
    gone — must be reaped with its keys, loudly."""
    import logging as _logging

    sched = _bare_scheduler()
    _mark(sched, "w-stale", batch="b1", workspace="/ws/a", branch="br1")
    row = {
        "workflow_id": "w-stale",
        "status": "developing",
        "locked_at": _fmt(datetime.now(timezone.utc) - timedelta(seconds=1801)),
    }
    repo = MagicMock(name="repo")
    with caplog.at_level(_logging.WARNING, logger="app.services.autonomous_scheduler"):
        sched._reclaim_stale_in_progress([row], repo=repo)
    assert "w-stale" not in sched._in_progress_ids
    assert "b1" not in sched._in_progress_batch_ids
    assert "/ws/a" not in sched._in_progress_workspaces
    assert "br1" not in sched._in_progress_branches
    assert any("Reaping stale in-progress" in r.message for r in caplog.records)


def test_fresh_lease_entry_is_kept():
    """Live worker: heartbeat renews the lease, so the entry stays."""
    sched = _bare_scheduler()
    _mark(sched, "w-live", batch="b2", workspace="/ws/a", branch="")
    row = {
        "workflow_id": "w-live",
        "status": "developing",
        "locked_at": _fmt(datetime.now(timezone.utc) - timedelta(seconds=60)),
    }
    sched._reclaim_stale_in_progress([row], repo=MagicMock())
    assert "w-live" in sched._in_progress_ids
    assert "b2" in sched._in_progress_batch_ids


def test_postgres_datetime_lease_shapes_do_not_crash():
    """Postgres (RealDictCursor) returns datetime objects for timestamp
    columns, SQLite returns strings — both must classify correctly instead
    of raising AttributeError on .strip() (plan-review B2: a raise here
    would kill every scheduler cycle)."""
    sched = _bare_scheduler()
    _mark(sched, "w-pg-live", batch=None, workspace="", branch="")
    _mark(sched, "w-pg-stale", batch=None, workspace="", branch="")
    now = datetime.now(timezone.utc)
    sched._reclaim_stale_in_progress(
        [
            {
                "workflow_id": "w-pg-live",
                "status": "developing",
                "locked_at": now - timedelta(seconds=60),  # datetime, naive-of-tz shape from PG
            },
            {
                "workflow_id": "w-pg-stale",
                "status": "developing",
                "locked_at": now - timedelta(seconds=2400),  # datetime, stale
            },
        ],
        repo=MagicMock(),
    )
    assert "w-pg-live" in sched._in_progress_ids
    assert "w-pg-stale" not in sched._in_progress_ids


def test_paused_row_missing_from_active_list_is_kept():
    """#1002: paused rows are NOT in get_active_workflows' status list, so the
    reclaim must fetch their status — a paused workflow's entry belongs to an
    in-flight advance() that owns its resumption. Never reap on absence."""
    sched = _bare_scheduler()
    _mark(sched, "w-paused", batch="b3", workspace="/ws/p", branch="")
    repo = MagicMock(name="repo")
    repo.get_workflow.return_value = {"workflow_id": "w-paused", "status": "paused"}
    sched._reclaim_stale_in_progress([], repo=repo)  # not in active set
    assert "w-paused" in sched._in_progress_ids
    assert "/ws/p" in sched._in_progress_workspaces


def test_terminal_row_missing_from_active_list_is_reaped():
    """A completed/failed/cancelled row absent from the active set cannot have
    a live advance worth protecting — reap so its keys stop starving siblings."""
    sched = _bare_scheduler()
    _mark(sched, "w-done", batch="b4", workspace="/ws/b", branch="")
    repo = MagicMock(name="repo")
    repo.get_workflow.return_value = {"workflow_id": "w-done", "status": "completed"}
    sched._reclaim_stale_in_progress([], repo=repo)
    assert "w-done" not in sched._in_progress_ids
    assert "/ws/b" not in sched._in_progress_workspaces


def test_missing_row_status_query_failure_keeps_entry():
    """Fail-closed: if the status lookup itself errors, keep the entry — a
    wrong reap is worse than a short wait for the next cycle."""
    sched = _bare_scheduler()
    _mark(sched, "w-unknown", batch="b5", workspace="/ws/c", branch="")
    repo = MagicMock(name="repo")
    repo.get_workflow.side_effect = RuntimeError("db down")
    sched._reclaim_stale_in_progress([], repo=repo)
    assert "w-unknown" in sched._in_progress_ids


def test_healthy_state_issues_no_queries():
    """Zero extra DB cost when nothing is leaked: no rows missing from the
    active list, all leases fresh."""
    sched = _bare_scheduler()
    _mark(sched, "w-ok", batch="b6", workspace="/ws/d", branch="")
    row = {
        "workflow_id": "w-ok",
        "status": "developing",
        "locked_at": _fmt(datetime.now(timezone.utc)),
    }
    repo = MagicMock(name="repo")
    sched._reclaim_stale_in_progress([row], repo=repo)
    repo.get_workflow.assert_not_called()


def test_missing_row_with_fresh_lease_is_kept():
    """A row missing from the snapshot but holding a fresh lease is a live
    worker behind a snapshot race (e.g. #1002 paused→resume flipping to
    active between fetch and lookup) — the lease is the truth, keep it."""
    sched = _bare_scheduler()
    _mark(sched, "w-race", batch="b7", workspace="/ws/e", branch="")
    repo = MagicMock(name="repo")
    repo.get_workflow.return_value = {
        "workflow_id": "w-race",
        "status": "developing",
        "locked_at": _fmt(datetime.now(timezone.utc) - timedelta(seconds=30)),
    }
    sched._reclaim_stale_in_progress([], repo=repo)
    assert "w-race" in sched._in_progress_ids
    assert "/ws/e" in sched._in_progress_workspaces


def test_active_row_with_unparseable_lease_is_kept():
    """Unknown is not stale (fail-closed, symmetric with the lookup-error
    branch): a present-but-unparseable lease keeps the entry."""
    sched = _bare_scheduler()
    _mark(sched, "w-junk", batch="b8", workspace="/ws/f", branch="")
    row = {"workflow_id": "w-junk", "status": "developing", "locked_at": "not-a-date"}
    sched._reclaim_stale_in_progress([row], repo=MagicMock())
    assert "w-junk" in sched._in_progress_ids


def test_discard_entry_legacy_wf_fallback_releases_keys():
    """Pre-map entries (created before a mid-flight deploy) fall back to the
    caller's workflow dict for conflict keys, preserving clear_in_progress's
    old semantics."""
    sched = _bare_scheduler()
    wid = "w-legacy"
    with sched._in_progress_lock:
        sched._in_progress_ids.add(wid)
        sched._in_progress_by_user.setdefault(3, set()).add(wid)
        sched._in_progress_batch_ids.add("b-legacy")
        sched._in_progress_workspaces.add("/ws/legacy")
    legacy_wf = {
        "batch_id": "b-legacy",
        "project_path": "/proj",
        "worktree_path": "/ws/legacy",
        "branch_name": "auto-dev/leg",
        "status": "developing",
    }
    sched._discard_in_progress_entry(wid, legacy_wf=legacy_wf)
    assert wid not in sched._in_progress_ids
    assert "b-legacy" not in sched._in_progress_batch_ids
    assert "/ws/legacy" not in sched._in_progress_workspaces


# ── Task 3: pre-try exception protection ─────────────────────────────────


def test_pre_try_exception_still_clears_entry():
    """get_workflow raising BEFORE the main try must not leak the entry (the
    executor's future.result() catch only logs)."""
    sched = _bare_scheduler()
    _mark(sched, "w-pre", batch=None, workspace="", branch="")
    sched._orchestrator_lock = threading.Lock()
    sched._running_orchestrators = {}
    sched._stop_event = threading.Event()

    repo = MagicMock(name="repo")
    repo.get_workflow.side_effect = RuntimeError("db hiccup")

    import pytest

    with patch("app.routes.autonomous._get_repo", return_value=repo):
        with pytest.raises(RuntimeError, match="db hiccup"):
            sched._advance_single("w-pre")

    assert "w-pre" not in sched._in_progress_ids

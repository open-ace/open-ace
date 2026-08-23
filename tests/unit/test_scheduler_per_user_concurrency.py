"""Scheduler concurrency: global ceiling (10) + per-user cap (max_sessions_per_user).

Historically the scheduler's running cap was a single GLOBAL ``MAX_CONCURRENT_WORKFLOWS=3``
across all users, which starved multi-user deployments. #2295 makes it two-layer:
a raised global ceiling (default 10) + a per-user running cap equal to the owner's
tenant ``max_sessions_per_user`` (default 5), enforced at scheduler selection
(aligned with the create-time ``_check_user_concurrent_limit``).

These tests pin: the raised default, per-user cap resolution, the
``_in_progress_by_user`` lifecycle (add on select, discard in ``_advance_single``
including the lock-not-acquired early return — "Site A", discard in
``clear_in_progress``), the selection gate, and that waiting workflows count
against the per-user cap (matching ``count_active_workflows_by_user``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2295)]


def _make_scheduler():
    from app.services.autonomous_scheduler import AutonomousScheduler

    return AutonomousScheduler()


# ── per-user cap resolution ─────────────────────────────────────────────────


def test_per_user_cap_default_is_five(monkeypatch):
    """A user whose tenant has no explicit quota gets the default 5."""
    sched = _make_scheduler()
    fake_user = {"tenant_id": None}
    with (
        patch("app.repositories.user_repo.UserRepository.get_user_by_id", return_value=fake_user),
    ):
        assert sched._per_user_cap(123) == 5


def test_per_user_cap_reads_tenant_quota(monkeypatch):
    """The cap comes from the owner's tenant quota.max_sessions_per_user."""
    sched = _make_scheduler()
    fake_user = {"tenant_id": 7}
    fake_tenant = MagicMock()
    fake_tenant.quota.max_sessions_per_user = 2
    with (
        patch("app.repositories.user_repo.UserRepository.get_user_by_id", return_value=fake_user),
        patch("app.repositories.tenant_repo.TenantRepository.get_by_id", return_value=fake_tenant),
    ):
        assert sched._per_user_cap(123) == 2


def test_per_user_cap_none_owner_is_unlimited():
    """A legacy workflow with user_id=None counts only against the global ceiling,
    not any per-user bucket — so its per-user cap is effectively unlimited."""
    sched = _make_scheduler()
    # Should not even attempt a tenant lookup for a None owner.
    cap = sched._per_user_cap(None)
    assert cap is None  # sentinel: caller treats None as "no per-user gate"


# ── _in_progress_by_user lifecycle ──────────────────────────────────────────


def test_clear_in_progress_discards_by_user():
    """clear_in_progress(wf=...) must remove the workflow from its owner's bucket."""
    sched = _make_scheduler()
    wf_id = "wf-bucket-1"
    wf = {"workflow_id": wf_id, "user_id": 5, "project_path": "/p", "branch_name": "auto-dev/b"}
    sched._in_progress_ids.add(wf_id)
    sched._in_progress_by_user.setdefault(5, set()).add(wf_id)
    assert wf_id in sched._in_progress_by_user[5]

    with patch("app.routes.autonomous._get_repo"):
        sched.clear_in_progress(wf_id, wf=wf)

    assert wf_id not in sched._in_progress_ids
    assert 5 not in sched._in_progress_by_user or wf_id not in sched._in_progress_by_user[5]


def test_clear_in_progress_without_wf_still_clears_ids():
    """Without wf we can't know the owner, but _in_progress_ids must still clear."""
    sched = _make_scheduler()
    wf_id = "wf-bucket-2"
    sched._in_progress_ids.add(wf_id)
    with patch("app.routes.autonomous._get_repo"):
        sched.clear_in_progress(wf_id)
    assert wf_id not in sched._in_progress_ids


def test_advance_single_lock_not_acquired_discards_by_user():
    """Site A: when repo.acquire_lock fails, the early return (before the try/finally)
    must still discard _in_progress_by_user — otherwise the bucket leaks."""
    sched = _make_scheduler()
    wf_id = "wf-site-a"
    workflow = {
        "workflow_id": wf_id,
        "user_id": 9,
        "status": "planning",
        "current_phase": "planning",
        "project_path": "/p",
        "branch_name": "auto-dev/a",
        "batch_id": None,
        "worktree_path": "",
    }
    sched._in_progress_ids.add(wf_id)
    sched._in_progress_by_user.setdefault(9, set()).add(wf_id)

    mock_repo = MagicMock()
    mock_repo.get_workflow.return_value = workflow
    mock_repo.acquire_lock.return_value = False  # triggers Site A early return

    with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
        sched._advance_single(wf_id)

    assert wf_id not in sched._in_progress_ids
    assert 9 not in sched._in_progress_by_user or wf_id not in sched._in_progress_by_user[9]


# ── selection gate ──────────────────────────────────────────────────────────


def _wf(wf_id, user_id, *, branch=None, project="/p", status="planning", batch=None):
    return {
        "workflow_id": wf_id,
        "user_id": user_id,
        "status": status,
        "current_phase": status,
        "project_path": project,
        "branch_name": branch or f"auto-dev/{wf_id[:8]}",
        "batch_id": batch,
        "worktree_path": "",
        "created_at": "2026-08-04T00:00:00",
    }


def test_selection_respects_per_user_cap(monkeypatch):
    """A user at their per-user cap has their extra workflow skipped; another user
    under cap is selected in the same cycle."""
    sched = _make_scheduler()
    # user A cap=1 (already running 1), user B cap=5 (running 0).
    sched._in_progress_by_user.setdefault(1, set()).add("running-A1")
    sched._in_progress_ids.add("running-A1")

    active = [_wf("new-A2", 1, branch="b-A2"), _wf("new-B1", 2, branch="b-B1")]

    selected = []
    with (
        patch.object(sched, "_promote_queued_workflows"),
        patch.object(sched, "_auto_resume_quota_paused"),
        patch.object(sched, "_reclaim_paused_slots"),
        # The lease-anchored reclaim is a separate lifecycle concern; these
        # selection tests seed cross-cycle in-progress entries deliberately.
        patch.object(sched, "_reclaim_stale_in_progress"),
        patch("app.services.autonomous_scheduler._retry_pending_git_cleanups"),
        patch.object(sched, "_advance_single", side_effect=lambda wid: selected.append(wid)),
        patch.object(sched, "_per_user_cap", side_effect=lambda uid: 1 if uid == 1 else 5),
    ):
        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = active
        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
            sched._process_workflows()

    assert "new-B1" in selected  # B under cap → selected
    assert "new-A2" not in selected  # A at cap (1 running) → skipped
    # And A's skipped workflow was NOT recorded in A's bucket.
    assert "new-A2" not in sched._in_progress_by_user.get(1, set())


def test_waiting_workflow_counts_against_per_user_cap(monkeypatch):
    """A waiting workflow still consumes its owner's per-user bucket (consistent
    with count_active_workflows_by_user), even though it bypasses conflict keys."""
    sched = _make_scheduler()
    # user 1 cap=1, already running 1 waiting workflow.
    sched._in_progress_by_user.setdefault(1, set()).add("waiting-A1")
    sched._in_progress_ids.add("waiting-A1")

    active = [_wf("new-A2", 1, branch="b-A2", status="planning")]
    selected = []
    with (
        patch.object(sched, "_promote_queued_workflows"),
        patch.object(sched, "_auto_resume_quota_paused"),
        patch.object(sched, "_reclaim_paused_slots"),
        # Selection-scoped test: seed entries must survive the reclaim pass.
        patch.object(sched, "_reclaim_stale_in_progress"),
        patch("app.services.autonomous_scheduler._retry_pending_git_cleanups"),
        patch.object(sched, "_advance_single", side_effect=lambda wid: selected.append(wid)),
        patch.object(sched, "_per_user_cap", return_value=1),
    ):
        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = active
        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
            sched._process_workflows()

    assert "new-A2" not in selected  # waiting-A1 already consumes user 1's cap of 1


def test_global_ceiling_still_binds(monkeypatch):
    """Even with many users each under their per-user cap, total selected ≤ the
    global ceiling."""
    sched = _make_scheduler()
    # 4 users, each with 2 ready workflows; per-user cap high (5); global=10.
    active = []
    for u in range(4):
        for i in range(2):
            active.append(_wf(f"wf-{u}-{i}", u, branch=f"b-{u}-{i}"))
    selected = []
    with (
        patch.object(sched, "_promote_queued_workflows"),
        patch.object(sched, "_auto_resume_quota_paused"),
        patch.object(sched, "_reclaim_paused_slots"),
        patch("app.services.autonomous_scheduler._retry_pending_git_cleanups"),
        patch.object(sched, "_advance_single", side_effect=lambda wid: selected.append(wid)),
        patch.object(sched, "_per_user_cap", return_value=5),
        patch(
            "app.services.autonomous_scheduler.get_max_concurrent_workflows",
            return_value=10,
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.get_active_workflows.return_value = active
        with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
            sched._process_workflows()

    assert len(selected) <= 10

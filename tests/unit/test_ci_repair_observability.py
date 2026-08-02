"""Regression tests for CI-repair observability.

Bug 1: a prior cycle's COMPLETED ci_repair milestone must not block a new cycle's
same-round milestone (cycle restarts don't bump dev_round, so the dedup key
(phase, type, dev_round, round_number) collides across cycles).

Bug 2: the CI-repair result summary must not be hard-truncated to 300 chars.
"""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def test_build_dev_result_summary_preserves_long_text():
    """Bug 2: the repair summary must not be hard-truncated to 300 chars."""
    long_summary = "## CI fix report\n\n" + ("detail line\n" * 200)  # well over 300 chars
    out = AutonomousOrchestrator._build_dev_result_summary(
        long_summary, {"files": 3, "additions": 10, "deletions": 2}, "abcdef12", True
    )
    assert out == long_summary.strip()
    assert len(out) > 300


def test_ci_repair_milestone_not_blocked_by_prior_completed_cycle():
    """Bug 1: a new cycle's ci_repair milestone must not be deduped against a
    prior cycle's COMPLETED milestone of the same (dev_round, round_number)."""
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = "wf-1"
    orch._emit = lambda *_a, **_k: None

    prior = {
        "milestone_id": "prior-1",
        "milestone_type": "ci_repair_applied",
        "phase": "merge",
        "dev_round": 1,
        "round_number": 2,
        "status": "completed",
    }
    created = {}

    def fake_list(wf_id, phase=None, status=None):
        return [prior] if status == "completed" else []

    def fake_create(data):
        created["called"] = True
        return {"milestone_id": "new-1", **data}

    repo = MagicMock()
    repo.list_milestones = fake_list
    repo.create_milestone = fake_create
    orch.repo = repo

    # New cycle (dev_round still 1 because the restart didn't bump it) creates
    # ci_repair_applied for round 2 — must NOT be deduped by the prior completed one.
    orch._create_milestone(
        phase="merge",
        dev_round=1,
        round_number=2,
        milestone_type="ci_repair_applied",
        status="in_progress",
        title="CI repair attempt 2 for PR #9",
    )
    assert created.get(
        "called"
    ), "new ci_repair milestone was wrongly deduped against a prior completed cycle"

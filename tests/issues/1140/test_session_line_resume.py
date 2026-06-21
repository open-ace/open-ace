"""Integration test: 3-session resume across planning → finalize → dev.

Verifies that when planning creates a session on the "main" line, finalize
(and subsequently dev) correctly resumes that session instead of creating
a new one. This is the core of the 3-session design.

Root cause being tested: _run_agent updates the session line in the DB, but
the in-memory wf dict may not reflect the update when the next phase in the
same _do_planning run calls _resolve_session_line. The fix adds a DB
fallback in _resolve_session_line.
"""

import uuid
from unittest.mock import MagicMock


def _make_orchestrator(db_state):
    """Create an orchestrator with a mock repo that returns db_state copies."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    workflow_id = str(uuid.uuid4())

    # AutonomousOrchestrator.__init__ creates its own repo from self.db.
    # We patch self.repo after construction to control get_workflow.
    repo = MagicMock()
    repo.get_workflow = MagicMock(return_value=dict(db_state))
    repo.update_workflow = MagicMock(side_effect=lambda wid, updates: db_state.update(updates))

    # Bypass __init__ to avoid real DB connection
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = workflow_id
    orch.repo = repo
    orch._session_lock = MagicMock()
    orch._current_session_id = None
    return orch, repo


def test_resolve_session_line_resumes_after_run_agent_update():
    """_resolve_session_line must see the session id written by a prior
    _run_agent call on the same line, even if the in-memory wf dict is stale.

    Simulates the _do_planning flow:
      1. wf = self.workflow (read once at entry, main_session_id empty)
      2. planning _run_agent(session_line="main") → updates DB
      3. finalize _resolve_session_line(stale_wf, "main") → must resume
    """
    db_state = {"main_session_id": "", "review_session_id": "", "status": "planning"}
    orch, repo = _make_orchestrator(db_state)

    # Phase 1: planning — session line is empty → create new
    wf = orch.workflow
    assert wf["main_session_id"] == "", "precondition: empty main_session_id"
    sid, resume_sid, resume = orch._resolve_session_line(wf, "main")
    assert resume is False, "first call on main line should not resume"

    # Simulate _run_agent success: update DB with the real session id
    planning_sid = "sess_planning_abc123"
    db_state["main_session_id"] = planning_sid
    # repo.get_workflow returns copies, so update the mock
    repo.get_workflow = MagicMock(return_value=dict(db_state))

    # Phase 2: finalize — wf dict is STALE (still has empty main_session_id)
    stale_wf = {"main_session_id": "", "review_session_id": "", "status": "planning"}
    sid2, resume_sid2, resume2 = orch._resolve_session_line(stale_wf, "main")
    assert resume2 is True, (
        "finalize must resume planning session even if wf dict is stale — "
        "DB fallback is required"
    )
    assert resume_sid2 == planning_sid, "resume_session_id must match planning session"


def test_resolve_session_line_resume_with_fresh_wf():
    """When wf dict IS up-to-date (PR #1156 wf[field] sync), resume works
    without needing DB fallback."""
    db_state = {"main_session_id": "", "review_session_id": ""}
    orch, repo = _make_orchestrator(db_state)

    # Simulate wf[field] = result.session_id (PR #1156)
    wf = {"main_session_id": "sess_planning_fresh", "review_session_id": ""}
    sid, resume_sid, resume = orch._resolve_session_line(wf, "main")
    assert resume is True
    assert resume_sid == "sess_planning_fresh"


def test_resolve_session_line_review_independent_from_main():
    """review session line is independent — resuming main does not affect it."""
    db_state = {"main_session_id": "sess_main", "review_session_id": ""}
    orch, repo = _make_orchestrator(db_state)

    wf = {"main_session_id": "sess_main", "review_session_id": ""}

    # review line should NOT resume (empty), even though main has a session
    sid, resume_sid, resume = orch._resolve_session_line(wf, "review")
    assert resume is False, "review line must be independent from main"

"""Orchestrator user-feedback prompt injection tests (Issue #886).

Covers ``AutonomousOrchestrator._get_user_feedback_prompt`` — the seam that
turns a workflow's stored ``user_feedback`` (set by the cancel/fork/resume
routes) into the prompt fragment injected into the next orchestration round.

Migrated from tests/issues/886/test_cancel_fork_redesign.py
(TestOrchestratorFeedbackInjection).
"""

from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(886)]


def _make_workflow(**overrides):
    """Create a sample workflow dict."""
    wf = {
        "workflow_id": "wf-001",
        "user_id": 1,
        "title": "Test Workflow",
        "status": "developing",
        "user_feedback": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    wf.update(overrides)
    return wf


class TestOrchestratorFeedbackInjection:
    """Tests for _get_user_feedback_prompt and prompt injection."""

    def test_feedback_prompt_injection(self):
        """_get_user_feedback_prompt returns formatted feedback text."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="Focus on testing and code coverage")

        prompt = orch._get_user_feedback_prompt(wf)
        assert "Focus on testing and code coverage" in prompt
        assert "用户反馈" in prompt

    def test_feedback_prompt_empty_when_no_feedback(self):
        """_get_user_feedback_prompt returns empty string when no feedback."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="")

        prompt = orch._get_user_feedback_prompt(wf)
        assert prompt == ""

    def test_feedback_prompt_empty_when_whitespace_only(self):
        """_get_user_feedback_prompt returns empty string for whitespace."""
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        wf = _make_workflow(user_feedback="   ")

        prompt = orch._get_user_feedback_prompt(wf)
        assert prompt == ""

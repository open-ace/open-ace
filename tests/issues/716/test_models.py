"""Unit tests for autonomous development data models."""

from datetime import datetime

import pytest

from app.modules.workspace.autonomous.models import (
    AgentTaskResult,
    AutonomousWorkflow,
    WorkflowEvent,
    WorkflowMilestone,
)


class TestAutonomousWorkflow:
    """Tests for AutonomousWorkflow dataclass."""

    def test_default_values(self):
        wf = AutonomousWorkflow()
        assert wf.workflow_id == ""
        assert wf.status == "pending"
        assert wf.current_phase == "preparation"
        assert wf.dev_round == 1
        assert wf.max_plan_rounds == 3
        assert wf.max_pr_review_rounds == 5
        assert wf.total_tokens == 0
        assert wf.is_new_project is False
        assert wf.workspace_type == "local"

    def test_custom_values(self):
        wf = AutonomousWorkflow(
            workflow_id="test-uuid-123",
            user_id=1,
            title="Test Task",
            status="planning",
            requirements_text="Build a feature",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
        )
        assert wf.workflow_id == "test-uuid-123"
        assert wf.user_id == 1
        assert wf.status == "planning"
        assert wf.requirements_text == "Build a feature"

    def test_to_dict(self):
        now = datetime(2026, 6, 5, 12, 0, 0)
        wf = AutonomousWorkflow(
            id=1,
            workflow_id="uuid-1",
            user_id=1,
            title="Test",
            status="pending",
            created_at=now,
        )
        d = wf.to_dict()
        assert d["workflow_id"] == "uuid-1"
        assert d["user_id"] == 1
        assert d["title"] == "Test"
        assert d["status"] == "pending"
        assert d["created_at"] == "2026-06-05T12:00:00"

    def test_to_dict_none_dates(self):
        wf = AutonomousWorkflow()
        d = wf.to_dict()
        assert d["created_at"] is None
        assert d["updated_at"] is None
        assert d["completed_at"] is None

    def test_from_dict(self):
        data = {
            "id": 1,
            "workflow_id": "uuid-2",
            "user_id": 2,
            "title": "From Dict",
            "status": "developing",
            "cli_tool": "codex",
            "model": "gpt-4",
            "dev_round": 2,
            "created_at": "2026-06-05T12:00:00",
        }
        wf = AutonomousWorkflow.from_dict(data)
        assert wf.workflow_id == "uuid-2"
        assert wf.status == "developing"
        assert wf.cli_tool == "codex"
        assert wf.dev_round == 2
        assert wf.created_at == datetime(2026, 6, 5, 12, 0, 0)

    def test_from_dict_empty(self):
        wf = AutonomousWorkflow.from_dict({})
        assert wf.workflow_id == ""
        assert wf.status == "pending"

    def test_from_dict_none(self):
        wf = AutonomousWorkflow.from_dict(None)
        assert wf.workflow_id == ""

    def test_is_active(self):
        for status in AutonomousWorkflow.ACTIVE_STATUSES:
            wf = AutonomousWorkflow(status=status)
            assert wf.is_active(), f"Status '{status}' should be active"

    def test_is_not_active(self):
        for status in ["completed", "failed", "cancelled", "paused"]:
            wf = AutonomousWorkflow(status=status)
            assert not wf.is_active(), f"Status '{status}' should not be active"

    def test_is_paused(self):
        wf = AutonomousWorkflow(status="paused")
        assert wf.is_paused()

    def test_is_not_paused(self):
        wf = AutonomousWorkflow(status="running")
        assert not wf.is_paused()

    def test_roundtrip(self):
        wf = AutonomousWorkflow(
            id=1,
            workflow_id="uuid-rt",
            title="Roundtrip Test",
            dev_round=3,
        )
        d = wf.to_dict()
        wf2 = AutonomousWorkflow.from_dict(d)
        assert wf2.workflow_id == wf.workflow_id
        assert wf2.title == wf.title
        assert wf2.dev_round == wf.dev_round


class TestWorkflowMilestone:
    """Tests for WorkflowMilestone dataclass."""

    def test_default_values(self):
        ms = WorkflowMilestone()
        assert ms.milestone_id == ""
        assert ms.phase == ""
        assert ms.status == "pending"
        assert ms.dev_round == 1

    def test_to_dict(self):
        now = datetime(2026, 6, 5, 12, 0, 0)
        ms = WorkflowMilestone(
            id=1,
            workflow_id="wf-1",
            milestone_id="ms-1",
            phase="planning",
            milestone_type="plan_created",
            status="completed",
            title="Plan created",
            created_at=now,
        )
        d = ms.to_dict()
        assert d["workflow_id"] == "wf-1"
        assert d["milestone_id"] == "ms-1"
        assert d["phase"] == "planning"
        assert d["milestone_type"] == "plan_created"
        assert d["created_at"] == "2026-06-05T12:00:00"

    def test_from_dict(self):
        data = {
            "id": 1,
            "workflow_id": "wf-2",
            "milestone_id": "ms-2",
            "phase": "development",
            "milestone_type": "dev_started",
            "status": "in_progress",
            "fork_workflow_id": "wf-fork-2",
            "created_at": "2026-06-05T12:00:00",
        }
        ms = WorkflowMilestone.from_dict(data)
        assert ms.milestone_id == "ms-2"
        assert ms.phase == "development"
        assert ms.status == "in_progress"
        assert ms.fork_workflow_id == "wf-fork-2"

    def test_from_dict_empty(self):
        ms = WorkflowMilestone.from_dict({})
        assert ms.milestone_id == ""

    def test_from_dict_none(self):
        ms = WorkflowMilestone.from_dict(None)
        assert ms.milestone_id == ""

    def test_roundtrip(self):
        ms = WorkflowMilestone(
            id=1,
            workflow_id="wf-rt",
            milestone_id="ms-rt",
            phase="pr_review",
            milestone_type="pr_created",
            title="PR #42",
            dev_round=2,
            round_number=1,
        )
        d = ms.to_dict()
        ms2 = WorkflowMilestone.from_dict(d)
        assert ms2.milestone_id == ms.milestone_id
        assert ms2.phase == ms.phase
        assert ms2.dev_round == ms.dev_round

    def test_to_dict_includes_fork_workflow_id(self):
        ms = WorkflowMilestone(
            milestone_id="ms-fork",
            milestone_type="workflow_forked",
            fork_workflow_id="wf-fork-001",
        )
        d = ms.to_dict()
        assert d["fork_workflow_id"] == "wf-fork-001"


class TestWorkflowEvent:
    """Tests for WorkflowEvent dataclass."""

    def test_default_values(self):
        ev = WorkflowEvent()
        assert ev.workflow_id == ""
        assert ev.event_type == ""

    def test_to_dict(self):
        now = datetime(2026, 6, 5, 12, 0, 0)
        ev = WorkflowEvent(
            id=1,
            workflow_id="wf-1",
            event_type="phase_change",
            event_data='{"phase": "development"}',
            created_at=now,
        )
        d = ev.to_dict()
        assert d["workflow_id"] == "wf-1"
        assert d["event_type"] == "phase_change"
        assert d["created_at"] == "2026-06-05T12:00:00"

    def test_from_dict(self):
        data = {
            "id": 1,
            "workflow_id": "wf-2",
            "event_type": "error",
            "event_data": '{"error": "test"}',
        }
        ev = WorkflowEvent.from_dict(data)
        assert ev.workflow_id == "wf-2"
        assert ev.event_type == "error"

    def test_from_dict_empty(self):
        ev = WorkflowEvent.from_dict({})
        assert ev.workflow_id == ""


class TestAgentTaskResult:
    """Tests for AgentTaskResult dataclass."""

    def test_default_values(self):
        r = AgentTaskResult()
        assert r.session_id == ""
        assert r.tracking_session_id == ""
        assert r.response_text == ""
        assert r.visible_response_text == ""
        assert r.structured_tags == {}
        assert r.success is False
        assert r.error is None
        assert r.total_tokens == 0
        assert r.request_count == 0
        assert r.messages == []
        assert r.tool_calls == []

    def test_success_result(self):
        r = AgentTaskResult(
            session_id="sess-1",
            tracking_session_id="track-1",
            response_text="Done",
            visible_response_text="Working...\n\nDone",
            structured_tags={"tldr": "done"},
            total_tokens=500,
            total_input_tokens=300,
            total_output_tokens=200,
            request_count=3,
            success=True,
        )
        assert r.success is True
        assert r.tracking_session_id == "track-1"
        assert r.visible_response_text == "Working...\n\nDone"
        assert r.structured_tags["tldr"] == "done"
        assert r.total_tokens == 500
        assert r.request_count == 3

    def test_error_result(self):
        r = AgentTaskResult(
            session_id="sess-2",
            success=False,
            error="Timeout after 1800s",
        )
        assert r.success is False
        assert r.error == "Timeout after 1800s"

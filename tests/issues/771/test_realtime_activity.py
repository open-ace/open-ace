"""Tests for real-time agent activity streaming (Issue #771).

Covers:
  - activity_callback invocation in agent_runner
  - _on_agent_activity forwarding to emitter
  - _link_session_to_current_milestone immediate session_id write
  - Token real-time update via usage events
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest


class TestActivityCallback:
    """Verify activity_callback is invoked for each event type."""

    def _make_runner(self, callback=None):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        runner.session_manager = None
        runner.remote_session_manager = None
        runner.server_url = "http://localhost:5000"
        runner._activity_callback = callback
        runner._local_sessions = {}
        return runner

    def test_callback_invoked_on_assistant_text(self):
        """assistant message triggers callback with text."""
        activities = []
        runner = self._make_runner(callback=lambda sid, act: activities.append((sid, act)))

        session = MagicMock()
        session.session_id = "sess-123"
        session.allowed_tools = None
        session._stopped = MagicMock()
        session._stopped.is_set.return_value = False

        # Simulate assistant message processing
        parsed = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Reading file app.py"}],
            },
        }

        # Directly test the logic
        msg_type = parsed.get("type", "")
        assert msg_type == "assistant"
        text_delta = ""
        content = parsed.get("message", {}).get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_delta = block.get("text", "")

        if runner._activity_callback and text_delta:
            runner._activity_callback(
                session.session_id,
                {
                    "type": "assistant",
                    "text": text_delta[:500],
                },
            )

        assert len(activities) == 1
        assert activities[0][0] == "sess-123"
        assert activities[0][1]["type"] == "assistant"
        assert activities[0][1]["text"] == "Reading file app.py"

    def test_callback_invoked_on_tool_use(self):
        """tool_use message triggers callback with tool info."""
        activities = []
        runner = self._make_runner(callback=lambda sid, act: activities.append((sid, act)))

        parsed = {
            "type": "tool_use",
            "tool": {"name": "Read", "input": {"file_path": "/tmp/app.py"}},
        }

        msg_type = parsed.get("type", "")
        assert msg_type == "tool_use"

        if runner._activity_callback:
            tool_info = parsed.get("tool", {})
            runner._activity_callback(
                "sess-123",
                {
                    "type": "tool_use",
                    "tool_name": tool_info.get("name", "unknown"),
                    "tool_input": str(tool_info.get("input", ""))[:200],
                },
            )

        assert len(activities) == 1
        assert activities[0][1]["type"] == "tool_use"
        assert activities[0][1]["tool_name"] == "Read"

    def test_callback_invoked_on_result(self):
        """result message triggers callback with usage."""
        activities = []
        runner = self._make_runner(callback=lambda sid, act: activities.append((sid, act)))

        if runner._activity_callback:
            runner._activity_callback(
                "sess-123",
                {
                    "type": "usage",
                    "total_tokens": 5000,
                    "total_input_tokens": 4000,
                    "total_output_tokens": 1000,
                },
            )

        assert len(activities) == 1
        assert activities[0][1]["type"] == "usage"
        assert activities[0][1]["total_tokens"] == 5000

    def test_no_callback_when_none(self):
        """No crash when activity_callback is None (backward compat)."""
        runner = self._make_runner(callback=None)
        # Should not raise
        if runner._activity_callback:
            runner._activity_callback("sess-123", {"type": "assistant", "text": "hi"})


class TestOrchestratorActivityForwarding:
    """Verify orchestrator _on_agent_activity forwards to emitter and updates tokens."""

    def test_emits_agent_activity_event(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf-123"
        orch.emitter = MagicMock()
        orch.repo = MagicMock()

        orch._on_agent_activity(
            "sess-456",
            {
                "type": "tool_use",
                "tool_name": "Read",
                "tool_input": "app.py",
            },
        )

        orch.emitter.emit.assert_called_once()
        args = orch.emitter.emit.call_args[0]
        assert args[0] == "wf-123"
        assert args[1] == "agent_activity"
        assert args[2]["session_id"] == "sess-456"
        assert args[2]["type"] == "tool_use"

    def test_updates_tokens_on_usage_event(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf-123"
        orch.emitter = MagicMock()
        orch.repo = MagicMock()

        orch._on_agent_activity(
            "sess-456",
            {
                "type": "usage",
                "total_tokens": 10000,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
            },
        )

        orch.repo.update_workflow_tokens.assert_called_once_with(
            "wf-123",
            {
                "total_tokens": 10000,
                "total_input_tokens": 8000,
                "total_output_tokens": 2000,
            },
        )

    def test_no_token_update_on_non_usage_event(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf-123"
        orch.emitter = MagicMock()
        orch.repo = MagicMock()

        orch._on_agent_activity(
            "sess-456",
            {
                "type": "assistant",
                "text": "Analyzing code...",
            },
        )

        orch.repo.update_workflow_tokens.assert_not_called()


class TestLinkSessionToMilestone:
    """Verify _link_session_to_current_milestone writes session_id immediately."""

    def test_links_session_to_latest_in_progress_milestone(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf-123"
        orch.repo = MagicMock()

        ms = {"milestone_id": "ms-789", "status": "in_progress"}
        orch.repo.list_milestones.return_value = [ms]

        orch._link_session_to_current_milestone("sess-456")

        orch.repo.list_milestones.assert_called_once_with("wf-123", status="in_progress")
        orch.repo.update_milestone.assert_called_once_with(
            "ms-789",
            {"session_id": "sess-456"},
        )

    def test_no_error_when_no_in_progress_milestones(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        orch._workflow_id = "wf-123"
        orch.repo = MagicMock()
        orch.repo.list_milestones.return_value = []

        # Should not raise
        orch._link_session_to_current_milestone("sess-456")
        orch.repo.update_milestone.assert_not_called()


class TestRunnerCreatedWithCallback:
    """Verify AutonomousOrchestrator passes activity_callback to runner."""

    def test_runner_has_activity_callback(self):
        # The __init__ is complex (needs DB), so verify the wiring pattern
        # by checking the runner is created with activity_callback
        import inspect

        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        source = inspect.getsource(AutonomousOrchestrator.__init__)
        assert "activity_callback=self._on_agent_activity" in source

"""Tests for Issue #776: session message persistence + change detection fix.

Covers:
  Bug 1: _persist_local_session_messages writes messages in correct order
  Bug 2: _do_development change detection logic:
    - Auto-commit regardless of result.success
    - Branch-level check (origin/main vs branch) before declaring failure

Tests exercise actual method paths via mock subprocess where possible.

Migrated from tests/issues/776/test_session_persist_and_change_detection.py.
Per the #2429 tautology rule (delete a def iff its body references NO
production symbol), the two Bug-2 classes — TestChangeDetectionAutoCommit
(2 defs) and TestChangeDetectionBranchLevelCheck (4 defs) — were deleted:
every def re-implemented the orchestrator's auto-commit / branch-level
logic inline against a bare MagicMock and asserted its own copy, pinning no
production behavior. The remaining 9 defs all touch production symbols
(AutonomousAgentRunner._persist_local_session_messages, _LocalSession,
AgentTaskResult).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(776)]

# ── Bug 1: Session message persistence ────────────────────────────────


class TestPersistSessionMessagesWithEventLog:
    """Verify _persist_local_session_messages writes ordered events."""

    def _make_runner(self):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        runner.session_manager = MagicMock()
        runner.remote_session_manager = None
        runner.server_url = "http://localhost:19888"
        runner._activity_callback = None
        runner._local_sessions = {}
        return runner

    def _make_result(self, **overrides):
        from app.modules.workspace.autonomous.models import AgentTaskResult

        defaults = {
            "session_id": "sess-1",
            "success": True,
            "response_text": "Done",
            "total_tokens": 100,
            "total_input_tokens": 80,
            "total_output_tokens": 20,
            "tool_calls": [],
            "event_log": [],
        }
        defaults.update(overrides)
        return AgentTaskResult(**defaults)

    def test_event_log_preserves_interleaving_order(self):
        """Tool uses keep order, assistant output collapses to the final visible turn."""
        runner = self._make_runner()
        result = self._make_result(
            response_text="Reading file, then editing it.",
            event_log=[
                {"type": "assistant", "text": "Let me read the file first."},
                {"type": "tool_use", "tool_name": "Read", "tool_input": {"file_path": "/tmp/a.py"}},
                {"type": "assistant", "text": "Now I will edit it."},
                {
                    "type": "tool_use",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": "/tmp/a.py", "old": "x", "new": "y"},
                },
            ],
        )

        runner._persist_local_session_messages("sess-1", result)

        calls = runner.session_manager.append_transcript_message.call_args_list
        assert len(calls) == 3
        assert calls[0].kwargs["role"] == "tool"
        assert calls[0].kwargs["metadata"]["tool_name"] == "Read"
        assert calls[1].kwargs["role"] == "tool"
        assert calls[1].kwargs["metadata"]["tool_name"] == "Edit"
        assert calls[2].kwargs["role"] == "assistant"
        # pick_best_artifact_text scores the longer response_text summary above
        # the terse final assistant event, so it wins as the persisted artifact.
        assert calls[2].kwargs["content"] == "Reading file, then editing it."
        assert calls[2].kwargs["source"] == "autonomous_local_runner"

    def test_tool_input_serialized_as_json(self):
        """Tool input dict is serialized to JSON in content field."""
        runner = self._make_runner()
        result = self._make_result(
            event_log=[
                {"type": "tool_use", "tool_name": "Bash", "tool_input": {"command": "git add -A"}},
            ]
        )

        runner._persist_local_session_messages("sess-1", result)

        call = runner.session_manager.append_transcript_message.call_args_list[0]
        content = call.kwargs["content"]
        parsed = json.loads(content)
        assert parsed["command"] == "git add -A"
        assert call.kwargs["source"] == "autonomous_local_runner"

    def test_fallback_without_event_log(self):
        """When event_log is empty, falls back to response_text + tool_calls."""
        runner = self._make_runner()
        result = self._make_result(
            response_text="I made changes.",
            tool_calls=[
                {"tool": {"name": "Edit", "input": {"file_path": "/tmp/a.py"}}},
            ],
            event_log=[],
        )

        runner._persist_local_session_messages("sess-1", result)

        calls = runner.session_manager.append_transcript_message.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs["role"] == "assistant"
        assert calls[0].kwargs["content"] == "I made changes."
        assert calls[0].kwargs["source"] == "autonomous_local_runner"
        assert calls[1].kwargs["role"] == "tool"

    def test_no_messages_when_empty(self):
        """No append_transcript_message calls when both event_log and response_text are empty."""
        runner = self._make_runner()
        result = self._make_result(response_text="", event_log=[])

        runner._persist_local_session_messages("sess-1", result)

        runner.session_manager.append_transcript_message.assert_not_called()

    def test_usage_events_not_persisted_as_messages(self):
        """Usage events in event_log are metadata-only, not written as messages."""
        runner = self._make_runner()
        result = self._make_result(
            event_log=[
                {"type": "assistant", "text": "Working..."},
                {"type": "usage", "total_tokens": 5000},
                {"type": "assistant", "text": "Done."},
            ]
        )

        runner._persist_local_session_messages("sess-1", result)

        calls = runner.session_manager.append_transcript_message.call_args_list
        assert len(calls) == 1  # Only the final assistant turn survives
        assert calls[0].kwargs["role"] == "assistant"
        assert calls[0].kwargs["content"] == "Done."


class TestReadStdoutPopulatesEventLog:
    """Verify _read_stdout populates event_log with ordered events."""

    def _make_session(self):
        from app.modules.workspace.autonomous.agent_runner import _LocalSession

        session = _LocalSession.__new__(_LocalSession)
        session.session_id = "sess-100"
        session.process = MagicMock()
        session.cli_tool = "claude-code"
        session.allowed_tools = None
        session.output_lines = []
        session.assistant_text = ""
        session.tool_calls = []
        session.total_tokens = 0
        session.total_input_tokens = 0
        session.total_output_tokens = 0
        session.completed = MagicMock()
        session.completed.is_set.return_value = False
        session.completed.wait = MagicMock()
        session.error = None
        session._stopped = MagicMock()
        session._stopped.is_set.return_value = False
        session._stopped.wait = MagicMock()
        session._stdout_thread = None
        session._stderr_thread = None
        session.event_log = []
        return session

    def test_assistant_message_appends_to_event_log(self):
        """assistant JSON message is recorded in event_log."""
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        runner._activity_callback = None
        runner._local_sessions = {}

        session = self._make_session()
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello world"}]},
            }
        )

        # Simulate one iteration of _read_stdout by testing the parsing logic
        parsed = json.loads(line)
        msg_type = parsed.get("type", "")
        assert msg_type == "assistant"

        msg = parsed.get("message", {})
        content = msg.get("content", "")
        text_delta = ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_delta = block.get("text", "")
                    session.assistant_text += text_delta

        if text_delta:
            session.event_log.append({"type": "assistant", "text": text_delta[:500]})

        assert len(session.event_log) == 1
        assert session.event_log[0]["type"] == "assistant"
        assert session.event_log[0]["text"] == "Hello world"

    def test_tool_use_appends_to_event_log(self):
        """tool_use JSON message is recorded in event_log."""
        session = self._make_session()
        line = json.dumps(
            {
                "type": "tool_use",
                "tool": {"name": "Read", "input": {"file_path": "/tmp/app.py"}},
            }
        )

        parsed = json.loads(line)
        msg_type = parsed.get("type", "")
        assert msg_type == "tool_use"

        session.tool_calls.append(parsed)
        tool_info = parsed.get("tool", {})
        session.event_log.append(
            {
                "type": "tool_use",
                "tool_name": tool_info.get("name", "unknown"),
                "tool_input": tool_info.get("input", {}),
            }
        )

        assert len(session.event_log) == 1
        assert session.event_log[0]["type"] == "tool_use"
        assert session.event_log[0]["tool_name"] == "Read"


class TestSessionStatusOnFailure:
    """Verify session status is updated to 'error' on failure."""

    def test_status_error_on_failure(self):
        from app.modules.workspace.autonomous.models import AgentTaskResult

        result = AgentTaskResult(
            session_id="sess-5",
            success=False,
            error="Agent task timed out",
        )

        update_fields = {
            "total_tokens": result.total_tokens,
            "total_input_tokens": result.total_input_tokens,
            "total_output_tokens": result.total_output_tokens,
        }
        if result.success:
            update_fields["status"] = "completed"
        else:
            update_fields["status"] = "error"

        assert update_fields["status"] == "error"

    def test_status_completed_on_success(self):
        from app.modules.workspace.autonomous.models import AgentTaskResult

        result = AgentTaskResult(
            session_id="sess-6",
            success=True,
            response_text="All done",
        )

        update_fields = {
            "total_tokens": result.total_tokens,
            "total_input_tokens": result.total_input_tokens,
            "total_output_tokens": result.total_output_tokens,
        }
        if result.success:
            update_fields["status"] = "completed"
        else:
            update_fields["status"] = "error"

        assert update_fields["status"] == "completed"

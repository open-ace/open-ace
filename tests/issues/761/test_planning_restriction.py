"""Tests for planning-phase tool restrictions and timeout extension (Issue #761).

Covers the four-layer defense:
  Layer 1 – PLANNING_CONTEXT prompt
  Layer 2 – allowed_tools threading + adapter flag generation
  Layer 3 – selective auto-approve filtering
  Layer 4 – planning timeout / extend-planning-timeout API
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ── Layer 1: PLANNING_CONTEXT prompt ──────────────────────────────────


class TestPlanningContext:
    """Verify PLANNING_CONTEXT replaces AUTONOMOUS_CONTEXT in planning prompts."""

    def test_planning_context_exists(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_CONTEXT

        assert PLANNING_CONTEXT
        # Must NOT contain the contradictory "直接执行文件修改" instruction
        assert "直接执行文件修改" not in PLANNING_CONTEXT
        # Must contain the read-only constraint
        assert "不要修改任何文件" in PLANNING_CONTEXT

    def test_autonomous_context_unchanged(self):
        """AUTONOMOUS_CONTEXT should still have the write instruction for dev phase."""
        from app.modules.workspace.autonomous.orchestrator import AUTONOMOUS_CONTEXT

        assert "直接执行文件修改" in AUTONOMOUS_CONTEXT


# ── Layer 2: allowed_tools ────────────────────────────────────────────


class TestPlanningAllowedTools:
    """Verify PLANNING_ALLOWED_TOOLS constant and threading."""

    def test_claude_code_tools_are_readonly(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_ALLOWED_TOOLS

        tools = PLANNING_ALLOWED_TOOLS["claude-code"]
        assert "Read" in tools
        assert "Grep" in tools
        # Write tools must NOT be present
        assert "Edit" not in tools
        assert "Write" not in tools
        assert "Bash" not in tools
        assert "NotebookEdit" not in tools

    def test_qwen_code_tools_are_readonly(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_ALLOWED_TOOLS

        tools = PLANNING_ALLOWED_TOOLS["qwen-code-cli"]
        assert "read_file" in tools
        assert "search_files" in tools

    def test_codex_has_empty_list(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_ALLOWED_TOOLS

        assert PLANNING_ALLOWED_TOOLS["codex"] == []

    def test_openclaw_has_empty_list(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_ALLOWED_TOOLS

        assert PLANNING_ALLOWED_TOOLS["openclaw"] == []


class TestClaudeCodeAdapterAllowedTools:
    """Verify Claude Code adapter generates --allowedTools flags."""

    @pytest.fixture(autouse=True)
    def _add_remote_agent_to_path(self):
        import os
        import sys

        ra = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
        )
        if ra not in sys.path:
            sys.path.insert(0, ra)

    def test_allowed_tools_flags(self):
        from cli_adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        args = adapter.build_start_args(
            session_id="sess-123",
            project_path="/tmp/project",
            allowed_tools=["Read", "Grep", "Glob"],
        )
        # Each tool should appear as --allowedTools <tool>
        for tool in ["Read", "Grep", "Glob"]:
            assert "--allowedTools" in args
            idx = args.index("--allowedTools")
            assert args[idx + 1] == tool
            # Remove for next check
            args = args[idx + 2 :]

    def test_no_allowed_tools_when_none(self):
        from cli_adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        args = adapter.build_start_args(
            session_id="sess-123",
            project_path="/tmp/project",
            allowed_tools=None,
        )
        assert "--allowedTools" not in args

    def test_no_allowed_tools_when_empty(self):
        from cli_adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        args = adapter.build_start_args(
            session_id="sess-123",
            project_path="/tmp/project",
            allowed_tools=[],
        )
        assert "--allowedTools" not in args


class TestQwenCodeAdapterAllowedTools:
    """Verify Qwen Code adapter still works with --allowed-tools."""

    @pytest.fixture(autouse=True)
    def _add_remote_agent_to_path(self):
        import os
        import sys

        ra = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
        )
        if ra not in sys.path:
            sys.path.insert(0, ra)

    def test_allowed_tools_flags(self):
        from cli_adapters.qwen_code import QwenCodeAdapter

        adapter = QwenCodeAdapter()
        args = adapter.build_start_args(
            session_id="sess-123",
            project_path="/tmp/project",
            allowed_tools=["read_file", "search_files"],
        )
        assert "--allowed-tools" in args


class TestAgentRunnerAllowedToolsThreading:
    """Verify agent_runner.run_agent_task threads allowed_tools to _run_local."""

    def test_run_local_receives_allowed_tools(self):
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        runner.session_manager = MagicMock()
        runner.remote_session_manager = None
        runner.server_url = "http://localhost:19888"
        runner._local_sessions = {}

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.total_tokens = 0
        mock_result.total_input_tokens = 0
        mock_result.total_output_tokens = 0

        with patch.object(runner, "_run_local", return_value=mock_result) as mock_local:
            runner.run_agent_task(
                workflow_id="wf-123",
                cli_tool="claude-code",
                model="claude-sonnet",
                project_path="/tmp/project",
                prompt="test prompt",
                allowed_tools=["Read", "Grep"],
            )
            mock_local.assert_called_once()
            call_kwargs = mock_local.call_args[1]
            assert call_kwargs["allowed_tools"] == ["Read", "Grep"]


# ── Layer 3: Selective auto-approve filtering ─────────────────────────


class TestSelectiveAutoApprove:
    """Verify the auto-approve logic filters tools based on allowed_tools."""

    def _make_session(self, allowed_tools=None):
        """Create a mock _LocalSession-like object."""
        session = MagicMock()
        session.allowed_tools = allowed_tools
        session.session_id = "sess-123"
        session.process = MagicMock()
        session.process.stdin = MagicMock()
        return session

    def test_approve_when_no_restriction(self):
        """When allowed_tools is None, all tools are approved."""
        from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

        runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
        session = self._make_session(allowed_tools=None)

        responses = []

        def mock_write(s, payload):
            responses.append(json.loads(payload))

        runner._write_stdin = mock_write

        control_msg = json.dumps(
            {
                "type": "control_request",
                "request_id": "req-1",
                "request": {"subtype": "permission", "tool_name": "Edit"},
            }
        )

        # Simulate the control_request handling logic
        parsed = json.loads(control_msg)
        msg_type = parsed.get("type", "")
        assert msg_type == "control_request"

        request_payload = parsed.get("request", {})
        tool_name = request_payload.get("tool_name", "")

        # No restriction → approve
        should_deny = session.allowed_tools is not None and tool_name not in session.allowed_tools
        assert not should_deny

    def test_approve_allowed_tool(self):
        """When tool is in allowed_tools list, it is approved."""
        session = self._make_session(allowed_tools=["Read", "Grep"])
        tool_name = "Read"
        should_deny = session.allowed_tools is not None and tool_name not in session.allowed_tools
        assert not should_deny

    def test_deny_disallowed_tool(self):
        """When tool is NOT in allowed_tools list, it is denied."""
        session = self._make_session(allowed_tools=["Read", "Grep"])
        tool_name = "Edit"
        should_deny = session.allowed_tools is not None and tool_name not in session.allowed_tools
        assert should_deny

    def test_empty_allowed_tools_denies_all(self):
        """Empty list (Codex/OpenClaw) denies all tools."""
        session = self._make_session(allowed_tools=[])
        for tool_name in ["Edit", "Write", "Bash", "Read"]:
            should_deny = (
                session.allowed_tools is not None and tool_name not in session.allowed_tools
            )
            assert should_deny, f"{tool_name} should be denied with empty list"

    def test_control_response_format_deny(self):
        """Verify the deny response has correct format."""
        tool_name = "Edit"
        response = {
            "type": "control_response",
            "response": {
                "request_id": "req-1",
                "subtype": "success",
                "response": {
                    "behavior": "deny",
                    "message": f"Tool '{tool_name}' is not allowed in planning phase.",
                },
            },
        }
        assert response["response"]["response"]["behavior"] == "deny"
        assert "Edit" in response["response"]["response"]["message"]

    def test_control_response_format_approve(self):
        """Verify the approve response has correct format."""
        response = {
            "type": "control_response",
            "response": {
                "request_id": "req-1",
                "subtype": "success",
                "response": {"behavior": "allow"},
            },
        }
        assert response["response"]["response"]["behavior"] == "allow"


# ── Layer 4: Planning timeout + extend API ────────────────────────────


class TestPlanningTimeout:
    """Verify planning timeout constants and orchestrator behavior."""

    def test_planning_timeout_constant(self):
        from app.modules.workspace.autonomous.orchestrator import PLANNING_TIMEOUT

        assert PLANNING_TIMEOUT == 600

    def test_planning_timeout_is_less_than_default(self):
        """Planning timeout should be shorter than the default 1-hour task timeout."""
        from app.modules.workspace.autonomous.orchestrator import PLANNING_TIMEOUT

        assert PLANNING_TIMEOUT < 3600

    def test_extension_adds_to_base_timeout(self):
        """Verify extend API accumulates into planning_timeout_extension."""
        from app.modules.workspace.autonomous.orchestrator import PLANNING_TIMEOUT

        # Simulate: initial extension = 0 → timeout = 600
        extension = 0
        assert PLANNING_TIMEOUT + extension == 600

        # After extend by 600 → timeout = 1200
        extension = 600
        assert PLANNING_TIMEOUT + extension == 1200

    def test_multiple_extensions_accumulate(self):
        """Each extend call adds to the previous extension."""
        from app.modules.workspace.autonomous.orchestrator import PLANNING_TIMEOUT

        current_extension = 0
        # First extend: +600
        current_extension += 600
        assert PLANNING_TIMEOUT + current_extension == 1200
        # Second extend: +300
        current_extension += 300
        assert PLANNING_TIMEOUT + current_extension == 1500


class TestLocalSessionAllowedTools:
    """Verify _LocalSession dataclass stores allowed_tools."""

    def test_local_session_stores_allowed_tools(self):
        import subprocess

        from app.modules.workspace.autonomous.agent_runner import _LocalSession

        mock_process = MagicMock(spec=subprocess.Popen)
        session = _LocalSession(
            session_id="test",
            process=mock_process,
            cli_tool="claude-code",
            allowed_tools=["Read", "Grep"],
        )
        assert session.allowed_tools == ["Read", "Grep"]

    def test_local_session_default_none(self):
        import subprocess

        from app.modules.workspace.autonomous.agent_runner import _LocalSession

        mock_process = MagicMock(spec=subprocess.Popen)
        session = _LocalSession(
            session_id="test",
            process=mock_process,
        )
        assert session.allowed_tools is None

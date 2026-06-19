"""Tests for multi-protocol CLI dispatch in the autonomous agent runner.

The autonomous runner must route CLI tools by their stdin protocol:
  - ZCode (zcode/zcode-code): ZCode Protocol app-server → _run_zcode_appserver
  - Claude SDK stream-json (claude-code, qwen-code-cli): _LocalSession path
  - No stdin protocol (codex, openclaw): single-shot mode → _run_single_shot

Previously the runner sent Claude SDK messages to ALL tools, causing zcode/
codex/openclaw to hang until timeout because they don't understand the
stream-json protocol.
"""

from unittest.mock import MagicMock, patch

import pytest

# ── Adapter protocol declarations ─────────────────────────────────────────


def test_codex_does_not_claim_stdin_input():
    """Codex must return False — it uses single-shot exec, not stream-json."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.codex_cli import CodexCLIAdapter

    assert CodexCLIAdapter().supports_stdin_input() is False


def test_openclaw_does_not_claim_stdin_input():
    """OpenClaw must return False — it has no stdin protocol at all."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.openclaw import OpenClawAdapter

    assert OpenClawAdapter().supports_stdin_input() is False


def test_zcode_provides_full_command():
    """ZCode provides a self-contained node command (not a PATH executable)."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.zcode import ZCodeAdapter

    assert ZCodeAdapter().provides_full_command() is True


def test_claude_code_still_supports_stdin():
    """claude-code is the native stream-json target — must remain True."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.claude_code import ClaudeCodeAdapter

    assert ClaudeCodeAdapter().supports_stdin_input() is True


def test_qwen_code_still_supports_stdin():
    """qwen-code-cli uses the same stream-json family — must remain True."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.qwen_code import QwenCodeAdapter

    assert QwenCodeAdapter().supports_stdin_input() is True


# ── _APPSERVER_TOOLS constant ─────────────────────────────────────────────


def test_appserver_tools_includes_zcode():
    from app.modules.workspace.autonomous.agent_runner import _APPSERVER_TOOLS

    assert "zcode" in _APPSERVER_TOOLS
    assert "zcode-code" in _APPSERVER_TOOLS


# ── Dispatch routing ──────────────────────────────────────────────────────


@pytest.fixture
def runner():
    """Create an AutonomousAgentRunner with mocked dependencies."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    return AutonomousAgentRunner(
        session_manager=MagicMock(),
        on_pid_registered=MagicMock(),
        on_pid_cleared=MagicMock(),
    )


def test_zcode_routes_to_appserver_path(runner):
    """zcode must go through _run_zcode_appserver, not _run_local's _LocalSession."""
    with patch.object(
        runner, "_run_zcode_appserver", return_value=MagicMock(success=True)
    ) as mock_zc:
        with patch.object(runner, "_run_single_shot") as mock_ss:
            runner._run_local(
                session_id="test-zcode",
                cli_tool="zcode",
                model="glm-5.2",
                project_path="/tmp/test",
                prompt="hello",
                permission_mode="auto-edit",
                timeout=60,
                workflow_id="wf-test",
                user_id=1,
                workspace_type="local",
            )
            mock_zc.assert_called_once()
            mock_ss.assert_not_called()


def test_zcode_code_also_routes_to_appserver(runner):
    """zcode-code alias must also use the app-server path."""
    with patch.object(
        runner, "_run_zcode_appserver", return_value=MagicMock(success=True)
    ) as mock_zc:
        runner._run_local(
            session_id="test-zcode2",
            cli_tool="zcode-code",
            model="glm-5.2",
            project_path="/tmp/test",
            prompt="hello",
            permission_mode="auto-edit",
            timeout=60,
            workflow_id="wf-test",
            user_id=1,
            workspace_type="local",
        )
        mock_zc.assert_called_once()


def test_codex_routes_to_single_shot(runner):
    """codex must go through _run_single_shot since supports_stdin_input is False."""
    with patch.object(runner, "_run_single_shot", return_value=MagicMock(success=True)) as mock_ss:
        with patch.object(runner, "_run_zcode_appserver") as mock_zc:
            runner._run_local(
                session_id="test-codex",
                cli_tool="codex",
                model="o4-mini",
                project_path="/tmp/test",
                prompt="hello",
                permission_mode="auto-edit",
                timeout=60,
                workflow_id="wf-test",
                user_id=1,
                workspace_type="local",
            )
            mock_ss.assert_called_once()
            mock_zc.assert_not_called()


def test_openclaw_routes_to_single_shot(runner):
    """openclaw must go through _run_single_shot."""
    with patch.object(runner, "_run_single_shot", return_value=MagicMock(success=True)) as mock_ss:
        runner._run_local(
            session_id="test-openclaw",
            cli_tool="openclaw",
            model="",
            project_path="/tmp/test",
            prompt="hello",
            permission_mode="auto-edit",
            timeout=60,
            workflow_id="wf-test",
            user_id=1,
            workspace_type="local",
        )
        mock_ss.assert_called_once()


def test_claude_code_still_uses_local_session(runner):
    """claude-code must NOT use zcode or single-shot paths."""
    with patch.object(runner, "_run_zcode_appserver") as mock_zc:
        with patch.object(runner, "_run_single_shot") as mock_ss:
            # Will fail trying to spawn claude, but we just need to verify
            # it doesn't route to zcode/single-shot
            try:
                runner._run_local(
                    session_id="test-claude",
                    cli_tool="claude-code",
                    model="sonnet",
                    project_path="/tmp/test",
                    prompt="hello",
                    permission_mode="auto-edit",
                    timeout=5,
                    workflow_id="wf-test",
                    user_id=1,
                    workspace_type="local",
                )
            except Exception:
                pass  # Expected — claude not installed or fails to start
            mock_zc.assert_not_called()
            mock_ss.assert_not_called()


# ── _ZcodeResultCollector ─────────────────────────────────────────────────


def test_collector_accumulates_assistant_text():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output(
        "sid",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "}]}}',
        "stdout",
        False,
    )
    c.on_output(
        "sid",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"world"}]}}',
        "stdout",
        False,
    )
    c.on_output("sid", "", "stdout", True)
    assert c.assistant_text == "Hello world"
    assert c.request_count == 1


def test_collector_accumulates_tool_calls():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output("sid", '{"type":"tool_use","name":"Read","input":{"file":"a.py"}}', "stdout", False)
    assert len(c.tool_calls) == 1
    assert c.tool_calls[0]["name"] == "Read"


def test_collector_captures_usage():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_usage("sid", {"totalTokens": 500, "inputTokens": 300, "outputTokens": 200})
    assert c.total_tokens == 500
    assert c.input_tokens == 300
    assert c.output_tokens == 200


def test_collector_captures_error():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output("sid", '{"type":"error","data":{"message":"model unavailable"}}', "stderr", False)
    assert c.error == "model unavailable"


def test_collector_ignores_non_json():
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output("sid", "not json at all", "stdout", False)
    assert c.assistant_text == ""
    assert len(c.event_log) == 1  # still logged

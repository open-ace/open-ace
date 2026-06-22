"""Tests for multi-protocol CLI dispatch in the autonomous agent runner.

The autonomous runner must route CLI tools by their stdin protocol:
  - ZCode (zcode/zcode-code): ZCode Protocol app-server → _run_zcode_appserver
  - Claude SDK stream-json (claude-code, qwen-code-cli): _LocalSession path
  - No stdin protocol (codex, openclaw): single-shot mode → _run_single_shot

Previously the runner sent Claude SDK messages to ALL tools, causing zcode/
codex/openclaw to hang until timeout because they don't understand the
stream-json protocol.
"""

import os
import sys
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
    """on_output must build event_log dicts matching _LocalSession._read_stdout
    shape: {"type":"assistant","text":...,"message_id":...,"model":...}.
    _persist_local_session_messages reads event.get("type")/event.get("text")."""
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
    # event_log entries must be dicts (not raw strings) for persistence
    assert len(c.event_log) == 2
    assert c.event_log[0]["type"] == "assistant"
    assert c.event_log[0]["text"] == "Hello "


def test_collector_accumulates_tool_calls():
    """Tool events arrive as tool.<name> from ZCode, normalized to
    {"tool":{"name","input","id"}} in tool_calls and {"type":"tool_use",...}
    in event_log — matching what _persist_local_session_messages expects."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output(
        "sid",
        '{"type":"tool.Read","data":{"input":{"file":"a.py"},"id":"tu_123"}}',
        "stdout",
        False,
    )
    assert len(c.tool_calls) == 1
    # Fallback path reads tool_call.get("tool", {}).get("name"/"input")
    assert c.tool_calls[0]["tool"]["name"] == "Read"
    assert c.tool_calls[0]["tool"]["input"] == {"file": "a.py"}
    # event_log path reads event.get("tool_name"/"tool_input"/"tool_use_id")
    assert c.event_log[0]["type"] == "tool_use"
    assert c.event_log[0]["tool_name"] == "Read"
    assert c.event_log[0]["tool_input"] == {"file": "a.py"}
    assert c.event_log[0]["tool_use_id"] == "tu_123"


def test_collector_captures_usage():
    """on_usage must read snake_case keys (input/output/model_requests),
    matching what ZCodeAppServerSession actually emits (zcode_app_server.py:335-417)."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_usage("sid", {"input": 300, "output": 200, "model_requests": 3})
    assert c.total_tokens == 500
    assert c.input_tokens == 300
    assert c.output_tokens == 200
    assert c.request_count == 3


def test_collector_no_double_count_request():
    """Real ZCode callback order: on_usage(model_requests=N) THEN
    on_output(done=True). The done=True must NOT increment again — otherwise
    request_count becomes N+1. See zcode_app_server.py:249-260."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    # Simulate the real callback sequence from _run_turn → _report_usage → output_callback(done=True)
    c.on_usage("sid", {"input": 300, "output": 200, "model_requests": 3})
    assert c.request_count == 3
    c.on_output("sid", "", "stdout", True)
    assert c.request_count == 3  # NOT 4


def test_collector_done_fallback_without_usage():
    """When no usage_callback fires (e.g. error before turn completes),
    done=True should count as 1 request as a fallback."""
    from app.modules.workspace.autonomous.agent_runner import _ZcodeResultCollector

    c = _ZcodeResultCollector()
    c.on_output("sid", "", "stdout", True)
    assert c.request_count == 1


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
    assert len(c.event_log) == 0  # non-JSON is silently dropped


def test_extract_final_response_text_uses_last_visible_turn():
    from app.modules.workspace.autonomous.agent_runner import _extract_final_response_text

    event_log = [
        {"type": "assistant", "text": "Let me inspect. "},
        {"type": "tool_use", "tool_name": "Read", "tool_input": {"file_path": "a.py"}},
        {"type": "assistant", "text": "## Final Review\n"},
        {"type": "assistant", "text": "Looks good."},
    ]
    assert _extract_final_response_text(event_log) == "## Final Review\nLooks good."


def test_extract_visible_response_text_preserves_all_visible_turns():
    from app.modules.workspace.autonomous.agent_runner import _extract_visible_response_text

    event_log = [
        {"type": "assistant", "text": "Applied fix\nCI_STATUS: pre-existing"},
        {"type": "tool_use", "tool_name": "Bash", "tool_input": {"command": "git push"}},
        {"type": "assistant", "text": "Done."},
    ]
    assert _extract_visible_response_text(event_log) == (
        "Applied fix\nCI_STATUS: pre-existing\n\nDone."
    )


def test_build_agent_task_result_separates_final_text_from_visible_text():
    from app.modules.workspace.autonomous.agent_runner import _build_agent_task_result

    result = _build_agent_task_result(
        session_id="sess-1",
        tracking_session_id="track-1",
        event_log=[
            {"type": "assistant", "text": "Applied fix\nCI_STATUS: pre-existing"},
            {"type": "tool_use", "tool_name": "Bash", "tool_input": {"command": "git push"}},
            {"type": "assistant", "text": "## Final Summary\nDone."},
        ],
        success=True,
    )

    assert result.response_text == "## Final Summary\nDone."
    assert result.visible_response_text == (
        "Applied fix\nCI_STATUS: pre-existing\n\n## Final Summary\nDone."
    )
    assert result.structured_tags["ci_status"] == "pre-existing"


def test_build_agent_task_result_uses_last_structured_status_tag():
    from app.modules.workspace.autonomous.agent_runner import _build_agent_task_result

    result = _build_agent_task_result(
        session_id="sess-2",
        tracking_session_id="track-2",
        event_log=[
            {"type": "assistant", "text": "Attempt 1\nTEST_STATUS: skipped"},
            {"type": "tool_use", "tool_name": "Bash", "tool_input": {"command": "pytest"}},
            {"type": "assistant", "text": "Retried\nTEST_STATUS: passed"},
            {
                "type": "tool_use",
                "tool_name": "Bash",
                "tool_input": {"command": "rerun ci checks"},
            },
            {"type": "assistant", "text": "CI_STATUS: pre-existing"},
            {
                "type": "tool_use",
                "tool_name": "Bash",
                "tool_input": {"command": "fix ci"},
            },
            {"type": "assistant", "text": "CI_STATUS: fixed"},
        ],
        success=True,
    )

    assert result.structured_tags["test_status"] == "passed"
    assert result.structured_tags["ci_status"] == "fixed"


# ── ZCode session failure cleanup ─────────────────────────────────────────


def test_zcode_session_start_failure_cleans_up(runner):
    """When ZCode session/create fails, _local_sessions and PID must be cleared."""
    with patch("app.modules.workspace.autonomous.agent_runner.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock(returncode=None, pid=12345)
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_popen.return_value = mock_proc

        # Patch ZCodeAppServerSession where _run_zcode_appserver imports it
        _ra = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
        )
        if _ra not in sys.path:
            sys.path.insert(0, _ra)
        import zcode_app_server

        mock_zc_instance = MagicMock()
        mock_zc_instance.start.return_value = False  # session/create fails
        mock_zc_instance._cli_session_id = None
        mock_zc_instance.stop = MagicMock()

        with patch.object(zcode_app_server, "ZCodeAppServerSession", return_value=mock_zc_instance):
            result = runner._run_zcode_appserver(
                session_id="test-fail",
                cli_tool="zcode",
                model="glm-5.2",
                project_path="/tmp/test",
                prompt="hello",
                permission_mode="edit",
                timeout=10,
                workflow_id="wf-1",
                user_id=1,
                workspace_type="local",
            )
        assert result.success is False
        assert "session/create failed" in result.error
        # Tracker must be removed even on failure (issue #2 fix)
        assert "test-fail" not in runner._local_sessions
        runner._on_pid_cleared.assert_called_once_with("test-fail")


# ── OpenClaw single-shot args ─────────────────────────────────────────────


def test_openclaw_single_shot_includes_agent_json_flags():
    """build_single_shot_args must include --agent --json, not just [exe, prompt]."""
    import os
    import sys

    _ra = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
    )
    if _ra not in sys.path:
        sys.path.insert(0, _ra)
    from cli_adapters.openclaw import OpenClawAdapter

    args = OpenClawAdapter().build_single_shot_args("do something", "/tmp/proj")
    assert "--agent" in args
    assert "--json" in args
    assert "do something" in args

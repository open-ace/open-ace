"""Tests for single-shot agent runner stdout parsing (issue #723).

Covers two bugs:
  * Bug #2: ``_run_single_shot`` stuffed raw JSON strings into ``event_log``,
    so the dict-based extractors (``_extract_visible_response_text`` etc.)
    produced empty ``visible_response_text`` — which made the orchestrator's
    test-skip detector false-positive on ``not has_test_result``.
  * Bug #3: ``subprocess.TimeoutExpired`` discarded partial stdout entirely,
    returning an empty result even when the agent had emitted real text.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

# ── _parse_single_shot_line ───────────────────────────────────────────


class TestParseSingleShotLine:
    """The per-line normalizer must emit dict events the extractors expect."""

    def setup_method(self):
        self.runner = AutonomousAgentRunner()

    def test_claude_assistant_event_becomes_dict(self):
        """Claude stream-json assistant line -> {"type":"assistant","text":...}."""
        parsed = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello from agent"}],
                "id": "msg_1",
                "model": "claude-sonnet-4-6",
            },
        }
        event = self.runner._parse_single_shot_line(parsed, "claude-code")
        assert event == {
            "type": "assistant",
            "text": "Hello from agent",
            "message_id": "msg_1",
            "model": "claude-sonnet-4-6",
        }

    def test_claude_tool_use_event_becomes_dict(self):
        """Claude tool_use line -> {"type":"tool_use",...}."""
        parsed = {
            "type": "tool_use",
            "tool": {"name": "read_file", "input": {"path": "/tmp/x.py"}, "id": "tu_1"},
        }
        event = self.runner._parse_single_shot_line(parsed, "claude-code")
        assert event == {
            "type": "tool_use",
            "tool_name": "read_file",
            "tool_input": {"path": "/tmp/x.py"},
            "tool_use_id": "tu_1",
        }

    def test_codex_message_event_becomes_assistant_dict(self):
        """Codex/OpenAI message line -> assistant event."""
        parsed = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "All tests passed"}],
        }
        event = self.runner._parse_single_shot_line(parsed, "codex")
        assert event["type"] == "assistant"
        assert event["text"] == "All tests passed"

    def test_codex_function_call_becomes_tool_use(self):
        """Codex function_call line -> tool_use event."""
        parsed = {"type": "function_call", "name": "shell", "arguments": "{}", "call_id": "c1"}
        event = self.runner._parse_single_shot_line(parsed, "codex")
        assert event["type"] == "tool_use"
        assert event["tool_name"] == "shell"

    def test_unrecognized_event_returns_none(self):
        """Usage/result lines that aren't assistant/tool events return None."""
        parsed = {"type": "result", "data": {"usage": {"input_tokens": 10, "output_tokens": 5}}}
        assert self.runner._parse_single_shot_line(parsed, "codex") is None

    def test_assistant_with_empty_content_returns_none(self):
        """An assistant event whose content extracts to no text returns None."""
        parsed = {"type": "assistant", "message": {"content": []}}
        assert self.runner._parse_single_shot_line(parsed, "claude-code") is None


# ── _parse_single_shot_stdout ─────────────────────────────────────────


class TestParseSingleShotStdout:
    """Full stdout parse: event_log must contain dict entries (not strings)."""

    def setup_method(self):
        self.runner = AutonomousAgentRunner()

    def test_event_log_entries_are_dicts(self):
        """Regression for Bug #2: event_log entries are dicts, not raw strings."""
        stdout = json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
        )
        event_log, text, *_ = self.runner._parse_single_shot_stdout(stdout, "claude-code")
        assert len(event_log) == 1
        assert isinstance(event_log[0], dict)
        assert event_log[0]["type"] == "assistant"
        assert "hi" in text

    def test_visible_text_and_tool_calls_populated(self):
        """Both assistant text and tool calls are extracted."""
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Working"}]},
                    }
                ),
                json.dumps(
                    {"type": "tool_use", "tool": {"name": "edit", "input": {"file": "a.py"}}}
                ),
            ]
        )
        event_log, text, in_tok, out_tok, tool_calls = self.runner._parse_single_shot_stdout(
            stdout, "claude-code"
        )
        assert "Working" in text
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool"]["name"] == "edit"
        assert len(event_log) == 2
        assert all(isinstance(e, dict) for e in event_log)

    def test_non_json_lines_become_text(self):
        """Plain-text lines (progress noise) accumulate into response_text."""
        stdout = "starting up...\nstill working...\n"
        event_log, text, *_ = self.runner._parse_single_shot_stdout(stdout, "codex")
        assert event_log == []
        assert "starting up" in text and "still working" in text


# ── _run_single_shot (subprocess mocking) ─────────────────────────────


def _patch_cli_adapters(runner, exe="codex"):
    """Patch cli_adapters.get_adapter so _run_single_shot finds an executable."""
    mock_adapter = MagicMock()
    mock_adapter.get_executable_name.return_value = exe
    mock_adapter.build_single_shot_args.return_value = [exe, "exec", "--json", "prompt"]
    mock_cli_adapters = MagicMock()
    mock_cli_adapters.get_adapter.return_value = mock_adapter
    return (
        patch.dict("sys.modules", {"cli_adapters": mock_cli_adapters}),
        patch("shutil.which", return_value=f"/usr/bin/{exe}"),
    )


class TestRunSingleShotResult:
    """End-to-end single-shot result construction."""

    def test_success_populates_visible_response_text(self):
        """Bug #2 fix: a completed run carries non-empty visible_response_text."""
        runner = AutonomousAgentRunner()
        stdout = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "All tests passed"}]},
            }
        )
        proc = MagicMock(returncode=0, stdout=stdout, stderr="")

        mod_patch, which_patch = _patch_cli_adapters(runner)
        with mod_patch, which_patch, patch("subprocess.run", return_value=proc):
            result = runner._run_single_shot(
                session_id="s1",
                cli_tool="codex",
                model="m",
                project_path="/tmp/p",
                prompt="do it",
                timeout=5,
                workflow_id="wf1",
            )

        assert result.success is True
        assert result.error is None
        assert "All tests passed" in result.response_text
        assert result.visible_response_text  # non-empty — the Bug #2 regression guard
        assert result.event_log and isinstance(result.event_log[0], dict)

    def test_timeout_salvages_partial_output(self):
        """Bug #3 fix: TimeoutExpired keeps partial stdout in the result."""
        runner = AutonomousAgentRunner()
        partial = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "partial work"}]},
            }
        )

        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["codex"], timeout=5, output=partial)

        mod_patch, which_patch = _patch_cli_adapters(runner)
        with mod_patch, which_patch, patch("subprocess.run", side_effect=_raise):
            result = runner._run_single_shot(
                session_id="s1",
                cli_tool="codex",
                model="m",
                project_path="/tmp/p",
                prompt="do it",
                timeout=5,
                workflow_id="wf1",
            )

        assert result.success is False
        assert "timed out after 5s" in result.error
        # The salvaged partial output must survive.
        assert "partial work" in result.response_text
        assert result.event_log and isinstance(result.event_log[0], dict)

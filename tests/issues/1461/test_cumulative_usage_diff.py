"""Tests for per-turn token delta via cumulative-usage differencing.

Background: qwen-code-webui's persistent sessions report CROSS-TURN CUMULATIVE
usage in the ``type: "result"`` message (``usage.input_tokens`` derives from
``computeUsageFromMetrics()`` → ``stats.totalPromptTokens``). The remote-agent
executor and the autonomous agent_runner previously reported this raw value as
a per-turn delta, re-adding the running total every turn and inflating
``session.total_*_tokens`` / quota.

The fix differences successive cumulative snapshots (``cur - last``) for tools
in ``CUMULATIVE_RESULT_TOOLS`` (initially qwen-code-cli); other tools keep
reporting their per-request result usage as-is.

These tests cover:
  * executor ``_read_stream`` result branch (real code path, fake stream), and
  * agent_runner ``_accumulate_turn_usage`` on a real ``_LocalSession``.
"""

import json
import os
import sys

# Make remote-agent importable (cli_adapters, executor, agent_runner helpers).
_REMOTE_AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
sys.path.insert(0, _REMOTE_AGENT)

from cli_adapters.usage_parser import is_cumulative_result_tool  # noqa: E402
from executor import SessionProcess  # noqa: E402

from app.modules.workspace.autonomous.agent_runner import (  # noqa: E402
    AutonomousAgentRunner,
    _ensure_usage_parser,
    _LocalSession,
)

# Populate the cached usage helpers (is_cumulative_result_tool / diff).
_ensure_usage_parser()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStream:
    """Mimic subprocess stdout: ``readline()`` returns queued lines, then ""."""

    def __init__(self, lines):
        self._lines = [ln if ln.endswith("\n") else ln + "\n" for ln in lines]

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _StubProcess:
    """Minimal stand-in for subprocess.Popen — only returncode is read."""

    returncode = None  # is_running → True


def _result_line(cli_tool, input_tokens, output_tokens, cached=0):
    """Build a stream-json ``result`` line for the given tool's format."""
    if cli_tool == "qwen-code-cli":
        meta = {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
        }
        if cached:
            meta["cachedContentTokenCount"] = cached
        payload = {"type": "result", "usageMetadata": meta}
    else:
        payload = {
            "type": "result",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    return json.dumps(payload)


def _make_session_process(cli_tool, reports):
    """Build a SessionProcess whose usage_callback records into *reports*."""

    def usage_callback(_session_id, tokens):
        reports.append(dict(tokens))

    return SessionProcess(
        session_id="sess-test",
        process=_StubProcess(),
        project_path=".",
        cli_tool=cli_tool,
        output_callback=lambda *a: None,
        usage_callback=usage_callback,
    )


# ---------------------------------------------------------------------------
# executor._read_stream — cumulative differencing (qwen)
# ---------------------------------------------------------------------------


class TestExecutorQwenCumulativeDiff:
    def test_multi_turn_reports_per_turn_delta(self):
        reports = []
        sp = _make_session_process("qwen-code-cli", reports)
        stream = _FakeStream(
            [
                _result_line("qwen-code-cli", 1000, 100),
                _result_line("qwen-code-cli", 2500, 300),
                _result_line("qwen-code-cli", 4500, 600),
            ]
        )
        sp._read_stream(stream, "stdout")
        # Cumulative [1000,2500,4500] → deltas [1000,1500,2000]
        assert reports == [
            {"input": 1000, "output": 100},
            {"input": 1500, "output": 200},
            {"input": 2000, "output": 300},
        ]

    def test_first_turn_seeds_baseline(self):
        reports = []
        sp = _make_session_process("qwen-code-cli", reports)
        sp._read_stream(_FakeStream([_result_line("qwen-code-cli", 700, 70)]), "stdout")
        assert reports == [{"input": 700, "output": 70}]
        assert sp._last_cum_input == 700
        assert sp._last_cum_output == 70

    def test_non_monotonic_clamps_to_zero_and_skips_report(self):
        reports = []
        sp = _make_session_process("qwen-code-cli", reports)
        sp._read_stream(
            _FakeStream(
                [
                    _result_line("qwen-code-cli", 5000, 500),
                    _result_line("qwen-code-cli", 100, 10),  # reset < last
                ]
            ),
            "stdout",
        )
        # First turn reports; clamped second turn (0/0) is NOT reported.
        assert reports == [{"input": 5000, "output": 500}]
        # Baseline still advances so the next turn re-seeds from cur.
        assert sp._last_cum_input == 100

    def test_zero_usage_not_reported(self):
        reports = []
        sp = _make_session_process("qwen-code-cli", reports)
        sp._read_stream(_FakeStream([_result_line("qwen-code-cli", 0, 0)]), "stdout")
        assert reports == []


# ---------------------------------------------------------------------------
# executor._read_stream — non-cumulative tools (claude-code) unchanged
# ---------------------------------------------------------------------------


class TestExecutorClaudeAsIs:
    def test_reports_raw_usage_no_differencing(self):
        reports = []
        sp = _make_session_process("claude-code", reports)
        sp._read_stream(
            _FakeStream(
                [
                    _result_line("claude-code", 100, 10),
                    _result_line("claude-code", 200, 20),
                ]
            ),
            "stdout",
        )
        # Non-cumulative tool: each result reported as-is (no subtraction).
        assert reports == [
            {"input": 100, "output": 10},
            {"input": 200, "output": 20},
        ]
        # Baseline must NOT be tracked for non-cumulative tools.
        assert sp._last_cum_input is None
        assert sp._last_cum_output is None


# ---------------------------------------------------------------------------
# executor._read_stream — cache deduction flows through differencing
# ---------------------------------------------------------------------------


class TestExecutorQwenCacheDeduction:
    def test_cached_tokens_deducted_before_differencing(self):
        # Turn 1: prompt=5000, cached=3000 → input=2000
        # Turn 2: prompt=8000, cached=3000 → input=5000 ; delta=3000
        reports = []
        sp = _make_session_process("qwen-code-cli", reports)
        sp._read_stream(
            _FakeStream(
                [
                    _result_line("qwen-code-cli", 5000, 100, cached=3000),
                    _result_line("qwen-code-cli", 8000, 200, cached=3000),
                ]
            ),
            "stdout",
        )
        assert reports == [
            {"input": 2000, "output": 100},
            {"input": 3000, "output": 100},
        ]


# ---------------------------------------------------------------------------
# agent_runner._accumulate_turn_usage — real _LocalSession
# ---------------------------------------------------------------------------


def _local_session(cli_tool):
    sess = _LocalSession(session_id="sess-local", process=None)
    sess.cli_tool = cli_tool
    return sess


def _runner():
    """_accumulate_turn_usage uses no instance state; a bare object suffices."""
    return AutonomousAgentRunner.__new__(AutonomousAgentRunner)


class TestAgentRunnerAccumulateTurnUsage:
    def test_qwen_cumulative_accumulates_deltas(self):
        sess = _local_session("qwen-code-cli")
        runner = _runner()
        for cur_in, cur_out in [(1000, 100), (2500, 300), (4500, 600)]:
            runner._accumulate_turn_usage(sess, {"input": cur_in, "output": cur_out})
        assert sess.total_input_tokens == 1000 + 1500 + 2000
        assert sess.total_output_tokens == 100 + 200 + 300

    def test_claude_accumulates_raw(self):
        sess = _local_session("claude-code")
        runner = _runner()
        runner._accumulate_turn_usage(sess, {"input": 100, "output": 10})
        runner._accumulate_turn_usage(sess, {"input": 200, "output": 20})
        assert sess.total_input_tokens == 300
        assert sess.total_output_tokens == 30
        # Non-cumulative: no baseline tracked.
        assert sess._last_cum_input is None

    def test_non_monotonic_clamps(self):
        sess = _local_session("qwen-code-cli")
        runner = _runner()
        runner._accumulate_turn_usage(sess, {"input": 5000, "output": 500})
        runner._accumulate_turn_usage(sess, {"input": 100, "output": 10})  # reset
        # Second turn clamps to 0 → totals unchanged.
        assert sess.total_input_tokens == 5000
        assert sess.total_output_tokens == 500
        # Baseline advanced to cur.
        assert sess._last_cum_input == 100

    def test_session_defaults_none_baseline(self):
        sess = _local_session("qwen-code-cli")
        assert sess._last_cum_input is None
        assert sess._last_cum_output is None


# ---------------------------------------------------------------------------
# Sanity: tool classification matches the parser module
# ---------------------------------------------------------------------------


class TestToolClassification:
    def test_qwen_classified_cumulative_everywhere(self):
        # The executor/agent_runner rely on the same single source of truth.
        assert is_cumulative_result_tool("qwen-code-cli") is True
        assert is_cumulative_result_tool("claude-code") is False

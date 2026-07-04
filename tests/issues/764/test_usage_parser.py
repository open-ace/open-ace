"""Tests for shared stream-json usage extraction (Issue #764)."""

import os
import sys

import pytest

# Add remote-agent to path so we can import cli_adapters
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent"))

from cli_adapters.usage_parser import (
    CUMULATIVE_RESULT_TOOLS,
    diff_cumulative_usage,
    extract_claude_stream_usage,
    extract_qwen_stream_usage,
    extract_stream_usage,
    is_cumulative_result_tool,
)


class TestExtractClaudeStreamUsage:
    """Test Claude Code stream-json result message parsing."""

    def test_usage_at_top_level(self):
        parsed = {
            "type": "result",
            "subtype": "success",
            "usage": {"input_tokens": 8112, "output_tokens": 5},
        }
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 8112
        assert result["output"] == 5

    def test_usage_nested_in_data(self):
        parsed = {
            "type": "result",
            "data": {"usage": {"input_tokens": 3000, "output_tokens": 100}},
        }
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 3000
        assert result["output"] == 100

    def test_usage_nested_in_data_message(self):
        parsed = {
            "type": "result",
            "data": {"message": {"usage": {"input_tokens": 500, "output_tokens": 50}}},
        }
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 500
        assert result["output"] == 50

    def test_no_usage(self):
        parsed = {"type": "result", "subtype": "success"}
        result = extract_claude_stream_usage(parsed)
        assert result is None

    def test_usage_with_legacy_keys(self):
        parsed = {"type": "result", "usage": {"input": 1000, "output": 200}}
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 1000
        assert result["output"] == 200

    def test_top_level_takes_priority_over_data(self):
        parsed = {
            "type": "result",
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "data": {"usage": {"input_tokens": 999, "output_tokens": 99}},
        }
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 100
        assert result["output"] == 10

    def test_empty_usage_dict(self):
        parsed = {"type": "result", "usage": {}}
        result = extract_claude_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 0
        assert result["output"] == 0


class TestExtractQwenStreamUsage:
    """Test Qwen Code stream-json result message parsing."""

    def test_claude_compatible_format(self):
        parsed = {"type": "result", "usage": {"input_tokens": 5000, "output_tokens": 300}}
        result = extract_qwen_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 5000
        assert result["output"] == 300

    def test_qwen_usage_metadata_format(self):
        parsed = {
            "type": "result",
            "usageMetadata": {"promptTokenCount": 4000, "candidatesTokenCount": 200},
        }
        result = extract_qwen_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 4000
        assert result["output"] == 200

    def test_qwen_with_cached_tokens(self):
        parsed = {
            "type": "result",
            "usageMetadata": {
                "promptTokenCount": 5000,
                "candidatesTokenCount": 200,
                "cachedContentTokenCount": 3000,
            },
        }
        result = extract_qwen_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 2000
        assert result["output"] == 200

    def test_qwen_nested_in_data(self):
        parsed = {
            "type": "result",
            "data": {"usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100}},
        }
        result = extract_qwen_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 1000
        assert result["output"] == 100

    def test_qwen_no_usage(self):
        parsed = {"type": "result", "subtype": "success"}
        result = extract_qwen_stream_usage(parsed)
        assert result is None

    def test_top_level_takes_priority(self):
        parsed = {
            "type": "result",
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "data": {"usage": {"input_tokens": 999, "output_tokens": 99}},
        }
        result = extract_qwen_stream_usage(parsed)
        assert result is not None
        assert result["input"] == 100
        assert result["output"] == 10


class TestExtractStreamUsageDispatch:
    """Test the dispatch function that routes by cli_tool."""

    def test_dispatches_to_claude_by_default(self):
        parsed = {"type": "result", "usage": {"input_tokens": 100, "output_tokens": 10}}
        result = extract_stream_usage("claude-code", parsed)
        assert result is not None
        assert result["input"] == 100

    def test_dispatches_to_qwen_for_qwen_tool(self):
        parsed = {
            "type": "result",
            "usageMetadata": {"promptTokenCount": 4000, "candidatesTokenCount": 200},
        }
        result = extract_stream_usage("qwen-code-cli", parsed)
        assert result is not None
        assert result["input"] == 4000

    def test_dispatches_to_claude_for_unknown_tool(self):
        parsed = {"type": "result", "usage": {"input_tokens": 50, "output_tokens": 5}}
        result = extract_stream_usage("some-unknown-tool", parsed)
        assert result is not None
        assert result["input"] == 50


class TestCumulativeResultTools:
    """Test cumulative-result-tool detection (cross-turn running totals)."""

    def test_qwen_is_cumulative(self):
        assert is_cumulative_result_tool("qwen-code-cli") is True

    def test_claude_is_not_cumulative(self):
        assert is_cumulative_result_tool("claude-code") is False

    def test_unknown_tool_is_not_cumulative(self):
        assert is_cumulative_result_tool("some-unknown-tool") is False

    def test_qwen_in_cumulative_set(self):
        # qwen-code-cli is in BOTH the qwen-format set and the cumulative set.
        assert "qwen-code-cli" in CUMULATIVE_RESULT_TOOLS


class TestDiffCumulativeUsage:
    """Test per-turn delta derivation from cumulative snapshots."""

    def test_first_turn_seeds_baseline_and_reports_full(self):
        # First turn (last is None): delta == cur, baseline seeded to cur.
        cur = {"input": 1000, "output": 100}
        delta, new_in, new_out = diff_cumulative_usage(cur, None, None)
        assert delta == {"input": 1000, "output": 100}
        assert new_in == 1000
        assert new_out == 100

    def test_subsequent_turn_reports_difference(self):
        # [1000, 2500] → deltas [1000, 1500]
        delta1, last_in, last_out = diff_cumulative_usage(
            {"input": 1000, "output": 100}, None, None
        )
        delta2, last_in, last_out = diff_cumulative_usage(
            {"input": 2500, "output": 300}, last_in, last_out
        )
        assert delta1 == {"input": 1000, "output": 100}
        assert delta2 == {"input": 1500, "output": 200}
        assert (last_in, last_out) == (2500, 300)

    def test_multi_turn_sequence_matches_increments(self):
        # Cumulative [1000, 2500, 4500] → per-turn [1000, 1500, 2000]
        seq = [(1000, 100), (2500, 300), (4500, 600)]
        last_in, last_out = None, None
        deltas = []
        for cur_in, cur_out in seq:
            delta, last_in, last_out = diff_cumulative_usage(
                {"input": cur_in, "output": cur_out}, last_in, last_out
            )
            deltas.append((delta["input"], delta["output"]))
        assert deltas == [(1000, 100), (1500, 200), (2000, 300)]

    def test_non_monotonic_clamped_to_zero(self):
        # cur < last (reset / correction) → delta clamped to 0, not negative.
        delta, new_in, new_out = diff_cumulative_usage({"input": 100, "output": 10}, 5000, 500)
        assert delta == {"input": 0, "output": 0}
        # Baseline still advances to cur so subsequent turns re-seed correctly.
        assert (new_in, new_out) == (100, 10)

    def test_partial_none_baseline_treated_as_first_turn(self):
        # If either baseline component is None, treat as first turn (safe).
        delta, new_in, new_out = diff_cumulative_usage({"input": 700, "output": 70}, 700, None)
        assert delta == {"input": 700, "output": 70}
        assert (new_in, new_out) == (700, 70)

    def test_missing_keys_default_to_zero(self):
        delta, new_in, new_out = diff_cumulative_usage({}, None, None)
        assert delta == {"input": 0, "output": 0}
        assert (new_in, new_out) == (0, 0)

    def test_string_numeric_values_coerced(self):
        # Defensive: some payloads may carry stringified ints.
        delta, new_in, new_out = diff_cumulative_usage(
            {"input": "1500", "output": "200"}, "1000", "100"
        )
        assert delta == {"input": 500, "output": 100}
        assert (new_in, new_out) == (1500, 200)

    def test_returns_independent_dict(self):
        # Mutating the returned delta must not affect future calls.
        cur = {"input": 100, "output": 10}
        delta, _, _ = diff_cumulative_usage(cur, None, None)
        delta["input"] = 999999
        delta2, _, _ = diff_cumulative_usage({"input": 110, "output": 11}, 100, 10)
        assert delta2 == {"input": 10, "output": 1}

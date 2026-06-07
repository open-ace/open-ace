"""Tests for shared stream-json usage extraction (Issue #764)."""

import os
import sys

import pytest

# Add remote-agent to path so we can import cli_adapters
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent"))

from cli_adapters.usage_parser import extract_claude_stream_usage, extract_qwen_stream_usage


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

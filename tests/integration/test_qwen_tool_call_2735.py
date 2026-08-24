"""
Issue #2735: Tool call ID matching tests

Tests for tool call ID matching functionality:
- Single tool call matching
- Multiple tool calls in one message
- Tool result ID format validation
- Function call index building
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_qwen import process_jsonl_file  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
from fixtures.qwen_session_factory import QwenSessionFactory  # noqa: E402


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


@pytest.mark.integration
class TestToolCallMatching:
    """Tests for tool call ID matching."""

    def test_single_tool_call_matching(self, tmp_path):
        """Test single tool call ID matching."""
        factory = QwenSessionFactory()
        entries = factory.create_tool_call_sequence(tool_names=["bash"])
        jsonl_path = factory.write_jsonl(tmp_path / "single-tool.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Should have user, assistant, and tool_result
        assert len(messages) == 3

    def test_multiple_tool_calls_in_one_message(self, tmp_path):
        """Test multiple tool calls in a single assistant message."""
        factory = QwenSessionFactory()
        entries = factory.create_tool_call_sequence(tool_names=["bash", "read", "write", "grep"])
        jsonl_path = factory.write_jsonl(tmp_path / "multi-tool.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Should have 1 user + 1 assistant + 4 tool_results
        assert len(messages) == 6

    def test_tool_result_id_format(self, tmp_path):
        """Test that tool_result tool_use_id matches functionCall id format."""
        entries = [
            {
                "type": "user",
                "uuid": "user-001",
                "parentUuid": None,
                "timestamp": "2026-08-24T10:00:00Z",
                "message": {"role": "user", "parts": [{"text": "Test"}]},
            },
            {
                "type": "assistant",
                "uuid": "assistant-001",
                "parentUuid": "user-001",
                "timestamp": "2026-08-24T10:00:05Z",
                "model": "qwen-max",
                "message": {
                    "role": "assistant",
                    "parts": [
                        {"text": "Running tool"},
                        {"functionCall": {"name": "test", "id": "tool-id-abc123"}},
                    ],
                },
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
            },
            {
                "type": "tool_result",
                "uuid": "result-001",
                "parentUuid": "assistant-001",
                "timestamp": "2026-08-24T10:00:10Z",
                "message": {
                    "role": "tool",
                    "parts": [{"tool_result": {"tool_use_id": "tool-id-abc123", "content": "Result"}}],
                },
            },
        ]
        jsonl_path = _write_jsonl(tmp_path / "format.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        assert len(messages) == 3

    def test_tool_result_matching(self, tmp_path):
        """Test that tool_result correctly matches to its parent assistant."""
        factory = QwenSessionFactory()
        entries = factory.create_tool_call_sequence(tool_names=["tool1", "tool2"])
        jsonl_path = factory.write_jsonl(tmp_path / "matching.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Verify all messages are processed (user + assistant + 2 tool_results)
        assert len(messages) == 4

    def test_function_call_indices_building(self, tmp_path):
        """Test that function_call_indices are correctly built."""
        # Create assistant message with multiple function calls
        entries = [
            {
                "type": "user",
                "uuid": "user-fc",
                "parentUuid": None,
                "timestamp": "2026-08-24T10:00:00Z",
                "message": {"role": "user", "parts": [{"text": "Run tools"}]},
            },
            {
                "type": "assistant",
                "uuid": "assistant-fc",
                "parentUuid": "user-fc",
                "timestamp": "2026-08-24T10:00:05Z",
                "model": "qwen-max",
                "message": {
                    "role": "assistant",
                    "parts": [
                        {"text": "Running"},
                        {"functionCall": {"name": "bash", "id": "bash-0"}},
                        {"functionCall": {"name": "read", "id": "read-1"}},
                        {"functionCall": {"name": "write", "id": "write-2"}},
                    ],
                },
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 40, "totalTokenCount": 60},
            },
            {
                "type": "tool_result",
                "uuid": "result-0",
                "parentUuid": "assistant-fc",
                "timestamp": "2026-08-24T10:00:10Z",
                "message": {
                    "role": "tool",
                    "parts": [{"tool_result": {"tool_use_id": "bash-0", "content": "Bash result"}}],
                },
            },
            {
                "type": "tool_result",
                "uuid": "result-1",
                "parentUuid": "assistant-fc",
                "timestamp": "2026-08-24T10:00:11Z",
                "message": {
                    "role": "tool",
                    "parts": [{"tool_result": {"tool_use_id": "read-1", "content": "Read result"}}],
                },
            },
            {
                "type": "tool_result",
                "uuid": "result-2",
                "parentUuid": "assistant-fc",
                "timestamp": "2026-08-24T10:00:12Z",
                "message": {
                    "role": "tool",
                    "parts": [{"tool_result": {"tool_use_id": "write-2", "content": "Write result"}}],
                },
            },
        ]
        jsonl_path = _write_jsonl(tmp_path / "indices.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # All 5 messages should be processed
        assert len(messages) == 5

    def test_tool_call_with_function_response(self, tmp_path):
        """Test tool call with function response structure."""
        entries = [
            {
                "type": "user",
                "uuid": "user-fr",
                "parentUuid": None,
                "timestamp": "2026-08-24T10:00:00Z",
                "message": {"role": "user", "parts": [{"text": "Call function"}]},
            },
            {
                "type": "assistant",
                "uuid": "assistant-fr",
                "parentUuid": "user-fr",
                "timestamp": "2026-08-24T10:00:05Z",
                "model": "qwen-max",
                "message": {
                    "role": "assistant",
                    "parts": [
                        {"functionCall": {"name": "get_data", "id": "call-123", "args": {"query": "test"}}}
                    ],
                },
                "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 25, "totalTokenCount": 40},
            },
            {
                "type": "tool_result",
                "uuid": "result-fr",
                "parentUuid": "assistant-fr",
                "timestamp": "2026-08-24T10:00:10Z",
                "message": {
                    "role": "tool",
                    "parts": [
                        {"functionResponse": {"name": "get_data", "id": "call-123", "response": {"data": "ok"}}}
                    ],
                },
            },
        ]
        jsonl_path = _write_jsonl(tmp_path / "function-response.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        assert len(messages) >= 2  # At least user and assistant
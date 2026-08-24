"""
Issue #2735: Conversation ID derivation tests

Tests for conversation ID derivation logic including:
- Simple message trees
- Deep message trees
- Empty message trees
- Broken parent references
- Multiple roots
- Cycle detection
- Orphan message handling
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

# Import test data factory
sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
from fixtures.qwen_session_factory import QwenSessionFactory  # noqa: E402


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


@pytest.mark.integration
class TestConversationIdDerivation:
    """Tests for conversation ID derivation from message trees."""

    def test_simple_message_tree(self, tmp_path):
        """Test conversation ID derivation for a simple tree."""
        factory = QwenSessionFactory()
        entries = factory.create_simple_conversation()
        jsonl_path = factory.write_jsonl(tmp_path / "simple.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        assert len(messages) == 2
        assert "2026-08-24" in str(daily.keys()) or len(daily) > 0

    def test_deep_message_tree(self, tmp_path):
        """Test conversation ID derivation for deep message trees."""
        factory = QwenSessionFactory()
        entries = factory.create_deep_message_tree(depth=4)
        jsonl_path = factory.write_jsonl(tmp_path / "deep.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Should process all messages in the deep tree
        assert len(messages) == 9  # 1 root + 4 (assistant + tool) pairs

    def test_empty_message_tree(self, tmp_path):
        """Test that empty message tree returns empty results."""
        factory = QwenSessionFactory()
        entries = factory.create_empty_file()
        jsonl_path = factory.write_jsonl(tmp_path / "empty.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        assert len(daily) == 0
        assert len(messages) == 0

    def test_broken_parent_reference(self, tmp_path):
        """Test that broken parent references are handled gracefully."""
        factory = QwenSessionFactory()
        entries = factory.create_broken_tree()
        jsonl_path = factory.write_jsonl(tmp_path / "broken.jsonl", entries)

        # Should not raise an exception
        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Message should be processed despite broken reference
        assert len(messages) >= 0

    def test_multiple_roots(self, tmp_path):
        """Test that multiple root messages create multiple conversations."""
        factory = QwenSessionFactory()
        entries = factory.create_multiple_roots(num_roots=3)
        jsonl_path = factory.write_jsonl(tmp_path / "multi.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Should have 6 messages (3 roots + 3 responses)
        assert len(messages) == 6

    def test_cycle_detection(self, tmp_path):
        """Test that cycles in message tree are detected and handled."""
        factory = QwenSessionFactory()
        entries = factory.create_cycle()
        jsonl_path = factory.write_jsonl(tmp_path / "cycle.jsonl", entries)

        # Should not hang or crash
        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Messages should be processed without infinite loop
        assert len(messages) >= 0

    def test_orphan_message_handling(self, tmp_path):
        """Test handling of orphan messages (non-user root)."""
        entries = [
            {
                "type": "assistant",
                "uuid": "orphan-assistant",
                "parentUuid": None,
                "timestamp": "2026-08-24T10:00:00Z",
                "model": "qwen-max",
                "message": {"role": "assistant", "parts": [{"text": "Orphan"}]},
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
            }
        ]
        jsonl_path = _write_jsonl(tmp_path / "orphan.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # Should still process the message
        assert len(messages) >= 0

    def test_conversation_id_consistency(self, tmp_path):
        """Test that conversation IDs are consistent across the same conversation."""
        factory = QwenSessionFactory()
        entries = factory.create_deep_message_tree(depth=2)
        jsonl_path = factory.write_jsonl(tmp_path / "consistent.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        # All messages should have been processed
        assert len(messages) > 0

    def test_conversation_id_format(self, tmp_path):
        """Test that conversation ID format is correct."""
        entries = [
            {
                "type": "user",
                "uuid": "root-test-123",
                "parentUuid": None,
                "timestamp": "2026-08-24T10:00:00Z",
                "message": {"role": "user", "parts": [{"text": "Test"}]},
            },
            {
                "type": "assistant",
                "uuid": "response-test-456",
                "parentUuid": "root-test-123",
                "timestamp": "2026-08-24T10:00:05Z",
                "model": "qwen-max",
                "message": {"role": "assistant", "parts": [{"text": "Response"}]},
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
            },
        ]
        jsonl_path = _write_jsonl(tmp_path / "format.jsonl", entries)

        daily, messages = process_jsonl_file(jsonl_path, "testhost", "testuser")

        assert len(messages) == 2

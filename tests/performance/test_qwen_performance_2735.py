"""
Issue #2735: Performance Tests

Tests for performance benchmarks:
- 100 session files processing
- Large file performance
- Deep message tree performance
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_qwen import process_jsonl_file  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "tests" / "integration"))
from fixtures.qwen_session_factory import QwenSessionFactory  # noqa: E402


# Performance tests are skipped by default
RUN_PERFORMANCE = os.environ.get("RUN_PERFORMANCE_TESTS", "")


@pytest.mark.performance
@pytest.mark.skipif(not RUN_PERFORMANCE, reason="Performance tests require RUN_PERFORMANCE_TESTS=1")
class TestPerformance:
    """Performance benchmark tests."""

    def test_100_session_files_performance(self, tmp_path):
        """Test processing 100 session files completes within time limit."""
        factory = QwenSessionFactory()

        # Create 100 session files
        for i in range(100):
            entries = factory.create_simple_conversation()
            session_path = tmp_path / f"session_{i}.jsonl"
            factory.write_jsonl(session_path, entries)

        start_time = time.time()

        # Process all files
        total_messages = 0
        for i in range(100):
            session_path = tmp_path / f"session_{i}.jsonl"
            daily, messages = process_jsonl_file(session_path, "testhost", "testuser")
            total_messages += len(messages)

        elapsed_time = time.time() - start_time

        # Should complete within 30 seconds
        assert elapsed_time < 30, f"Processing took {elapsed_time:.1f}s, expected < 30s"
        assert total_messages >= 100  # At least one message per session

    def test_single_large_file_performance(self, tmp_path):
        """Test processing a large session file."""
        factory = QwenSessionFactory()

        # Create a session with many messages
        entries = []
        for i in range(1000):
            entries.append({
                "type": "user",
                "uuid": f"msg-{i}",
                "parentUuid": None if i == 0 else f"msg-{i-1}",
                "timestamp": f"2026-08-24T10:{i % 60:02d}:00Z",
                "message": {"role": "user" if i % 2 == 0 else "assistant", "parts": [{"text": f"Message {i}"}]},
            })

        session_path = tmp_path / "large_session.jsonl"
        factory.write_jsonl(session_path, entries)

        start_time = time.time()
        daily, messages = process_jsonl_file(session_path, "testhost", "testuser")
        elapsed_time = time.time() - start_time

        # Should complete within 5 seconds
        assert elapsed_time < 5, f"Processing took {elapsed_time:.1f}s, expected < 5s"

    def test_deep_message_tree_performance(self, tmp_path):
        """Test processing deep message trees."""
        factory = QwenSessionFactory()

        # Create a very deep tree
        entries = factory.create_deep_message_tree(depth=50)
        session_path = tmp_path / "deep_tree.jsonl"
        factory.write_jsonl(session_path, entries)

        start_time = time.time()
        daily, messages = process_jsonl_file(session_path, "testhost", "testuser")
        elapsed_time = time.time() - start_time

        # Should complete quickly even with deep trees
        assert elapsed_time < 2, f"Processing took {elapsed_time:.1f}s, expected < 2s"


@pytest.mark.performance
@pytest.mark.skipif(not RUN_PERFORMANCE, reason="Performance tests require RUN_PERFORMANCE_TESTS=1")
class TestPerformanceWithDatabase:
    """Performance tests with database persistence."""

    def test_100_session_files_with_db_performance(self, tmp_path):
        """Test processing 100 session files with database writes."""
        pytest.skip("Requires PostgreSQL database for this test")

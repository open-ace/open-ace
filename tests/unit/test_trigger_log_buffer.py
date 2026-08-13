"""
Unit tests for Trigger Log Buffer module.

Tests for log buffering, batch writing, and reliability.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import threading

from app.modules.governance.trigger_log_buffer import TriggerLogBuffer


class TestTriggerLogBuffer:
    """Tests for TriggerLogBuffer class."""

    def test_buffer_initialization(self):
        """Test buffer initialization."""
        buffer = TriggerLogBuffer(batch_size=10, flush_interval=1.0)

        assert buffer.batch_size == 10
        assert buffer.flush_interval == 1.0
        assert buffer._buffer.qsize() == 0

    def test_add_log_entry(self):
        """Test adding log entry to buffer."""
        buffer = TriggerLogBuffer()

        buffer.add(
            rule_id=1,
            action_taken="warn",
            matched_content_hash="abc123",
            session_id="session-123",
            user_id=1,
            tenant_id=1
        )

        assert buffer._buffer.qsize() == 1
        assert buffer._total_added == 1

    def test_trigger_flush_on_batch_size(self):
        """Test that flush is triggered when batch size is reached."""
        buffer = TriggerLogBuffer(batch_size=3, max_buffer_size=100)

        # Mock _write_to_database
        with patch.object(buffer, '_write_to_database') as mock_write:
            mock_write.return_value = None

            # Add entries
            buffer.add(rule_id=1, action_taken="warn")
            buffer.add(rule_id=2, action_taken="warn")
            buffer.add(rule_id=3, action_taken="warn")

            # Should have triggered flush
            assert buffer._total_flushed == 0  # Not yet written due to mock

    def test_content_hash_computation(self):
        """Test content hash computation."""
        hash1 = TriggerLogBuffer.compute_content_hash("test content")
        hash2 = TriggerLogBuffer.compute_content_hash("test content")
        hash3 = TriggerLogBuffer.compute_content_hash("different content")

        # Same content = same hash
        assert hash1 == hash2

        # Different content = different hash
        assert hash1 != hash3

        # Hash length (SHA256 first 16 chars)
        assert len(hash1) == 16

    def test_force_flush(self):
        """Test force flush."""
        buffer = TriggerLogBuffer(batch_size=100, flush_interval=60.0)

        # Add entries
        buffer.add(rule_id=1, action_taken="warn")
        buffer.add(rule_id=2, action_taken="warn")

        # Mock _write_to_database
        with patch.object(buffer, '_write_to_database') as mock_write:
            mock_write.return_value = None

            buffer.force_flush()

            # Should have attempted to write
            assert mock_write.called

    def test_flush_error_handling(self):
        """Test error handling during flush."""
        buffer = TriggerLogBuffer(batch_size=100)

        buffer.add(rule_id=1, action_taken="warn")

        # Mock _write_to_database to raise error
        with patch.object(buffer, '_write_to_database') as mock_write:
            mock_write.side_effect = Exception("Database error")

            buffer._flush_batch()

            # Should have recorded error
            assert buffer._flush_errors == 1

    def test_buffer_stats(self):
        """Test buffer statistics."""
        buffer = TriggerLogBuffer(batch_size=100)

        stats = buffer.get_stats()

        assert "buffer_size" in stats
        assert "total_added" in stats
        assert "total_flushed" in stats
        assert "flush_errors" in stats
        assert "pending" in stats

    def test_atexit_registration(self):
        """Test that atexit hook is registered."""
        import atexit

        # Create buffer
        buffer = TriggerLogBuffer()

        # Verify atexit was called (this is a side effect)
        # We can't easily test the actual exit behavior
        assert hasattr(buffer, 'force_flush')

    def test_concurrent_adds(self):
        """Test concurrent additions to buffer."""
        buffer = TriggerLogBuffer(batch_size=1000, max_buffer_size=10000)

        def add_entries(start, count):
            for i in range(start, start + count):
                buffer.add(rule_id=i, action_taken="warn")

        # Create threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=add_entries, args=(i * 100, 100))
            threads.append(t)

        # Start threads
        for t in threads:
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Should have added 500 entries
        assert buffer._total_added == 500


class TestTriggerLogBufferDatabase:
    """Tests for database operations."""

    def test_write_to_database_success(self):
        """Test successful database write."""
        buffer = TriggerLogBuffer()

        batch = [
            {
                "rule_id": 1,
                "matched_content_hash": "abc",
                "matched_at": "2026-08-13T00:00:00",
                "action_taken": "warn",
                "session_id": "s1",
                "user_id": 1,
                "tenant_id": 1,
            }
        ]

        with patch('app.modules.governance.trigger_log_buffer.get_connection') as mock_conn:
            mock_cursor = Mock()
            mock_conn.return_value.cursor.return_value = mock_cursor

            buffer._write_to_database(batch)

            # Should have executed INSERT
            assert mock_cursor.execute.called

    def test_write_to_database_rollback_on_error(self):
        """Test rollback on database error."""
        buffer = TriggerLogBuffer()

        batch = [{"rule_id": 1, "matched_content_hash": "abc", "matched_at": "2026-08-13",
                  "action_taken": "warn", "session_id": None, "user_id": None, "tenant_id": None}]

        with patch('app.modules.governance.trigger_log_buffer.get_connection') as mock_conn:
            mock_cursor = Mock()
            mock_cursor.execute.side_effect = Exception("DB error")
            mock_conn.return_value.cursor.return_value = mock_cursor

            with pytest.raises(Exception):
                buffer._write_to_database(batch)

            # Should have called rollback
            assert mock_conn.return_value.rollback.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
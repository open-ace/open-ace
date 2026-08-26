"""
Unit tests for RequestPerformanceRecorder.

Tests the performance event recording, async queue processing,
and metrics collection.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.utils.request_performance import (
    PerformanceEvent,
    PerformanceMetrics,
    RequestPerformanceRecorder,
    RequestRecord,
    generate_request_id,
)


class TestPerformanceMetrics(unittest.TestCase):
    """Tests for PerformanceMetrics class."""

    def test_initial_metrics(self):
        """Test initial metric values."""
        metrics = PerformanceMetrics()
        result = metrics.get_metrics()

        self.assertEqual(result["write_timeout_total"], 0)
        self.assertEqual(result["queue_overflow_total"], 0)
        self.assertEqual(result["write_error_total"], 0)
        self.assertEqual(result["missing_tenant_total"], 0)
        self.assertEqual(result["events_total"], 0)
        self.assertEqual(result["writes_total"], 0)

    def test_increment_metric(self):
        """Test incrementing a metric."""
        metrics = PerformanceMetrics()
        metrics.increment("write_timeout_count", 5)

        result = metrics.get_metrics()
        self.assertEqual(result["write_timeout_total"], 5)

    def test_thread_safety(self):
        """Test metrics are thread-safe."""
        import threading

        metrics = PerformanceMetrics()
        threads = []

        def increment():
            for _ in range(100):
                metrics.increment("total_events")

        for _ in range(10):
            t = threading.Thread(target=increment)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        result = metrics.get_metrics()
        self.assertEqual(result["events_total"], 1000)


class TestPerformanceEvent(unittest.TestCase):
    """Tests for PerformanceEvent dataclass."""

    def test_create_event(self):
        """Test creating a performance event."""
        event = PerformanceEvent(
            request_id="test-123",
            session_id="session-1",
            tenant_id=1,
            tool_name="test-tool",
            event_type="start",
        )

        self.assertEqual(event.request_id, "test-123")
        self.assertEqual(event.session_id, "session-1")
        self.assertEqual(event.tenant_id, 1)
        self.assertEqual(event.tool_name, "test-tool")
        self.assertEqual(event.event_type, "start")
        self.assertIsNone(event.timestamp)

    def test_event_with_timestamp(self):
        """Test creating an event with a timestamp."""
        timestamp = time.monotonic()
        event = PerformanceEvent(
            request_id="test-123",
            event_type="start",
            timestamp=timestamp,
        )

        self.assertEqual(event.timestamp, timestamp)


class TestRequestRecord(unittest.TestCase):
    """Tests for RequestRecord dataclass."""

    def test_create_record(self):
        """Test creating a request record."""
        record = RequestRecord(
            request_id="req-1",
            session_id="sess-1",
            tenant_id=1,
            tool_name="tool-1",
        )

        self.assertEqual(record.request_id, "req-1")
        self.assertEqual(record.tenant_id, 1)
        self.assertIsNone(record.started_at)
        self.assertIsNone(record.ttft_ms)


class TestRequestPerformanceRecorder(unittest.TestCase):
    """Tests for RequestPerformanceRecorder class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a mock database writer
        self.mock_writer = MagicMock(return_value=True)

        # Reset singleton instance
        RequestPerformanceRecorder._instance = None

        # Create fresh recorder instance with enabled=True
        self.recorder = RequestPerformanceRecorder(
            db_writer=self.mock_writer,
            enabled=True,
        )
        # Stop the flush thread for testing
        self.recorder._shutdown_event.set()
        if self.recorder._flush_thread:
            self.recorder._flush_thread.join(timeout=2.0)

    def tearDown(self):
        """Clean up after test."""
        # Reset singleton
        RequestPerformanceRecorder._instance = None

    def test_singleton_pattern(self):
        """Test that recorder is a singleton."""
        recorder1 = RequestPerformanceRecorder()
        recorder2 = RequestPerformanceRecorder()

        self.assertIs(recorder1, recorder2)

    def test_record_request_start(self):
        """Test recording request start."""
        result = self.recorder.record_request_start(
            request_id="req-1",
            tenant_id=1,
            tool_name="test-tool",
        )

        self.assertTrue(result)
        self.assertEqual(len(self.recorder.event_queue), 1)

        event = self.recorder.event_queue[0]
        self.assertEqual(event.request_id, "req-1")
        self.assertEqual(event.event_type, "start")
        self.assertEqual(event.tenant_id, 1)

    def test_record_first_response(self):
        """Test recording first response."""
        result = self.recorder.record_first_response("req-1")

        self.assertTrue(result)
        self.assertEqual(len(self.recorder.event_queue), 1)

        event = self.recorder.event_queue[0]
        self.assertEqual(event.request_id, "req-1")
        self.assertEqual(event.event_type, "first_response")

    def test_record_request_complete(self):
        """Test recording request complete."""
        result = self.recorder.record_request_complete(
            request_id="req-1",
            status="success",
            tool_call_count=2,
            tool_call_duration_ms=500,
        )

        self.assertTrue(result)
        self.assertEqual(len(self.recorder.event_queue), 1)

        event = self.recorder.event_queue[0]
        self.assertEqual(event.request_id, "req-1")
        self.assertEqual(event.event_type, "complete")
        self.assertEqual(event.status, "success")
        self.assertEqual(event.tool_call_count, 2)

    def test_queue_overflow(self):
        """Test queue overflow behavior."""
        # Reset singleton
        RequestPerformanceRecorder._instance = None

        # Create a small queue
        small_recorder = RequestPerformanceRecorder(
            db_writer=self.mock_writer,
            queue_max_size=5,
            enabled=True,
        )
        # Stop the flush thread for testing
        small_recorder._shutdown_event.set()
        if small_recorder._flush_thread:
            small_recorder._flush_thread.join(timeout=2.0)

        # Fill the queue
        for i in range(6):
            small_recorder.record_request_start(
                request_id=f"req-{i}",
                tenant_id=1,
            )

        # Queue should be at max size, not exceed it
        self.assertEqual(len(small_recorder.event_queue), 5)

        # Check overflow metric
        metrics = small_recorder.get_metrics()
        self.assertGreater(metrics["queue_overflow_total"], 0)

    def test_missing_tenant_id(self):
        """Test handling of missing tenant_id."""
        # Record a request without tenant_id
        self.recorder.record_request_start(
            request_id="req-no-tenant",
            tenant_id=None,
        )

        # Process events
        self.recorder._flush()

        # Check that missing tenant metric was incremented
        metrics = self.recorder.get_metrics()
        self.assertGreater(metrics["missing_tenant_total"], 0)

    def test_process_events_full_lifecycle(self):
        """Test processing a complete request lifecycle."""
        # Record full lifecycle with tenant_id for all events
        self.recorder.record_request_start(
            request_id="req-full",
            tenant_id=1,
            tool_name="test-tool",
        )
        time.sleep(0.01)  # Small delay
        self.recorder.record_first_response("req-full")
        time.sleep(0.01)  # Small delay
        self.recorder.record_request_complete(
            "req-full",
            status="success",
            tool_call_duration_ms=100,
        )

        # Flush to trigger processing
        self.recorder._flush()

        # Check that writer was called
        self.mock_writer.assert_called_once()

        # Check the record that was written
        call_args = self.mock_writer.call_args
        records = call_args[0][0]
        self.assertEqual(len(records), 1)

        record = records[0]
        self.assertEqual(record.request_id, "req-full")
        self.assertEqual(record.status, "success")
        self.assertGreater(record.ttft_ms, 0)
        self.assertEqual(record.tool_call_duration_ms, 100)

    def test_negative_ttft_handling(self):
        """Test handling of negative TTFT (clock skew)."""
        # Create events with manually manipulated timestamps
        start_time = time.monotonic() + 1  # Future time
        response_time = time.monotonic()  # Earlier time (negative TTFT)

        # All events must have tenant_id
        self.recorder.record_event(
            PerformanceEvent(
                request_id="req-negative",
                tenant_id=1,
                event_type="start",
                timestamp=start_time,
            )
        )
        self.recorder.record_event(
            PerformanceEvent(
                request_id="req-negative",
                tenant_id=1,  # Must provide tenant_id for all events
                event_type="first_response",
                timestamp=response_time,
            )
        )
        self.recorder.record_event(
            PerformanceEvent(
                request_id="req-negative",
                tenant_id=1,  # Must provide tenant_id for all events
                event_type="complete",
                timestamp=response_time,
            )
        )

        # Process events
        self.recorder._flush()

        # Check that TTFT was corrected to 0
        call_args = self.mock_writer.call_args
        self.assertIsNotNone(call_args, "Writer should have been called")
        records = call_args[0][0]
        record = records[0]
        self.assertGreaterEqual(record.ttft_ms, 0)

    def test_get_metrics(self):
        """Test getting metrics."""
        # Reset singleton first
        RequestPerformanceRecorder._instance = None

        # Create fresh recorder with enabled=True but no flush thread
        recorder = RequestPerformanceRecorder(
            db_writer=self.mock_writer,
            enabled=True,
        )
        # Stop the flush thread for testing
        recorder._shutdown_event.set()
        if recorder._flush_thread:
            recorder._flush_thread.join(timeout=2.0)

        # Record some events
        recorder.record_request_start("req-1", tenant_id=1)
        recorder.record_request_start("req-2", tenant_id=1)

        metrics = recorder.get_metrics()

        self.assertIn("queue_size", metrics)
        self.assertIn("inflight_requests", metrics)
        self.assertIn("events_total", metrics)

        # Queue size should be 2 after recording 2 events
        # events_total is updated during flush, not record
        self.assertEqual(metrics["queue_size"], 2)

    def test_shutdown(self):
        """Test shutdown flushes remaining events."""
        # Reset singleton for this test
        RequestPerformanceRecorder._instance = None

        # Create a new recorder for this test
        recorder = RequestPerformanceRecorder(
            db_writer=self.mock_writer,
            enabled=True,
        )

        recorder.record_request_start(
            request_id="req-shutdown",
            tenant_id=1,
        )
        recorder.record_request_complete("req-shutdown")

        # Call shutdown to flush and stop
        recorder.shutdown()

        # Verify writer was called during shutdown
        self.mock_writer.assert_called_once()


class TestGenerateRequestId(unittest.TestCase):
    """Tests for generate_request_id function."""

    def test_generate_request_id(self):
        """Test request ID generation."""
        request_id = generate_request_id("session-123")

        self.assertIn("session-123", request_id)
        self.assertTrue(request_id.startswith("session-123-"))

    def test_request_id_uniqueness(self):
        """Test that request IDs are unique."""
        ids = [generate_request_id("session-1") for _ in range(100)]

        self.assertEqual(len(ids), len(set(ids)))

    def test_request_id_format(self):
        """Test request ID format."""
        request_id = generate_request_id("sess-1")

        # Format: {session_id}-{timestamp_ms}-{random_suffix}
        parts = request_id.split("-")
        self.assertGreaterEqual(len(parts), 3)  # session, timestamp, random


if __name__ == "__main__":
    unittest.main()

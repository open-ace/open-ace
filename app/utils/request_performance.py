"""
Open ACE - Request Performance Recorder

Records request lifecycle events (start, first response, complete) for
response time tracking. Uses async queue and batch writes for performance.

Issue #3080: Response time metrics for trend analysis.
"""

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Constants
QUEUE_MAX_SIZE = 1000
BATCH_SIZE = 100
FLUSH_INTERVAL_SECONDS = 5.0
WRITE_TIMEOUT_MS = 50
MAX_RETRIES = 3


@dataclass
class PerformanceEvent:
    """A single performance event to be recorded."""

    request_id: str
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    tenant_id: Optional[int] = None
    tool_name: str = "unknown"
    host_name: str = "localhost"
    user_id: Optional[int] = None

    # Event type: 'start', 'first_response', 'complete'
    event_type: str = "start"
    timestamp: Optional[float] = None  # Monotonic time

    # For complete events
    status: str = "success"
    sample_type: str = "streaming"
    model: Optional[str] = None
    tool_call_count: int = 0
    tool_call_duration_ms: int = 0


@dataclass
class RequestRecord:
    """Aggregated record for a single request."""

    request_id: str
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    tenant_id: int = 1  # Default tenant
    tool_name: str = "unknown"
    host_name: str = "localhost"
    user_id: Optional[int] = None

    started_at: Optional[datetime] = None
    started_at_monotonic: Optional[float] = None
    first_response_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    ttft_ms: Optional[int] = None
    tool_call_duration_ms: int = 0
    total_duration_ms: Optional[int] = None

    status: str = "success"
    sample_type: str = "streaming"
    model: Optional[str] = None
    tool_call_count: int = 0

    # For tracking
    first_response_monotonic: Optional[float] = None
    completed_monotonic: Optional[float] = None


class PerformanceMetrics:
    """Metrics for monitoring the performance recorder."""

    def __init__(self):
        self.write_timeout_count = 0
        self.queue_overflow_count = 0
        self.write_error_count = 0
        self.missing_tenant_count = 0
        self.total_events = 0
        self.total_writes = 0
        self.lock = threading.Lock()

    def increment(self, metric: str, count: int = 1):
        """Increment a metric counter."""
        with self.lock:
            if hasattr(self, metric):
                current = getattr(self, metric)
                setattr(self, metric, current + count)

    def get_metrics(self) -> dict:
        """Get current metrics snapshot."""
        with self.lock:
            return {
                "write_timeout_total": self.write_timeout_count,
                "queue_overflow_total": self.queue_overflow_count,
                "write_error_total": self.write_error_count,
                "missing_tenant_total": self.missing_tenant_count,
                "events_total": self.total_events,
                "writes_total": self.total_writes,
            }


class RequestPerformanceRecorder:
    """
    Recorder for request performance events.

    Uses an in-memory queue and background thread for async writes.
    Implements batching, retries, and graceful degradation.
    """

    _instance: Optional["RequestPerformanceRecorder"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern for global recorder instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db_writer: Optional[Callable] = None,
        queue_max_size: int = QUEUE_MAX_SIZE,
        batch_size: int = BATCH_SIZE,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        enabled: bool = True,
    ):
        """
        Initialize the recorder.

        Args:
            db_writer: Function to write records to database (injected for testing)
            queue_max_size: Maximum queue size before dropping old events
            batch_size: Number of records to write in a single batch
            flush_interval: Seconds between automatic flushes
            enabled: Whether recording is enabled
        """
        # Prevent re-initialization of singleton
        if hasattr(self, "_initialized") and self._initialized:
            return

        self._initialized = True
        self.enabled = enabled
        self.queue_max_size = queue_max_size
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        # Queue for events
        self.event_queue: deque[PerformanceEvent] = deque(maxlen=queue_max_size)

        # In-memory tracking of in-flight requests
        self.inflight_requests: dict[str, RequestRecord] = {}
        self.inflight_lock = threading.Lock()

        # Metrics
        self.metrics = PerformanceMetrics()

        # Database writer (injected or default)
        self._db_writer = db_writer or self._default_db_writer

        # Background flush thread
        self._flush_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        if self.enabled:
            self._start_flush_thread()

    def _default_db_writer(self, records: list[RequestRecord]) -> bool:
        """Default database writer implementation."""
        if not records:
            return True

        try:
            from app.repositories.database import get_db_connection, is_postgresql

            with get_db_connection() as conn:
                cursor = conn.cursor()

                for record in records:
                    if is_postgresql():
                        cursor.execute(
                            """
                            INSERT INTO request_performance
                            (request_id, session_id, conversation_id, tenant_id, tool_name,
                             host_name, user_id, started_at, first_response_at, completed_at,
                             ttft_ms, tool_call_duration_ms, total_duration_ms, status,
                             sample_type, model, tool_call_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (request_id) DO UPDATE SET
                                first_response_at = EXCLUDED.first_response_at,
                                completed_at = EXCLUDED.completed_at,
                                ttft_ms = EXCLUDED.ttft_ms,
                                tool_call_duration_ms = EXCLUDED.tool_call_duration_ms,
                                total_duration_ms = EXCLUDED.total_duration_ms,
                                status = EXCLUDED.status
                            """,
                            (
                                record.request_id,
                                record.session_id,
                                record.conversation_id,
                                record.tenant_id,
                                record.tool_name,
                                record.host_name,
                                record.user_id,
                                record.started_at,
                                record.first_response_at,
                                record.completed_at,
                                record.ttft_ms,
                                record.tool_call_duration_ms,
                                record.total_duration_ms,
                                record.status,
                                record.sample_type,
                                record.model,
                                record.tool_call_count,
                            ),
                        )
                    else:
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO request_performance
                            (request_id, session_id, conversation_id, tenant_id, tool_name,
                             host_name, user_id, started_at, first_response_at, completed_at,
                             ttft_ms, tool_call_duration_ms, total_duration_ms, status,
                             sample_type, model, tool_call_count)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                record.request_id,
                                record.session_id,
                                record.conversation_id,
                                record.tenant_id,
                                record.tool_name,
                                record.host_name,
                                record.user_id,
                                record.started_at,
                                record.first_response_at,
                                record.completed_at,
                                record.ttft_ms,
                                record.tool_call_duration_ms,
                                record.total_duration_ms,
                                record.status,
                                record.sample_type,
                                record.model,
                                record.tool_call_count,
                            ),
                        )

                conn.commit()
                self.metrics.increment("total_writes", len(records))
                return True

        except Exception as e:
            logger.error(f"Failed to write performance records: {e}")
            self.metrics.increment("write_error_count", len(records))
            return False

    def _start_flush_thread(self):
        """Start the background flush thread."""
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="PerformanceRecorder-Flush",
        )
        self._flush_thread.start()

    def _flush_loop(self):
        """Background loop to periodically flush the queue."""
        while not self._shutdown_event.is_set():
            try:
                self._flush()
            except Exception as e:
                logger.error(f"Error in flush loop: {e}")

            # Wait for next flush interval or shutdown
            self._shutdown_event.wait(self.flush_interval)

        # Final flush on shutdown
        try:
            self._flush()
        except Exception as e:
            logger.error(f"Error in final flush: {e}")

    def _flush(self):
        """Flush pending events to database."""
        if not self.event_queue:
            return

        # Collect up to batch_size events
        events_to_process = []
        while self.event_queue and len(events_to_process) < self.batch_size:
            try:
                events_to_process.append(self.event_queue.popleft())
            except IndexError:
                break

        if not events_to_process:
            return

        # Process events into records
        records = self._process_events(events_to_process)
        if not records:
            return

        # Write to database with retries
        for attempt in range(MAX_RETRIES):
            if self._db_writer(records):
                break
            elif attempt < MAX_RETRIES - 1:
                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
            else:
                logger.warning(f"Failed to write {len(records)} records after {MAX_RETRIES} retries")

    def _process_events(self, events: list[PerformanceEvent]) -> list[RequestRecord]:
        """Process events into database records."""
        records_to_write = []

        with self.inflight_lock:
            for event in events:
                self.metrics.increment("total_events")

                # For start events, tenant_id is required
                # For other events, we can get it from inflight_requests
                if event.event_type == "start" and event.tenant_id is None:
                    self.metrics.increment("missing_tenant_count")
                    logger.warning(
                        f"Missing tenant_id for start event {event.request_id}, skipping"
                    )
                    continue

                # Get or create inflight record
                if event.request_id not in self.inflight_requests:
                    # Only create new record for start events with tenant_id
                    if event.event_type == "start":
                        self.inflight_requests[event.request_id] = RequestRecord(
                            request_id=event.request_id,
                            session_id=event.session_id,
                            conversation_id=event.conversation_id,
                            tenant_id=event.tenant_id,
                            tool_name=event.tool_name,
                            host_name=event.host_name,
                            user_id=event.user_id,
                        )
                    else:
                        # Non-start event without inflight record - skip
                        logger.debug(
                            f"No inflight record for {event.request_id}, skipping {event.event_type} event"
                        )
                        continue

                record = self.inflight_requests[event.request_id]

                # Update record based on event type
                if event.event_type == "start":
                    record.started_at = datetime.utcnow()
                    record.started_at_monotonic = event.timestamp or time.monotonic()
                    record.sample_type = event.sample_type
                    record.model = event.model

                elif event.event_type == "first_response":
                    record.first_response_at = datetime.utcnow()
                    record.first_response_monotonic = event.timestamp or time.monotonic()

                    # Calculate TTFT
                    if record.started_at_monotonic and record.first_response_monotonic:
                        record.ttft_ms = int(
                            (record.first_response_monotonic - record.started_at_monotonic)
                            * 1000
                        )
                        # TTFT should be non-negative
                        if record.ttft_ms < 0:
                            logger.warning(
                                f"Negative TTFT for {event.request_id}: {record.ttft_ms}ms, setting to 0"
                            )
                            record.ttft_ms = 0

                elif event.event_type == "complete":
                    record.completed_at = datetime.utcnow()
                    record.completed_monotonic = event.timestamp or time.monotonic()
                    record.status = event.status
                    record.tool_call_count = event.tool_call_count
                    record.tool_call_duration_ms = event.tool_call_duration_ms

                    # Calculate total duration
                    if record.started_at_monotonic and record.completed_monotonic:
                        record.total_duration_ms = int(
                            (record.completed_monotonic - record.started_at_monotonic) * 1000
                        )
                        if record.total_duration_ms < 0:
                            logger.warning(
                                f"Negative duration for {event.request_id}: {record.total_duration_ms}ms"
                            )
                            record.total_duration_ms = 0

                    # Adjust TTFT to exclude tool call time if not already set
                    if record.ttft_ms is None and record.total_duration_ms is not None:
                        record.ttft_ms = max(0, record.total_duration_ms - record.tool_call_duration_ms)

                    # Mark for write
                    records_to_write.append(record)
                    # Remove from inflight
                    del self.inflight_requests[event.request_id]

        return records_to_write

    def record_event(self, event: PerformanceEvent) -> bool:
        """
        Record a performance event.

        Args:
            event: The event to record

        Returns:
            True if event was queued successfully, False if queue is full
        """
        if not self.enabled:
            return False

        # Check queue size
        if len(self.event_queue) >= self.queue_max_size:
            self.metrics.increment("queue_overflow_count")
            logger.warning(
                f"Performance queue overflow, dropping event for {event.request_id}"
            )
            return False

        # Set timestamp if not provided
        if event.timestamp is None:
            event.timestamp = time.monotonic()

        # Add to queue
        self.event_queue.append(event)
        return True

    def record_request_start(
        self,
        request_id: str,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        tenant_id: Optional[int] = None,
        tool_name: str = "unknown",
        host_name: str = "localhost",
        user_id: Optional[int] = None,
        sample_type: str = "streaming",
        model: Optional[str] = None,
    ) -> bool:
        """
        Record the start of a request.

        Args:
            request_id: Unique identifier for this request
            session_id: Optional session ID
            conversation_id: Optional conversation ID
            tenant_id: Tenant ID (required)
            tool_name: Tool name
            host_name: Host name
            user_id: Optional user ID
            sample_type: 'streaming' or 'batch'
            model: Optional model name

        Returns:
            True if event was queued successfully
        """
        return self.record_event(
            PerformanceEvent(
                request_id=request_id,
                session_id=session_id,
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                host_name=host_name,
                user_id=user_id,
                event_type="start",
                sample_type=sample_type,
                model=model,
            )
        )

    def record_first_response(self, request_id: str) -> bool:
        """
        Record the first response event for a request.

        Args:
            request_id: The request ID

        Returns:
            True if event was queued successfully
        """
        return self.record_event(
            PerformanceEvent(
                request_id=request_id,
                event_type="first_response",
            )
        )

    def record_request_complete(
        self,
        request_id: str,
        status: str = "success",
        tool_call_count: int = 0,
        tool_call_duration_ms: int = 0,
    ) -> bool:
        """
        Record the completion of a request.

        Args:
            request_id: The request ID
            status: 'success', 'failed', 'cancelled', or 'timeout'
            tool_call_count: Number of tool calls made
            tool_call_duration_ms: Total duration of tool calls in ms

        Returns:
            True if event was queued successfully
        """
        return self.record_event(
            PerformanceEvent(
                request_id=request_id,
                event_type="complete",
                status=status,
                tool_call_count=tool_call_count,
                tool_call_duration_ms=tool_call_duration_ms,
            )
        )

    def get_metrics(self) -> dict:
        """Get current metrics snapshot."""
        metrics = self.metrics.get_metrics()
        metrics["queue_size"] = len(self.event_queue)
        metrics["inflight_requests"] = len(self.inflight_requests)
        return metrics

    def shutdown(self):
        """Shutdown the recorder, flushing remaining events."""
        logger.info("Shutting down RequestPerformanceRecorder")
        self._shutdown_event.set()
        if self._flush_thread:
            self._flush_thread.join(timeout=10.0)
        self.enabled = False

    def flush(self):
        """Manually flush pending events."""
        self._flush()


def get_recorder() -> RequestPerformanceRecorder:
    """Get the global recorder instance."""
    return RequestPerformanceRecorder()


def generate_request_id(session_id: str) -> str:
    """Generate a unique request ID."""
    timestamp_ms = int(time.time() * 1000)
    # Use a larger random range to reduce collision probability
    # 6-digit random number gives 1M possibilities
    random_suffix = random.randint(100000, 999999)
    return f"{session_id}-{timestamp_ms}-{random_suffix}"
# mypy: disable-error-code="var-annotated"
"""
Open ACE - Autonomous Event Emitter
SSE event publisher for real-time workflow timeline updates.
Uses an in-process queue per subscriber for push-based notifications.
Includes TTL-based cleanup to prevent memory leaks from disconnected clients.
"""

from __future__ import annotations



import logging
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum time (seconds) a subscriber queue can live without being read
# before it is considered stale and garbage-collected.
SUBSCRIBER_TTL_SECONDS = 300  # 5 minutes


class AutonomousEventEmitter:
    """Singleton that manages SSE subscriptions and event broadcasting."""

    _instance: Optional["AutonomousEventEmitter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queues: dict[str, list[tuple[queue.Queue, float]]] = (
            {}
        )  # workflow_id -> [(queue, last_read_ts)]
        self._emit_lock = threading.Lock()
        self._cleanup_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @classmethod
    def instance(cls) -> "AutonomousEventEmitter":
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def subscribe(self, workflow_id: str) -> queue.Queue:
        """Subscribe to events for a workflow. Returns a queue to read from."""
        q = queue.Queue(maxsize=100)
        now = time.time()
        with self._emit_lock:
            if workflow_id not in self._queues:
                self._queues[workflow_id] = []
            self._queues[workflow_id].append((q, now))
            # Start cleanup thread if not running
            if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
                self._stop_event.clear()
                self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
                self._cleanup_thread.start()
        return q

    def unsubscribe(self, workflow_id: str, q: queue.Queue) -> None:
        """Unsubscribe a queue from workflow events."""
        with self._emit_lock:
            if workflow_id in self._queues:
                self._queues[workflow_id] = [
                    (mq, ts) for mq, ts in self._queues[workflow_id] if mq is not q
                ]
                if not self._queues[workflow_id]:
                    del self._queues[workflow_id]

    def mark_read(self, workflow_id: str, q: queue.Queue) -> None:
        """Update last-read timestamp for a subscriber queue (prevents TTL eviction)."""
        now = time.time()
        with self._emit_lock:
            subscribers = self._queues.get(workflow_id, [])
            for i, (mq, _ts) in enumerate(subscribers):
                if mq is q:
                    subscribers[i] = (mq, now)
                    break

    def emit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Broadcast an event to all subscribers of a workflow."""
        event_payload = {
            "workflow_id": workflow_id,
            "event_type": event_type,
            "data": data,
        }
        with self._emit_lock:
            subscribers = self._queues.get(workflow_id, [])

        for q, _ts in subscribers:
            try:
                q.put_nowait(event_payload)
            except queue.Full:
                logger.warning("SSE queue full for workflow %s, dropping event", workflow_id[:8])

    def _cleanup_loop(self) -> None:
        """Periodically remove stale subscriber queues."""
        while not self._stop_event.is_set():
            self._stop_event.wait(60)  # Check every 60 seconds
            if self._stop_event.is_set():
                break

            now = time.time()
            with self._emit_lock:
                stale_keys = []
                for workflow_id in list(self._queues.keys()):
                    subscribers = self._queues[workflow_id]
                    alive = [
                        (q, ts) for q, ts in subscribers if (now - ts) < SUBSCRIBER_TTL_SECONDS
                    ]
                    if alive:
                        self._queues[workflow_id] = alive
                    else:
                        stale_keys.append(workflow_id)
                for key in stale_keys:
                    del self._queues[key]

            if stale_keys:
                logger.debug("Cleaned up %d stale SSE subscriber groups", len(stale_keys))

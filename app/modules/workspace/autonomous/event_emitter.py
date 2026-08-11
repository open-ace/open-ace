# mypy: disable-error-code="var-annotated"
"""
Open ACE - Autonomous Event Emitter

SSE event publisher for real-time workflow timeline updates.
Uses an in-process queue per subscriber for push-based notifications.
Includes TTL-based cleanup to prevent memory leaks from disconnected clients.
"""

import logging
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum time (seconds) a subscriber queue can live without being read
# before it is considered stale and garbage-collected.
SUBSCRIBER_TTL_SECONDS = 300  # 5 minutes

# Keep a small, short-lived replay window for late/reconnecting browser
# subscribers.  Agent activity is intentionally not written to the workflow
# event table because it is high-volume, run-time-only data, but a live-only
# queue made the activity panel blank after every page refresh or transient SSE
# reconnect.  The bounds below keep memory usage predictable.
ACTIVITY_HISTORY_MAX_ITEMS = 50
ACTIVITY_HISTORY_TTL_SECONDS = 15 * 60


class AutonomousEventEmitter:
    """Singleton that manages SSE subscriptions and event broadcasting."""

    _instance: Optional["AutonomousEventEmitter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queues: dict[str, list[tuple[queue.Queue, float]]] = (
            {}
        )  # workflow_id -> [(queue, last_read_ts)]
        self._activity_history: dict[str, deque[tuple[float, dict]]] = {}
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
        """Subscribe and replay recent agent activity to the new queue."""
        q = queue.Queue(maxsize=100)
        now = time.time()
        with self._emit_lock:
            self._prune_activity_history_locked(now)
            if workflow_id not in self._queues:
                self._queues[workflow_id] = []
            self._queues[workflow_id].append((q, now))

            # Seed while holding the same lock used by emit().  This preserves
            # ordering: an event is either in the replay window or delivered as
            # a new event, never lost in the subscribe race.
            for _emitted_at, event_payload in self._activity_history.get(workflow_id, ()):
                q.put_nowait(event_payload)

            self._ensure_cleanup_thread_locked()
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
        event_data = dict(data)
        if event_type == "agent_activity":
            event_data.setdefault("activity_id", uuid.uuid4().hex)
            event_data.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        event_payload = {
            "workflow_id": workflow_id,
            "event_type": event_type,
            "data": event_data,
        }
        now = time.time()
        with self._emit_lock:
            if event_type == "agent_activity":
                history = self._activity_history.setdefault(
                    workflow_id, deque(maxlen=ACTIVITY_HISTORY_MAX_ITEMS)
                )
                history.append((now, event_payload))
                self._ensure_cleanup_thread_locked()
            self._prune_activity_history_locked(now)
            subscribers = self._queues.get(workflow_id, [])

        for q, _ts in subscribers:
            try:
                q.put_nowait(event_payload)
            except queue.Full:
                logger.warning("SSE queue full for workflow %s, dropping event", workflow_id[:8])

    def _ensure_cleanup_thread_locked(self) -> None:
        """Start the cleanup worker. Caller must hold ``_emit_lock``."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._stop_event.clear()
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

    def _prune_activity_history_locked(self, now: float) -> None:
        """Drop expired replay entries. Caller must hold ``_emit_lock``."""
        cutoff = now - ACTIVITY_HISTORY_TTL_SECONDS
        stale_workflows = []
        for workflow_id, history in self._activity_history.items():
            while history and history[0][0] < cutoff:
                history.popleft()
            if not history:
                stale_workflows.append(workflow_id)
        for workflow_id in stale_workflows:
            del self._activity_history[workflow_id]

    def _cleanup_loop(self) -> None:
        """Periodically remove stale subscriber queues."""
        while not self._stop_event.is_set():
            self._stop_event.wait(60)  # Check every 60 seconds
            if self._stop_event.is_set():
                break

            now = time.time()
            with self._emit_lock:
                self._prune_activity_history_locked(now)
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

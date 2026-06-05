# mypy: disable-error-code="var-annotated"
"""
Open ACE - Autonomous Event Emitter

SSE event publisher for real-time workflow timeline updates.
Uses an in-process queue per subscriber for push-based notifications.
"""

import logging
import queue
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class AutonomousEventEmitter:
    """Singleton that manages SSE subscriptions and event broadcasting."""

    _instance: Optional["AutonomousEventEmitter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queues: dict[str, list[queue.Queue]] = {}
        self._emit_lock = threading.Lock()

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
        with self._emit_lock:
            if workflow_id not in self._queues:
                self._queues[workflow_id] = []
            self._queues[workflow_id].append(q)
        return q

    def unsubscribe(self, workflow_id: str, q: queue.Queue) -> None:
        """Unsubscribe a queue from workflow events."""
        with self._emit_lock:
            if workflow_id in self._queues:
                try:
                    self._queues[workflow_id].remove(q)
                except ValueError:
                    pass
                if not self._queues[workflow_id]:
                    del self._queues[workflow_id]

    def emit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Broadcast an event to all subscribers of a workflow."""
        event_payload = {
            "workflow_id": workflow_id,
            "event_type": event_type,
            "data": data,
        }
        with self._emit_lock:
            subscribers = self._queues.get(workflow_id, [])

        for q in subscribers:
            try:
                q.put_nowait(event_payload)
            except queue.Full:
                logger.warning("SSE queue full for workflow %s, dropping event", workflow_id[:8])

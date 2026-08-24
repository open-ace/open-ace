# mypy: disable-error-code="var-annotated"
"""
Open ACE - Autonomous Event Emitter

SSE event publisher for real-time workflow timeline updates.
Uses an in-process queue per subscriber for push-based notifications.
Includes TTL-based cleanup to prevent memory leaks from disconnected clients.
"""

import json
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

# ── Cross-process forwarding (scheduler → web SSE ingest) ──────────────
# Bounds keep the pipe predictable under a busy agent: a bounded buffer with
# drop-oldest, byte+count capped batches (so a batch can never exceed the
# ingest route's body limit), and a 60s staleness cut for realtime-only data.
FORWARD_BUFFER_MAX_EVENTS = 1000
FORWARD_BATCH_MAX_ITEMS = 50
FORWARD_BATCH_MAX_BYTES = 512 * 1024
FORWARD_EVENT_MAX_BYTES = 256 * 1024
FORWARD_EVENT_TTL_SECONDS = 60.0
FORWARD_BUFFER_MAX_BYTES_TOTAL = 8 * 1024 * 1024
FORWARD_BACKOFF_START = 1.0
FORWARD_BACKOFF_MAX = 5.0

_RATE_LIMITED_LOG_INTERVAL = 60.0
_last_ratelimited_log: dict[str, float] = {}


def _ratelimited_log(key: str, level: int, msg: str, *args) -> None:
    """Log at most once per interval per key (forwarder failure chatter)."""
    now = time.time()
    if now - _last_ratelimited_log.get(key, 0.0) >= _RATE_LIMITED_LOG_INTERVAL:
        _last_ratelimited_log[key] = now
        logger.log(level, msg, *args)


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
        # Cross-process forwarding state (scheduler process only; see
        # enable_remote_forwarding for why it must never self-enable).
        self._forward_url: str | None = None
        self._forward_secret: str = ""
        self._forward_buffer: list[tuple[float, str, int]] = []  # (ts, json, bytes)
        self._forward_buffer_bytes = 0
        self._forward_lock = threading.Lock()
        self._forward_thread: threading.Thread | None = None
        self._forward_stop = threading.Event()
        self._forward_wakeup = threading.Event()

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

        if self._forward_url is not None:
            self._forward_enqueue(event_payload)

    # ── Cross-process forwarding (scheduler → web SSE ingest) ──────────

    def enable_remote_forwarding(self, url: str, secret: str) -> bool:
        """Forward every emitted event to the web process's ingest route.

        HARD CONSTRAINT: this is called ONLY from the scheduler worker's
        startup sequence. It must never be self-enabled from an environment
        variable inside the emitter or ``create_app``: a combined process
        (``SCHEDULER_MODE=scheduler`` serving web too) would otherwise loop
        ingest → emit → forward → ingest and amplify events forever.
        """
        if not url or not secret:
            logger.error("Remote forwarding requires a non-empty url and secret")
            return False
        with self._forward_lock:
            self._forward_url = url
            self._forward_secret = secret
            # is_set() covers disable→quick-enable: the old thread may still
            # be alive while winding down, but with the stop flag set — reuse
            # would leave forwarding "enabled" with no live sender.
            start_thread = (
                self._forward_thread is None
                or not self._forward_thread.is_alive()
                or self._forward_stop.is_set()
            )
            if start_thread:
                self._forward_stop.clear()
                self._forward_thread = threading.Thread(
                    target=self._forward_loop, name="sse-event-forwarder", daemon=True
                )
                self._forward_thread.start()
        return True

    def disable_remote_forwarding(self) -> None:
        """Stop forwarding and drop buffered events (tests, shutdown)."""
        with self._forward_lock:
            self._forward_url = None
            self._forward_secret = ""
            self._forward_buffer = []
            self._forward_buffer_bytes = 0
        self._forward_stop.set()
        self._forward_wakeup.set()

    def _forward_enqueue(self, event_payload: dict) -> None:
        """Queue an event for the sender thread. Bounded, never raises."""
        try:
            blob = json.dumps(event_payload, ensure_ascii=False, default=str)
            size = len(blob.encode("utf-8"))
            if size > FORWARD_EVENT_MAX_BYTES:
                _ratelimited_log(
                    "forward_oversize",
                    logging.WARNING,
                    "Dropping oversized forwarded event (%d bytes > %d)",
                    size,
                    FORWARD_EVENT_MAX_BYTES,
                )
                return
            with self._forward_lock:
                while (
                    len(self._forward_buffer) >= FORWARD_BUFFER_MAX_EVENTS
                    or self._forward_buffer_bytes + size > FORWARD_BUFFER_MAX_BYTES_TOTAL
                ):
                    if not self._forward_buffer:
                        break
                    _dropped_ts, _dropped_blob, dropped_size = self._forward_buffer.pop(0)
                    self._forward_buffer_bytes -= dropped_size
                    _ratelimited_log(
                        "forward_overflow",
                        logging.WARNING,
                        "Forward buffer full (%d events / %d bytes), dropping oldest event",
                        FORWARD_BUFFER_MAX_EVENTS,
                        FORWARD_BUFFER_MAX_BYTES_TOTAL,
                    )
                self._forward_buffer.append((time.time(), blob, size))
                self._forward_buffer_bytes += size
            self._forward_wakeup.set()
        except Exception as exc:
            # Delivery is best-effort realtime data; the local broadcast above
            # already succeeded, so a forwarding failure must never propagate.
            _ratelimited_log(
                "forward_enqueue_error",
                logging.WARNING,
                "Failed to enqueue forwarded event: %s",
                exc,
            )

    def _forward_take_batch(self) -> list[tuple[float, str, int]]:
        """Pop the oldest pending events, capped by count AND bytes.

        Items older than FORWARD_EVENT_TTL_SECONDS are dropped first — this is
        the same "equivalent to today's behavior" degradation a split-container
        deployment without an explicit ingest URL sees (the loopback URL is
        valid but unreachable there, so events expire instead of piling up).
        """
        now = time.time()
        with self._forward_lock:
            kept = [
                entry
                for entry in self._forward_buffer
                if now - entry[0] <= FORWARD_EVENT_TTL_SECONDS
            ]
            if len(kept) != len(self._forward_buffer):
                _ratelimited_log(
                    "forward_expired",
                    logging.WARNING,
                    "Dropped %d expired forwarded events (>%ds old)",
                    len(self._forward_buffer) - len(kept),
                    int(FORWARD_EVENT_TTL_SECONDS),
                )
            self._forward_buffer = kept
            self._forward_buffer_bytes = sum(entry[2] for entry in kept)
            batch: list[tuple[float, str, int]] = []
            total = 0
            while self._forward_buffer and len(batch) < FORWARD_BATCH_MAX_ITEMS:
                candidate = self._forward_buffer[0]
                if batch and total + candidate[2] > FORWARD_BATCH_MAX_BYTES:
                    break
                batch.append(self._forward_buffer.pop(0))
                total += candidate[2]
                self._forward_buffer_bytes -= candidate[2]
            return batch

    def _forward_requeue(self, batch: list[tuple[float, str, int]]) -> None:
        """Return an undelivered batch to the front without evicting newer events."""
        with self._forward_lock:
            merged = list(batch) + self._forward_buffer
            dropped = 0
            while (
                len(merged) > FORWARD_BUFFER_MAX_EVENTS
                or sum(entry[2] for entry in merged) > FORWARD_BUFFER_MAX_BYTES_TOTAL
            ):
                if not merged:
                    break
                merged.pop(0)
                dropped += 1
            if dropped:
                _ratelimited_log(
                    "forward_requeue_drop",
                    logging.WARNING,
                    "Dropped %d oldest events while requeuing undelivered batch",
                    dropped,
                )
            self._forward_buffer = merged
            self._forward_buffer_bytes = sum(entry[2] for entry in merged)

    def _forward_post(self, batch: list[tuple[float, str, int]]) -> None:
        import requests

        from app.modules.workspace.autonomous.events_ingest import INGEST_SECRET_HEADER

        url = self._forward_url
        if url is None:  # disable_remote_forwarding() raced us
            return
        events = [json.loads(blob) for _ts, blob, _size in batch]
        # Serialize ourselves with ensure_ascii=False: requests' json= helper
        # re-serializes with ensure_ascii=True, inflating CJK (2x) and emoji
        # (3x) payloads on the wire beyond the byte caps the buffer accounts
        # for — exactly the large non-ASCII activity events this pipe exists
        # to deliver. data= keeps wire bytes == accounted bytes.
        body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
        response = requests.post(
            url,
            data=body,
            headers={
                INGEST_SECRET_HEADER: self._forward_secret,
                "Content-Type": "application/json",
            },
            timeout=2,
        )
        if response.status_code >= 400:
            # A retry would reproduce the same response (bad batch/auth/limit)
            # and stall the pipe behind it — drop and keep flowing.
            _ratelimited_log(
                "forward_rejected",
                logging.ERROR,
                "Ingest endpoint rejected a %d-event batch (HTTP %d); dropping it",
                len(batch),
                response.status_code,
            )
            return
        _ratelimited_log(
            "forward_ok",
            logging.INFO,
            "Forwarded SSE events to ingest endpoint (batch of %d)",
            len(batch),
        )

    def _forward_loop(self) -> None:
        backoff = FORWARD_BACKOFF_START
        while not self._forward_stop.is_set():
            batch: list[tuple[float, str, int]] = []
            try:
                batch = self._forward_take_batch()
                if not batch:
                    self._forward_wakeup.wait(0.25)
                    self._forward_wakeup.clear()
                    continue
                self._forward_post(batch)
                backoff = FORWARD_BACKOFF_START
            except Exception as exc:
                # Connection-type failure (web restarting): retry the batch
                # after backoff. At-least-once — see module docstring.
                if batch:
                    self._forward_requeue(batch)
                _ratelimited_log(
                    "forward_conn_error",
                    logging.ERROR,
                    "Ingest endpoint unreachable (%s); %d events requeued",
                    exc,
                    len(batch),
                )
                self._forward_stop.wait(backoff)
                backoff = min(backoff * 2, FORWARD_BACKOFF_MAX)

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

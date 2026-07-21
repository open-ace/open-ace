"""Unit tests for AutonomousEventEmitter."""

import queue
import threading
import time
from unittest.mock import patch

from app.modules.workspace.autonomous.event_emitter import (
    ACTIVITY_HISTORY_MAX_ITEMS,
    ACTIVITY_HISTORY_TTL_SECONDS,
    AutonomousEventEmitter,
)


class TestEventEmitter:
    """Tests for AutonomousEventEmitter singleton and pub/sub."""

    def setup_method(self):
        # Reset singleton between tests
        AutonomousEventEmitter._instance = None

    def test_singleton(self):
        e1 = AutonomousEventEmitter.instance()
        e2 = AutonomousEventEmitter.instance()
        assert e1 is e2

    def test_subscribe_returns_queue(self):
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-1")
        assert isinstance(q, queue.Queue)
        assert q.maxsize == 100

    def test_emit_delivers_to_subscriber(self):
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-1")
        emitter.emit("wf-1", "test_event", {"key": "value"})
        event = q.get(timeout=1)
        assert event["event_type"] == "test_event"
        assert event["data"] == {"key": "value"}
        assert event["workflow_id"] == "wf-1"

    def test_emit_to_multiple_subscribers(self):
        emitter = AutonomousEventEmitter.instance()
        q1 = emitter.subscribe("wf-1")
        q2 = emitter.subscribe("wf-1")
        emitter.emit("wf-1", "multi", {"x": 1})
        e1 = q1.get(timeout=1)
        e2 = q2.get(timeout=1)
        assert e1["event_type"] == "multi"
        assert e2["event_type"] == "multi"

    def test_emit_no_subscribers_no_error(self):
        emitter = AutonomousEventEmitter.instance()
        # Should not raise
        emitter.emit("wf-nonexistent", "orphan", {})

    def test_late_subscriber_replays_recent_agent_activity(self):
        """A page opened mid-run receives recent activity immediately."""
        emitter = AutonomousEventEmitter.instance()
        emitter.emit("wf-1", "agent_activity", {"session_id": "s-1", "type": "assistant"})

        q = emitter.subscribe("wf-1")
        event = q.get_nowait()

        assert event["event_type"] == "agent_activity"
        assert event["data"]["activity_id"]
        assert event["data"]["timestamp"].endswith("+00:00")

    def test_replay_excludes_non_activity_events(self):
        emitter = AutonomousEventEmitter.instance()
        emitter.emit("wf-1", "workflow_updated", {"status": "planning"})

        q = emitter.subscribe("wf-1")

        assert q.empty()

    def test_activity_replay_is_bounded(self):
        emitter = AutonomousEventEmitter.instance()
        for i in range(ACTIVITY_HISTORY_MAX_ITEMS + 5):
            emitter.emit(
                "wf-1",
                "agent_activity",
                {"session_id": "s-1", "type": "assistant", "text": str(i)},
            )

        q = emitter.subscribe("wf-1")
        replayed = [q.get_nowait() for _ in range(q.qsize())]

        assert len(replayed) == ACTIVITY_HISTORY_MAX_ITEMS
        assert replayed[0]["data"]["text"] == "5"

    def test_expired_activity_is_not_replayed(self):
        emitter = AutonomousEventEmitter.instance()
        emitter.emit("wf-1", "agent_activity", {"session_id": "s-1", "type": "assistant"})
        with emitter._emit_lock:
            emitted_at, payload = emitter._activity_history["wf-1"][0]
            emitter._activity_history["wf-1"][0] = (
                emitted_at - ACTIVITY_HISTORY_TTL_SECONDS - 1,
                payload,
            )

        q = emitter.subscribe("wf-1")

        assert q.empty()

    def test_unsubscribe(self):
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-1")
        emitter.unsubscribe("wf-1", q)
        emitter.emit("wf-1", "after_unsub", {"gone": True})
        assert q.empty()

    def test_unsubscribe_nonexistent_queue(self):
        emitter = AutonomousEventEmitter.instance()
        q = queue.Queue()
        # Should not raise
        emitter.unsubscribe("wf-1", q)

    def test_emit_isolated_per_workflow(self):
        emitter = AutonomousEventEmitter.instance()
        q1 = emitter.subscribe("wf-1")
        q2 = emitter.subscribe("wf-2")
        emitter.emit("wf-1", "only_wf1", {})
        assert q2.empty() is not False  # q2 should be empty
        # Actually check properly
        assert q1.empty() is False
        assert q2.empty() is True

    def test_queue_full_drops_event(self):
        """When queue is full, emit silently drops the event without raising."""
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-1")
        # Fill the queue to max capacity
        for i in range(100):
            q.put_nowait({"filler": i})

        # Emitting to a full queue should not raise
        emitter.emit("wf-1", "overflow", {"dropped": True})

        # Verify the overflow event was NOT added — queue size stays at maxsize
        assert q.full(), "Queue should still be full after overflow emit"
        assert q.qsize() == 100, "Queue should remain at max capacity"

        # Verify existing items are intact
        first_item = q.get_nowait()
        assert first_item["filler"] == 0

    def test_thread_safety(self):
        """Test that emit and subscribe are thread-safe."""
        emitter = AutonomousEventEmitter.instance()
        results = []

        def subscribe_and_listen(wf_id):
            q = emitter.subscribe(wf_id)
            try:
                event = q.get(timeout=2)
                results.append((wf_id, event["event_type"]))
            except queue.Empty:
                pass
            finally:
                emitter.unsubscribe(wf_id, q)

        threads = []
        for i in range(5):
            t = threading.Thread(target=subscribe_and_listen, args=(f"wf-{i}",))
            t.start()
            threads.append(t)

        # Give threads time to subscribe
        time.sleep(0.2)

        for i in range(5):
            emitter.emit(f"wf-{i}", "thread_test", {"i": i})

        for t in threads:
            t.join(timeout=3)

        assert len(results) == 5


class TestEventEmitterCleanup:
    """Tests for the TTL-based _cleanup_loop mechanism."""

    def setup_method(self):
        AutonomousEventEmitter._instance = None

    def _run_cleanup_once(self, emitter):
        """Run _cleanup_loop so that the cleanup logic executes exactly once, then exits."""
        # _cleanup_loop does: wait(60) → check stop → cleanup → loop
        # We need wait to return (not set) on first call so cleanup runs,
        # then set the stop event so the loop exits on the next iteration.
        call_count = [0]

        def fast_wait(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 2:
                # On second call, set stop event so the loop exits
                emitter._stop_event.set()
            # Always return immediately without blocking
            return emitter._stop_event.is_set()

        with patch.object(emitter._stop_event, "wait", fast_wait):
            emitter._cleanup_loop()

    def test_cleanup_removes_stale_subscribers(self):
        """Stale subscriber queues (past TTL) are removed by cleanup loop."""
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-stale")

        # Manually backdate the last_read timestamp to simulate expiry
        with emitter._emit_lock:
            subscribers = emitter._queues.get("wf-stale", [])
            for i, (mq, _ts) in enumerate(subscribers):
                if mq is q:
                    subscribers[i] = (mq, time.time() - 600)
                    break

        self._run_cleanup_once(emitter)

        # The stale queue should have been removed
        assert "wf-stale" not in emitter._queues

    def test_cleanup_keeps_active_subscribers(self):
        """Active subscriber queues (within TTL) are NOT removed by cleanup."""
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-active")

        # mark_read to refresh timestamp (already fresh from subscribe)
        emitter.mark_read("wf-active", q)

        self._run_cleanup_once(emitter)

        # The active queue should still be present
        assert "wf-active" in emitter._queues
        subscriber_queues = [mq for mq, _ in emitter._queues["wf-active"]]
        assert q in subscriber_queues

    def test_cleanup_removes_empty_workflow_entries(self):
        """When all subscribers of a workflow are stale, the workflow key is removed."""
        emitter = AutonomousEventEmitter.instance()
        q1 = emitter.subscribe("wf-all-stale")

        # Backdate all subscribers
        with emitter._emit_lock:
            subscribers = emitter._queues.get("wf-all-stale", [])
            for i, (mq, _ts) in enumerate(subscribers):
                if mq is q1:
                    subscribers[i] = (mq, time.time() - 1000)
                    break

        self._run_cleanup_once(emitter)

        assert "wf-all-stale" not in emitter._queues

    def test_mark_read_prevents_cleanup(self):
        """Calling mark_read refreshes the timestamp, preventing TTL eviction."""
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-refresh")

        # Backdate, then mark_read to refresh
        with emitter._emit_lock:
            subscribers = emitter._queues.get("wf-refresh", [])
            for i, (mq, _ts) in enumerate(subscribers):
                if mq is q:
                    subscribers[i] = (mq, time.time() - 600)
                    break

        # mark_read should refresh the timestamp
        emitter.mark_read("wf-refresh", q)

        self._run_cleanup_once(emitter)

        # Should still be present because mark_read refreshed the timestamp
        assert "wf-refresh" in emitter._queues

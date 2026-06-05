"""Unit tests for AutonomousEventEmitter."""

import queue
import threading

from app.modules.workspace.autonomous.event_emitter import AutonomousEventEmitter


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
        emitter = AutonomousEventEmitter.instance()
        q = emitter.subscribe("wf-1")
        # Fill the queue
        for i in range(100):
            q.put_nowait({"filler": i})
        # This should not raise, just log warning
        emitter.emit("wf-1", "overflow", {"dropped": True})

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
        import time

        time.sleep(0.2)

        for i in range(5):
            emitter.emit(f"wf-{i}", "thread_test", {"i": i})

        for t in threads:
            t.join(timeout=3)

        assert len(results) == 5

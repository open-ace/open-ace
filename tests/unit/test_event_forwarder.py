"""#3046: scheduler-side SSE event forwarder (emitter → web ingest route).

#2187 split the scheduler into its own process while AutonomousEventEmitter
stayed an in-process singleton. These tests pin the forwarder half of the fix:
bounded buffer semantics, count+byte batch caps, drop rules (oldest, expired,
oversized, HTTP-rejected), retry-without-evicting-newest on connection
failures, and the hard rule that a disabled forwarder never touches HTTP.
"""

from __future__ import annotations

import json as jsonlib
import time
from unittest.mock import MagicMock

import pytest
import requests

from app.modules.workspace.autonomous import event_emitter as em_mod
from app.modules.workspace.autonomous.event_emitter import (
    FORWARD_BATCH_MAX_BYTES,
    FORWARD_BUFFER_MAX_EVENTS,
    FORWARD_EVENT_MAX_BYTES,
    FORWARD_EVENT_TTL_SECONDS,
    AutonomousEventEmitter,
)

pytestmark = [pytest.mark.issue(3046)]

URL = "http://127.0.0.1:19888/api/autonomous/internal/events/ingest"
SECRET = "test-ingest-secret"


@pytest.fixture
def emitter():
    em = AutonomousEventEmitter()
    yield em
    em.disable_remote_forwarding()
    if em._forward_thread is not None:
        em._forward_thread.join(timeout=2)


def _wait_for(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _enqueue(em, payload):
    em._forward_enqueue(payload)


def _buffer_len(em):
    with em._forward_lock:
        return len(em._forward_buffer)


def test_disabled_forwarder_never_posts(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("requests.post must not be called when forwarding is disabled")

    monkeypatch.setattr(requests, "post", _boom)
    em = AutonomousEventEmitter()
    try:
        em.emit("wf-1", "status_change", {"status": "planning"})
    finally:
        em.disable_remote_forwarding()
    assert em._forward_url is None
    assert _buffer_len(em) == 0


def test_wire_body_is_not_ascii_escaped(monkeypatch, emitter):
    # requests' json= helper re-serializes with ensure_ascii=True, inflating
    # CJK/emoji payloads 2-3x past the byte caps the buffer accounts for. The
    # sender must serialize with ensure_ascii=False so wire bytes == accounted
    # bytes (review MAJOR on PR #3047).
    posted = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kwargs: (posted.append((url, kwargs)), MagicMock(status_code=200))[1],
    )
    emitter.enable_remote_forwarding(URL, SECRET)
    emitter.emit("wf-1", "agent_activity", {"text": "中文活动 ✓"})
    assert _wait_for(lambda: len(posted) >= 1)
    _url, kwargs = posted[0]
    body = kwargs["data"]
    assert "中文活动".encode() in body  # raw UTF-8, not \uXXXX escapes
    assert b"\\u4e2d" not in body
    assert kwargs["headers"]["Content-Type"] == "application/json"


def test_buffer_total_byte_cap_drops_oldest(emitter):
    emitter._forward_url = URL
    chunk = "z" * 100_000
    # 100 events × ~100KB ≈ 10MB > 8MB total cap → oldest dropped along the way
    for i in range(100):
        _enqueue(emitter, {"workflow_id": f"wf-{i}", "event_type": "e", "data": {"text": chunk}})
    with emitter._forward_lock:
        total = emitter._forward_buffer_bytes
        first = jsonlib.loads(emitter._forward_buffer[0][1])["workflow_id"]
    assert total <= em_mod.FORWARD_BUFFER_MAX_BYTES_TOTAL
    assert first != "wf-0"  # the very oldest entries were evicted


def test_disable_clears_state_and_stops_thread(monkeypatch, emitter):
    emitter.enable_remote_forwarding(URL, SECRET)
    thread = emitter._forward_thread
    emitter.disable_remote_forwarding()
    assert emitter._forward_url is None
    assert emitter._forward_buffer == []
    thread.join(timeout=2)
    assert not thread.is_alive()
    # After disable, emit must not enqueue anything.
    emitter.emit("wf-1", "status_change", {"status": "planning"})
    assert _buffer_len(emitter) == 0


def test_connection_failure_backs_off_between_attempts(monkeypatch, emitter):
    timestamps = []

    def _fail(url, **kwargs):
        timestamps.append(time.time())
        raise requests.ConnectionError("web restarting")

    monkeypatch.setattr(requests, "post", _fail)
    emitter.enable_remote_forwarding(URL, SECRET)
    emitter.emit("wf-1", "status_change", {"status": "planning"})
    assert _wait_for(lambda: len(timestamps) >= 2)
    # The retry after a connection failure waits ≥ FORWARD_BACKOFF_START (1s).
    assert timestamps[1] - timestamps[0] >= 0.9


def test_enabled_emit_forwards_event_with_defaults(monkeypatch, emitter):
    posted = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kwargs: (posted.append((url, kwargs)), MagicMock(status_code=200))[1],
    )
    assert emitter.enable_remote_forwarding(URL, SECRET)
    emitter.emit("wf-1", "agent_activity", {"activity_type": "tool_use", "text": "x"})
    assert _wait_for(lambda: len(posted) >= 1)
    url, kwargs = posted[0]
    assert url == URL
    assert kwargs["headers"]["X-OpenACE-Events-Key"] == SECRET
    events = jsonlib.loads(kwargs["data"])["events"]
    assert len(events) == 1
    assert events[0]["workflow_id"] == "wf-1"
    assert events[0]["event_type"] == "agent_activity"
    # setdefault ran before enqueue, so the forwarded copy keeps one identity.
    assert events[0]["data"]["activity_id"]
    assert events[0]["data"]["timestamp"]


def test_enable_requires_url_and_secret():
    em = AutonomousEventEmitter()
    try:
        assert em.enable_remote_forwarding("", SECRET) is False
        assert em.enable_remote_forwarding(URL, "") is False
        assert em._forward_thread is None
    finally:
        em.disable_remote_forwarding()


def test_double_enable_reuses_single_thread():
    em = AutonomousEventEmitter()
    try:
        assert em.enable_remote_forwarding(URL, SECRET)
        first = em._forward_thread
        assert em.enable_remote_forwarding(URL, SECRET)
        assert em._forward_thread is first
    finally:
        em.disable_remote_forwarding()


def test_oversized_event_is_skipped(emitter):
    emitter._forward_url = URL  # unit-level: exercise enqueue without the thread
    _enqueue(
        emitter,
        {
            "workflow_id": "wf-1",
            "event_type": "agent_activity",
            "data": {"text": "x" * (FORWARD_EVENT_MAX_BYTES + 1)},
        },
    )
    assert _buffer_len(emitter) == 0


def test_buffer_overflow_drops_oldest(emitter):
    emitter._forward_url = URL
    for i in range(FORWARD_BUFFER_MAX_EVENTS + 5):
        _enqueue(emitter, {"workflow_id": f"wf-{i}", "event_type": "e", "data": {}})
    assert _buffer_len(emitter) == FORWARD_BUFFER_MAX_EVENTS
    with emitter._forward_lock:
        first = emitter._forward_buffer[0]
    assert jsonlib.loads(first[1])["workflow_id"] == "wf-5"  # oldest five dropped


def test_take_batch_respects_byte_cap(emitter):
    emitter._forward_url = URL
    # Each event is under FORWARD_EVENT_MAX_BYTES, but three together exceed
    # FORWARD_BATCH_MAX_BYTES — the batch must stop at two and leave the third.
    big = "y" * 200_001
    for wf in ("wf-a", "wf-b", "wf-c"):
        _enqueue(emitter, {"workflow_id": wf, "event_type": "e", "data": {"text": big}})
    batch = emitter._forward_take_batch()
    assert len(batch) == 2
    assert jsonlib.loads(batch[0][1])["workflow_id"] == "wf-a"
    assert jsonlib.loads(batch[1][1])["workflow_id"] == "wf-b"
    assert _buffer_len(emitter) == 1


def test_take_batch_drops_expired_entries(emitter):
    emitter._forward_url = URL
    emitter._forward_buffer.append(
        (time.time() - FORWARD_EVENT_TTL_SECONDS - 30, '{"workflow_id": "old"}', 22)
    )
    _enqueue(emitter, {"workflow_id": "wf-new", "event_type": "e", "data": {}})
    batch = emitter._forward_take_batch()
    assert [jsonlib.loads(blob)["workflow_id"] for _ts, blob, _size in batch] == ["wf-new"]


def test_requeue_keeps_newest_events(emitter):
    # Buffer one short of full; requeuing two undelivered entries overflows by
    # one, and the overflow drops the OLDEST overall (a requeued entry), never
    # a newer buffered event.
    old_batch = [(time.time(), '{"i": "old-%d"}' % i, 12) for i in range(2)]
    with emitter._forward_lock:
        emitter._forward_buffer = [
            (time.time(), '{"i": "new-%d"}' % i, 12) for i in range(FORWARD_BUFFER_MAX_EVENTS - 1)
        ]
    emitter._forward_requeue(old_batch)
    assert _buffer_len(emitter) == FORWARD_BUFFER_MAX_EVENTS
    with emitter._forward_lock:
        first = jsonlib.loads(emitter._forward_buffer[0][1])
        last = jsonlib.loads(emitter._forward_buffer[-1][1])
    assert first["i"] == "old-1"  # the older requeued entry survived in order
    assert last["i"] == "new-%d" % (FORWARD_BUFFER_MAX_EVENTS - 2)


def test_http_error_drops_batch(monkeypatch, emitter):
    posted = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kwargs: (posted.append(1), MagicMock(status_code=413))[1],
    )
    emitter.enable_remote_forwarding(URL, SECRET)
    emitter.emit("wf-1", "status_change", {"status": "planning"})
    assert _wait_for(lambda: len(posted) >= 1)
    assert _wait_for(lambda: _buffer_len(emitter) == 0)  # dropped, not requeued


def test_connection_failure_requeues(monkeypatch, emitter):
    posted = []

    def _fail(url, **kwargs):
        posted.append(1)
        raise requests.ConnectionError("web restarting")

    monkeypatch.setattr(requests, "post", _fail)
    emitter.enable_remote_forwarding(URL, SECRET)
    emitter.emit("wf-1", "status_change", {"status": "planning"})
    assert _wait_for(lambda: len(posted) >= 1)
    # The batch came back to the buffer for retry (at-least-once).
    assert _wait_for(lambda: _buffer_len(emitter) >= 1)


def test_emit_never_raises_from_forwarding(monkeypatch, emitter):
    def _boom(*_args, **_kwargs):
        raise AssertionError("enqueue must be reached to fail")

    monkeypatch.setattr(em_mod, "json", _boom)  # break serialization inside enqueue
    emitter._forward_url = URL
    emitter.emit("wf-1", "status_change", {"status": "planning"})  # must not raise
    monkeypatch.undo()
    assert _buffer_len(emitter) == 0

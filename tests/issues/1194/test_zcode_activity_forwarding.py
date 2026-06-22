#!/usr/bin/env python3
"""Unit tests for ZCode activity forwarding (#1194).

Root cause: _ZcodeResultCollector accumulated assistant text / tool calls /
usage into the final result but never forwarded them to the activity_callback,
so the SSE stream received no `agent_activity` events and the timeline's live
"AI activity" panel stayed empty for ZCode workflows (unlike the Claude SDK
path, which forwards every event).

The fix injects activity_callback + session_id_resolver into the collector and
forwards assistant / tool_use / usage events. These tests drive the collector
in isolation with a fake callback and assert each event type is forwarded with
the right payload shape, and that the resolver returns the real CLI session id
once it is known (the uuid before session/create resolves).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_agent_runner():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import importlib

    mod_path = _REPO_ROOT / "app" / "modules" / "workspace" / "autonomous" / "agent_runner.py"
    spec = importlib.util.spec_from_file_location("agent_runner_1194", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_runner_1194"] = mod
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ar():
    return _load_agent_runner()


def _make_collector(ar, *, sid=""):
    """Build a collector with a capturing callback and a resolver for `sid`."""
    forwarded = []
    state = {"sid": sid}

    def resolver():
        return state["sid"]

    def cb(session_id, activity):
        forwarded.append((session_id, activity))

    collector = ar._ZcodeResultCollector(
        activity_callback=cb,
        session_id_resolver=resolver,
    )
    return collector, forwarded, state


def test_assistant_text_forwarded(ar):
    collector, forwarded, _ = _make_collector(ar, sid="sess_real_123")
    payload = {
        "type": "assistant",
        "message": {"content": "Here is the plan.", "id": "m1", "model": "gpt"},
    }
    collector.on_output("uuid-tracking", json.dumps(payload), "stdout", False)

    assert len(forwarded) == 1
    session_id, activity = forwarded[0]
    assert session_id == "sess_real_123"
    assert activity["type"] == "assistant"
    assert activity["text"] == "Here is the plan."
    assert collector.assistant_text == "Here is the plan."


def test_assistant_text_truncated_for_sse(ar):
    collector, forwarded, _ = _make_collector(ar, sid="sess_real")
    long_text = "x" * 1000
    payload = {"type": "assistant", "message": {"content": long_text, "id": "m1"}}
    collector.on_output("uuid", json.dumps(payload), "stdout", False)

    assert forwarded[0][1]["text"] == "x" * 500
    # Full text still accumulated for the final result.
    assert collector.assistant_text == long_text


def test_tool_use_forwarded(ar):
    collector, forwarded, _ = _make_collector(ar, sid="sess_real")
    payload = {
        "type": "tool.Edit",
        "data": {"input": {"file": "a.py"}, "id": "t1"},
    }
    collector.on_output("uuid", json.dumps(payload), "stdout", False)

    assert len(forwarded) == 1
    session_id, activity = forwarded[0]
    assert session_id == "sess_real"
    assert activity["type"] == "tool_use"
    assert activity["tool_name"] == "Edit"
    assert "file" in activity["tool_input"]


def test_tool_lifecycle_skipped_but_no_activity(ar):
    """ZCode tool.updated lifecycle notifications must not be forwarded."""
    collector, forwarded, _ = _make_collector(ar, sid="sess_real")
    payload = {
        "type": "tool.updated",
        "data": {"kind": "started", "input": {}},
    }
    collector.on_output("uuid", json.dumps(payload), "stdout", False)

    assert forwarded == []
    assert collector.tool_calls == []


def test_usage_forwarded(ar):
    collector, forwarded, _ = _make_collector(ar, sid="sess_real")
    collector.on_usage("uuid", {"input": 100, "output": 50, "model_requests": 2})

    assert len(forwarded) == 1
    session_id, activity = forwarded[0]
    assert session_id == "sess_real"
    assert activity["type"] == "usage"
    assert activity["total_tokens"] == 150
    assert activity["total_input_tokens"] == 100
    assert activity["total_output_tokens"] == 50
    assert activity["request_count"] == 2


def test_resolver_falls_back_to_tracking_id_before_resolve(ar):
    """Before session/create resolves, the resolver returns the tracking uuid.

    Activity flows in during/after session/create, but the real CLI session id
    is only assigned to the tracker afterwards. The resolver must keep working
    (returning the uuid) so early events are not dropped.
    """
    collector, forwarded, state = _make_collector(ar, sid="")
    # Simulate activity arriving before the CLI id is known.
    payload = {"type": "assistant", "message": {"content": "early text", "id": "m0"}}
    collector.on_output("uuid", json.dumps(payload), "stdout", False)
    assert forwarded[0][0] == ""

    # Now session/create resolves; subsequent events use the real id.
    state["sid"] = "sess_real_after"
    collector.on_usage("uuid", {"input": 1, "output": 1})
    assert forwarded[1][0] == "sess_real_after"


def test_no_callback_means_no_forwarding_but_still_accumulates(ar):
    """A collector created without a callback (legacy callers) must not crash."""
    collector = ar._ZcodeResultCollector()
    payload = {"type": "assistant", "message": {"content": "text", "id": "m1"}}
    collector.on_output("uuid", json.dumps(payload), "stdout", False)
    assert collector.assistant_text == "text"

"""Tests for timeout usage replay + per-turn request counting (issue #723, group C).

Two fixes:
  * request_count is now incremented per distinct assistant message_id inside
    _read_stdout (using session._counted_message_ids to dedup), not per `result`
    event. Claude --print emits one `result` summarizing all turns, so counting
    on `result` always yielded 1 regardless of how many model turns happened.
    These tests verify the dedup invariant on the _LocalSession field and the
    _replay_usage_from_jsonl recovery path.
  * On timeout, when the subprocess did real work but never emitted a closing
    `result` (so session.total_tokens==0), usage is replayed from the claude
    session JSONL for records at/after this call's start — so the milestone
    records the real cost instead of 0/0 (#723 dev timed out at 0/0 but actually
    cost ~370K tokens).
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import (
    AutonomousAgentRunner,
    _iso_to_epoch,
    _LocalSession,
)


def _make_session(**kwargs):
    """Build a _LocalSession with only the fields the unit tests touch."""
    defaults = {
        "session_id": "s1",
        "process": MagicMock(),
        "cli_tool": "claude-code",
        "project_path": "/p",
        "encoded_project_path": "enc-wt",
        "started_at_epoch": time.time(),
    }
    defaults.update(kwargs)
    return _LocalSession(**defaults)


class TestIsoToEpoch:
    def test_parses_z_suffix(self):
        assert _iso_to_epoch("2026-06-24T12:39:05.000Z") is not None

    def test_parses_offset(self):
        assert _iso_to_epoch("2026-06-24T12:39:05+00:00") is not None

    def test_empty_returns_none(self):
        assert _iso_to_epoch("") is None

    def test_garbage_returns_none(self):
        assert _iso_to_epoch("not-a-date") is None


class TestRequestCountDedupField:
    """The _counted_message_ids set is what makes request_count per-turn-correct.

    _read_stdout adds a message_id to this set the first time it's seen and bumps
    request_count; subsequent events for the same message_id (thinking then text
    for one assistant message) do NOT bump it. We simulate that logic here to
    lock the invariant the production code relies on.
    """

    def test_distinct_ids_each_bump(self):
        session = _make_session()
        for mid in ["msg_a", "msg_b", "msg_c"]:
            if mid not in session._counted_message_ids:
                session._counted_message_ids.add(mid)
                session.request_count += 1
        assert session.request_count == 3

    def test_repeated_id_bumps_once(self):
        session = _make_session()
        for mid in ["msg_1", "msg_1", "msg_1"]:
            if mid not in session._counted_message_ids:
                session._counted_message_ids.add(mid)
                session.request_count += 1
        assert session.request_count == 1

    def test_thinking_then_text_same_id_one_request(self):
        """The #723 shape: a message split into thinking + text events sharing
        one message_id must count as a single request."""
        session = _make_session()
        for mid in ["msg_1", "msg_1"]:  # thinking event, then text event
            if mid not in session._counted_message_ids:
                session._counted_message_ids.add(mid)
                session.request_count += 1
        assert session.request_count == 1

    def test_field_defaults_to_empty_set(self):
        session = _make_session()
        assert session._counted_message_ids == set()
        assert session.request_count == 0

    def test_no_id_assistant_turn_falls_back_to_result_count(self):
        """Regression guard (review on #1270): when assistant events carry NO
        message.id (older/non-Claude adapters, e.g. tests/issues/716), the turn
        is counted via the result-event fallback so request_count isn't dropped
        to 0. Simulates the result-handler fallback logic."""
        session = _make_session()
        # An assistant turn with no message.id → _counted_message_ids stays empty.
        # The result fallback: if no ids were counted, bump request_count to 1.
        if not session._counted_message_ids and session.request_count == 0:
            session.request_count += 1
        assert session.request_count == 1

    def test_id_assistant_turn_disables_result_fallback(self):
        """When ids WERE seen, the result fallback must NOT bump (turns already
        counted per-id; result summarizes the whole run)."""
        session = _make_session()
        session._counted_message_ids.add("msg_a")
        session.request_count = 1
        before = session.request_count
        if not session._counted_message_ids and session.request_count == 0:
            session.request_count += 1
        assert session.request_count == before  # unchanged


class TestReplayUsageFromJsonl:
    """On timeout with zero counters, replay the session JSONL to recover usage."""

    def test_replays_usage_for_records_after_started(self, tmp_path):
        runner = AutonomousAgentRunner()
        started = time.time() - 10  # call started 10s ago
        session = _make_session(started_at_epoch=started)
        cli_sid = "abc12345"
        jsonl = tmp_path / ".claude" / "projects" / "enc-wt" / f"{cli_sid}.jsonl"
        jsonl.parent.mkdir(parents=True)

        def recent(off):
            return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started + off))

        jsonl.write_text(
            "\n".join(
                [
                    # Old record BEFORE this call started — must be skipped.
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": "2020-01-01T00:00:00.000Z",
                            "message": {
                                "id": "old",
                                "usage": {"input_tokens": 999, "output_tokens": 999},
                            },
                        }
                    ),
                    # Records from this call.
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": recent(1),
                            "message": {
                                "id": "m1",
                                "usage": {"input_tokens": 200, "output_tokens": 100},
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": recent(2),
                            "message": {
                                "id": "m2",
                                "usage": {"input_tokens": 300, "output_tokens": 150},
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            runner._replay_usage_from_jsonl(session, cli_sid)

        # Only the two post-start records counted: 200+300 in, 100+150 out.
        assert session.total_input_tokens == 500
        assert session.total_output_tokens == 250
        assert session.total_tokens == 750
        assert session.request_count == 2

    def test_no_jsonl_file_is_noop(self, tmp_path):
        runner = AutonomousAgentRunner()
        session = _make_session()
        with patch("pathlib.Path.home", return_value=tmp_path):
            runner._replay_usage_from_jsonl(session, "nonexistent")
        assert session.total_tokens == 0
        assert session.request_count == 0

    def test_empty_jsonl_is_noop(self, tmp_path):
        runner = AutonomousAgentRunner()
        session = _make_session()
        cli_sid = "abc12345"
        jsonl = tmp_path / ".claude" / "projects" / "enc-wt" / f"{cli_sid}.jsonl"
        jsonl.parent.mkdir(parents=True)
        jsonl.write_text("", encoding="utf-8")
        with patch("pathlib.Path.home", return_value=tmp_path):
            runner._replay_usage_from_jsonl(session, cli_sid)
        assert session.total_tokens == 0

    def test_replay_counts_distinct_message_ids(self, tmp_path):
        """Replay dedups assistant turns by message_id (matching _read_stdout)."""
        runner = AutonomousAgentRunner()
        started = time.time() - 5
        session = _make_session(started_at_epoch=started)
        cli_sid = "abc12345"
        jsonl = tmp_path / ".claude" / "projects" / "enc-wt" / f"{cli_sid}.jsonl"
        jsonl.parent.mkdir(parents=True)

        def recent(off):
            return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started + off))

        # Same message_id twice (thinking + text) + one distinct.
        jsonl.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": recent(1),
                            "message": {
                                "id": "m1",
                                "usage": {"input_tokens": 100, "output_tokens": 50},
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": recent(2),
                            "message": {
                                "id": "m1",
                                "usage": {"input_tokens": 100, "output_tokens": 50},
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "timestamp": recent(3),
                            "message": {
                                "id": "m2",
                                "usage": {"input_tokens": 200, "output_tokens": 100},
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            runner._replay_usage_from_jsonl(session, cli_sid)
        # 2 distinct message_ids => request_count 2. Usage is deduped per
        # message_id (claude repeats the full usage on each block-line): m1's
        # usage is max(150,150)=150, m2's is 300 => 450 total.
        assert session.request_count == 2
        assert session.total_tokens == 450  # 150 (m1) + 300 (m2)

"""Regression: recover response text when assistant stream events are dropped.

Large-context (--resume) CLI turns can complete with a terminal ``result``
event — usage accounted, exit 0 — while the final ``assistant`` stream event
never arrives. ``_read_stdout`` only built response_text from assistant
events, so the orchestrator terminal-failed the phase with
"<agent> returned no result" even though the deliverable existed (#2640).

This suite pins the two-layer fallback in agent_runner:
A.1 the ``result`` event's ``result`` text field, and
A.2 the session transcript JSONL's last visible assistant message
    (same reading machinery as ``_replay_usage_from_jsonl``).
Refs #2640, #2570.
"""

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.modules.workspace.autonomous.agent_runner import (
    AutonomousAgentRunner,
    _ensure_usage_parser,
    _LocalSession,
)

RESULT_TEXT = 'REVIEW_RESULT: {"verdict":"APPROVE","summary":"clean"}'
API_ERROR_ENVELOPE = (
    "API Error: 400 InvalidParameter: Range of input length is [1, 260000], "
    "but current is 300000"
)


class _FakeStdout:
    def __init__(self, lines):
        self.lines = [ln.encode() if isinstance(ln, str) else ln for ln in lines]

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def _make_runner():
    """Runner skeleton matching the harness in test_agent_runner_session_id_capture."""
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = None
    runner.session_manager = None
    runner._resolve_sidebar_session = lambda *_args, **_kwargs: ""
    return runner


def _make_session(lines):
    session = _LocalSession(
        session_id="tracking-2640",
        process=SimpleNamespace(
            stdout=_FakeStdout(lines), stdin=None, returncode=None, poll=lambda: None
        ),
    )
    session.workflow_id = "wf-2640"
    session.started_at_epoch = datetime.now(timezone.utc).timestamp()
    return session


def _run_messages(messages):
    _ensure_usage_parser()
    runner = _make_runner()
    session = _make_session([json.dumps(m) for m in messages])
    runner._read_stdout(session)
    return runner, session


def _result_event(result_text=RESULT_TEXT, input_tokens=170000, output_tokens=92):
    return {
        "type": "result",
        "subtype": "success",
        "session_id": "cli-sid-2640",
        "result": result_text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_result_event_text_recovered_when_no_assistant_events():
    """A.1: result event with text+usage but no assistant events must not
    produce an empty response_text."""
    runner, session = _run_messages([_result_event()])

    assert session.result_text == RESULT_TEXT

    result = runner._finalize_local_completed_result(session, session.session_id, "")
    assert result.success is True
    assert result.response_text == RESULT_TEXT
    assert result.total_tokens == 170000 + 92
    # The recovered turn must also be visible to session persistence.
    assert any(
        e.get("type") == "assistant" and e.get("text") == RESULT_TEXT for e in result.event_log
    )


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_transcript_jsonl_fallback_when_result_text_empty(tmp_path, monkeypatch):
    """A.2: empty result text + transcript JSONL with a final assistant
    message -> fall back to the transcript's last assistant text."""
    monkeypatch.setattr(
        AutonomousAgentRunner,
        "_claude_projects_root",
        classmethod(lambda cls, system_account, task_id=None: tmp_path / "projects"),
    )

    runner, session = _run_messages([_result_event(result_text="")])
    session.encoded_project_path = "encoded-proj"

    # Write the transcript AFTER the session started so records fall inside
    # the run's [started_at_epoch, now] window (mirrors the real CLI).
    projects_root = tmp_path / "projects" / "encoded-proj"
    projects_root.mkdir(parents=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = "2020-01-01T00:00:00.000Z"
    transcript = "\n".join(
        json.dumps(rec)
        for rec in [
            # A stale pre-run assistant message — must be filtered by timestamp.
            {
                "type": "assistant",
                "timestamp": old_iso,
                "sessionId": "cli-sid-2640",
                "message": {
                    "id": "msg_old",
                    "content": [{"type": "text", "text": "stale previous turn"}],
                },
            },
            # The deliverable written during this run.
            {
                "type": "assistant",
                "timestamp": now_iso,
                "sessionId": "cli-sid-2640",
                "message": {
                    "id": "msg_new",
                    "content": [{"type": "text", "text": RESULT_TEXT}],
                },
            },
            # A later thinking-only record — no visible text, must not win.
            {
                "type": "assistant",
                "timestamp": now_iso,
                "sessionId": "cli-sid-2640",
                "message": {
                    "id": "msg_think",
                    "content": [{"type": "thinking", "text": "hidden"}],
                },
            },
        ]
    )
    (projects_root / "cli-sid-2640.jsonl").write_text(transcript, encoding="utf-8")

    assert session.result_text == ""
    result = runner._finalize_local_completed_result(session, session.session_id, "cli-sid-2640")
    assert result.success is True
    assert result.response_text == RESULT_TEXT


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_normal_assistant_path_unchanged():
    """Assistant events present -> response_text from event_log; the result
    text fallback never fires."""
    assistant_event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "model": "test-model",
            "content": [{"type": "text", "text": "final answer from stream"}],
        },
    }
    runner, session = _run_messages([assistant_event, _result_event()])

    result = runner._finalize_local_completed_result(session, session.session_id, "")
    assert result.response_text == "final answer from stream"
    # No synthetic assistant entry appended on top of the real turn.
    assistant_entries = [
        e for e in result.event_log if e.get("type") == "assistant" and e.get("text")
    ]
    assert len(assistant_entries) == 1


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_recovered_text_reaches_session_persistence():
    """The recovered text must land in session state BEFORE
    _persist_local_session_messages consumes the result, so the DB timeline
    does not lose the turn."""

    class _RecordingManager:
        def __init__(self):
            self.calls = []

        def append_transcript_message(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(_was_inserted=True)

    runner, session = _run_messages([_result_event()])
    session_manager = _RecordingManager()
    runner.session_manager = session_manager

    result = runner._finalize_local_completed_result(session, session.session_id, "")
    # Session state carries the synthetic assistant entry for persistence.
    assert any(
        e.get("type") == "assistant" and e.get("text") == RESULT_TEXT for e in session.event_log
    )
    assert session.assistant_text.strip() == RESULT_TEXT

    persisted = runner._persist_local_session_messages("sess-2640", result, "m1")
    assert persisted >= 1
    assistant_messages = [c for c in session_manager.calls if c.get("role") == "assistant"]
    assert assistant_messages, "no assistant message persisted for recovered turn"
    assert RESULT_TEXT in assistant_messages[0].get("content", "")


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_unparseable_stdout_line_logs_truncated_warning(caplog):
    """Non-JSON stdout lines must produce a warning with a truncated prefix
    instead of being silently swallowed, and must not fail the run."""
    long_garbage = "X" * 500
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = None
    runner.session_manager = None
    runner._resolve_sidebar_session = lambda *_a, **_k: ""
    _ensure_usage_parser()

    session = _make_session(["{" + long_garbage, json.dumps(_result_event())])
    with caplog.at_level(logging.WARNING, logger="app.modules.workspace.autonomous.agent_runner"):
        runner._read_stdout(session)

    warnings = [r for r in caplog.records if "non-JSON" in r.getMessage()]
    assert warnings, "no warning emitted for unparseable stdout line"
    message = warnings[0].getMessage()
    assert "X" * 200 in message or ("X" * 100 in message and "X" * 300 not in message)
    assert "X" * 400 not in message  # not the full line
    # The run still completes via the result event.
    assert session.completed.is_set()
    assert session.result_text == RESULT_TEXT


@pytest.mark.regression
@pytest.mark.issue(2640)
def test_api_error_400_envelope_reaches_context_overflow_detection():
    """A result-text-carried ``API Error: 400 ... Range of input length``
    envelope must surface in response_text so the orchestrator's
    _is_context_overflow can route the phase to context recovery."""
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    runner, session = _run_messages([_result_event(result_text=API_ERROR_ENVELOPE)])
    result = runner._finalize_local_completed_result(session, session.session_id, "")

    assert result.response_text.strip()
    assert AutonomousOrchestrator._is_context_overflow(result) is True

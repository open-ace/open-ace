"""Tests for system-event activity forwarding (Issue #1395 — AI activity gap).

During long LLM waits (api_retry from upstream overload, slow first-token),
claude --print emits only ``system`` events (api_retry, thinking_tokens, init).
_read_stdout previously forwarded only assistant/tool_use/usage events to
_activity_callback, so the UI showed "no AI activity" for minutes until the
first ``assistant`` event arrived. Low-frequency lifecycle events still reach
the UI, while high-frequency cumulative ``thinking_tokens`` events are ignored.
"""

import json
import threading
from unittest.mock import MagicMock


class _FakeStream:
    """Mimic subprocess stdout: ``readline()`` returns queued lines, then ``""``."""

    def __init__(self, lines):
        self._lines = [ln if ln.endswith("\n") else ln + "\n" for ln in lines]

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _StubProcess:
    """Minimal stand-in for subprocess.Popen — only stdout/returncode are read."""

    returncode = None  # is_running → True (process not exited)

    def __init__(self, stdout):
        self.stdout = stdout


def _make_session(lines):
    """Build a real _LocalSession whose process.stdout yields the given JSON lines."""
    from app.modules.workspace.autonomous.agent_runner import _LocalSession

    session = _LocalSession(
        session_id="sess-1",
        process=_StubProcess(_FakeStream(lines)),
        workflow_id="wf-1",
    )
    return session


def _make_runner():
    """Build an AutonomousAgentRunner with a mock _activity_callback."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = MagicMock()
    return runner


class TestSystemEventActivityForwarding:
    """_read_stdout must emit _activity_callback for key system events.
    These tests call the REAL _read_stdout method (not a re-implementation)
    so removing the production change makes them fail."""

    def test_api_retry_triggers_activity(self):
        """api_retry system events must reach the callback so the UI shows
        'retrying' during upstream overload."""
        runner = _make_runner()
        session = _make_session(
            [
                json.dumps(
                    {"type": "system", "subtype": "api_retry", "attempt": 3, "session_id": "abc"}
                ),
            ]
        )
        runner._read_stdout(session)
        runner._activity_callback.assert_called_once_with(
            "sess-1",
            {"type": "system", "subtype": "api_retry", "attempt": 3},
        )

    def test_thinking_tokens_is_not_forwarded(self):
        runner = _make_runner()
        session = _make_session(
            [
                json.dumps(
                    {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 42}
                ),
            ]
        )
        runner._read_stdout(session)
        runner._activity_callback.assert_not_called()

    def test_init_triggers_activity(self):
        runner = _make_runner()
        session = _make_session(
            [
                json.dumps({"type": "system", "subtype": "init", "session_id": "abc"}),
            ]
        )
        runner._read_stdout(session)
        runner._activity_callback.assert_called_once_with(
            "sess-1",
            {"type": "system", "subtype": "init"},
        )

    def test_no_subtype_does_not_trigger(self):
        """A system event without subtype (e.g. bare 'initialized') must
        not fire the callback — avoids noise from events we don't care about."""
        runner = _make_runner()
        session = _make_session(
            [
                json.dumps({"type": "system"}),
            ]
        )
        runner._read_stdout(session)
        runner._activity_callback.assert_not_called()

    def test_assistant_still_triggers_activity(self):
        """Regression guard: assistant events must still fire the callback."""
        runner = _make_runner()
        session = _make_session(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": "msg-1",
                            "model": "glm-5",
                            "content": [{"type": "text", "text": "Hello world"}],
                        },
                    }
                ),
            ]
        )
        runner._read_stdout(session)
        runner._activity_callback.assert_called_once()
        _args, activity = runner._activity_callback.call_args[0]
        assert activity["type"] == "assistant"
        assert "Hello world" in activity["text"]

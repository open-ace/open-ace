"""Regression: capture cli_session_id from the system/init stream message.

The Claude CLI emits the first stream-json line as
``{"type":"system","subtype":"init","session_id":"<uuid>", ...}``.
agent_runner must capture that session_id at agent start so the orchestrator
tracks the real session in real time, instead of always falling back to
mtime-based JSONL discovery.
"""

import json
from types import SimpleNamespace

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession


def _run_messages(messages):
    """Drive _read_stdout with a fake process emitting the given JSON lines."""

    class FakeStdout:
        def __init__(self, lines):
            self.lines = [json.dumps(ln).encode() for ln in lines]

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._activity_callback = None
    # _read_stdout calls _resolve_sidebar_session once cli_session_id becomes
    # truthy (lines ~4158-4163); under __new__ the runner has no session_manager,
    # so the real resolver would AttributeError. Stub it (mirrors the existing
    # harness in test_autonomous_ci_guardrails.py). The capture path itself —
    # _capture_cli_session_id — is intentionally left REAL: the bug is the gate
    # that decides whether to call it.
    runner._resolve_sidebar_session = lambda *_args, **_kwargs: ""
    # _read_stdout's `finally` (lines ~4178-4183) calls session.process.poll()
    # and reads .returncode when no `result` set completed. Provide both so the
    # test exits cleanly instead of AttributeError (matches the existing harness).
    session = _LocalSession(
        session_id="tracking-1",
        process=SimpleNamespace(
            stdout=FakeStdout(messages), stdin=None, returncode=None, poll=lambda: None
        ),
    )
    session.workflow_id = "wf-1"
    runner._read_stdout(session)
    return session


def test_system_init_captures_cli_session_id():
    session = _run_messages(
        [
            {"type": "system", "subtype": "init", "session_id": "cli-sid-1234", "uuid": "u1"},
        ]
    )
    assert session.cli_session_id == "cli-sid-1234"


def test_legacy_initialized_subtype_still_captures():
    """Older CLI versions used subtype 'initialized' — keep supporting them."""
    session = _run_messages(
        [
            {"type": "system", "subtype": "initialized", "session_id": "cli-sid-5678"},
        ]
    )
    assert session.cli_session_id == "cli-sid-5678"

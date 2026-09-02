"""Agent-state persistence declaration (#3237).

A provider that does not say its HOME survives between turns must not be
trusted to resume: ``--resume`` would be sent into a fresh HOME and the CLI
would answer ``No conversation found with session ID: <id>`` — verified
against a real CLI (2.1.170). The default is therefore the refusing one.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import (
    AGENT_STATE_CARRIED,
    AGENT_STATE_EPHEMERAL,
    AGENT_STATE_PERSISTS,
    agent_state_persistence,
)
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]


def test_an_undeclared_provider_is_treated_as_ephemeral():
    class Undeclared:
        pass

    assert agent_state_persistence(Undeclared()) == AGENT_STATE_EPHEMERAL


def test_an_unrecognised_declaration_is_treated_as_ephemeral():
    """A typo must not buy the ability to resume."""

    class Typo:
        agent_state_persistence = "persistent"  # not one of the three

    assert agent_state_persistence(Typo()) == AGENT_STATE_EPHEMERAL


def test_legacy_declares_persists_because_its_home_is_the_hosts():
    assert agent_state_persistence(LegacyPosixProvider()) == AGENT_STATE_PERSISTS


def test_remote_declares_persists_because_the_remote_home_is_durable():
    # Read off the class: __init__ requires a live RemoteSessionManager, and the
    # declaration is a class attribute precisely so it needs no instance.
    assert agent_state_persistence(RemoteMachineProvider) == AGENT_STATE_PERSISTS


def test_the_fake_declares_persists_so_existing_resume_tests_still_hold():
    assert agent_state_persistence(FakeSandboxProvider()) == AGENT_STATE_PERSISTS


def test_the_three_states_are_distinct():
    assert len({AGENT_STATE_PERSISTS, AGENT_STATE_CARRIED, AGENT_STATE_EPHEMERAL}) == 3


class _ClaudeLikeAdapter:
    """Minimal stand-in with the two methods _build_agent_argv calls."""

    def build_start_args(self, session_id, project_path, model, **kw):
        args = ["claude", "--print"]
        if kw.get("resume"):
            args += ["--resume", session_id]
        return args

    def provides_full_command(self):
        return True

    def get_executable_name(self):
        return "claude"


def test_argv_carries_resume_only_when_asked():
    """The whole point: --resume must not be sent into an empty HOME."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    adapter = _ClaudeLikeAdapter()

    with_state = runner._build_agent_argv(
        adapter, "sid-1", "/workspace", "opus", None, None, resume=True
    )
    without_state = runner._build_agent_argv(
        adapter, "sid-1", "/workspace", "opus", None, None, resume=False
    )

    assert "--resume" in with_state
    assert "sid-1" in with_state
    assert "--resume" not in without_state


# ── the planning decision, one test per row of the spec's §5.5 table ──


def _runner(tmp_path=None):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    if tmp_path is not None:
        runner._agent_state_store = AgentStateStore(root=str(tmp_path))
    return runner


class _Ephemeral:
    pass


class _Carried:
    agent_state_persistence = AGENT_STATE_CARRIED


def test_a_resuming_turn_on_an_ephemeral_provider_is_refused_before_create():
    """Free by construction: no sandbox, no tokens, no wasted invocation.

    Forward-looking, and inert today — no shipped provider is `ephemeral`
    (Legacy/Remote/Fake declare `persists`, OpenSandbox `carried`). What fixes
    today's wasted run under OpenSandbox is the store, not this branch. This
    guard is what stops the NEXT provider added without the seam from silently
    reintroducing the bug: it fails closed instead.
    """
    plan = _runner()._plan_agent_state(
        _Ephemeral(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is True
    assert plan.reason_code == "agent_state_unavailable"


def test_a_non_resuming_turn_on_an_ephemeral_provider_is_fine():
    plan = _runner()._plan_agent_state(
        _Ephemeral(), workflow_id="wf-1", tracking_session_id="sid-1", resume=False
    )
    assert plan.refuse is False
    assert plan.resume is False


def test_a_persisting_provider_resumes_with_no_transfer():
    plan = _runner()._plan_agent_state(
        LegacyPosixProvider(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is False
    assert plan.resume is True
    assert plan.blob is None


def test_a_carried_provider_with_no_stored_state_starts_fresh(tmp_path):
    """Absent is NOT a failure — first turn, or tmpfs cleared by a reboot.

    openace-run-as.sh guards its restore with `if [ -d ... ]` and skips. If
    absent were fail-closed instead, every control-plane restart would kill
    in-flight workflows.
    """
    plan = _runner(tmp_path)._plan_agent_state(
        _Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is False
    assert plan.resume is False
    assert plan.blob is None


def test_a_carried_provider_with_stored_state_plans_to_resume(tmp_path):
    runner = _runner(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", b"TRANSCRIPT\n")

    plan = runner._plan_agent_state(
        _Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is False
    assert plan.resume is True
    assert plan.blob == b"TRANSCRIPT\n"


def test_a_corrupt_slot_is_refused_rather_than_read_as_absent(tmp_path, monkeypatch):
    """Present-but-unreadable is the mis-shaped-tree hazard `exit 70` exists for."""
    runner = _runner(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", b"data")

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_bytes", boom)

    plan = runner._plan_agent_state(
        _Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is True
    assert plan.reason_code == "agent_state_unavailable"


def test_an_unusable_key_is_refused_not_silently_run_without_history(tmp_path):
    plan = _runner(tmp_path)._plan_agent_state(
        _Carried(), workflow_id="../escape", tracking_session_id="sid-1", resume=True
    )
    assert plan.refuse is True
    assert plan.reason_code == "agent_state_unavailable"


# ── the secondary breakage: host-path readers are empty under a sandbox ──


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _session_at(started_at_epoch: float):
    """A minimal _LocalSession for the transcript readers."""
    from app.modules.workspace.autonomous.agent_runner import _LocalSession

    session = _LocalSession(session_id="sid-1", process=None, transport=None)
    session.workflow_id = "wf-1"
    session.started_at_epoch = started_at_epoch
    return session


def _row(text: str, ts: str) -> bytes:
    import json

    return (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": ts,
                "message": {"content": [{"type": "text", "text": text}]},
            }
        )
        + "\n"
    ).encode()


def test_recovery_returns_only_this_turns_final_answer(tmp_path):
    """The carried blob is the WHOLE session line, appended across milestones.

    A reader without the started_at_epoch filter hands back milestones 1..N
    concatenated, and the caller persists that as this milestone's deliverable
    — a plausible-looking wrong answer rather than an honest failure. An
    earlier version of this reader did exactly that, and its own test asserted
    the concatenation; an independent review caught it.
    """
    runner = _runner(tmp_path)
    runner._agent_state_store.put(
        "wf-1",
        "sid-1",
        _row("PLAN FROM MILESTONE 1", "2026-08-31T10:00:00Z")
        + _row("REVIEW FROM MILESTONE 2", "2026-08-31T11:00:00Z")
        + _row("Let me read the file.", "2026-08-31T12:00:30Z")
        + _row("THIS TURNS REAL ANSWER", "2026-08-31T12:00:45Z"),
    )
    session = _session_at(_epoch("2026-08-31T12:00:00Z"))

    recovered = runner._recover_response_text_from_store(session, "wf-1", "sid-1")

    assert recovered == "THIS TURNS REAL ANSWER"
    assert "MILESTONE 1" not in recovered
    assert "MILESTONE 2" not in recovered
    assert (
        "Let me read the file." not in recovered
    ), "intermediate narration was folded into the deliverable"


def test_recovery_matches_the_host_reader(tmp_path):
    """Both readers must agree — they delegate to one parser on purpose."""
    runner = _runner(tmp_path)
    blob = _row("EARLIER MILESTONE", "2026-08-31T10:00:00Z") + _row(
        "THIS ANSWER", "2026-08-31T12:00:45Z"
    )
    runner._agent_state_store.put("wf-1", "sid-1", blob)
    session = _session_at(_epoch("2026-08-31T12:00:00Z"))

    carried = runner._recover_response_text_from_store(session, "wf-1", "sid-1")
    host = runner._last_assistant_text_in_transcript(blob.decode(), session, "sid-1")

    assert carried == host == "THIS ANSWER"


def test_recovery_reads_the_carried_transcript_at_all(tmp_path):
    """The host's ~/.claude/projects is empty for a run inside a sandbox.

    Without this source the large-context recovery net silently no-ops on
    exactly the turns it exists for.
    """
    runner = _runner(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", _row("RECOVERED", "2026-08-31T12:00:45Z"))
    session = _session_at(_epoch("2026-08-31T12:00:00Z"))

    assert runner._recover_response_text_from_store(session, "wf-1", "sid-1") == "RECOVERED"


@pytest.mark.parametrize(
    "blob",
    [b"", b"not json at all\n", b'{"type":"assistant"}\n', b"\n\n"],
)
def test_recovery_never_raises_on_junk(tmp_path, blob):
    """A net that can fail the run it is catching is worse than no net."""
    runner = _runner(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", blob)
    session = _session_at(0.0)

    assert runner._recover_response_text_from_store(session, "wf-1", "sid-1") == ""


def test_recovery_returns_empty_without_ids(tmp_path):
    runner = _runner(tmp_path)
    session = _session_at(0.0)
    assert runner._recover_response_text_from_store(session, "", "sid-1") == ""
    assert runner._recover_response_text_from_store(session, "wf-1", "") == ""


# ── carry is claude-code-specific, and must say so (#3237 review) ──────


def test_a_resume_for_a_tool_the_provider_cannot_carry_is_refused():
    """qwen-code-cli runs on OpenSandbox, but its transcript is not `.claude`.

    `_AGENT_STATE_DIR` is claude-code specific and `_capture_cli_session_id`
    only yields an id for claude-code, so on Qwen the export received an empty
    id and discarded the slot, and the next turn found nothing and started
    cold. Silent continuity loss for a tool the sandbox genuinely supports —
    the exact defect this change exists to remove. Refusing costs nothing at
    this point: no sandbox has been created and no tokens spent.
    """
    plan = _runner()._plan_agent_state(
        _Carried(),
        workflow_id="wf-1",
        tracking_session_id="s-1",
        resume=True,
        cli_tool="qwen-code-cli",
    )

    assert plan.refuse, "a Qwen resume was allowed to proceed into an empty HOME"
    assert plan.resume is False
    assert plan.reason_code == "agent_state_unavailable"
    assert "qwen-code-cli" in plan.detail


def test_claude_code_still_carries(tmp_path):
    """The refusal must not catch the tool the carry was built for."""
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    runner = _runner(tmp_path)
    AgentStateStore(root=str(tmp_path)).put("wf-1", "s-1", b"HISTORY\n")

    plan = runner._plan_agent_state(
        _Carried(),
        workflow_id="wf-1",
        tracking_session_id="s-1",
        resume=True,
        cli_tool="claude-code",
    )

    assert not plan.refuse, plan.detail
    assert plan.resume is True
    assert plan.blob == b"HISTORY\n"

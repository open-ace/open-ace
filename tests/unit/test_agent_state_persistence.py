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

    This is what converts today's guaranteed failed run — --resume into an
    empty HOME, "No conversation found with session ID: <id>", then the #2035
    recovery retrying fresh — into an up-front refusal.
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


def test_response_recovery_reads_the_carried_transcript(tmp_path):
    """The host's ~/.claude/projects is empty for a run inside a sandbox.

    Without this the large-context recovery net silently no-ops on exactly
    the turns it exists for — assistant stream events dropped near the
    context limit.
    """
    runner = _runner(tmp_path)
    runner._agent_state_store.put(
        "wf-1",
        "sid-1",
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"RECOVERED"}]}}\n',
    )

    assert "RECOVERED" in runner._recover_response_text_from_store("wf-1", "sid-1")


def test_response_recovery_concatenates_every_assistant_block(tmp_path):
    runner = _runner(tmp_path)
    runner._agent_state_store.put(
        "wf-1",
        "sid-1",
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"AB"}]}}\n'
        b'{"type":"user","message":{"content":"ignored"}}\n'
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"CD"}]}}\n',
    )

    assert runner._recover_response_text_from_store("wf-1", "sid-1") == "ABCD"


def test_response_recovery_tolerates_a_string_content_block(tmp_path):
    """Older transcript rows carry `content` as a bare string."""
    runner = _runner(tmp_path)
    runner._agent_state_store.put(
        "wf-1", "sid-1", b'{"type":"assistant","message":{"content":"PLAIN"}}\n'
    )

    assert runner._recover_response_text_from_store("wf-1", "sid-1") == "PLAIN"


@pytest.mark.parametrize(
    "blob",
    [b"", b"not json at all\n", b'{"type":"assistant"}\n', b"\n\n"],
)
def test_response_recovery_never_raises_on_junk(tmp_path, blob):
    """A net that can fail the run it is catching is worse than no net."""
    runner = _runner(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", blob)

    assert runner._recover_response_text_from_store("wf-1", "sid-1") == ""


def test_response_recovery_returns_empty_without_ids(tmp_path):
    runner = _runner(tmp_path)
    assert runner._recover_response_text_from_store("", "sid-1") == ""
    assert runner._recover_response_text_from_store("wf-1", "") == ""

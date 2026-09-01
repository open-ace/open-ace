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

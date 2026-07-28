"""Contract tests for the Phase B (#2044) handler decoupling layer."""

from __future__ import annotations

from app.modules.workspace.autonomous.phase_contract import WorkflowContext
from app.modules.workspace.autonomous.phase_host import PhaseDeps, PhaseHandler, PhaseHost


def test_phase_host_protocol_has_narrow_surface():
    """A handler must not depend on the orchestrator concrete class; it sees
    only these members of PhaseHost."""
    for method in [
        "emit_phase_change",
        "session_offsets",
        "cancellation",
        "create_milestone_idempotent",
    ]:
        assert hasattr(PhaseHost, method), f"PhaseHost missing {method}"
    # annotation-only data member lives in __annotations__, not dir()
    assert "workflow_id" in PhaseHost.__annotations__


def test_phase_deps_carries_services_not_orchestrator():
    """PhaseDeps fields are service objects + host, never the orchestrator."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(PhaseDeps)}
    assert field_names == {
        "host",
        "gh",
        "git_workspace",
        "evidence",
        "sandbox",
        "repo",
        "agent_runner",
    }


def test_phase_handler_protocol_has_handle_taking_ctx_deps():
    assert hasattr(PhaseHandler, "handle")

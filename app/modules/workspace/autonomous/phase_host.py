"""Open ACE — Autonomous Phase B (#2044) handler decoupling layer.

Phase A gave phases ``WorkflowContext`` + ``PhaseResult``. Phase B extracts the
complex phases (development/pr_review/merge) into ``phases/*.py`` modules. To
keep those modules independently testable (no ``AutonomousOrchestrator``
instance), they depend on the narrow interfaces defined here instead of the
~10k-line orchestrator concrete class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycles
    import threading

    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.evidence_service import EvidenceService
    from app.modules.workspace.autonomous.git_workspace import GitWorkspaceService
    from app.modules.workspace.autonomous.github_ops import GitHubOps
    from app.modules.workspace.autonomous.phase_contract import PhaseResult, WorkflowContext
    from app.modules.workspace.autonomous.sandbox.provider import SandboxProvider
    from app.repositories.autonomous_repo import AutonomousWorkflowRepository


@runtime_checkable
class PhaseHost(Protocol):
    """The narrow slice of the orchestrator a phase handler may touch.

    Anything not listed here is off-limits to handlers: phase/status writes go
    through ``PhaseResult`` (committed by ``_commit_phase_result``), bookkeeping
    field writes go through the services in ``PhaseDeps``.
    """

    workflow_id: str

    def emit_phase_change(self, payload: dict) -> None:
        """Emit a phase_change event. ``_commit_phase_result`` deliberately does
        NOT emit these (Phase A contract), so a migrated handler emits its own
        with the phase-specific payload the old inline ``_do_*`` used.
        """

    def session_offsets(self) -> object:
        """Read the main/review/test session topology (usage offsets)."""

    def cancellation(self) -> threading.Event:
        """The shutdown/cancel event a phase observes mid-run."""

    def create_milestone_idempotent(self, **kwargs) -> None:
        """Idempotently create a milestone when a handler needs a durable
        milestone point mid-phase. Terminal milestones also go into
        ``PhaseResult.milestone_events`` for unified commit; this is for the
        correlation/lookup case.
        """


@dataclass
class PhaseDeps:
    """Service bundle injected into a phase handler. All objects already exist;
    handlers never construct these.
    """

    host: PhaseHost
    gh: GitHubOps
    git_workspace: GitWorkspaceService
    evidence: EvidenceService
    sandbox: SandboxProvider
    repo: AutonomousWorkflowRepository
    agent_runner: AutonomousAgentRunner


@runtime_checkable
class PhaseHandler(Protocol):
    """A migrated complex phase. ``name`` is the registry key; ``handle``
    returns a ``PhaseResult`` consumed by ``_commit_phase_result``.
    """

    name: str

    def handle(self, ctx: WorkflowContext, deps: PhaseDeps) -> PhaseResult:
        """Execute the phase and return its structured result."""

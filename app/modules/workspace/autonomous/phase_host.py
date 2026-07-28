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

    def emit_status_change(self, payload: dict) -> None:
        """Emit a status_change domain event. Distinct from phase_change (the
        merge handler emits this on the policy-pause branch, mirroring the
        legacy inline ``_emit("status_change", ...)``).
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

    # ── Merge-phase helpers (#2044 Phase B T10) ──────────────────────────
    # These are orchestrator-private methods (each tens-to-hundreds of lines,
    # with their own transitive ``self._`` calls) that the merge handler needs
    # but which are NOT yet on a service. They are exposed on the host (not
    # PhaseDeps) because they read/commit orchestrator bookkeeping fields
    # (worktree_path, base_commit_sha, ci_repair_*) inline — the same reason
    # the un-migrated _do_* methods stayed on ``self``. Moving them into a
    # service is a larger refactor (tracked separately); for now the handler
    # reaches them through the host so it can live in phases/merge.py without
    # a concrete orchestrator reference. The orchestrator already satisfies
    # these as bound methods, so the duck-typed Protocol adds no new code.

    def validate_pre_merge_change_scope(self, gh, wf, pr_head_sha) -> str:
        """Validate the effective PR delta against an immutable main snapshot."""

    def sync_failed_pr_with_main(self, gh, branch_name, pr_number, pr_head_sha) -> bool:
        """Synchronize a stale PR with main before merge.

        Returns True when it took over the cycle (caller should defer).
        """

    def branch_contains_main(self, gh, pr_head_sha, branch_name="") -> bool | None:
        """Whether the PR branch already contains current main."""

    def start_ci_repair_round(self, wf, pr_number, failed_checks) -> None:
        """Repair merge-phase CI failures in-place on the existing PR branch."""

    def perform_git_cleanup(self) -> tuple[str, str]:
        """Post-merge Git cleanup retry entry; returns (cleanup_status, error)."""

    def resolve_merge_conflicts(self, gh, branch_name, pr_number) -> None:
        """Resolve a real merge conflict on the PR branch (delegates to the
        GitWorkspaceService). Exposed on the host so tests that stub the
        orchestrator's ``_resolve_merge_conflicts`` bound method keep working
        with the migrated handler, and so the handler does not reach into the
        git_workspace service directly for what is logically a phase action.
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

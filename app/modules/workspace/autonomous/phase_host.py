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

    # ── PR-review-phase helpers (#2044 Phase B T11) ──────────────────────────
    # Same rationale as the merge helpers above: orchestrator-private methods
    # (agent runner wrappers, artifact readers, CI polling, scope validation,
    # the review-fix sub-method, GitHub-comment posting) that the pr_review
    # handler needs but which are NOT on a service — each reads/commits
    # orchestrator bookkeeping inline (worktree_path, base_commit_sha,
    # session_line topology, repo integrity state). Exposed on the host as
    # thin aliases so phases/pr_review.py lives without a concrete orchestrator
    # reference. The orchestrator already satisfies these as bound methods.

    def get_workflow_field(self, field: str):
        """Read one field from the host's live (re-queried) workflow snapshot.

        The handler receives a frozen ``ctx.workflow`` snapshot; the legacy
        ``_do_pr_review`` re-read ``self.workflow`` (a @property re-querying the
        repo) to see github_pr_number persisted by an earlier advance() cycle.
        This delegate gives the handler that fresh read without exposing the
        whole orchestrator.
        """

    def refresh_workflow_snapshot(self) -> dict:
        """Re-read the full live workflow dict after a side effect that may have
        rotated session bookkeeping (e.g. context-recovery during a review fix).
        """

    def post_github_comment(self, gh, number, body, *, is_pr=False, context="") -> None:
        """Post a length-capped comment to a GitHub issue or PR (failure is
        logged, never aborts the phase). Delegates to the orchestrator's
        ``_post_github_comment``.
        """

    def must_run_full_review_rounds(self, wf: dict) -> bool:
        """Whether the workflow is configured to require all review rounds
        regardless of an early approval (delegates to
        ``_must_run_full_review_rounds``).
        """

    def get_pr_review_diff(self, gh, pr_number, branch_name) -> str:
        """Fetch the PR diff text for the review prompt (delegates to the
        ``_get_pr_review_diff`` staticmethod).
        """

    def smart_truncate_diff(self, diff_text: str) -> str:
        """Length-cap a diff for prompt inclusion (delegates to
        ``_smart_truncate_diff``).
        """

    def clean_agent_text(self, text: str) -> str:
        """Strip agent intro/closing boilerplate from a text blob (delegates to
        ``_clean_agent_text``).
        """

    def poll_ci_status(self, gh, pr_number) -> list:
        """Poll CI checks for a PR until they finish or timeout (delegates to
        ``_poll_ci_status``). May raise WorkflowPaused on shutdown — the handler
        re-raises it so advance() honours the pause.
        """

    def run_agent_with_context_recovery(self, **kwargs):
        """Run an agent call with automatic context-recovery on overflow
        (delegates to ``_run_agent_with_context_recovery``).
        """

    def accumulate_tokens(self, result) -> None:
        """Refresh workflow usage totals from an agent result (delegates to
        ``_accumulate_tokens``).
        """

    def abort_on_repo_integrity_violation(self, result, milestone_id: str) -> bool:
        """Check an agent result for a repository-integrity violation and, if
        found, mark the milestone failed + pause the workflow. Returns True when
        the caller should abort the phase (delegates to
        ``_abort_on_repo_integrity_violation``).
        """

    def is_context_overflow(self, result) -> bool:
        """Whether an agent result indicates a context-window overflow
        (delegates to ``_is_context_overflow``).
        """

    def artifact_text(self, result) -> str:
        """Extract the primary text artifact from an agent result (delegates to
        the ``_artifact_text`` classmethod).
        """

    def artifact_tldr(self, result) -> str:
        """Extract the TL;DR line from an agent result (delegates to the
        ``_artifact_tldr`` classmethod).
        """

    def review_is_approved(self, review_text: str, approval_phrase: str) -> bool:
        """Decide whether a review text constitutes approval (delegates to
        ``_review_is_approved``).
        """

    def validate_autonomous_change_scope(self, gh, wf, base_sha, head_sha) -> str:
        """Validate the diff between two commits against the autonomous scope
        guard; returns ``""`` on success or an error message (delegates to
        ``_validate_autonomous_change_scope``).
        """

    def apply_pr_review_fix(
        self, wf, gh, review_text, round_num, dev_round, ci_failures, pr_number
    ) -> bool:
        """Apply one round of code-review fixes (the ``pr_updated`` milestone).
        Returns True on success, False after writing status=failed inline.
        Delegates to the orchestrator's ``_apply_pr_review_fix`` (a ~300-line
        sub-method with its own transitive ``self._`` calls — kept on the
        orchestrator, exposed here as a bound alias).
        """

    def cancel_milestone_for_shutdown(self, milestone_id: str) -> None:
        """Mark an in-progress milestone cancelled when shutdown interrupts a
        phase (delegates to ``_cancel_milestone_for_shutdown``).
        """

    # ── Development-phase helpers (#2044 Phase B T12) ───────────────────────
    # The development sub-methods (_run_development_agent /
    # _post_dev_completion_comment / _run_test_phase) stay on the orchestrator
    # (each is hundreds of lines with deep orchestrator-state coupling and
    # several direct-call + static-source tests under tests/issues/). They
    # commit forbidden fields inline (status=failed on dev/test failure,
    # status=pr_review + current_phase=pr_review on test-phase success). The
    # migrated development handler (phases/development.py) is a thin top-level
    # orchestrator that calls them via these host aliases and reflects the
    # committed transition on the returned PhaseResult. Same accepted trade-off
    # as T11's apply_pr_review_fix alias; PhaseHost-width debt documented T14.

    def run_development_agent(self, wf, dev_round, gh) -> None:
        """Run the dev agent for one round (delegates to
        ``_run_development_agent``). Commits status=failed inline on failure.
        """

    def post_dev_completion_comment(self, wf, dev_round, gh) -> None:
        """Post the development completion comment to the issue (delegates to
        ``_post_dev_completion_comment``).
        """

    def run_test_phase(self, wf, dev_round, gh) -> None:
        """Run the test agent + retry/verdict logic (delegates to
        ``_run_test_phase``). Commits status=failed / test_retries /
        current_phase=pr_review inline.
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

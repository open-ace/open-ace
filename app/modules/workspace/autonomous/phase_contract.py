"""
Open ACE - Autonomous Phase Contract

Phase A of Issue #2044 introduces a minimal, stable contract between the
orchestrator's scheduler loop and individual workflow phases. The goal is NOT
to move the existing ``_do_*`` methods (that is Phase B, after the dependency
services in #2022/#2043 land). Phase A only:

1. Defines ``WorkflowContext`` — the read-only snapshot a phase receives.
2. Defines ``PhaseResult`` — the structured outcome a phase returns, so the
   scheduler (not the phase) commits phase/status transitions, milestones and
   usage. This prevents a phase from partially succeeding and then writing the
   *next* phase's state before its own side effects are confirmed.
3. Provides a unified commit entrypoint (``_commit_phase_result`` on the
   orchestrator) as the single authoritative path for phase/status mutation.

Old ``_do_*`` methods continue to return ``None`` and commit inline until they
are migrated one-by-one onto this contract; the scheduler treats a ``None``
return as the legacy path so behaviour does not change during the migration.

Design notes
------------
- Fields mirror what the orchestrator already threads through ``advance()`` and
  the phase methods, so adapting a phase is mechanical, not a rewrite.
- ``PhaseResult`` is a value object: it carries *intent* (what should change)
  rather than mutating anything itself. The orchestrator validates and applies
  it atomically-ish (DB updates remain individual calls, but they all flow
  through one method, which is the property Phase A requires).
- ``structured_error`` is deliberately ``object | None`` rather than a typed
  exception: Phase B will replace it with the structured error contract from
  #2045/#2046 once those evidence services are wired in. Keeping it opaque now
  avoids a premature type the rest of the system cannot satisfy yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    import threading


# Outcomes a phase can request. The scheduler maps each to a persistence action:
#   completed -> apply patch, advance current_phase/status, commit milestones
#   wait      -> apply patch, set waiting status, do NOT advance phase yet
#   retry     -> leave phase unchanged, bump transient_retry_count
#   pause     -> set status=paused, emit pause event (WorkflowPaused equivalent)
#   failed    -> set status=failed, record error_message
PhaseOutcome = Literal["completed", "wait", "retry", "pause", "failed"]


@dataclass
class WorkflowContext:
    """Read-only snapshot of everything a phase needs to execute.

    Built by the scheduler from the persisted workflow dict (never from a
    stale in-memory object), so a restart/retry re-enters a phase from its
    durable state — a core Phase A acceptance criterion (#2044).
    """

    workflow: dict
    # Frozen workflow definition captured at creation time; phases must not
    # read mutable fields (model, permission_mode) from the live workflow mid-
    # run, because a user edit would otherwise change behaviour mid-phase.
    definition_snapshot: dict | None
    # Repo-HEAD / branch binding captured before the phase runs (may be None
    # for preparation, which has no worktree yet). Phases use this instead of
    # re-deriving git state ad hoc.
    repository_context: object | None
    # Live session binding registry (main/review/test topology). Phases declare
    # the session they will bind; the scheduler owns the registry.
    session_bindings: object
    # Shared cancellation/shutdown event so a phase can observe a stop without
    # the scheduler having to abort it externally.
    cancellation: threading.Event


@dataclass
class PhaseResult:
    """Structured outcome returned by a phase handler.

    A phase builds this instead of calling ``_update_workflow`` /
    ``_create_milestone`` directly. The scheduler's ``_commit_phase_result``
    is the only code that consumes it and writes to the DB, which makes
    "phase/status transition" a single auditable path (#2044 acceptance:
    "phase/status 通过统一提交入口更新").
    """

    outcome: PhaseOutcome
    # Target phase for the *next* scheduler cycle. ``None`` means "terminal"
    # (workflow completed) or "do not advance" (wait/retry/pause/failed).
    next_phase: str | None = None
    # Explicit status override for the transition. When ``None`` the commit
    # entrypoint derives it from ``next_phase`` via ``PHASE_STATUS_MAP``.
    next_status: str | None = None
    # Workflow fields to patch (e.g. branch_name, github_pr_number, dev_round).
    # Never include ``current_phase``/``status`` here — those come from
    # next_phase/next_status so the commit entrypoint stays the sole authority.
    workflow_patch: dict = field(default_factory=dict)
    # Milestones the phase produced, in creation order. Each entry is the same
    # kwargs dict ``_create_milestone`` accepts, so the commit entrypoint can
    # forward them unchanged and keep idempotency.
    milestone_events: list[dict] = field(default_factory=list)
    # Token/request deltas accrued by this phase. ``None`` means the phase did
    # not touch usage (the scheduler refreshes totals from sessions instead).
    usage_delta: object | None = None
    # Opaque structured failure description for ``failed``/``pause`` outcomes.
    # Typed in Phase B once the evidence/error services (#2045/#2046) land.
    structured_error: object | None = None

    @staticmethod
    def completed(
        next_phase: str | None,
        *,
        next_status: str | None = None,
        workflow_patch: dict | None = None,
        milestone_events: list[dict] | None = None,
        usage_delta: object | None = None,
    ) -> PhaseResult:
        """Build a normal-transition result advancing to ``next_phase``."""
        return PhaseResult(
            outcome="completed",
            next_phase=next_phase,
            next_status=next_status,
            workflow_patch=workflow_patch or {},
            milestone_events=milestone_events or [],
            usage_delta=usage_delta,
        )

    @staticmethod
    def wait(
        *,
        workflow_patch: dict | None = None,
        milestone_events: list[dict] | None = None,
    ) -> PhaseResult:
        """Build a result that parks the workflow in ``waiting`` status."""
        return PhaseResult(
            outcome="wait",
            next_phase=None,
            next_status="waiting",
            workflow_patch=workflow_patch or {},
            milestone_events=milestone_events or [],
        )

    @staticmethod
    def retry(*, workflow_patch: dict | None = None) -> PhaseResult:
        """Build a result that leaves the phase unchanged for the next cycle."""
        return PhaseResult(
            outcome="retry",
            next_phase=None,
            next_status=None,
            workflow_patch=workflow_patch or {},
        )

    @staticmethod
    def pause(
        *,
        workflow_patch: dict | None = None,
        structured_error: object | None = None,
    ) -> PhaseResult:
        """Build a result that pauses the workflow (user-facing pause)."""
        return PhaseResult(
            outcome="pause",
            next_phase=None,
            next_status="paused",
            workflow_patch=workflow_patch or {},
            structured_error=structured_error,
        )

    @staticmethod
    def failed(
        *,
        structured_error: object | None = None,
        workflow_patch: dict | None = None,
    ) -> PhaseResult:
        """Build a terminal failure result (sets status=failed)."""
        return PhaseResult(
            outcome="failed",
            next_phase=None,
            next_status="failed",
            workflow_patch=workflow_patch or {},
            structured_error=structured_error,
        )

"""DevelopmentPhase handler (#2044 Phase B T12). Extracted from
``AutonomousOrchestrator._do_development``. See phases/merge.py (T10) /
phases/pr_review.py (T11) for the pattern and the Migration Procedure in the
plan.

Same decisions as the legacy inline-commit method; only the recording mechanism
changes at the *top-level orchestration* boundary:

- The four forbidden fields (``current_phase``/``status``/``completed_at``/
  ``paused_at``) travel on the returned ``PhaseResult`` when the phase advances
  — they are never written inline here (the T3 AST guard scans this file).
- ``phase_change`` events go through ``deps.host.emit_phase_change``.

Deviation notes (mirroring the T8/T9/T10/T11 patterns):

- The three development sub-methods (``_run_development_agent`` /
  ``_post_dev_completion_comment`` / ``_run_test_phase``) are kept on the
  orchestrator and exposed as bound PhaseHost aliases (``run_development_agent``
  / ``post_dev_completion_comment`` / ``run_test_phase``). Each is hundreds of
  lines with deep orchestrator-state coupling (``self._update_workflow`` /
  ``self._run_agent`` / ``self.repo`` / ``self._create_milestone`` /
  ``self._compute_structured_test_verdict`` / ...). They commit forbidden fields
  inline (status=failed on dev/test failure, status=pr_review +
  current_phase=pr_review on success), exactly as the legacy
  ``_do_development`` did — those writes happen on the orchestrator (NOT in
  phases/), so the T3 guard is satisfied. The handler observes the post-helper
  workflow status and returns a PhaseResult that mirrors the committed
  transition so the unified-commit entrypoint records the authoritative outcome.
  This is the same accepted trade-off as T11's ``apply_pr_review_fix`` alias,
  and the PhaseHost-width debt is documented for T14. Moving these bodies into
  the handler would either (a) require ~15 new host aliases (exceeding the 40
  cap even after service-routing) or (b) break the static-source and direct-call
  tests under tests/issues/1140, tests/issues/1897, tests/issues/1647,
  tests/issues/1520, tests/issues/1277, tests/issues/1574, tests/issues/1547,
  tests/unit/test_autonomous_ci_guardrails.py (each calls
  ``orch._run_development_agent`` / ``orch._run_test_phase`` /
  ``orch._validate_test_report_format`` directly).
- The dev-failure branch (``status==failed`` after ``run_development_agent``)
  returns ``PhaseResult.retry()`` — phase/status stay unchanged; advance()'s
  convergence point reclaims the worktree for the terminal-failure path (the
  inline status=failed is what matters, same as the legacy bare ``return``).
- The test-retry / skip-retry / dev-retry-on-test-fail branches inside
  ``run_test_phase`` bump ``test_retries`` / ``skip_retries`` /
  ``dev_retries_on_test_fail`` / ``dev_round`` inline and leave the phase on
  ``development``; the handler returns ``PhaseResult.retry()`` (matches the
  legacy bare ``return`` — phase unchanged, scheduler re-enters development).
- The test-passed success path: ``run_test_phase`` already wrote
  ``current_phase=pr_review`` + ``status=pr_review`` + ``current_round=0`` +
  emitted ``phase_change{pr_review}`` inline. The handler returns
  ``PhaseResult.completed(next_phase="pr_review", next_status="pr_review",
  workflow_patch={"current_round": 0})`` so the unified-commit entrypoint
  records the same transition idempotently (the re-write is a no-op — values
  match). This keeps the handler unable to bypass the commit entrypoint even on
  the success path.
"""

from __future__ import annotations

import logging

from app.modules.workspace.autonomous.phase_contract import PhaseResult

NAME = "development"

logger = logging.getLogger(__name__)


def handle(ctx, deps) -> PhaseResult:
    """Execute one development-phase cycle.

    Body moved from ``_do_development`` per the Migration Procedure; see module
    docstring for the deviation notes.

    When ``test_retries > 0`` (or ``skip_retries > 0``), the dev phase was
    already completed on a prior cycle and only the test step is re-run (e.g.
    the test agent itself timed out or hit an API error last attempt). On any
    other cycle, the dev agent runs first; if it fails the workflow is parked
    (status=failed written inline by the dev sub-method) and tests are skipped.
    """
    wf = ctx.workflow
    gh = deps.gh
    host = deps.host
    dev_round = wf.get("dev_round", 1)
    test_retries = wf.get("test_retries", 0)
    skip_retries = wf.get("skip_retries", 0)

    # ── Development phase (skipped on test-only/skip retry) ──
    if test_retries > 0 or skip_retries > 0:
        logger.info(
            "Test/skip retry (test=%d, skip=%d) for dev round %d, skipping development phase",
            test_retries,
            skip_retries,
            dev_round,
        )
    else:
        host.run_development_agent(wf, dev_round, gh)
        wf = host.refresh_workflow_snapshot() or {}
        if wf.get("status") == "failed":
            # _run_development_agent wrote status=failed inline (dev agent
            # failed / branch mismatch / scope violation / no code changes).
            # Return retry so phase/status stay unchanged — advance()'s
            # convergence point reclaims the worktree for the terminal-failure
            # path. Matches the legacy bare ``return`` here.
            logger.info(
                "Development round %d failed, skipping test phase for workflow %s",
                dev_round,
                host.workflow_id[:8],
            )
            return PhaseResult.retry()
        # Post development completion comment — but only if dev succeeded.
        # _run_development_agent sets status="failed" on failure; without this
        # guard, a "✅ Completed" comment is posted with a stale commit that
        # isn't the agent's work (#525).
        host.post_dev_completion_comment(wf, dev_round, gh)

    # ── Test phase (always runs) ──
    host.run_test_phase(wf, dev_round, gh)
    wf = host.refresh_workflow_snapshot() or {}

    status = wf.get("status")
    if status == "failed":
        # Test phase wrote status=failed inline (test agent exhausted retries /
        # unfixable failures / skipped-after-retry). Phase unchanged; the
        # convergence point reclaims the worktree. Matches the legacy bare
        # ``return``.
        return PhaseResult.retry()

    # Test phase advanced the workflow to pr_review inline (wrote
    # current_phase=pr_review + status=pr_review + current_round=0 + emitted
    # phase_change{pr_review}). Mirror that transition on the PhaseResult so the
    # unified-commit entrypoint records the authoritative outcome idempotently.
    if wf.get("current_phase") == "pr_review":
        return PhaseResult.completed(
            next_phase="pr_review",
            next_status="pr_review",
            workflow_patch={"current_round": 0},
        )

    # Test phase left the workflow parked on ``development`` for another cycle
    # (test-retry / skip-retry / dev-retry-on-test-fail bumps). Phase unchanged;
    # the scheduler re-enters development. Matches the legacy bare ``return``.
    return PhaseResult.retry()

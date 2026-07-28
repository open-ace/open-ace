"""MergePhase handler (#2044 Phase B T10). Extracted from
``AutonomousOrchestrator._do_merge``. See Migration Procedure in the plan.

Same decisions as the legacy inline-commit method; only the recording mechanism
changes:

- The four forbidden fields (``current_phase``/``status``/``completed_at``/
  ``paused_at``) travel on the returned ``PhaseResult`` — they are never written
  inline here (the T3 AST guard scans this file). Terminal completion is
  signalled with ``PhaseResult.completed(next_phase="completed", ...)``; the
  orchestrator's ``_commit_phase_result`` maps the "completed" pseudo-phase to
  ``status=completed`` + ``completed_at`` + ``current_phase=merge``.
- ``phase_change`` events (e.g. ``{"phase": "completed"}``) go through
  ``deps.host.emit_phase_change`` — the commit entrypoint does NOT emit them.
- The five orchestrator-private helpers this phase reaches for
  (``validate_pre_merge_change_scope`` / ``sync_failed_pr_with_main`` /
  ``branch_contains_main`` / ``start_ci_repair_round`` / ``perform_git_cleanup``)
  are NOT on a service — each is tens-to-hundreds of lines with its own
  transitive ``self._`` calls that read/commit orchestrator bookkeeping. They
  are exposed on ``PhaseHost`` (duck-typed; the orchestrator satisfies them as
  bound aliases) so this handler lives in ``phases/`` without a concrete
  orchestrator reference. Moving them into a service is a larger, separate
  refactor.
- git ops that ARE on a service go through ``deps.host`` aliases
  (``resolve_merge_conflicts`` delegates to the orchestrator's
  ``_resolve_merge_conflicts`` which in turn delegates to
  ``GitWorkspaceService``; routed via the host so tests that stub the bound
  ``_resolve_merge_conflicts`` method keep working). ``gh``/``evidence`` come
  from ``deps``.

Deviation notes (mirroring the T8/T9 patterns):

- The deferral branches (head-unverified, CI-pending, sync-took-over,
  CI-repair-started, conflict-resolved-and-defer) returned bare ``None`` in the
  legacy method — ``advance()`` left phase/status unchanged and the scheduler
  retried in ~10s. Here each returns ``PhaseResult.retry()`` (same effect:
  retry leaves phase/status untouched; advance() still resets the transient
  retry counter on the clean return). The ``pr_head_unverified`` milestone on
  the head-deferral branch is created inline via
  ``deps.host.create_milestone_idempotent`` because the workflow parks mid-phase
  and the milestone is a durable correlation record, not a terminal marker.
- The scope-error branch wrote ``status=failed`` inline; here it returns
  ``PhaseResult.failed(structured_error={"message": scope_error})`` — the commit
  entrypoint writes status=failed + error_message.
- The policy-rejection branch wrote ``status=paused`` + ``paused_at`` inline,
  emitted a ``status_change`` event, and raised ``WorkflowPaused`` to abort
  advance() before its success cleanup cleared the persisted pause reason.
  Here it emits the ``status_change`` event via ``deps.host.emit_status_change``
  (a domain event, not phase_change) and returns
  ``PhaseResult.pause(structured_error={"message": message}, workflow_patch=
  {"error_message": message, "agent_pid": None})``. The commit entrypoint
  writes status=paused + paused_at. advance() now skips the transient-retry
  reset (which clears error_message) when the committed result is a pause, so
  a normal return is enough — the WorkflowPaused short-circuit is no longer
  needed.
- The conflict-resolution-failure branch recorded a failed milestone inline
  then re-raised so advance()'s exception handler runs ``_mark_failed``
  (writing status=failed + the ``error`` event). Returning
  ``PhaseResult.failed`` here would skip ``_mark_failed`` and drop the error
  event — a behaviour change. So the inline ``raise`` is preserved, matching
  the T8/T9 failure-path deviation.
- The success path is the one structural reshape: legacy wrote
  ``status=completed`` + ``completed_at`` + ``cleanup_status=pending`` inline,
  emitted ``phase_change{completed}``, ran cleanup (which itself persists the
  final ``cleanup_*`` fields), then recorded a cleanup milestone. Because
  ``PhaseResult`` commits the four forbidden fields via
  ``_commit_phase_result`` on return, the cleanup is run BEFORE building the
  result: ``merge_pr`` → run cleanup (returns a status, does not raise on
  partial failure; cleanup persists its own ``cleanup_status`` /
  ``cleanup_attempts`` / ``cleanup_error`` / ``cleanup_updated_at`` /
  ``cleanup_next_retry_at``) → build ``PhaseResult.completed(next_phase=
  "completed", milestone_events=[cleaned_up|cleanup_pending])``. The merged
  milestone is created inline (it doubles as the durable success anchor); the
  cleaned_up / cleanup_pending milestone rides in ``milestone_events``. The
  ``workflow_patch`` carries no ``cleanup_*`` fields — cleanup's own writes are
  already authoritative by the time the result commits, and re-writing them
  would clobber cleanup's final status with a stale value.
"""

from __future__ import annotations

import json
import logging

from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import PhaseResult

# Mirrors AutonomousOrchestrator.MERGE_POLICY_PAUSE_REASON_PREFIX. Duplicated
# here (not imported) to avoid a circular import: the orchestrator imports
# ``phases`` at module load (for resolve_phase_handler), so phases/merge.py
# cannot import back from the orchestrator module. Keep in sync.
MERGE_POLICY_PAUSE_REASON_PREFIX = "Merge blocked by repository policy:"

NAME = "merge"

logger = logging.getLogger(__name__)


def handle(ctx, deps) -> PhaseResult:
    """Execute one merge-phase cycle.

    Body moved from ``_do_merge`` per the Migration Procedure; see module
    docstring for the deviation notes.
    """
    wf = ctx.workflow
    gh = deps.gh
    pr_number = wf.get("github_pr_number")
    branch_name = wf.get("branch_name", "")

    if pr_number:
        # Phase B (#2045): verify the PR head through the evidence contract
        # before any probe consumes it. Fail closed — an unverifiable head
        # defers to the next scheduler cycle rather than driving scope/sync/
        # merge probes on a raw API SHA.
        head_ev = deps.evidence.resolve_verified_pr_head(gh, pr_number, branch_name)
        if head_ev.verdict is not Verdict.CONFIRMED:
            logger.info(
                "PR #%s: head not verified (verdict=%s), deferring merge",
                pr_number,
                head_ev.verdict.value,
            )
            deps.host.create_milestone_idempotent(
                phase="merge",
                milestone_type="pr_head_unverified",
                status="in_progress",
                title=f"PR #{pr_number} head not verifiable; deferring merge",
                error_message=head_ev.reason,
                metadata=json.dumps({"evidence": head_ev.to_dict()}, ensure_ascii=False),
            )
            return PhaseResult.retry()
        pr_head_sha = head_ev.commit_shas[0]
        try:
            scope_error = deps.host.validate_pre_merge_change_scope(gh, wf, pr_head_sha)
        except Exception as exc:
            scope_error = f"Pre-merge change scope could not be verified: {exc}"
        if scope_error:
            return PhaseResult.failed(structured_error={"message": scope_error})

        # Before syncing or repairing, check whether the PR is already
        # mergeable despite non-required check failures
        # (mergeable_state=unstable). If so, skip branch sync and CI
        # repair — attempt merge directly. Syncing/repairing such PRs is
        # wasteful and can fail (the agent can't fix dependency
        # vulnerabilities like Security Audit Gate), causing the workflow
        # to fail unnecessarily (#2034).
        try:
            pre_merge_state = gh.get_pr_merge_state(pr_number)
            pre_mergeable_state = str(pre_merge_state.get("mergeable_state") or "").lower()
        except Exception as state_err:
            logger.warning(
                "PR #%s: failed to query merge state before merge: %s",
                pr_number,
                state_err,
            )
            pre_mergeable_state = ""

        checks: list[dict] = []
        if pre_mergeable_state != "unstable":
            # PR is not mergeable-as-is; sync branch and check CI.
            # Required-branch-update rules reject an immediate merge with
            # the same generic "repository rule violations" error used for
            # pending checks. Synchronize a stale PR explicitly before
            # querying the new head's CI. This reuses the trusted
            # clean/conflict merge path and returns without consuming a
            # CI-repair attempt.
            if (
                branch_name
                and pr_head_sha
                and deps.host.sync_failed_pr_with_main(gh, branch_name, pr_number, pr_head_sha)
            ):
                return PhaseResult.retry()

            try:
                checks = gh.get_pr_checks(pr_number)
            except Exception as e:
                raise GitHubOpsError(
                    f"Unable to query CI checks before merging PR #{pr_number}: {e}"
                ) from e
            failed = [c for c in checks if c.get("bucket") == "fail"]
            if failed:
                deps.host.start_ci_repair_round(wf, pr_number, failed)
                return PhaseResult.retry()
        else:
            logger.info(
                "PR #%s: mergeable_state=unstable; skipping branch sync "
                "and CI repair, attempting merge directly",
                pr_number,
            )
        # If CI is still running, defer this merge to the next scheduler
        # cycle instead of blocking (synchronous poll) or failing. The
        # scheduler re-enters the merge phase every ~10s.
        # (checks is empty for unstable PRs — no deferral needed.)
        pending = [c for c in checks if c.get("bucket") == "pending"]
        if pending:
            logger.info(
                "PR #%s: %d CI checks pending, deferring merge to next cycle",
                pr_number,
                len(pending),
            )
            return PhaseResult.retry()

        try:
            gh.merge_pr(pr_number, strategy="merge")
            deps.host.create_milestone_idempotent(
                phase="merge",
                milestone_type="merged",
                status="completed",
                title=f"PR #{pr_number} merged",
            )
        except GitHubOpsError as e:
            err_msg = str(e)

            # Merge readiness can change after the pre-merge check query:
            # a newly-pushed head may acquire required checks between these
            # two calls. Refresh both CI and GitHub's merge classification
            # before deciding whether this is policy lag or a real conflict.
            try:
                refreshed_checks = gh.get_pr_checks(pr_number)
            except Exception as checks_err:
                logger.warning(
                    "PR #%s: failed to refresh checks after merge rejection: %s",
                    pr_number,
                    checks_err,
                )
                refreshed_checks = checks
            failed = [c for c in refreshed_checks if c.get("bucket") == "fail"]
            if failed:
                deps.host.start_ci_repair_round(wf, pr_number, failed)
                return PhaseResult.retry()
            pending = [c for c in refreshed_checks if c.get("bucket") == "pending"]

            try:
                merge_state = gh.get_pr_merge_state(pr_number)
            except Exception as state_err:
                logger.warning(
                    "PR #%s: failed to refresh merge state after rejection: %s",
                    pr_number,
                    state_err,
                )
                merge_state = {}
            mergeable = merge_state.get("mergeable")
            mergeable_state = str(merge_state.get("mergeable_state") or "").lower()
            lowered_error = err_msg.lower()
            is_policy_rejection = any(
                marker in lowered_error
                for marker in (
                    "base branch policy prohibits",
                    "repository rule violations",
                    "required status check",
                    "review required",
                    "review is required",
                    "branch protection",
                    "protected branch",
                    "pull request is in draft",
                )
            )
            is_conflict_rejection = any(
                marker in lowered_error
                for marker in (
                    "merge commit cannot be cleanly created",
                    "merge conflict",
                    "conflicting files",
                )
            )

            # Pending checks are confirmed transient state. Keep polling
            # without consuming an AI repair attempt, even when GitHub's
            # mergeability cache concurrently reports ``dirty``.
            if pending:
                logger.info(
                    "PR #%s: merge blocked with %d checks pending " "(state=%s), deferring",
                    pr_number,
                    len(pending),
                    mergeable_state or "unknown",
                )
                return PhaseResult.retry()

            is_real_conflict = (
                mergeable_state == "dirty"
                or is_conflict_rejection
                or (mergeable is False and not mergeable_state)
            )
            # GitHub's mergeability cache can report a stale "dirty"
            # immediately after a synchronization push, before the
            # synthetic merge commit is recomputed. The PR branch already
            # contains main in that case, so verifying ancestry avoids a
            # no-op merge that fails with "made no commit". Only the
            # cache-derived "dirty" path needs the probe; text evidence
            # and a definitive non-mergeable branch are authoritative.
            if (
                is_real_conflict
                and mergeable_state == "dirty"
                and not is_conflict_rejection
                and mergeable is not False
                and pr_head_sha
                and deps.host.branch_contains_main(gh, pr_head_sha, branch_name) is True
            ):
                logger.info(
                    "PR #%s mergeable_state=dirty is stale (branch has main); "
                    "deferring to policy/check path",
                    pr_number,
                )
                is_real_conflict = False
            if is_real_conflict:
                try:
                    # Authoritative conflict evidence wins over generic
                    # repository-rule text. GitHub can return both for the
                    # same rejected merge.
                    logger.info(
                        "PR #%s has a real merge conflict (state=%s), resolving",
                        pr_number,
                        mergeable_state or "unknown",
                    )
                    deps.host.resolve_merge_conflicts(gh, branch_name, pr_number)
                    # Conflicts resolved + pushed, but NOT merged yet — the push
                    # triggered a fresh CI run. Return here (staying in 'merging')
                    # so the next merge cycle's CI-pending deferral handles the
                    # wait. Falling through to cleanup would delete the branch
                    # before the PR is merged (#1112 P1).
                    return PhaseResult.retry()
                except Exception as resolve_err:
                    deps.host.create_milestone_idempotent(
                        phase="merge",
                        milestone_type="merged",
                        status="failed",
                        title="PR merge failed",
                        error_message=f"Merge conflict resolution failed: {resolve_err}",
                    )
                    raise

            if is_policy_rejection:
                # With no failed/pending checks and no conflict evidence,
                # repository policy requires external action (approval,
                # marking ready, or a rule change). Persist a manually
                # recoverable pause instead of retrying forever.
                state_label = mergeable_state or "unknown"
                message = (
                    f"{MERGE_POLICY_PAUSE_REASON_PREFIX} PR #{pr_number} "
                    f"is not merge-ready (state={state_label}). Satisfy the "
                    f"repository requirement, then resume the workflow. {err_msg}"
                )
                deps.host.emit_status_change(
                    {
                        "status": "paused",
                        "reason": "merge_policy",
                        "message": message,
                    }
                )
                # status=paused + paused_at travel on PhaseResult.pause (the
                # commit entrypoint writes both). error_message + agent_pid
                # ride in workflow_patch. Legacy raised WorkflowPaused after
                # the inline write to short-circuit advance()'s transient-
                # retry reset (which clears error_message); the orchestrator's
                # advance() now skips that reset when the committed result is
                # a pause (see advance()), so a normal return is enough.
                return PhaseResult.pause(
                    structured_error={"message": message},
                    workflow_patch={"error_message": message, "agent_pid": None},
                )

            # A mergeable/blocked/unknown PR is not by itself evidence of
            # either a Git conflict or a recognized policy rejection.
            # Preserve the original permission, API, or infrastructure
            # error so the workflow fails visibly instead of spinning.
            raise

    # ── Success tail: delivery completion + best-effort cleanup ───────
    # Legacy persisted delivery completion FIRST (independent of cleanup
    # outcome, #2043), then ran cleanup, then recorded a cleanup milestone.
    # PhaseResult commits status=completed + completed_at via
    # _commit_phase_result on return, so we run cleanup first, then carry
    # delivery + cleanup fields + cleanup milestone in one result. cleanup
    # returns a status (it does not raise on partial failure), so a cleanup
    # hiccup still lands the workflow in status=completed with
    # cleanup_status=pending — same convergence behaviour, just committed
    # atomically rather than as two writes.
    deps.host.emit_phase_change({"phase": "completed"})

    cleanup_status, cleanup_error = deps.host.perform_git_cleanup()
    # _perform_git_cleanup already persists cleanup_status / cleanup_attempts /
    # cleanup_error / cleanup_updated_at / cleanup_next_retry_at itself (it is
    # the retry entry point, #2043). The legacy success path wrote
    # cleanup_status="pending" first then let cleanup overwrite it; since
    # cleanup runs here BEFORE the result is committed, its own writes are
    # already authoritative — do NOT re-write cleanup_* in workflow_patch (that
    # would clobber cleanup's final status with a stale "pending").
    workflow_patch: dict[str, object] = {}
    milestone_events: list[dict] = []
    if cleanup_status == "completed":
        milestone_events.append(
            {
                "phase": "merge",
                "milestone_type": "cleaned_up",
                "status": "completed",
                "title": "Branch/worktree cleaned up",
            }
        )
    else:
        # cleanup_status is pending or failed — record why without faking
        # a cleaned_up milestone. The scheduler sweep retries pending ones.
        milestone_events.append(
            {
                "phase": "merge",
                "milestone_type": "cleanup_pending",
                "status": "failed",
                "title": "Git cleanup pending retry",
                "error_message": (cleanup_error or "")[:300],
            }
        )

    return PhaseResult.completed(
        next_phase="completed",
        workflow_patch=workflow_patch,
        milestone_events=milestone_events,
    )

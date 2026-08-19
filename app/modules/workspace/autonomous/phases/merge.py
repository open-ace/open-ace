"""MergePhase handler (#2044 Phase B T10). Extracted from
``AutonomousOrchestrator._do_merge``. See Migration Procedure in the plan.

Same decisions as the legacy inline-commit method; only the recording mechanism
changes:

- The four forbidden fields (``current_phase``/``status``/``completed_at``/
  ``paused_at``) travel on the returned ``PhaseResult`` — they are never written
  inline here (the T3 AST guard scans this file). Terminal completion is
  signalled with ``PhaseResult.completed(next_phase="completed", ...)``; the
  orchestrator's ``_commit_phase_result`` maps the "completed" pseudo-phase to
  ``status=completed`` + ``completed_at`` + ``current_phase=acceptance_verification``
  (the default terminal real phase; ``merge`` now advances to
  ``acceptance_verification`` rather than completing directly).
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
from datetime import datetime, timezone

from app.modules.workspace.autonomous.constants import MERGE_POLICY_PAUSE_REASON_PREFIX
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.github_ops import GitHubOpsError
from app.modules.workspace.autonomous.phase_contract import PhaseResult

NAME = "merge"

logger = logging.getLogger(__name__)

# Bound on how long a freshly pushed head may take before its required
# check-runs exist. Mirrors ZERO_CHECK_RUNS_WALL_CLOCK_FLOOR in the
# orchestrator — the codebase's measured bound for the same CI provisioning
# lag (#2673); half of it here would re-freeze slow-provisioning heads.
_POLICY_SETTLE_GRACE_SECONDS = 1200


def _required_contexts(gh, pr_number: int, base_branch: str) -> set[str] | None:
    """Required-check contexts for ``base_branch``, or None if undeterminable.

    None means "could not observe" and is distinct from an empty set, which
    means "observed, and the branch requires nothing".
    """
    try:
        protection = gh.get_branch_protection(base_branch)
    except Exception as exc:  # noqa: BLE001 — degrade, never stall the merge
        logger.warning(
            "PR #%s: could not resolve required checks for '%s' (%s)",
            pr_number,
            base_branch,
            exc,
        )
        return None
    return set((protection.get("required_status_checks") or {}).get("contexts") or [])


def _head_freshly_pushed(gh, pr_head_sha: str) -> bool:
    """Whether the PR head commit is younger than the settle grace window.

    Right after a push (typically a CI-repair commit), check-runs for the new
    head SHA may not exist yet, so a refreshed rollup shows no pending entry
    while GitHub already reports ``blocked`` (required contexts missing for
    that SHA). A fresh head there means "CI has not settled", not a policy
    block.

    Fail-closed: an empty SHA, an API failure or an unparseable date returns
    False so the caller keeps the legacy pause (the monitor sweep
    re-classifies) — #1989 fail-closed spirit.
    """
    sha = (pr_head_sha or "").strip()
    if not sha:
        return False
    try:
        committed_at = gh.get_commit_committed_at(sha)
    except Exception as exc:  # noqa: BLE001 — degrade to the legacy pause
        logger.warning(
            "PR head %s: could not resolve commit time for policy-settle check: %s",
            sha[:8],
            exc,
        )
        return False
    if committed_at is None:
        return False
    age = (datetime.now(timezone.utc) - committed_at).total_seconds()
    # A negative age (clock skew / future committer date) reads as fresh —
    # the safer direction for a transient-vs-permanent discriminator.
    return bool(age < _POLICY_SETTLE_GRACE_SECONDS)


def _blocking_pending(gh, checks: list[dict], pr_number: int, base_branch: str) -> list[dict]:
    """Pending checks to defer the merge for (issue #2428).

    Only a pending REQUIRED check defers: a slow non-required job
    (``Critical PR E2E``, ``Full E2E``) must not re-defer the merge every
    scheduler cycle. ``ReadinessService._partition_checks`` documents the same
    rule. Degrades to "all pending block" when the required set cannot be
    observed.

    Unlike the failure path — which dropped its required-filter in #27 because
    aggregate gates made ``failing ∩ required`` return only the unrepairable
    gate — the pending filter is kept. Dropping it would re-defer every cycle on
    slow non-required jobs, and a pending required check is usually observable:
    an aggregate gate (e.g. ``PR Gate``) reports its own status as pending while
    its underlying jobs run, so the filter normally catches the wait.

    Note on the aggregate-gate propagation window: a pending *underlying* job
    whose name is not in the literal required set (e.g. ``test (3.10)`` while
    ``required == {'PR Gate'}``) is classified non-blocking HERE — this filter
    governs only the pre-merge / post-rejection *required*-pending deferral,
    where over-deferring on slow non-required jobs is the cost (#2428). In the
    narrow window before the gate's own pending status propagates, such a job
    used to reach the policy-pause branch and freeze the workflow. That freeze
    is now prevented by the post-rejection transient guard in :func:`handle`
    (any pending check OR ``mergeable_state == "unknown"`` defers before the
    pause), so the gap no longer strands workflows.
    """
    pending = [c for c in checks if c.get("bucket") == "pending"]
    if not pending:
        return []
    required = _required_contexts(gh, pr_number, base_branch)
    if not required:
        return pending
    blocking = [c for c in pending if (c.get("name") or "") in required]
    ignored = [c for c in pending if (c.get("name") or "") not in required]
    if ignored:
        logger.info(
            "PR #%s: not deferring for %d non-blocking pending check(s): %s",
            pr_number,
            len(ignored),
            ", ".join(c.get("name") or "?" for c in ignored),
        )
    return blocking


def _ci_repair_targets(checks: list[dict]) -> list[dict]:
    """Failing checks to hand to CI-repair (#27; supersedes the #2428 filter).

    A branch's required check may be an AGGREGATE GATE — one status-check
    context that summarizes many underlying jobs via ``needs:`` (whatever the
    repo names it; on open-ace it is ``PR Gate`` since #2455). Such a gate has
    no actionable failure of its own — its log only reports which underlying
    jobs failed — and the real failures sit OUTSIDE the required set. The
    ``#2428`` filter (``failing ∩ required``) therefore returned only the
    (unrepairable) gate, or nothing on a propagation lag, and workflows stalled
    at the merge-policy pause instead of repairing the real failures.

    Target every failing check instead. The #2428 concern — spending the
    bounded repair budget on checks that do not gate the merge — is held by the
    separate ``mergeable_state == "unstable"`` short-circuit earlier in
    :func:`handle`: a PR that is mergeable despite failing non-required checks
    is merged directly without a repair round. So this function is only reached
    when a required check is actually failing, meaning every failing check here
    is a real merge-gating failure (the gate's underlying jobs). No check name
    is hardcoded and no gate detection is needed: the underlying jobs always
    appear in the failing set alongside the gate.
    """
    return [c for c in checks if c.get("bucket") == "fail"]


def handle(ctx, deps) -> PhaseResult:
    """Execute one merge-phase cycle.

    Body moved from ``_do_merge`` per the Migration Procedure; see module
    docstring for the deviation notes.
    """
    wf = ctx.workflow
    gh = deps.gh
    pr_number = wf.get("github_pr_number")
    branch_name = wf.get("branch_name", "")
    # The branch the PR targets — the one whose protection/ruleset defines which
    # checks are required. Hardcoding "main" silently reports the wrong required
    # set for any workflow targeting another base. Mirrors the
    # ``original_branch_name or "main"`` idiom the orchestrator already uses.
    base_branch = (wf.get("original_branch_name") or "main").strip() or "main"

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
                dev_round=int(wf.get("dev_round", 1) or 1),
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
            # Issue #2673: a PR whose head reports ZERO check-runs on a
            # check-gated base is the GitHub event-delivery-gap signature —
            # required checks can never appear, so the merge below can only
            # be rejected and the phase used to retry silently forever.
            # ``zero_check_runs_fallback`` owns the gate (only fires when the
            # base branch actually requires checks), the wall-clock floored
            # observation window, the close+reopen retrigger and the final
            # visible transient escalation. With check-runs present the same
            # call just closes out any tracker an earlier episode left open
            # (returning False) — one unconditional call, no branching.
            if deps.host.zero_check_runs_fallback(gh, pr_number, pr_head_sha, base_branch, checks):
                return PhaseResult.retry()
            failed = _ci_repair_targets(checks)
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
        pending = _blocking_pending(gh, checks, pr_number, base_branch)
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
                dev_round=int(wf.get("dev_round", 1) or 1),
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
            failed = _ci_repair_targets(refreshed_checks)
            if failed:
                deps.host.start_ci_repair_round(wf, pr_number, failed)
                return PhaseResult.retry()
            pending = _blocking_pending(gh, refreshed_checks, pr_number, base_branch)

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
            # Any mergeability signal — cache-derived "dirty", conflict-
            # rejection text, or a definitive non-mergeable state — can be
            # stale after a prior merge cycle already synced the branch with
            # main (e.g. a conflicts-resolved push). When the branch already
            # contains main there is no real git conflict, so probing ancestry
            # avoids a no-op resolve that merges "Already up to date" and
            # terminally fails with "made no commit" (workflow e274ec0e/#2467:
            # a later cycle re-entered resolve on a branch a prior cycle had
            # already pushed, failing on a PR that was genuinely mergeable).
            # ``branch_contains_main`` is a ground-truth git check — main as
            # an ancestor means no conflict is possible — so it overrules even
            # authoritative-looking text. When the rejection is a stale
            # *conflict* signal, defer so GitHub recomputes mergeability; when
            # it is a *policy* block, fall through to the policy handler below
            # (the block is independent of any git conflict).
            if (
                is_real_conflict
                and pr_head_sha
                and deps.host.branch_contains_main(gh, pr_head_sha, branch_name) is True
            ):
                if is_policy_rejection:
                    logger.info(
                        "PR #%s: branch has main (no git conflict possible); "
                        "the merge rejection is policy (state=%s), deferring "
                        "to policy handling",
                        pr_number,
                        mergeable_state or "unknown",
                    )
                    is_real_conflict = False
                else:
                    logger.info(
                        "PR #%s: conflict signal is stale (branch already has "
                        "main); deferring for GitHub to recompute mergeability",
                        pr_number,
                    )
                    return PhaseResult.retry()
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
                        dev_round=int(wf.get("dev_round", 1) or 1),
                        title="PR merge failed",
                        error_message=f"Merge conflict resolution failed: {resolve_err}",
                    )
                    raise

            if is_policy_rejection:
                # A "policy" rejection text overlaps two situations GitHub
                # reports with the same "repository rule violations" /
                # "required status check" wording:
                #
                #  (a) a required status check is still PENDING for this head
                #      — most often right after a sync/repair push, while an
                #      aggregate required gate (whatever the repo names it)
                #      has not yet propagated its own pending status.
                #      ``_blocking_pending`` only defers for a pending check in
                #      the required set, so it misses a pending *underlying*
                #      job (e.g. ``test (3.10)`` under an aggregate gate) in
                #      that window; GitHub concurrently reports ``blocked`` and
                #      the workflow froze at a manual-recovery pause it could
                #      never recover from (#27 follow-up; cf. 50ba8724 /
                #      c0758607 / cd939cbf / 1c1b63f0).
                #  (b) a genuine non-CI block (missing review, draft, required
                #      signing) where every check has settled.
                #
                # Only (b) warrants a manual-recovery pause. (a) is transient:
                # any pending check, or GitHub still computing
                # (``mergeable_state == "unknown"``), means CI has not settled
                # — keep polling instead of freezing. In practice this is
                # bounded: pending checks complete and the unknown state
                # resolves within minutes, so a workflow does not loop here
                # indefinitely. A permanently-absent required context (a repo
                # misconfig — required context with no provider) has no pending
                # check and a known ``blocked`` state, so it still pauses for a
                # human to fix the ruleset. ``PhaseResult.retry`` does not
                # increment any counter, so the formal backstop for a
                # degenerate stuck-pending/unknown (e.g. a runner that never
                # times out, or a GitHub incident) is the
                # monitor-autonomous-workflows sweep that re-classifies a
                # workflow stuck in this phase.
                any_pending = any(c.get("bucket") == "pending" for c in refreshed_checks)
                if any_pending or mergeable_state == "unknown":
                    logger.info(
                        "PR #%s: merge rejected by policy but CI has not "
                        "settled (state=%s, any_pending=%s); deferring",
                        pr_number,
                        mergeable_state or "unknown",
                        any_pending,
                    )
                    return PhaseResult.retry()
                # Residual #27 race (workflow #2778 / PR #2804): seconds after
                # a CI-repair push, the new head has runs only for fast
                # non-required jobs — the required aggregate gate has no run
                # yet, so no bucket is pending while state is ``blocked``.
                # Required contexts ABSENT from the rollup (not pending, not
                # failed) on a freshly pushed head are unsettled CI: keep
                # deferring. Beyond the grace window the absence is the
                # repo-misconfig signature (required context with no
                # provider) and still pauses for a human. Literal name
                # matching only — the same limitation _blocking_pending has.
                required = _required_contexts(gh, pr_number, base_branch)
                if required:
                    missing_required = required - {(c.get("name") or "") for c in refreshed_checks}
                    if missing_required and _head_freshly_pushed(gh, pr_head_sha):
                        logger.info(
                            "PR #%s: merge rejected by policy with required "
                            "context(s) %s absent on a head pushed within "
                            "%ds; deferring",
                            pr_number,
                            ", ".join(sorted(missing_required)),
                            _POLICY_SETTLE_GRACE_SECONDS,
                        )
                        return PhaseResult.retry()
                # No pending checks and GitHub has finished computing, yet
                # repository policy still requires external action (approval,
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
    # workflow_patch stays empty: cleanup's own writes are already authoritative
    # by the time the result commits (see comment above), and re-writing the
    # cleanup_* fields would clobber cleanup's final status with a stale value.
    # PhaseResult.completed() defaults workflow_patch to {} so we pass no
    # cleanup_* fields.
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
        next_phase="acceptance_verification",
        milestone_events=milestone_events,
    )

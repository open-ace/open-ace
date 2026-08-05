"""Merge-readiness classification (issue #2045 Phase B).

Extends the verify-before-act contract from pure git/graph signals
(:mod:`evidence_service`) to composite GitHub-API signals: the 7-state merge
readiness of a PR. Each method returns an :class:`Evidence` whose
``classification`` carries the finer-grained label while ``verdict`` keeps the
uniform tri-state fail-closed discipline.

Migrated from the inline classification in
``AutonomousOrchestrator._do_merge`` so every merge decision flows through one
auditable contract. The historical incidents #1989 (pending checks mistaken
for conflicts), #1991/#1993 (PR head trusted without local object), #1999
(stale local ref) all stemmed from ad-hoc classification of stale/cached/
derived signals before an irreversible merge.

Phase B ``collect_actionable_ci_failures`` (required/optional split) is added
in a follow-up edit to this same module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .evidence import Evidence, Verdict
from .evidence_service import EvidenceService

if TYPE_CHECKING:
    from .github_ops import GitHubOps


# 7-state merge-readiness labels (issue #2045 Phase B acceptance). Mutually
# exclusive; ``classify_merge_readiness`` returns exactly one.
MERGEABLE = "mergeable"
PENDING_REQUIRED_CHECKS = "pending_required_checks"
FAILING_REQUIRED_CHECKS = "failing_required_checks"
FAILING_OPTIONAL_CHECKS = "failing_optional_checks"
CONFLICT_CONFIRMED = "conflict_confirmed"
POLICY_BLOCKED = "policy_blocked"
READINESS_INDETERMINATE = "indeterminate"

# collect_actionable_ci_failures classifications (Phase B).
ACTIONABLE_REQUIRED_FAILURES = "actionable_required_failures"
OPTIONAL_ONLY_NO_REPAIR = "optional_only_no_repair"
NO_ACTIONABLE_FAILURES = "no_actionable_failures"

_MERGEABLE_STATES = {"clean", "unstable"}
_POLICY_BLOCKED_STATES = {"blocked", "draft"}


class ReadinessService:
    """Classifies merge readiness before an irreversible merge operation.

    Each method returns an :class:`Evidence`. Indeterminate results never
    silently resolve to a definitive state; callers fail closed (defer or
    pause). See issue #2045 Phase B.
    """

    def __init__(self) -> None:
        """Initialize with a Phase A EvidenceService for stale-dirty probes."""
        # Reuses Phase A ancestry/object probes for stale-dirty disambiguation.
        self._evidence = EvidenceService()

    @staticmethod
    def _now() -> datetime:
        """Return the current UTC timestamp; tests patch this single seam."""
        return datetime.now(timezone.utc)

    def _required_contexts(self, gh: GitHubOps, branch: str) -> tuple[list[str], str | None]:
        """Return ``(required check names, error)``. ``error`` is None on success.

        A 404 (no branch protection) is a success with an empty list — "no
        required checks" is a valid answer, not a probe failure. A permission/
        API error returns ``([], reason)`` so the caller surfaces
        ``indeterminate`` rather than guessing every failing check is optional.
        """
        try:
            protection = gh.get_branch_protection(branch)
        except Exception as exc:  # noqa: BLE001 — any fetch failure is indeterminate
            return [], f"branch protection fetch failed: {exc}"
        ctxs = ((protection.get("required_status_checks") or {}).get("contexts")) or []
        return list(ctxs), None

    @staticmethod
    def _partition_checks(
        checks: list[dict], required_contexts: list[str]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Split checks into ``(required_failed, required_pending, optional_failed)``.

        Pending optional checks do not block a merge and are not counted — only
        required pending matters (it maps to ``pending_required_checks``).
        """
        required_set = set(required_contexts)
        required_failed: list[dict] = []
        required_pending: list[dict] = []
        optional_failed: list[dict] = []
        for check in checks or []:
            name = check.get("name", "")
            bucket = check.get("bucket", "")
            if name in required_set:
                if bucket == "fail":
                    required_failed.append(check)
                elif bucket == "pending":
                    required_pending.append(check)
            elif bucket == "fail":
                optional_failed.append(check)
        return required_failed, required_pending, optional_failed

    def classify_merge_readiness(
        self,
        gh: GitHubOps,
        pr_number: int,
        branch_name: str,
        verified_head: str,
    ) -> Evidence:
        """Classify a PR's merge readiness into one of 7 mutually-exclusive states.

        Consumes only verified signals: ``verified_head`` must already be a
        Phase A ``resolve_verified_pr_head`` CONFIRMED SHA — the caller fails
        closed before reaching here if the head is unverifiable. A stale
        GitHub ``dirty`` cache is disambiguated via ancestry
        (``verify_branch_contains``) before being trusted, closing the
        #1991/#1999 failure mode where a cached/derived signal silently drove a
        conflict resolver.

        Returns an :class:`Evidence` with ``subject="merge_readiness"`` and
        ``classification`` set to one of the 7 labels. ``verdict`` maps the
        label to tri-state so the caller can fail-closed uniformly:

        * CONFIRMED — ``mergeable``, ``failing_optional_checks`` (may proceed;
          optional failures do not consume a repair attempt).
        * INDETERMINATE — ``pending_required_checks``, ``indeterminate`` (defer).
        * REJECTED — ``failing_required_checks``, ``conflict_confirmed``,
          ``policy_blocked`` (do not merge; repair / resolve / escalate).
        """
        observed_at = self._now()

        # 1. Gather raw signals — any API failure is indeterminate (fail closed).
        try:
            merge_state = gh.get_pr_merge_state(pr_number)
        except Exception as exc:  # noqa: BLE001 — API failure → indeterminate
            return self._readiness_evidence(
                Verdict.INDETERMINATE,
                READINESS_INDETERMINATE,
                observed_at,
                verified_head,
                reason=f"merge state API failed: {exc}",
            )
        mergeable = merge_state.get("mergeable")
        mergeable_state = str(merge_state.get("mergeable_state") or "").lower()

        try:
            checks = gh.get_pr_checks(pr_number)
        except Exception as exc:  # noqa: BLE001
            return self._readiness_evidence(
                Verdict.INDETERMINATE,
                READINESS_INDETERMINATE,
                observed_at,
                verified_head,
                reason=f"checks API failed: {exc}",
            )

        required_contexts, prot_err = self._required_contexts(gh, "main")
        if prot_err:
            return self._readiness_evidence(
                Verdict.INDETERMINATE,
                READINESS_INDETERMINATE,
                observed_at,
                verified_head,
                reason=prot_err,
            )

        required_failed, required_pending, optional_failed = self._partition_checks(
            checks, required_contexts
        )

        effective_state = mergeable_state

        # 2. Disambiguate a cached "dirty" via ancestry before trusting it.
        if effective_state == "dirty":
            ancestry = self._probe_contains_main(gh, verified_head)
            if ancestry.verdict is Verdict.CONFIRMED:
                # Head already contains main → GitHub's dirty cache is stale
                # (a synthetic merge was just pushed and not yet recomputed).
                # Drop the dirty label and reclassify by the remaining signals.
                effective_state = ""
            elif ancestry.verdict is Verdict.REJECTED:
                return self._readiness_evidence(
                    Verdict.REJECTED,
                    CONFLICT_CONFIRMED,
                    observed_at,
                    verified_head,
                    reason=f"dirty and head lacks main (not stale): {ancestry.reason}",
                )
            else:  # INDETERMINATE
                return self._readiness_evidence(
                    Verdict.INDETERMINATE,
                    READINESS_INDETERMINATE,
                    observed_at,
                    verified_head,
                    reason=f"dirty but ancestry inconclusive: {ancestry.reason}",
                )

        # 3. Required checks drive the merge gate.
        if required_pending:
            return self._readiness_evidence(
                Verdict.INDETERMINATE,
                PENDING_REQUIRED_CHECKS,
                observed_at,
                verified_head,
                reason=f"{len(required_pending)} required check(s) pending",
            )
        if required_failed:
            names = ", ".join(c.get("name", "?") for c in required_failed)
            return self._readiness_evidence(
                Verdict.REJECTED,
                FAILING_REQUIRED_CHECKS,
                observed_at,
                verified_head,
                reason=f"required check(s) failing: {names}",
            )

        # 4. Definitive conflict — mergeable=False with no required issue and no
        #    stale-dirty. (merge_pr text-conflict evidence is only observed
        #    after an attempted merge and is classified by the caller.)
        if mergeable is False and effective_state not in _POLICY_BLOCKED_STATES:
            return self._readiness_evidence(
                Verdict.REJECTED,
                CONFLICT_CONFIRMED,
                observed_at,
                verified_head,
                reason=f"mergeable=False (state={mergeable_state})",
            )

        # 5. Policy block (draft / required reviews / protection rules).
        if effective_state in _POLICY_BLOCKED_STATES:
            return self._readiness_evidence(
                Verdict.REJECTED,
                POLICY_BLOCKED,
                observed_at,
                verified_head,
                reason=f"mergeable_state={effective_state}",
            )

        # 6. Optional-only failure — non-blocking, but recorded so the caller
        #    does not spend a repair attempt on it (#1989/#2034 lesson).
        if optional_failed:
            names = ", ".join(c.get("name", "?") for c in optional_failed)
            return self._readiness_evidence(
                Verdict.CONFIRMED,
                FAILING_OPTIONAL_CHECKS,
                observed_at,
                verified_head,
                reason=f"optional check(s) failing (non-blocking): {names}",
            )

        # 7. Clean path.
        if effective_state in _MERGEABLE_STATES or mergeable is True:
            return self._readiness_evidence(
                Verdict.CONFIRMED,
                MERGEABLE,
                observed_at,
                verified_head,
                reason=f"mergeable (state={effective_state or 'unknown'})",
            )

        # 8. Unknown / behind / anything unclassified — fail closed.
        return self._readiness_evidence(
            Verdict.INDETERMINATE,
            READINESS_INDETERMINATE,
            observed_at,
            verified_head,
            reason=f"unclassified mergeable={mergeable} state={mergeable_state}",
        )

    def _probe_contains_main(self, gh: GitHubOps, verified_head: str) -> Evidence:
        """Probe whether ``verified_head`` is a descendant of current main.

        Mirrors ``AutonomousOrchestrator._branch_contains_main``: fetch origin
        main, resolve FETCH_HEAD, then ``verify_branch_contains``. CONFIRMED
        means head contains main (stale-dirty cache); REJECTED means real
        divergence (true conflict); INDETERMINATE means the probe could not
        answer and the caller must fail closed.
        """
        fetch_res = gh._run_git(["fetch", "origin", "main"], check=False)
        if fetch_res.returncode != 0:
            return Evidence(
                source="git_fetch",
                subject="branch_contains",
                verdict=Verdict.INDETERMINATE,
                observed_at=self._now(),
                verified_at=self._now(),
                verification_method="fetch origin main",
                commit_shas=(verified_head,),
                reason=f"main fetch failed rc={fetch_res.returncode}",
            )
        try:
            main_head = gh.resolve_commit("FETCH_HEAD")
        except Exception as exc:  # noqa: BLE001
            return Evidence(
                source="git_fetch",
                subject="branch_contains",
                verdict=Verdict.INDETERMINATE,
                observed_at=self._now(),
                verified_at=self._now(),
                verification_method="resolve FETCH_HEAD",
                commit_shas=(verified_head,),
                reason=f"main resolve failed: {exc}",
            )
        return self._evidence.verify_branch_contains(gh, head=verified_head, base=main_head)

    def _readiness_evidence(
        self,
        verdict: Verdict,
        classification: str,
        observed_at: datetime,
        verified_head: str,
        reason: str,
    ) -> Evidence:
        """Build a merge-readiness Evidence with the standard source/method."""
        return Evidence(
            source="github_api",
            subject="merge_readiness",
            verdict=verdict,
            observed_at=observed_at,
            verified_at=self._now(),
            verification_method="get_pr_merge_state + get_pr_checks + get_branch_protection",
            commit_shas=(verified_head,) if verified_head else (),
            classification=classification,
            reason=reason,
        )

    def collect_actionable_ci_failures(
        self,
        gh: GitHubOps,
        pr_number: int,
        verified_head: str,
        failed_checks: list[dict],
        branch_name: str = "",
    ) -> tuple[list[dict], Evidence]:
        """Return the required-CI failures worth an AI repair attempt + evidence.

        Filters ``failed_checks`` to required failures (per branch protection)
        that are not cancelled, and attaches a ``failure_excerpt`` to each. Only
        required failures consume a bounded repair attempt — optional failures
        are recorded in the evidence reason but do NOT trigger repair (the
        #1989/#2034 lesson: an optional/``unstable`` failure must not launch the
        conflict/repair path).

        Head binding: the returned evidence carries ``verified_head`` in
        ``commit_shas`` so a stale failure log bound to an older head is
        detectable downstream.

        Returns ``(actionable_required_checks, evidence)`` where the list is the
        enriched required failures (each carries ``failure_excerpt``, possibly
        empty when the log could not be fetched — the caller's
        diagnostics-pending logic decides whether to wait). The evidence
        ``classification`` is one of:

        * ``actionable_required_failures`` (REJECTED) — repair these.
        * ``optional_only_no_repair`` (CONFIRMED) — do not repair; proceed.
        * ``no_actionable_failures`` (CONFIRMED) — nothing to act on.
        * ``indeterminate`` — protection inaccessible; caller defers.
        """
        observed_at = self._now()
        required_contexts, prot_err = self._required_contexts(gh, "main")
        if prot_err:
            return [], self._ci_evidence(
                Verdict.INDETERMINATE,
                READINESS_INDETERMINATE,
                verified_head,
                observed_at,
                reason=prot_err,
            )

        required_set = set(required_contexts)
        actionable: list[dict] = []
        optional_failed: list[dict] = []
        for raw in failed_checks or []:
            if raw.get("bucket") != "fail":
                continue
            if str(raw.get("state") or "").lower() in {"cancelled", "canceled"}:
                continue
            check = dict(raw)
            if check.get("name", "") in required_set:
                try:
                    check["failure_excerpt"] = gh.get_check_failure_excerpt(check)
                except Exception:  # noqa: BLE001 — empty excerpt, caller waits
                    check["failure_excerpt"] = ""
                actionable.append(check)
            else:
                optional_failed.append(check)

        if actionable:
            names = ", ".join(c.get("name", "?") for c in actionable)
            return actionable, self._ci_evidence(
                Verdict.REJECTED,
                ACTIONABLE_REQUIRED_FAILURES,
                verified_head,
                observed_at,
                reason=f"{len(actionable)} required failure(s): {names}",
            )
        if optional_failed:
            names = ", ".join(c.get("name", "?") for c in optional_failed)
            return [], self._ci_evidence(
                Verdict.CONFIRMED,
                OPTIONAL_ONLY_NO_REPAIR,
                verified_head,
                observed_at,
                reason=f"{len(optional_failed)} optional failure(s) (non-blocking): {names}",
            )
        return [], self._ci_evidence(
            Verdict.CONFIRMED,
            NO_ACTIONABLE_FAILURES,
            verified_head,
            observed_at,
            reason="no actionable CI failures",
        )

    def _ci_evidence(
        self,
        verdict: Verdict,
        classification: str,
        verified_head: str,
        observed_at: datetime,
        reason: str,
    ) -> Evidence:
        """Build an actionable-CI Evidence bound to the verified head."""
        return Evidence(
            source="github_api",
            subject="actionable_ci_failures",
            verdict=verdict,
            observed_at=observed_at,
            verified_at=self._now(),
            verification_method="get_pr_checks + get_branch_protection + get_check_failure_excerpt",
            commit_shas=(verified_head,) if verified_head else (),
            classification=classification,
            reason=reason,
        )

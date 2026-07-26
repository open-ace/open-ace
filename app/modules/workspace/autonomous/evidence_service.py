"""Verification service for external/derived signals before irreversible git ops.

Issue #2045 Phase A. Each method returns an :class:`Evidence` carrying a tri-state
:class:`Verdict`. Indeterminate results must never silently resolve to
confirmed/rejected; callers fail closed (defer or abort rather than guess).

The logic here is migrated from the inline helpers on ``AutonomousOrchestrator``
(``_ancestor_check``, ``_ensure_pr_head_local``, ``_branch_contains_main``) so
that all external-signal verification flows through one auditable contract.
Those orchestrator methods are retained as thin delegation wrappers to preserve
the existing call sites and test patches.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .evidence import Evidence, Verdict

if TYPE_CHECKING:
    from .github_ops import GitHubOps


class EvidenceService:
    """Verifies external/derived signals before irreversible git ops.

    Each method returns an :class:`Evidence` carrying a tri-state
    :class:`Verdict`. Indeterminate results never silently resolve to
    confirmed/rejected; callers fail closed (see issue #2045 Phase A).
    """

    @staticmethod
    def _now() -> datetime:
        """Current UTC timestamp; centralized so tests can patch one seam."""
        return datetime.now(timezone.utc)

    def verify_commit_available(
        self,
        gh: GitHubOps,
        sha: str,
        branch_name: str = "",
    ) -> Evidence:
        """Whether the local object DB can resolve ``sha`` (``cat-file -e``).

        Fetches ``branch_name`` first if the object is absent, so the probe has a
        chance to succeed after a worktree teardown (the prior
        ``_ensure_pr_head_local`` behavior). REJECTED means the object truly
        cannot be resolved after fetching; callers must treat that as a
        definitive "no" rather than retrying.
        """
        observed_at = self._now()
        method = "cat-file -e"
        if sha and gh._run_git(["cat-file", "-e", sha], check=False).returncode == 0:
            return Evidence(
                source="local_object_db",
                subject="commit_availability",
                verdict=Verdict.CONFIRMED,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method=method,
                commit_shas=(sha,),
                reason="object present in local DB",
            )
        if branch_name:
            gh._run_git(["fetch", "origin", branch_name])
            method = f"fetch origin {branch_name} + cat-file -e"
        if sha and gh._run_git(["cat-file", "-e", sha], check=False).returncode == 0:
            return Evidence(
                source="git_fetch",
                subject="commit_availability",
                verdict=Verdict.CONFIRMED,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method=method,
                commit_shas=(sha,),
                reason="object fetched then resolved",
            )
        return Evidence(
            source="local_object_db",
            subject="commit_availability",
            verdict=Verdict.REJECTED,
            observed_at=observed_at,
            verified_at=self._now(),
            verification_method=method,
            commit_shas=(sha,) if sha else (),
            reason="object absent after fetch attempt",
        )

    def verify_branch_contains(
        self,
        gh: GitHubOps,
        head: str,
        base: str,
        branch_name: str = "",
    ) -> Evidence:
        """Whether ``head`` has ``base`` as an ancestor.

        Distinguishes a definitive REJECTED (``base`` is NOT reachable from
        ``head``, rc=1) from INDETERMINATE (git error / missing object, rc>=128).
        Ensures ``head`` is local first via :meth:`verify_commit_available`; an
        unavailable head object is INDETERMINATE, not REJECTED, because the
        ancestry probe cannot run.
        """
        observed_at = self._now()
        head_ev = self.verify_commit_available(gh, head, branch_name)
        if head_ev.verdict is not Verdict.CONFIRMED:
            return Evidence(
                source=head_ev.source,
                subject="branch_contains",
                verdict=Verdict.INDETERMINATE,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="merge-base --is-ancestor (blocked)",
                commit_shas=(head, base),
                reason=f"head object unavailable: {head_ev.reason}",
            )
        rc = gh._run_git(["merge-base", "--is-ancestor", base, head], check=False).returncode
        if rc == 0:
            return Evidence(
                source="git_ancestor",
                subject="branch_contains",
                verdict=Verdict.CONFIRMED,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="merge-base --is-ancestor",
                commit_shas=(head, base),
                reason=f"{base[:8]} is ancestor of {head[:8]}",
            )
        if rc == 1:
            return Evidence(
                source="git_ancestor",
                subject="branch_contains",
                verdict=Verdict.REJECTED,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="merge-base --is-ancestor",
                commit_shas=(head, base),
                reason=f"{base[:8]} NOT ancestor of {head[:8]}",
            )
        return Evidence(
            source="git_ancestor",
            subject="branch_contains",
            verdict=Verdict.INDETERMINATE,
            observed_at=observed_at,
            verified_at=self._now(),
            verification_method="merge-base --is-ancestor",
            commit_shas=(head, base),
            reason=f"git error rc={rc}",
        )

    def verify_remote_branch_state(
        self,
        gh: GitHubOps,
        branch_name: str,
        expected_head: str,
    ) -> Evidence:
        """Compare the remote ``branch_name`` head against ``expected_head``.

        Fetches the remote ref, resolves FETCH_HEAD, and compares. Mismatch or
        fetch failure is INDETERMINATE (never silently pick one side); a match
        is CONFIRMED. There is no REJECTED here because a mismatch does not tell
        us which SHA is authoritative — the caller must re-derive expected_head.
        """
        observed_at = self._now()
        fetch_res = gh._run_git(["fetch", "origin", branch_name], check=False)
        if fetch_res.returncode != 0:
            return Evidence(
                source="git_fetch",
                subject="remote_branch_state",
                verdict=Verdict.INDETERMINATE,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method=f"fetch origin {branch_name}",
                commit_shas=(expected_head,),
                reason=f"fetch failed rc={fetch_res.returncode}",
            )
        try:
            remote_head = gh.resolve_commit("FETCH_HEAD")
        except Exception as exc:  # noqa: BLE001 — surface any resolve failure as indeterminate
            return Evidence(
                source="git_fetch",
                subject="remote_branch_state",
                verdict=Verdict.INDETERMINATE,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="resolve FETCH_HEAD",
                commit_shas=(expected_head,),
                reason=f"resolve failed: {exc}",
            )
        if remote_head == expected_head:
            return Evidence(
                source="git_fetch",
                subject="remote_branch_state",
                verdict=Verdict.CONFIRMED,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="fetch + resolve FETCH_HEAD",
                commit_shas=(expected_head,),
                reason="remote head matches expected",
            )
        return Evidence(
            source="git_fetch",
            subject="remote_branch_state",
            verdict=Verdict.INDETERMINATE,
            observed_at=observed_at,
            verified_at=self._now(),
            verification_method="fetch + resolve FETCH_HEAD",
            commit_shas=(expected_head, remote_head),
            reason=f"remote {remote_head[:8]} != expected {expected_head[:8]}",
        )

    def resolve_verified_pr_head(
        self,
        gh: GitHubOps,
        pr_number: int,
        branch_name: str,
    ) -> Evidence:
        """The GitHub API head SHA + fetch + local object existence, in one call.

        Composite of :meth:`verify_commit_available`; this is the canonical entry
        point before any irreversible op on a PR head. Subject is ``pr_head``;
        ``commit_shas[0]`` is the verified SHA (only set when CONFIRMED). An API
        failure or an unverifiable local object is INDETERMINATE so the caller
        defers rather than acting on a raw API SHA.
        """
        observed_at = self._now()
        try:
            api_sha = gh.get_pr_head_sha(pr_number)
        except Exception as exc:  # noqa: BLE001 — any API failure is indeterminate
            return Evidence(
                source="github_api",
                subject="pr_head",
                verdict=Verdict.INDETERMINATE,
                observed_at=observed_at,
                verified_at=self._now(),
                verification_method="get_pr_head_sha",
                commit_shas=(),
                reason=f"API error: {exc}",
            )
        avail = self.verify_commit_available(gh, api_sha, branch_name)
        if avail.verdict is Verdict.CONFIRMED:
            return Evidence(
                source="github_api",
                subject="pr_head",
                verdict=Verdict.CONFIRMED,
                observed_at=observed_at,
                verified_at=avail.verified_at,
                verification_method=f"get_pr_head_sha + {avail.verification_method}",
                commit_shas=(api_sha,),
                reason=f"verified local: {avail.reason}",
            )
        return Evidence(
            source="github_api",
            subject="pr_head",
            verdict=Verdict.INDETERMINATE,
            observed_at=observed_at,
            verified_at=avail.verified_at,
            verification_method=f"get_pr_head_sha + {avail.verification_method}",
            commit_shas=(api_sha,),
            reason=f"head not verifiable locally: {avail.reason}",
        )

"""Evidence contract for verifying external/derived signals before irreversible ops.

Issue #2045 Phase A introduces a uniform tri-state verification semantic so that
GitHub PR head SHAs, remote branch state, commit-graph ancestry, and (in Phase B)
merge readiness / CI failures are never trusted raw. Every verification returns
an :class:`Evidence` carrying a :class:`Verdict` plus enough metadata to audit
*why* a decision was made, not just the outcome.

This module only defines the immutable types; the verification logic lives in
:mod:`app.modules.workspace.autonomous.evidence_service`. The tri-state semantics
mirrors the existing ``bool | None`` pattern already proven by
``AutonomousOrchestrator._ancestor_check`` (True=confirmed, False=rejected,
None=indeterminate).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum


class Verdict(str, Enum):
    """Tri-state outcome of any external-signal verification.

    ``CONFIRMED`` and ``REJECTED`` are definitive commit-graph / API answers the
    caller may act on; ``INDETERMINATE`` is a probe that could not produce a
    definitive answer (git error, missing object, API failure, head mismatch) and
    must fail closed — the caller defers or aborts rather than guessing.
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INDETERMINATE = "indeterminate"

    def to_bool_or_none(self) -> bool | None:
        """Map CONFIRMED→True, REJECTED→False, INDETERMINATE→None.

        Convenience for the legacy ``bool | None`` return contract used by the
        orchestrator delegation layer; new callers should branch on the verdict
        directly.
        """
        if self is Verdict.CONFIRMED:
            return True
        if self is Verdict.REJECTED:
            return False
        return None


@dataclass(frozen=True)
class Evidence:
    """A single verified external signal consumed before an irreversible op.

    Carries enough metadata to audit a decision. See issue #2045 Phase A
    acceptance criteria: evidence must record source, subject, observed/verified
    timestamps, commit SHAs, verification method, and reason.

    Attributes:
        source: Where the raw signal originated — ``"github_api"``,
            ``"local_object_db"``, ``"git_fetch"``, or ``"git_ancestor"``.
        subject: What was verified — ``"pr_head"``, ``"commit_availability"``,
            ``"branch_contains"``, or ``"remote_branch_state"``.
        verdict: Tri-state outcome; never silently resolved away from
            :attr:`Verdict.INDETERMINATE`.
        observed_at: When the raw signal was read (before verification).
        verified_at: When the verification probe completed.
        verification_method: How the verdict was reached, e.g.
            ``"cat-file -e"`` or ``"merge-base --is-ancestor"``.
        commit_shas: SHAs the evidence binds to; the order is method-specific
            (e.g. for ancestry: ``(head, base)``).
        reason: Human-readable explanation of the verdict.
        classification: Optional method-specific sub-result. Empty for Phase A
            probes (``verify_*``); Phase B ``classify_merge_readiness`` sets it
            to one of the 7 merge-readiness labels (``mergeable``,
            ``pending_required_checks``, ``failing_required_checks``,
            ``failing_optional_checks``, ``conflict_confirmed``,
            ``policy_blocked``, ``indeterminate``). ``verdict`` stays tri-state
            so the fail-closed discipline is uniform; ``classification`` carries
            the finer action the caller should take.
    """

    source: str
    subject: str
    verdict: Verdict
    observed_at: datetime
    verified_at: datetime
    verification_method: str
    commit_shas: tuple[str, ...] = ()
    reason: str = ""
    classification: str = ""

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict for milestone/event metadata."""
        data = asdict(self)
        data["verdict"] = self.verdict.value
        data["observed_at"] = self.observed_at.isoformat()
        data["verified_at"] = self.verified_at.isoformat()
        data["commit_shas"] = list(self.commit_shas)
        return data

"""Per-item acceptance verdicts + issue-level aggregation (#2335). Pure."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.workspace.autonomous.evidence import Verdict

# The issue-level status string written to verification_status.
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_INDETERMINATE = "indeterminate"


@dataclass
class ItemVerdict:
    """One acceptance item's verdict, with concrete evidence refs."""

    item: str  # checklist text or required path
    verdict: Verdict  # CONFIRMED / REJECTED / INDETERMINATE
    evidence: list[dict] = field(
        default_factory=list
    )  # [{"ref": "file:line|git-diff", "note": "..."}]
    rationale: str = ""


def aggregate_verdicts(items: list[ItemVerdict]) -> str:
    """Any REJECTED -> rejected; else any INDETERMINATE -> indeterminate; else confirmed.

    An empty item list is indeterminate (nothing was affirmatively confirmed).
    """
    if any(iv.verdict is Verdict.REJECTED for iv in items):
        return STATUS_REJECTED
    if any(iv.verdict is Verdict.INDETERMINATE for iv in items):
        return STATUS_INDETERMINATE
    if items and all(iv.verdict is Verdict.CONFIRMED for iv in items):
        return STATUS_CONFIRMED
    return STATUS_INDETERMINATE

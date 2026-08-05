"""acceptance_verification phase handler (#2335).

Independent post-merge verification: the workflow does NOT auto-close the issue
on merge. Instead this phase spawns a credentialless read-only verifier on the
merged main SHA, runs a deterministic scope gate, aggregates per-item verdicts,
and only closes the issue (as @open-ace-bot) on ``confirmed``.

Task 8 wired the phase into the machine. Task 9 adds the deterministic scope
gate. Task 10 fills in ``handle`` (verifier spawn, snapshot persistence,
aggregation, transitions, idempotency, reopen guard, close-on-confirm).
"""

from __future__ import annotations

import fnmatch

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult


def _glob_matches(pattern: str, paths: list[str]) -> str | None:
    """Return the first changed path matching the pattern (glob), else None."""
    for p in paths:
        if fnmatch.fnmatch(p, pattern) or p == pattern:
            return p
    return None


def run_scope_gate(
    gh, required_paths: list[str], base_sha: str, merge_sha: str
) -> list[ItemVerdict]:
    """Deterministic scope gate: each required path must appear in base..merge diff.

    Returns one ``ItemVerdict`` per required path: CONFIRMED if a changed path
    matches (glob), REJECTED with the missing path as evidence otherwise.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    verdicts: list[ItemVerdict] = []
    for path in required_paths:
        hit = _glob_matches(path, changed)
        if hit is not None:
            verdicts.append(
                ItemVerdict(
                    item=path,
                    verdict=Verdict.CONFIRMED,
                    evidence=[{"ref": f"git-diff:{hit}", "note": "required path present in merge"}],
                )
            )
        else:
            verdicts.append(
                ItemVerdict(
                    item=path,
                    verdict=Verdict.REJECTED,
                    evidence=[
                        {
                            "ref": f"missing:{path}",
                            "note": "required path absent from base..merge diff",
                        }
                    ],
                    rationale="Issue scope requires this path; it was not changed on the merged branch.",
                )
            )
    return verdicts


def handle(ctx, deps) -> PhaseResult:  # pragma: no cover  (replaced in Task 10)
    """Stub: pause safely until the full handler lands (Task 10)."""
    return PhaseResult.pause(
        workflow_patch={"error_message": "acceptance_verification pending implementation (#2335)"},
        structured_error={"message": "acceptance_verification not yet implemented"},
    )

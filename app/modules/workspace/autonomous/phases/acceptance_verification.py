"""acceptance_verification phase handler (#2335).

Independent post-merge verification: the workflow does NOT auto-close the issue
on merge. Instead this phase spawns a credentialless read-only verifier on the
merged main SHA, runs a deterministic scope gate, aggregates per-item verdicts,
and only closes the issue (as @open-ace-bot) on ``confirmed``.

Task 8 wires the phase into the machine + registers this stub. Task 9 adds the
deterministic scope gate. Task 10 fills in ``handle`` (verifier spawn, snapshot
persistence, aggregation, transitions, idempotency, reopen guard, close-on-confirm).
"""

from __future__ import annotations

from app.modules.workspace.autonomous.phase_contract import PhaseResult


def handle(ctx, deps) -> PhaseResult:  # pragma: no cover  (replaced in Task 10)
    """Stub: pause safely until the full handler lands (Task 10)."""
    return PhaseResult.pause(
        workflow_patch={"error_message": "acceptance_verification pending implementation (#2335)"},
        structured_error={"message": "acceptance_verification not yet implemented"},
    )

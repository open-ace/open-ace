"""acceptance_verification phase handler (#2335).

Independent post-merge verification: the workflow does NOT auto-close the issue
on merge (autonomous PRs use ``Implements #N``, not ``Closes #N``). Instead this
phase spawns a credentialless read-only verifier on the merged main SHA, runs a
deterministic scope gate, aggregates per-item verdicts, and only closes the
issue (as @open-ace-bot) on ``confirmed``.

Transitions:
  confirmed      -> close issue + acceptance report -> completed
  rejected       -> new development round (rejection report as feedback),
                    or failed if the dev-round cap is exhausted
  indeterminate  -> paused (issue open; human provides missing evidence)

Idempotent on the confirmed result: a re-entry whose ``verification_status`` is
already ``confirmed`` is a terminal no-op (no re-close, no re-comment). A new
merge SHA or an edited issue (new ``issue_acceptance_hash``) re-runs the
verifier naturally because the phase re-enters; full (merge_sha, hash) dedup
of in-flight attempts is S5 polish.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import time

from app.modules.workspace.autonomous.acceptance_gates import run_mechanical_gates
from app.modules.workspace.autonomous.acceptance_snapshot import (
    AcceptanceSnapshot,
    hash_snapshot,
    parse_acceptance_snapshot,
)
from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict, aggregate_verdicts
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult

VERIFIED_BY = "acceptance-verifier-v1"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _snapshot_to_json(snapshot: AcceptanceSnapshot) -> str:
    """Full snapshot (incl. source/confidence) for round-trip persistence."""
    return json.dumps(dataclasses.asdict(snapshot), ensure_ascii=False)


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


def _verdict_from_str(s: str) -> Verdict:
    s = (s or "").lower()
    if s == "confirmed":
        return Verdict.CONFIRMED
    if s == "rejected":
        return Verdict.REJECTED
    return Verdict.INDETERMINATE


def _parse_issue_body(gh, issue_number) -> str:
    """Fetch the issue body via gh (best-effort; '' on failure/non-dict)."""
    if not issue_number:
        return ""
    try:
        res = gh.get_issue(int(issue_number))
    except Exception:
        return ""
    if not isinstance(res, dict):
        return ""
    body = res.get("body")
    return body if isinstance(body, str) else ""


def _format_report_comment(report: dict) -> str:
    lines = [
        "## ✅ Acceptance verified",
        f"**Merge SHA:** `{report.get('merge_sha')}`",
        f"**Verifier:** `{report.get('verified_by')}`",
        "",
        "**Scope gate:**",
    ]
    for s in report.get("scope", []):
        lines.append(f"- `{s['item']}` — {s['verdict']}")
    if report.get("verifier"):
        lines += ["", "**Verifier findings:**"]
        for v in report["verifier"]:
            lines.append(f"- {v['verdict']} — {v['item']}")
    return "\n".join(lines)


def handle(ctx, deps) -> PhaseResult:
    wf = ctx.workflow
    issue_number = wf.get("github_issue_number")
    pr_number = wf.get("github_pr_number")
    base_sha = wf.get("base_commit_sha") or ""
    gh = deps.gh

    # Idempotency: already confirmed -> terminal no-op (no re-close, no re-verify).
    if wf.get("verification_status") == "confirmed":
        return PhaseResult.completed(next_phase="completed")

    # Resolve the merge commit SHA on main (cache it on the workflow).
    merge_sha = wf.get("verification_merge_sha")
    if not merge_sha and pr_number:
        merge_sha = gh.get_merge_commit_sha(pr_number)
    if not merge_sha or not base_sha:
        # Cannot verify yet (PR not merged / base unknown) — retry next cycle.
        return PhaseResult.retry()

    # Reopen guard: if the issue was closed out-of-band before confirmation, reopen.
    if issue_number and not deps.host.issue_is_open(issue_number):
        gh.reopen_issue(issue_number)
        deps.host.emit_audit_event("acceptance_reopened_issue", {"issue": issue_number})

    # Build the acceptance snapshot (persisted; hash drives re-verification).
    snapshot = None
    if wf.get("issue_acceptance_snapshot"):
        try:
            snapshot = AcceptanceSnapshot(**json.loads(wf["issue_acceptance_snapshot"]))
        except Exception:
            snapshot = None
    if snapshot is None:
        snapshot = parse_acceptance_snapshot(_parse_issue_body(gh, issue_number))
    snap_hash = hash_snapshot(snapshot)

    # Spawn the independent verifier on merged main. If the snapshot was missing
    # convention sections, the verifier extracts scope/checklist (LLM) and returns
    # the completed snapshot; persist it so later rounds reuse it.
    agent_out = (
        deps.host.run_verification_agent(
            snapshot=snapshot,
            merge_sha=merge_sha,
            base_sha=base_sha,
            issue_number=issue_number,
            pr_number=pr_number,
        )
        or {}
    )
    verifier_verdicts = [
        ItemVerdict(
            item=v.get("item", ""),
            verdict=_verdict_from_str(v.get("verdict")),
            evidence=v.get("evidence") or [],
            rationale=v.get("rationale", ""),
        )
        for v in (agent_out.get("verdicts") or [])
    ]
    if agent_out.get("snapshot"):
        try:
            snapshot = AcceptanceSnapshot(**agent_out["snapshot"])
            snap_hash = hash_snapshot(snapshot)
        except Exception:
            pass

    # Mechanical scope gate (deterministic): required paths must be in the diff.
    scope_verdicts = run_scope_gate(gh, snapshot.required_paths, base_sha, merge_sha)

    # The other 5 mechanical gates (#2335 S4): conservative static-analysis
    # checks whose verdicts fold into the issue-level aggregation alongside the
    # scope gate and the verifier findings.
    gate_verdicts = run_mechanical_gates(gh, snapshot, base_sha, merge_sha)

    status = aggregate_verdicts(scope_verdicts + gate_verdicts + verifier_verdicts)
    report = {
        "merge_sha": merge_sha,
        "issue_acceptance_hash": snap_hash,
        "verified_by": VERIFIED_BY,
        "scope": [
            {"item": v.item, "verdict": v.verdict.value, "evidence": v.evidence}
            for v in scope_verdicts
        ],
        "gates": [
            {
                "item": v.item,
                "verdict": v.verdict.value,
                "evidence": v.evidence,
                "rationale": v.rationale,
            }
            for v in gate_verdicts
        ],
        "verifier": [
            {
                "item": v.item,
                "verdict": v.verdict.value,
                "evidence": v.evidence,
                "rationale": v.rationale,
            }
            for v in verifier_verdicts
        ],
        "status": status,
        "verified_at": _now_iso(),
    }

    common_patch = {
        "verification_status": status,
        "verification_merge_sha": merge_sha,
        "verification_started_at": wf.get("verification_started_at") or _now_iso(),
        "verification_completed_at": _now_iso(),
        "verification_report": json.dumps(report, ensure_ascii=False),
        # Persist the FULL snapshot (incl. source/confidence) so a reload
        # round-trips; only the hash is canonicalized to content-only (#2335).
        "issue_acceptance_snapshot": _snapshot_to_json(snapshot),
        "issue_acceptance_hash": snap_hash,
        "verified_by": VERIFIED_BY,
        "verification_attempt": (wf.get("verification_attempt") or 0) + 1,
    }
    milestone = {
        "workflow_id": wf.get("workflow_id"),
        "phase": "acceptance_verification",
        "milestone_type": "acceptance_verification",
        "status": status,
        "title": f"Acceptance verification: {status}",
        "result_summary": report,
        "metadata": report,
    }

    if status == "confirmed":
        gh.add_issue_comment(issue_number, _format_report_comment(report))
        gh.close_issue(issue_number)
        common_patch["issue_closed_by_workflow_at"] = _now_iso()
        return PhaseResult.completed(
            next_phase="completed",
            workflow_patch={**common_patch, "completed_at": _now_iso()},
            milestone_events=[milestone],
        )
    if status == "rejected":
        if deps.host.dev_round_cap_remaining(wf) > 0:
            return PhaseResult.completed(
                next_phase="development",
                workflow_patch={
                    **common_patch,
                    "dev_round": (wf.get("dev_round") or 1) + 1,
                    "error_message": "Acceptance verification rejected: see report",
                },
                milestone_events=[milestone],
            )
        return PhaseResult.failed(
            structured_error={"message": "Acceptance rejected and dev rounds exhausted"},
            workflow_patch=common_patch,
        )
    # indeterminate
    return PhaseResult.pause(
        workflow_patch={
            **common_patch,
            "error_message": "Acceptance indeterminate: awaiting evidence",
        },
        structured_error={"message": "indeterminate", "report": report},
    )

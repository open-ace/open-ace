"""acceptance_verification phase handler (#2335).

Independent post-merge verification. When explicitly disabled, the handler
completes immediately without running the verifier or changing the issue. When
enabled, the workflow does NOT auto-close the issue on merge (autonomous PRs use
``Implements #N``, not ``Closes #N``). Instead this
phase spawns a credentialless read-only verifier on the merged main SHA, runs a
deterministic scope gate, aggregates per-item verdicts, and only closes the
issue (as @open-ace-bot) on ``confirmed``.

Transitions:
  confirmed      -> close issue + acceptance report -> completed
  rejected       -> paused (delivered code is never marked failed; human reviews)
  indeterminate  -> paused (issue open; human provides missing evidence)
  infrastructure -> retry up to 3 times, then pause for review

Idempotent on the confirmed result: a re-entry whose ``verification_status`` is
already ``confirmed`` is a terminal no-op (no re-close, no re-comment). A new
merge SHA or an edited issue (new ``issue_acceptance_hash``) re-runs the
verifier naturally because the phase re-enters. Full (merge_sha, hash) dedup
only reuses settled evidence; infrastructure failures always retry.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import logging
import time
from typing import cast

from app.modules.workspace.autonomous.acceptance_gates import run_mechanical_gates
from app.modules.workspace.autonomous.acceptance_snapshot import (
    AcceptanceSnapshot,
    hash_snapshot,
    parse_acceptance_snapshot,
)
from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict, aggregate_verdicts
from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phase_contract import PhaseResult
from app.utils.config import is_acceptance_verification_enabled

VERIFIED_BY = "acceptance-verifier-v1"
MAX_VERIFIER_INFRA_RETRIES = 3
# A deterministic parse failure (agent output that cannot be parsed) that
# repeats identically is not a transient hiccup — re-running just reproduces the
# same unparseable form. Cap consecutive repeats of that kind here so the
# workflow reaches human review after 2 attempts instead of burning the full
# transient-retry budget on a certain-to-fail retry (#2867).
DETERMINISTIC_PARSE_MAX_RETRIES = 2

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _snapshot_to_json(snapshot: AcceptanceSnapshot) -> str:
    """Full snapshot (incl. source/confidence) for round-trip persistence."""
    return json.dumps(dataclasses.asdict(snapshot), ensure_ascii=False)


def _validate_extracted_snapshot(payload: object) -> AcceptanceSnapshot:
    """Build a conservative LLM-extracted snapshot or raise ``ValueError``.

    When the issue has no convention snapshot, this object becomes the source
    of truth for checklist coverage and scope gates. Accepting unknown/missing
    fields or wrong types would let a fabricated verdict bypass those gates.
    """
    expected_fields = {
        "required_paths",
        "checklist",
        "non_scope",
        "closure_constraints",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("extracted snapshot fields were incomplete or unknown")

    list_fields: dict[str, list[str]] = {}
    for field_name in ("required_paths", "checklist", "non_scope"):
        value = payload[field_name]
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"extracted snapshot {field_name} was not a string list")
        list_fields[field_name] = [item.strip() for item in value]
    if not isinstance(payload["closure_constraints"], bool):
        raise ValueError("extracted snapshot closure_constraints was not boolean")
    if not list_fields["required_paths"] and not list_fields["checklist"]:
        raise ValueError("extracted snapshot contained no verifiable criteria")

    return AcceptanceSnapshot(
        required_paths=list_fields["required_paths"],
        checklist=list_fields["checklist"],
        non_scope=list_fields["non_scope"],
        closure_constraints=payload["closure_constraints"],
        source="llm",
        confidence="low",
    )


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
    try:
        changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    except Exception as exc:  # noqa: BLE001 - git/API failures are inconclusive, not rejection
        return [
            ItemVerdict(
                item="scope:changed-files",
                verdict=Verdict.INDETERMINATE,
                evidence=[{"ref": "git-diff:error", "note": f"scope diff failed: {exc!r}"}],
                rationale="Required-path scope could not be read; verification must pause.",
                retryable=True,
            )
        ]
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


def _normalized_item(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


# Conservative similarity floor for the paraphrase fallback (#2675). The real
# prod paraphrase pair ("..., Windows, and Darwin variants" vs the same minus
# "and") scores 0.889; genuinely different requirements that merely share
# vocabulary (CSV vs XLSX export) score at most 0.75 and stay unmatched.
_FUZZY_ITEM_MATCH_THRESHOLD = 0.8

# Tokens that flip the meaning of a requirement when added or removed.
_NEGATION_MARKER_TOKENS = frozenset({"not", "no", "never", "without"})


def _negation_markers(tokens: list[str]) -> set[str]:
    """Canonical negation markers carried by a token list.

    Contractions ending in ``n't`` and ``cannot`` are canonicalized to
    ``not`` so a paraphrase that only swaps the contraction form ("must
    not" vs "mustn't") keeps matching, while an actual negation flip always
    yields a different set.
    """
    markers: set[str] = set()
    for token in tokens:
        word = token.strip(".,;:!?()[]\"'")
        if not word:
            continue
        if word == "cannot" or word.endswith("n't"):
            markers.add("not")
        elif word in _NEGATION_MARKER_TOKENS:
            markers.add(word)
    return markers


def _contains_token_subsequence(haystack: list[str], needle: list[str]) -> bool:
    """True when ``needle`` appears as a contiguous run of tokens in ``haystack``."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
    )


def _items_match_fuzzy(a: str, b: str) -> float:
    """Conservative similarity in [0, 1] between two checklist item strings.

    1.0 for identical normalized strings, or for an elaboration of the same
    requirement — the short side's full token sequence appears as a
    contiguous token run of the long side AND the short side carries at
    least 3 tokens and at least half the long side's token count. Bare
    character-level substrings ("Auth" inside "OAuth2 token refresh")
    therefore never score 1.0; they fall through to Jaccard. A negation
    flip (one side negates, the other does not) scores 0.0 outright: those
    are semantic opposites, never paraphrases. Everything else is the
    token-set Jaccard similarity of the normalized strings, which
    deliberately ignores word order.
    """
    normalized_a = _normalized_item(a)
    normalized_b = _normalized_item(b)
    if not normalized_a or not normalized_b:
        return 0.0
    if normalized_a == normalized_b:
        return 1.0
    tokens_a = normalized_a.split()
    tokens_b = normalized_b.split()
    if not tokens_a or not tokens_b:
        return 0.0
    if _negation_markers(tokens_a) != _negation_markers(tokens_b):
        # One side negates and the other does not: a semantic opposite must
        # never inherit the other's verdict, however similar the wording.
        return 0.0
    short_tokens, long_tokens = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    if (
        len(short_tokens) >= 3
        and len(short_tokens) / len(long_tokens) >= 0.5
        and _contains_token_subsequence(long_tokens, short_tokens)
    ):
        return 1.0
    return len(set(tokens_a) & set(tokens_b)) / len(set(tokens_a) | set(tokens_b))


def _cover_missing_checklist_items(
    verifier_verdicts: list[ItemVerdict], checklist: list[str]
) -> list[ItemVerdict]:
    """Return verdicts extended to explicitly cover every checklist item (#2675).

    Exact normalized match is the primary coverage path. Checklist items the
    verifier only paraphrased fall back to a conservative fuzzy matcher
    (token-boundary containment or token-Jaccard >=
    ``_FUZZY_ITEM_MATCH_THRESHOLD``) assigned one-to-one, highest similarity
    first; a fuzzy match reuses the matched verdict's own status — only the
    identity matching is fuzzy, never the decision. Anything still unmatched
    gets a synthetic INDETERMINATE so it cannot disappear from the aggregate.
    """
    covered: set[str] = set()
    for verdict in verifier_verdicts:
        normalized = _normalized_item(verdict.item)
        if normalized:
            covered.add(normalized)

    # Greedy one-to-one fuzzy assignment for checklist items without an exact
    # match. Deduplicate by normalized text BEFORE generating candidates so a
    # duplicate entry (discarded by the extension loop anyway) cannot consume
    # the only verdict able to cover a distinct later item.
    candidates: list[tuple[float, int, ItemVerdict]] = []
    seen_for_candidates: set[str] = set()
    for index, checklist_item in enumerate(checklist):
        normalized = _normalized_item(checklist_item)
        if not normalized or normalized in covered or normalized in seen_for_candidates:
            continue
        seen_for_candidates.add(normalized)
        for verdict in verifier_verdicts:
            score = _items_match_fuzzy(checklist_item, verdict.item)
            if score >= _FUZZY_ITEM_MATCH_THRESHOLD:
                candidates.append((score, index, verdict))
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    fuzzy_matched: dict[int, ItemVerdict] = {}
    consumed: set[int] = set()
    for score, index, verdict in candidates:
        if index in fuzzy_matched or id(verdict) in consumed:
            continue
        fuzzy_matched[index] = verdict
        consumed.add(id(verdict))
        logger.info(
            "acceptance verifier paraphrase match: checklist item %r covered by "
            "verifier verdict %r (similarity %.3f)",
            checklist[index],
            verdict.item,
            score,
        )

    extended = list(verifier_verdicts)
    for index, checklist_item in enumerate(checklist):
        normalized = _normalized_item(checklist_item)
        if not normalized or normalized in covered:
            continue
        matched = fuzzy_matched.get(index)
        if matched is not None:
            extended.append(
                ItemVerdict(
                    item=checklist_item,
                    verdict=matched.verdict,
                    evidence=list(matched.evidence),
                    rationale=matched.rationale,
                )
            )
        else:
            extended.append(
                ItemVerdict(
                    item=checklist_item,
                    verdict=Verdict.INDETERMINATE,
                    evidence=[
                        {
                            "ref": "verifier:missing-item",
                            "note": "The verifier returned no verdict for this acceptance item.",
                        }
                    ],
                    rationale=(
                        "The verifier did not cover this checklist item; no acceptance "
                        "decision was made for it."
                    ),
                )
            )
        covered.add(normalized)
    return extended


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


def _failed_items(report: dict) -> list[tuple[str, dict]]:
    """Actionable items: scope/gates/verifier entries not confirmed."""
    out: list[tuple[str, dict]] = []
    for kind in ("scope", "gates", "verifier"):
        for entry in report.get(kind, []):
            if entry.get("verdict") != "confirmed":
                out.append((kind, entry))
    return out


def feedback_prefill_from_report(report: dict) -> str:
    """Pre-fill text for the resume-with-feedback modal (#2491 UX).

    Mirrors the "Rejected / missing" bullet section of the issue comment so
    the user can submit the verifier's failed-items list as feedback verbatim
    or lightly edit it, instead of copy-pasting from GitHub. Shares
    ``_failed_items`` with ``_format_report_comment`` so the two never drift.
    Returns ``""`` when nothing failed.
    """
    status = (report.get("status") or "").lower()
    failed = _failed_items(report)
    if not failed:
        return ""
    label = "Rejected / missing" if status == "rejected" else "Could not verify"
    lines = [f"{label}:"]
    for kind, entry in failed:
        detail = entry.get("rationale") or ""
        if not detail:
            ev = entry.get("evidence") or []
            if ev and isinstance(ev[0], dict):
                detail = ev[0].get("note", "")
        tail = f" — {detail}" if detail else ""
        lines.append(f"- [{kind}] `{entry.get('item')}` ({entry.get('verdict')}){tail}")
    return "\n".join(lines)


_MAX_FEEDBACK_CHARS = 4000


def _rejection_feedback(wf: dict, pr_number) -> str:
    """Dev-round repair target derived from the last rejected verification."""
    raw = wf.get("verification_report") or ""
    try:
        report = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        report = {}
    prefill = feedback_prefill_from_report(report) if isinstance(report, dict) else ""
    delivery = f"PR #{pr_number}" if pr_number else "the previous delivery"
    if prefill:
        body = (
            f"Acceptance verification REJECTED the previous delivery ({delivery}). "
            f"Fix these failed items in this development round:\n\n{prefill}"
        )
    else:
        body = (
            f"Acceptance verification REJECTED the previous delivery ({delivery}). "
            "Review the verification report comment on the issue and fix the failed items."
        )
    return body[:_MAX_FEEDBACK_CHARS]


def _acceptance_summary(status: str, report: dict) -> str:
    """Human-readable one-line summary for the milestone card.

    On confirmed, counts suffice. On rejected/indeterminate, name the items
    that failed so a human reading the card knows what to fix — not just
    ``scope=1 gates=0``. Capped to stay a single readable line.
    """
    failed_names: list[str] = []
    for _, entry in _failed_items(report):
        name = entry.get("item")
        if isinstance(name, str) and name.strip():
            failed_names.append(name.strip())
    if failed_names:
        listed = ", ".join(failed_names[:6])
        extra = f" (+{len(failed_names) - 6} more)" if len(failed_names) > 6 else ""
        return f"status={status}; not-verified: {listed}{extra}"
    return (
        f"status={status}; "
        f"scope={len(report.get('scope', []))} "
        f"gates={len(report.get('gates', []))} "
        f"verifier={len(report.get('verifier', []))}"
    )


def _format_report_comment(report: dict) -> str:
    status = (report.get("status") or "").lower()
    icon = {"confirmed": "✅", "rejected": "❌"}.get(status, "⚠️")
    title = {
        "confirmed": "Acceptance verified",
        "rejected": "Acceptance not verified",
    }.get(status, "Acceptance inconclusive")
    lines = [
        f"## {icon} {title}",
        f"**Merge SHA:** `{report.get('merge_sha')}`",
        f"**Verifier:** `{report.get('verified_by')}`",
    ]
    if status == "confirmed":
        lines += ["", "**Scope gate:**"]
        for s in report.get("scope", []):
            lines.append(f"- `{s['item']}` — {s['verdict']}")
        if report.get("verifier"):
            lines += ["", "**Verifier findings:**"]
            for v in report["verifier"]:
                lines.append(f"- {v['verdict']} — {v['item']}")
        return "\n".join(lines)
    # rejected / indeterminate: surface WHAT failed so a human knows the next step.
    failed = _failed_items(report)
    if failed:
        label = "Rejected / missing" if status == "rejected" else "Could not verify"
        lines += ["", f"**{label}:**"]
        for kind, entry in failed:
            detail = entry.get("rationale") or ""
            if not detail:
                ev = entry.get("evidence") or []
                if ev and isinstance(ev[0], dict):
                    detail = ev[0].get("note", "")
            tail = f" — {detail}" if detail else ""
            lines.append(f"- [{kind}] `{entry.get('item')}` ({entry.get('verdict')}){tail}")
    lines += [
        "",
        "**Next step:** address the items above, then resume the workflow to re-verify.",
    ]
    return "\n".join(lines)


def _post_verdict_comment(deps, issue_number, report) -> None:
    """Best-effort: post the human-readable verdict as an issue comment so the
    author sees WHAT failed + what to do. A comment failure must not block the
    pause — the verdict is also persisted in the workflow/milestone.
    """
    if not issue_number:
        return
    try:
        deps.gh.add_issue_comment(issue_number, _format_report_comment(report))
    except Exception:
        logger.warning(
            "acceptance verifier: failed to post verdict comment for issue %s",
            issue_number,
            exc_info=True,
        )


_TERMINAL_VERIFICATION_STATUSES = frozenset({"confirmed", "rejected", "indeterminate"})


def _already_verified_for(wf: dict, merge_sha: str, snap_hash: str) -> dict | None:
    """Return the prior verification report if this pair is already settled.

    Idempotency key is ``(verification_merge_sha, issue_acceptance_hash)``. If
    the workflow already ran the verifier for the CURRENT pair and reached a
    terminal status (confirmed/rejected/indeterminate), reuse that result
    instead of re-running the expensive verifier. Returns the parsed prior
    report dict, or ``None`` when the pair changed / status is non-terminal /
    no report was persisted. (#2335 S5)
    """
    status = (wf.get("verification_status") or "").strip()
    if status not in _TERMINAL_VERIFICATION_STATUSES:
        return None
    raw = wf.get("verification_report")
    if not raw:
        return None
    try:
        report = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return None
    if not isinstance(report, dict):
        return None
    # Infrastructure failures are observations about an attempt, not settled
    # acceptance evidence. A resume/retry for the same pair must run a fresh
    # verifier instead of permanently replaying the transient failure.
    if report.get("infra_error"):
        return None
    # Match on the CURRENT pair: merge_sha + the hash at verify time. A changed
    # merge (new PR) or an edited issue (new hash) misses and re-verifies.
    if report.get("merge_sha") != merge_sha:
        return None
    if report.get("issue_acceptance_hash") != snap_hash:
        return None
    return report


def _prior_infra_retry_count(wf: dict, merge_sha: str, snap_hash: str) -> int:
    """Return consecutive infra attempts for the current verification pair."""
    raw = wf.get("verification_report")
    if not raw:
        return 0
    try:
        report = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return 0
    if not isinstance(report, dict) or not report.get("infra_error"):
        return 0
    if report.get("merge_sha") != merge_sha or report.get("issue_acceptance_hash") != snap_hash:
        return 0
    try:
        return max(0, int(report.get("infra_retry_count") or 0))
    except (TypeError, ValueError):
        return 0


def _prior_infra_error_kind(wf: dict, merge_sha: str, snap_hash: str) -> str | None:
    """Return the prior attempt's ``infra_error_kind`` for the current pair.

    Used to detect a deterministic parse failure repeating identically across
    consecutive attempts (#2867). Returns ``None`` when the prior report is for
    a different merge/snapshot pair, was not an infra failure, or carried no
    kind (older reports predating the field).
    """
    raw = wf.get("verification_report")
    if not raw:
        return None
    try:
        report = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return None
    if not isinstance(report, dict) or not report.get("infra_error"):
        return None
    if report.get("merge_sha") != merge_sha or report.get("issue_acceptance_hash") != snap_hash:
        return None
    kind = report.get("infra_error_kind")
    return kind if isinstance(kind, str) and kind else None


def _acceptance_milestone(*, workflow_id, dev_round, attempt, status, report) -> dict:
    """Build the acceptance-verification milestone row.

    ``result_summary`` / ``metadata`` are DB columns inserted verbatim by
    ``repo.create_milestone`` (no JSON serialization), so they must be strings
    — passing the raw ``report`` dict crashed with ``can't adapt type 'dict'``
    the moment a verdict was committed (#2394). ``metadata`` keeps the full
    structured report as JSON; ``result_summary`` is a short readable line.
    """
    summary = _acceptance_summary(status, report)
    return {
        "workflow_id": workflow_id,
        "phase": "acceptance_verification",
        "dev_round": dev_round,
        "round_number": attempt,
        "milestone_type": "acceptance_verification",
        "status": status,
        "title": f"Acceptance verification: {status}",
        "result_summary": summary,
        "metadata": json.dumps(report, ensure_ascii=False),
    }


def handle(ctx, deps) -> PhaseResult:
    # Keep this guard before all context/dependency access. Parked production
    # rows may have already released their worktrees and must drain safely while
    # the verifier is opt-in.
    if not is_acceptance_verification_enabled():
        return PhaseResult.completed(next_phase="completed")

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

    # Reopen only a closure performed by the configured service account.  A
    # human-closed issue must stay closed.  The persisted
    # issue_closed_by_workflow_at field cannot distinguish a human close from a
    # GitHub auto-close caused by an agent-authored ``Closes #N`` commit, so use
    # the actual timeline actor instead.
    if issue_number and not deps.host.issue_is_open(issue_number):
        try:
            closure = gh.get_issue_closure(int(issue_number))
            service_login = gh.get_authenticated_login()
        except Exception:  # noqa: BLE001 - uncertainty must preserve the human-visible state
            closure = None
            service_login = None
        closer_login = (closure or {}).get("closer_login") or ""
        if service_login and closer_login.casefold() == service_login.casefold():
            if ctx.cancellation.is_set() is True or deps.host.ensure_scheduler_lock() is False:
                return PhaseResult.retry()
            gh.reopen_issue(issue_number)
            deps.host.emit_audit_event(
                "acceptance_reopened_issue",
                {
                    "issue": issue_number,
                    "closer": closer_login,
                    "closed_at": (closure or {}).get("closed_at"),
                },
            )

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
    snapshot_requires_extraction = snapshot.source == "missing" or not (
        snapshot.required_paths or snapshot.checklist
    )

    # Idempotency (#2335 S5): if the verifier already settled THIS
    # (merge_sha, issue_acceptance_hash) pair to a terminal verdict, reuse the
    # prior result instead of re-running the (expensive) credentialless agent.
    # A changed merge SHA or an edited issue (new hash) misses and re-verifies.
    # An empty/missing snapshot was never a settled acceptance basis: force a
    # fresh extraction even if an older build persisted a terminal-looking
    # report for the same empty hash.
    prior = (
        None if snapshot_requires_extraction else _already_verified_for(wf, merge_sha, snap_hash)
    )
    if prior is not None:
        prior_status = prior.get("status") or (wf.get("verification_status") or "")
        if prior_status == "confirmed":
            return PhaseResult.completed(next_phase="completed")
        if prior_status == "rejected":
            # Replaying the same rejected delivery must remain paused.  The
            # merge worktree has already been cleaned up, so acceptance never
            # tries to re-enter development on that deleted branch.
            return PhaseResult.pause(
                workflow_patch={
                    "verification_status": "rejected",
                    "error_message": "Acceptance already rejected for this merge; awaiting next action",
                },
                structured_error={"message": "replayed-rejected", "report": prior},
            )
        # indeterminate (or anything else terminal) — stay paused for a human.
        return PhaseResult.pause(
            workflow_patch={
                "verification_status": "indeterminate",
                "error_message": "Acceptance indeterminate (reused prior result); awaiting evidence",
            },
            structured_error={"message": "replayed-indeterminate", "report": prior},
        )

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
    verifier_verdicts: list[ItemVerdict] = []
    for index, raw_verdict in enumerate(agent_out.get("verdicts") or []):
        if not isinstance(raw_verdict, dict):
            agent_out["infra_error"] = "verification agent verdict fields were malformed"
            continue
        item = raw_verdict.get("item")
        raw_status = raw_verdict.get("verdict")
        evidence = raw_verdict.get("evidence")
        rationale = raw_verdict.get("rationale", "")
        valid_evidence = isinstance(evidence, list) and all(
            isinstance(entry, dict)
            and isinstance(entry.get("ref"), str)
            and bool(entry.get("ref", "").strip())
            and ("note" not in entry or isinstance(entry.get("note"), str))
            for entry in evidence
        )
        if (
            not isinstance(item, str)
            or not item.strip()
            or raw_status not in {"confirmed", "rejected", "indeterminate"}
            or not valid_evidence
            or not isinstance(rationale, str)
        ):
            agent_out["infra_error"] = (
                f"verification agent verdict fields were malformed at index {index}"
            )
            continue
        evidence_items = cast("list[dict]", evidence)
        verdict = _verdict_from_str(raw_status)
        if verdict in {Verdict.CONFIRMED, Verdict.REJECTED} and not evidence_items:
            verdict = Verdict.INDETERMINATE
            evidence_items = [
                {
                    "ref": "verifier:missing-evidence",
                    "note": "A definitive verifier verdict had no concrete evidence reference.",
                }
            ]
            rationale = "Definitive acceptance verdicts require concrete evidence."
        verifier_verdicts.append(
            ItemVerdict(
                item=item.strip(),
                verdict=verdict,
                evidence=evidence_items,
                rationale=rationale,
            )
        )
    extracted_payload = agent_out.get("snapshot")
    if extracted_payload is not None:
        if not snapshot_requires_extraction:
            # The issue's convention snapshot is authoritative. A verifier
            # must not replace it with an easier LLM-authored checklist.
            agent_out["infra_error"] = (
                "verification agent returned an unexpected snapshot for structured criteria"
            )
        else:
            try:
                snapshot = _validate_extracted_snapshot(extracted_payload)
                snap_hash = hash_snapshot(snapshot)
            except ValueError as exc:
                agent_out["infra_error"] = f"verification agent returned invalid snapshot: {exc}"
    elif snapshot_requires_extraction and not agent_out.get("infra_error"):
        agent_out["infra_error"] = (
            "verification agent omitted the required extracted acceptance snapshot"
        )

    # A syntactically valid verifier response is not necessarily complete.  A
    # missing checklist verdict must never disappear from the aggregate and
    # let unrelated scope/gate confirmations close the issue.  Require an
    # exact item match after harmless whitespace/case normalization (with a
    # conservative paraphrase fallback, #2675); anything still omitted remains
    # explicitly indeterminate for human follow-up.
    verifier_verdicts = _cover_missing_checklist_items(verifier_verdicts, snapshot.checklist)

    # Mechanical scope gate (deterministic): required paths must be in the diff.
    scope_verdicts = run_scope_gate(gh, snapshot.required_paths, base_sha, merge_sha)

    # The other 4 mechanical gates (#2335 S4): conservative static-analysis
    # checks whose verdicts fold into the issue-level aggregation alongside the
    # scope gate and the verifier findings.
    gate_verdicts = run_mechanical_gates(gh, snapshot, base_sha, merge_sha)

    retryable_gate_items = [
        verdict.item for verdict in scope_verdicts + gate_verdicts if verdict.retryable
    ]
    if retryable_gate_items and not agent_out.get("infra_error"):
        agent_out["infra_error"] = "acceptance probes failed to run: " + ", ".join(
            retryable_gate_items
        )
    if agent_out.get("infra_error"):
        verifier_verdicts.append(
            ItemVerdict(
                item="verifier:infrastructure",
                verdict=Verdict.INDETERMINATE,
                evidence=[
                    {
                        "ref": "verifier:infra-error",
                        "note": str(agent_out["infra_error"]),
                    }
                ],
                rationale="The verifier did not complete successfully; no acceptance decision was made.",
                retryable=True,
            )
        )

    status = aggregate_verdicts(scope_verdicts + gate_verdicts + verifier_verdicts)
    if agent_out.get("infra_error"):
        # A probe failure makes the overall attempt inconclusive even if some
        # unrelated deterministic gate happened to reject.
        status = "indeterminate"
    # verified_by records the verifier model/version when the agent surfaced it
    # (S5); fall back to the static runner tag otherwise.
    verified_by = agent_out.get("verified_by") or VERIFIED_BY
    infra_retry_count = 0
    infra_error_kind = None
    if agent_out.get("infra_error"):
        infra_retry_count = _prior_infra_retry_count(wf, merge_sha, snap_hash) + 1
        # infra_error_kind is set only by _parse_verifier_output's parse-failure
        # path, which also returns verdicts=[] and snapshot=None. So whenever a
        # kind is present the per-verdict loop above is empty (its verdict-level
        # infra_error assignments never fire) and the snapshot/probe sites are
        # skipped (None snapshot) or guarded by `not infra_error` — the kind can
        # never go stale relative to THIS attempt's final infra_error.
        infra_error_kind = agent_out.get("infra_error_kind")
    report = {
        "merge_sha": merge_sha,
        "issue_acceptance_hash": snap_hash,
        "verified_by": verified_by,
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
        "infra_error": agent_out.get("infra_error") or None,
        "infra_error_kind": infra_error_kind,
        "infra_retry_count": infra_retry_count,
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
        "verified_by": verified_by,
        "verification_attempt": (wf.get("verification_attempt") or 0) + 1,
    }
    milestone = _acceptance_milestone(
        workflow_id=wf.get("workflow_id"),
        dev_round=int(wf.get("dev_round") or 1),
        attempt=common_patch["verification_attempt"],
        status=status,
        report=report,
    )
    # A deterministic parse failure that repeats identically across consecutive
    # attempts is certain to keep failing, so cap it below the transient budget
    # (#2867). The first occurrence still gets one retry (a genuine one-off is
    # possible); the second consecutive same-kind failure stops early.
    effective_max = MAX_VERIFIER_INFRA_RETRIES
    deterministic_repeat = (
        infra_error_kind == "unparseable_output"
        and _prior_infra_error_kind(wf, merge_sha, snap_hash) == "unparseable_output"
    )
    if deterministic_repeat:
        effective_max = DETERMINISTIC_PARSE_MAX_RETRIES
    if agent_out.get("infra_error") and infra_retry_count < effective_max:
        # Infrastructure failures are not acceptance evidence and must not
        # create a terminal milestone. Keep the workflow in its current phase
        # so the scheduler can retry automatically; the attempt report remains
        # persisted for diagnostics.
        return PhaseResult.retry(
            workflow_patch={
                **common_patch,
                "error_message": "Acceptance verifier infrastructure failure; retrying",
            }
        )
    if agent_out.get("infra_error"):
        exhausted_msg = (
            "Acceptance verifier produced unparseable output "
            f"{infra_retry_count}x; awaiting review"
            if deterministic_repeat
            else "Acceptance verifier infrastructure retries exhausted; awaiting review"
        )
        return PhaseResult.pause(
            workflow_patch={
                **common_patch,
                "error_message": exhausted_msg,
            },
            milestone_events=[milestone],
            structured_error={"message": "verifier-infrastructure-exhausted", "report": report},
        )

    if status == "confirmed":
        if ctx.cancellation.is_set() is True or deps.host.ensure_scheduler_lock() is False:
            return PhaseResult.retry()
        gh.add_issue_comment(issue_number, _format_report_comment(report))
        if ctx.cancellation.is_set() is True or deps.host.ensure_scheduler_lock() is False:
            return PhaseResult.retry()
        gh.close_issue(issue_number)
        common_patch["issue_closed_by_workflow_at"] = _now_iso()
        return PhaseResult.completed(
            next_phase="completed",
            workflow_patch={
                **common_patch,
                "completed_at": _now_iso(),
                "error_message": None,
            },
            milestone_events=[milestone],
        )
    if status == "rejected":
        _post_verdict_comment(deps, issue_number, report)
        return PhaseResult.pause(
            structured_error={
                "message": "Acceptance verification rejected",
                "report": report,
            },
            workflow_patch={
                **common_patch,
                "error_message": "Acceptance verification rejected; awaiting review",
            },
            milestone_events=[milestone],
        )
    # indeterminate
    _post_verdict_comment(deps, issue_number, report)
    return PhaseResult.pause(
        workflow_patch={
            **common_patch,
            "error_message": "Acceptance indeterminate: awaiting evidence",
        },
        milestone_events=[milestone],
        structured_error={"message": "indeterminate", "report": report},
    )

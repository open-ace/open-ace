"""Expected-vs-observed comparator and debt classifier (Issue #2491).

Two responsibilities:

* the **N1 infra rule**: ``infrastructure_error`` is decided from run-envelope
  server-level evidence (process exit / startup readiness / liveness windows /
  run-level environment), never from exception signatures alone. A single
  test's ConnectionRefused while the server is alive is a normal test bug and
  a legal ``deterministic-known-fail`` member, not infra;
* the fail-closed reconciliation of observed attempts against the debt state:
  missing / unexpected / duplicate / changed / unexpected skip/xfail/xpass /
  infra / lane timeout-cancel / resolved-awaiting-shrink all exit non-zero.
  A known-only clean run is a legal success (interpretation (a) of the
  "known-only 正常 run 为 0" acceptance item, pending the issue #2491
  clarification - see docs/dev-notes/2491-rerunfailures-junit-probe.md).
"""

from __future__ import annotations

from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import OUTCOME_CATEGORIES, GovernanceError, failure_fingerprint, normalize_message
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        OUTCOME_CATEGORIES,
        GovernanceError,
        failure_fingerprint,
        normalize_message,
    )

INFRA = "infrastructure_error"
LANE_KILLED_CONCLUSIONS = ("cancelled", "timed_out")


# ---------------------------------------------------------------------------
# N1: infra classification from server-level evidence
# ---------------------------------------------------------------------------


def is_infra_failure(failure: dict[str, Any], server_evidence: dict[str, Any] | None) -> bool:
    """True only when run-envelope server evidence covers the failure.

    Server-level evidence is the *primary and only* trigger for infra:
    readiness not achieved, abnormal server exit, a liveness-failure window
    covering the attempt, or run-level environment missing. Exception
    signatures (ConnectionRefused, timeouts) are deliberately NOT consulted
    here: with the server alive they are ordinary test bugs (failure cluster
    (b) of issue #2491) and must flow into the normal three-way split.
    """
    if not server_evidence:
        return False
    if server_evidence.get("readiness_achieved") is False:
        return True
    exit_info = server_evidence.get("exit") or {}
    if exit_info.get("abnormal"):
        return True
    if server_evidence.get("environment_missing"):
        return True
    timestamp = failure.get("timestamp")
    windows = server_evidence.get("liveness_failures") or []
    if timestamp is None:
        # No timestamp to correlate: only run-global evidence counts.
        return bool(windows) and server_evidence.get("liveness_covered_all") is True
    for window in windows:
        start = window.get("start")
        end = window.get("end", start)
        if start is not None and start <= timestamp and (end is None or timestamp <= end):
            return True
    return False


def classify_failure(failure: dict[str, Any], server_evidence: dict[str, Any] | None) -> str:
    """Classify one failure into an OUTCOME_CATEGORIES member (N1 order)."""
    if is_infra_failure(failure, server_evidence):
        return INFRA
    phase = failure.get("phase", "call")
    exc_class = failure.get("exception_class", "")
    message = normalize_message(failure.get("message") or "")
    if "timeout" in exc_class.lower() or failure.get("timeout"):
        return "timeout"
    if phase == "setup":
        return "setup_error"
    if phase == "collect":
        return "collection_error"
    if "assert" in exc_class.lower() or failure.get("assertion"):
        return "assertion_failure"
    if not server_evidence and "environment" in message.lower():
        # run-level environment loss without envelope evidence at all
        return "environment_missing"
    return "test_body_exception"


def fingerprint_failure(failure: dict[str, Any]) -> str:
    return failure_fingerprint(
        failure.get("exception_class"),
        failure.get("message"),
        failure.get("frames"),
    )


# ---------------------------------------------------------------------------
# Three-way classification from reference runs
# ---------------------------------------------------------------------------


def classify_three_way(runs: list[dict[str, Any]]) -> str:
    """Classify one nodeid from >=3 same-contract reference runs.

    ``stable-pass``: every run's first attempt passed. ``deterministic-
    known-fail``: every run failed with the same normalized outcome and
    fingerprint. ``quarantined-flaky``: anything inconsistent (attempts or
    runs disagree) - the conservative default, including the long tail that
    three runs cannot distinguish.
    """
    if len(runs) < 3:
        raise GovernanceError(f"three-way classification needs >=3 reference runs, got {len(runs)}")
    outcomes = []
    for run in runs:
        category = run.get("category")
        if category == INFRA:
            raise GovernanceError(
                "reference run contains infrastructure_error; infra never "
                "enters a baseline (fix the infrastructure and re-run 3x)"
            )
        outcomes.append((run.get("first_attempt_outcome"), category, run.get("fingerprint")))
    if all(outcome == "pass" for outcome, _, _ in outcomes):
        return "stable-pass"
    if all(outcome == "fail" for outcome, _, _ in outcomes) and len(set(outcomes)) == 1:
        return "deterministic-known-fail"
    return "quarantined-flaky"


def validate_reference_runs(runs: list[dict[str, Any]]) -> list[str]:
    """Hard gate before a baseline may be built: infra count must be zero."""
    errors: list[str] = []
    seen_contracts = {run.get("contract_key") for run in runs}
    if len(seen_contracts) != 1 or None in seen_contracts:
        errors.append(
            f"reference runs must share one contract key, got {sorted(map(str, seen_contracts))}"
        )
    seen_shas = {run.get("commit_sha") for run in runs}
    if len(seen_shas) != 1 or None in seen_shas:
        errors.append(
            f"reference runs must share one commit SHA, got {sorted(map(str, seen_shas))}"
        )
    infra_ids = []
    for run in runs:
        if run.get("category") == INFRA:  # whole-run server failure
            infra_ids.append(f"<run:{run.get('run_id', '?')}>")
        for record in run.get("outcomes") or []:
            if record.get("category") == INFRA:
                infra_ids.append(record.get("nodeid"))
    if infra_ids:
        errors.append(
            f"reference runs contain {len(infra_ids)} infrastructure_error entries "
            f"(e.g. {infra_ids[:3]}): runs are void, fix infra and re-run 3x"
        )
    return errors


# ---------------------------------------------------------------------------
# Expected-vs-observed reconciliation
# ---------------------------------------------------------------------------


def compare_run(
    expected_ids: list[str],
    observed: dict[str, dict[str, Any]],
    state_entries: dict[str, dict[str, Any]],
    *,
    job_conclusion: str | None = None,
    envelope_present: bool = True,
) -> dict[str, Any]:
    """Reconcile one lane run. ``verdict_exit_code`` drives the gate.

    Lane hard-timeout kills are classified ``invalid(timeout/cancel)`` - a
    distinct, explicit category - and never fall into the ``missing`` branch,
    which is reserved for "envelope exists but the item is absent".
    """
    diff: dict[str, Any] = {
        "missing": [],
        "unexpected": [],
        "duplicates": [],
        "new_failures": [],
        "changed": [],
        "unexpected_skips": [],
        "invalid": {},
        "resolved_pending_shrink": [],
        "known_only": False,
    }

    if job_conclusion in LANE_KILLED_CONCLUSIONS and not envelope_present:
        for item_id in sorted(set(expected_ids)):
            diff["invalid"][item_id] = f"invalid(timeout/cancel): lane {job_conclusion}"
        diff["verdict_exit_code"] = 1
        return diff

    if not envelope_present:
        for item_id in sorted(set(expected_ids)):
            diff["invalid"][item_id] = "missing/corrupt run envelope (fail closed)"
        diff["verdict_exit_code"] = 1
        return diff

    seen: dict[str, int] = {}
    for item_id in observed:
        seen[item_id] = seen.get(item_id, 0) + 1
    diff["duplicates"] = sorted(i for i, c in seen.items() if c > 1)

    for item_id in sorted(set(expected_ids) - set(observed)):
        diff["missing"].append(item_id)
    for item_id in sorted(set(observed) - set(expected_ids)):
        diff["unexpected"].append(item_id)

    for item_id, record in sorted(observed.items()):
        state = state_entries.get(item_id) or {}
        debt = state.get("debt", "unclassified")
        outcome = record.get("final_outcome")
        category = record.get("category")
        if category == INFRA:
            diff["invalid"][item_id] = "infrastructure_error (fail closed, never known)"
            continue
        if debt == "resolved":
            diff["resolved_pending_shrink"].append(item_id)
            continue
        if outcome == "pass":
            if debt == "deterministic-known-fail":
                # recovery is handled by the scheduled state machine; a single
                # clean pass never shrinks the baseline
                continue
            continue
        if outcome in ("skip", "xfail"):
            # strict match: a skip is only expected by an expected_skip record
            # and an xfail by an expected_xfail record - the loose or-match
            # would let a skip<->xfail flip pass unnoticed
            expected = state.get(f"expected_{outcome}")
            if not expected:
                diff["unexpected_skips"].append(item_id)
            continue
        if outcome == "xpass":
            diff["unexpected_skips"].append(item_id)
            continue
        if outcome == "fail" or outcome == "error":
            fingerprint = record.get("fingerprint")
            if debt == "deterministic-known-fail":
                if state.get("fingerprint") and fingerprint != state.get("fingerprint"):
                    diff["changed"].append(item_id)
                continue
            if debt in ("quarantined-flaky", "recovering"):
                continue
            diff["new_failures"].append(item_id)
            continue
        diff["invalid"][item_id] = f"unrecognized final_outcome {outcome!r}"

    known = {
        i
        for i, rec in observed.items()
        if (state_entries.get(i) or {}).get("debt") in ("deterministic-known-fail",)
        and rec.get("final_outcome") == "fail"
    }
    diff["known_only"] = (
        bool(known)
        and len(known) == len(observed)
        and not (
            diff["new_failures"]
            or diff["changed"]
            or diff["invalid"]
            or diff["missing"]
            or diff["unexpected"]
            or diff["unexpected_skips"]
            or diff["duplicates"]
            or diff["resolved_pending_shrink"]
        )
    )
    blocking = bool(
        diff["new_failures"]
        or diff["changed"]
        or diff["invalid"]
        or diff["missing"]
        or diff["unexpected"]
        or diff["unexpected_skips"]
        or diff["duplicates"]
        or diff["resolved_pending_shrink"]
    )
    diff["verdict_exit_code"] = 1 if blocking else 0
    return diff

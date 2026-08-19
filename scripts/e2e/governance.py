"""Governance validator + the single legal writer for the E2E JSONs (#2491).

Two deliberately separated entry points (v3 decision: the validator must not
depend on writer code paths, so a writer bug cannot contaminate validation):

* ``validate``  - read-only, scheduled + paths-triggered, fail-closed checks
  over inventory / state / promotion / contract / manifest;
* writer subcommands (``set-disposition``, ``classify``, ``promote``,
  ``quarantine``, ``rehome``, ``mark-manual``, ``remove``) - the only legal
  mutation path for the governance artifacts. Every command atomically
  validates cross-file consistency before writing, so a change to three of
  the four files that is still self-consistent-but-wrong is rejected at
  write time instead of at gate time.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import (
        BUDGETS,
        INVENTORY_SCHEMA_NAME,
        PROMOTION_MAX_RUNS,
        PROMOTION_REVIEW_DAYS,
        PROMOTION_SAMPLE_MIN,
        PROMOTION_STATES,
        QUARANTINE_MAX_DAYS,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )
    from .comparator import validate_reference_runs
    from .inventory import DEFAULT_INVENTORY, entries, load_inventory, validate_inventory
    from .manifest import DEFAULT_MANIFEST, validate_manifest
    from .state import (
        DEFAULT_PROMOTION,
        DEFAULT_STATE,
        EXPECTED_SKIP_REQUIRED_FIELDS,
        QUARANTINE_REQUIRED_FIELDS,
        load_promotion,
        load_state,
        quarantine_status,
        save_promotion,
        save_state,
    )
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        BUDGETS,
        INVENTORY_SCHEMA_NAME,
        PROMOTION_MAX_RUNS,
        PROMOTION_REVIEW_DAYS,
        PROMOTION_SAMPLE_MIN,
        PROMOTION_STATES,
        QUARANTINE_MAX_DAYS,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )
    from comparator import validate_reference_runs  # type: ignore[no-redef]
    from inventory import (  # type: ignore[no-redef]
        DEFAULT_INVENTORY,
        entries,
        load_inventory,
        validate_inventory,
    )
    from manifest import DEFAULT_MANIFEST, validate_manifest  # type: ignore[no-redef]
    from state import (  # type: ignore[no-redef]
        DEFAULT_PROMOTION,
        DEFAULT_STATE,
        EXPECTED_SKIP_REQUIRED_FIELDS,
        QUARANTINE_REQUIRED_FIELDS,
        load_promotion,
        load_state,
        quarantine_status,
        save_promotion,
        save_state,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = PROJECT_ROOT / "ci" / "e2e-contract.json"

# required debt legality for the PR lane (N3 dispositions recorded here)
REQUIRED_PR_LEGAL_DEBT = ("stable-pass", "resolved")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ===========================================================================
# Validator (read-only; no writer imports beyond artifact IO)
# ===========================================================================


def validate_promotion_clocks(promotion: dict[str, Any], now: datetime | None = None) -> list[str]:
    """Dual-clock check: review_by expiry AND effective-run caps (R2)."""
    now = now or _now()
    errors: list[str] = []
    default_review_by = (promotion.get("metadata") or {}).get("default_review_by")
    for item_id, entry in sorted((promotion.get("entries") or {}).items()):
        if not isinstance(entry, dict):
            errors.append(f"{item_id}: promotion entry is not an object")
            continue
        state_ = entry.get("state")
        if state_ not in PROMOTION_STATES:
            errors.append(f"{item_id}: invalid promotion state {state_!r}")
            continue
        review_by = entry.get("review_by") or default_review_by
        if not review_by:
            errors.append(f"{item_id}: no review_by and no file-level default")
        else:
            try:
                if datetime.fromisoformat(review_by) <= now:
                    errors.append(
                        f"{item_id}: {state_} review_by {review_by} expired - "
                        "review PR must decide (promote/rehome/quarantine/manual/delete)"
                    )
            except ValueError:
                errors.append(f"{item_id}: invalid review_by {review_by!r}")
        runs = entry.get("effective_runs")
        if runs is not None:
            cap = PROMOTION_MAX_RUNS[state_]
            if runs > cap:
                errors.append(f"{item_id}: {state_} effective_runs={runs} > cap {cap}")
    return errors


def validate_required_legality(state: dict[str, Any], promotion: dict[str, Any]) -> list[str]:
    """required+flaky must not exist at rest; required+known-fail needs an
    atomic disposition record (N3) until the accepting review PR lands."""
    errors: list[str] = []
    state_entries = state.get("entries") or {}
    for item_id, promo in sorted((promotion.get("entries") or {}).items()):
        if not isinstance(promo, dict) or promo.get("state") != "required":
            continue
        debt = (state_entries.get(item_id) or {}).get("debt", "unclassified")
        if debt == "quarantined-flaky":
            errors.append(
                f"{item_id}: required + quarantined-flaky at rest - the accepting "
                "review PR must atomically demote AND quarantine (owner/issue/expiry<=30d)"
            )
        elif debt not in REQUIRED_PR_LEGAL_DEBT:
            disposition = (state_entries.get(item_id) or {}).get("atomic_disposition")
            if not disposition:
                errors.append(
                    f"{item_id}: required + {debt} - review PR must atomically demote, "
                    "rehome to nightly/weekly governed, or record atomic_disposition"
                )
    return errors


def validate_quarantines(state: dict[str, Any], now: datetime | None = None) -> list[str]:
    now = now or _now()
    errors: list[str] = []
    for item_id, entry in sorted((state.get("entries") or {}).items()):
        quarantine = entry.get("quarantine")
        if not quarantine:
            continue
        for field_name in QUARANTINE_REQUIRED_FIELDS:
            if not quarantine.get(field_name):
                errors.append(f"{item_id}: quarantine missing {field_name!r}")
        expiry = quarantine.get("expiry")
        if expiry:
            try:
                expiry_dt = datetime.fromisoformat(expiry)
            except ValueError:
                errors.append(f"{item_id}: quarantine invalid expiry {expiry!r}")
                continue
            created = quarantine.get("created")
            if created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    if expiry_dt > created_dt + timedelta(days=QUARANTINE_MAX_DAYS):
                        errors.append(f"{item_id}: quarantine window > {QUARANTINE_MAX_DAYS}d")
                except ValueError:
                    errors.append(f"{item_id}: quarantine invalid created {created!r}")
            if expiry_dt <= now:
                errors.append(f"{item_id}: quarantine expired - review PR must decide")
    return errors


def validate_expected_skips(state: dict[str, Any], now: datetime | None = None) -> list[str]:
    now = now or _now()
    errors: list[str] = []
    for item_id, entry in sorted((state.get("entries") or {}).items()):
        for key in ("expected_skip", "expected_xfail"):
            record = entry.get(key)
            if not record:
                continue
            for field_name in EXPECTED_SKIP_REQUIRED_FIELDS:
                if not record.get(field_name):
                    errors.append(f"{item_id}: {key} missing {field_name!r}")
            expiry = record.get("expiry")
            if expiry:
                try:
                    if datetime.fromisoformat(expiry) <= now:
                        errors.append(f"{item_id}: {key} expired (fail closed)")
                except ValueError:
                    errors.append(f"{item_id}: {key} invalid expiry {expiry!r}")
    return errors


def validate_execution_dispositions(
    inventory: dict[str, Any], state: dict[str, Any], promotion: dict[str, Any]
) -> list[str]:
    """Reject state records whose id no longer matches a file's executor."""
    mode_by_path = {str(row.get("path")): row.get("mode") for row in entries(inventory)}
    errors: list[str] = []
    for ledger_name, ledger in (("state", state), ("promotion", promotion)):
        for item_id in ledger.get("entries") or {}:
            standalone = item_id.startswith("standalone::")
            path = item_id.removeprefix("standalone::") if standalone else item_id.split("::", 1)[0]
            mode = mode_by_path.get(path)
            if mode is None:
                continue
            if standalone and mode != "standalone-automated":
                errors.append(
                    f"{item_id}: {ledger_name} entry requires standalone-automated disposition"
                )
            elif not standalone and mode != "pytest-automated":
                errors.append(
                    f"{item_id}: {ledger_name} entry requires pytest-automated disposition"
                )
    return errors


def check_budgets(lane: str, total_minutes: float, per_item_seconds: dict[str, float]) -> list[str]:
    """Duration budget check (Issue #2491 §时长预算)."""
    budget = BUDGETS.get(lane)
    if budget is None:
        return [f"unknown lane {lane!r} for budget check"]
    errors: list[str] = []
    if total_minutes > budget["hard_timeout_minutes"]:
        errors.append(
            f"{lane}: total {total_minutes:.1f}m exceeds hard timeout "
            f"{budget['hard_timeout_minutes']}m"
        )
    cap = budget["per_item_seconds"]
    for item_id, seconds in sorted(per_item_seconds.items()):
        if seconds > cap:
            errors.append(f"{lane}: {item_id} took {seconds:.0f}s > per-item cap {cap}s")
    return errors


def effective_samples(history: list[dict[str, Any]], contract_key: str) -> int:
    """Count valid runs under the CURRENT contract key (R5/Arch note:
    schema_version drift silently empties the 20-run promotion window, so the
    governance summary must surface the number instead of burying it)."""
    return sum(1 for run in history if run.get("contract_key") == contract_key)


def validate_all(
    project_root: Path = PROJECT_ROOT,
    inventory_path: Path = DEFAULT_INVENTORY,
    state_path: Path = DEFAULT_STATE,
    promotion_path: Path = DEFAULT_PROMOTION,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[list[str], dict[str, Any]]:
    """Full read-only governance validation. Empty error list == healthy."""
    errors: list[str] = []
    report: dict[str, Any] = {}
    try:
        inventory = load_inventory(inventory_path)
    except GovernanceError as exc:
        return [str(exc)], report
    errors.extend(validate_inventory(inventory, project_root))
    try:
        manifest = load_artifact(manifest_path, "openace-e2e-expected-nodeids")
    except GovernanceError as exc:
        errors.append(str(exc))
        manifest = None
    if manifest is not None:
        errors.extend(
            validate_manifest(manifest, inventory=inventory, inventory_path=inventory_path)
        )
    try:
        state = load_state(state_path)
    except GovernanceError as exc:
        state = None
        errors.append(str(exc))
    try:
        promotion = load_promotion(promotion_path)
    except GovernanceError as exc:
        promotion = None
        errors.append(str(exc))
    if state is not None:
        errors.extend(validate_quarantines(state))
        errors.extend(validate_expected_skips(state))
    if promotion is not None:
        errors.extend(validate_promotion_clocks(promotion))
    if state is not None and promotion is not None:
        errors.extend(validate_required_legality(state, promotion))
        errors.extend(validate_execution_dispositions(inventory, state, promotion))
    if manifest is not None and state is not None:
        nodeids = set(manifest.get("nodeids") or [])
        for item_id in state.get("entries") or {}:
            if not item_id.startswith("standalone::") and item_id not in nodeids:
                errors.append(f"{item_id}: state entry orphaned (not in manifest)")
    report["counts"] = {
        "inventory_entries": len(entries(inventory)),
        "manifest_nodeids": len(manifest.get("nodeids") or []) if manifest else None,
        "state_entries": len((state or {}).get("entries") or {}),
        "promotion_entries": len((promotion or {}).get("entries") or {}),
    }
    return errors, report


# ===========================================================================
# Writer (the only legal mutation path for governance JSONs)
# ===========================================================================


def _load_or_default(path: Path, schema_name: str, description: str) -> dict[str, Any]:
    try:
        return load_artifact(path, schema_name)
    except GovernanceError:
        return {"description": description, "entries": {}}


def _entries(artifact: dict[str, Any]) -> dict[str, Any]:
    value = artifact.setdefault("entries", {})
    if isinstance(value, list):  # inventory uses a list; state/promotion a map
        raise GovernanceError("unexpected list entries in map-style artifact")
    return value


def cmd_set_disposition(args: argparse.Namespace) -> int:
    inventory = load_inventory(Path(args.inventory))
    rows = entries(inventory)
    hit = False
    for row in rows:
        if row.get("path") == args.path:
            hit = True
            row["mode"] = args.mode
            if args.mode == "manual-demo":
                row["executor"] = "none"
                row["collects"] = False
                row["notes"] = args.note or "manual demo: not automated coverage"
            elif args.mode == "standalone-automated":
                row["executor"] = "standalone"
                row["collects"] = False
                row["notes"] = args.note or "standalone automated entry"
            else:
                row["executor"] = "pytest"
                row["collects"] = True
                row["notes"] = args.note or ""
            if args.home_lane:
                row["home_lane"] = args.home_lane
    if not hit:
        print(f"ERROR: {args.path} not in inventory", file=sys.stderr)
        return 1
    issues = validate_inventory(inventory, Path(args.root))
    if issues:
        for line in issues:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1
    state = load_state(Path(args.state))
    promotion = load_promotion(Path(args.promotion))
    issues = validate_execution_dispositions(inventory, state, promotion)
    if issues:
        for line in issues:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1
    dump_artifact(Path(args.inventory), inventory, INVENTORY_SCHEMA_NAME)
    print(f"set-disposition: {args.path} -> {args.mode}")
    return 0


def cmd_quarantine(args: argparse.Namespace) -> int:
    """Atomically quarantine AND demote (required+flaky can never rest)."""
    state = _load_or_default(Path(args.state), "openace-e2e-state", "E2E debt state")
    promotion = _load_or_default(
        Path(args.promotion), "openace-e2e-promotion", "E2E promotion state"
    )
    state_map = _entries(state)
    promo_map = _entries(promotion)
    if args.id not in state_map:
        print(f"ERROR: {args.id} has no state entry", file=sys.stderr)
        return 1
    created = _now()
    expiry = created + timedelta(days=args.days)
    if args.days > QUARANTINE_MAX_DAYS:
        print(f"ERROR: quarantine window {args.days}d > {QUARANTINE_MAX_DAYS}d", file=sys.stderr)
        return 1
    state_map[args.id]["quarantine"] = {
        "owner": args.owner,
        "issue": args.issue,
        "created": _iso(created),
        "expiry": _iso(expiry),
        "reason": args.reason,
    }
    state_map[args.id]["debt"] = "quarantined-flaky"
    # atomic demotion: a required item turning flaky must not stay required
    promo = promo_map.get(args.id)
    if promo and promo.get("state") == "required":
        promo["state"] = args.demote_to
        promo["review_by"] = _iso(created + timedelta(days=PROMOTION_REVIEW_DAYS[args.demote_to]))
        promo["demoted_from"] = "required"
    save_state(state, Path(args.state))
    save_promotion(promotion, Path(args.promotion))
    print(f"quarantine: {args.id} (owner={args.owner} issue={args.issue} expiry={expiry.date()})")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    promotion = _load_or_default(
        Path(args.promotion), "openace-e2e-promotion", "E2E promotion state"
    )
    state = _load_or_default(Path(args.state), "openace-e2e-state", "E2E debt state")
    promo_map = _entries(promotion)
    state_map = _entries(state)
    if args.id not in state_map:
        print(f"ERROR: {args.id} has no state entry (classify first)", file=sys.stderr)
        return 1
    debt = state_map[args.id].get("debt")
    if args.to == "required":
        if not args.evidence:
            print(
                "ERROR: promote-to-required needs --evidence "
                "(>=20 effective runs, flaky=0, p95<=15m under current contract key)",
                file=sys.stderr,
            )
            return 1
        if debt not in REQUIRED_PR_LEGAL_DEBT:
            print(
                f"ERROR: promote-to-required needs stable-pass/resolved debt, got {debt!r}",
                file=sys.stderr,
            )
            return 1
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        samples = int(evidence.get("effective_samples") or 0)
        if samples < PROMOTION_SAMPLE_MIN:
            print(
                f"ERROR: promotion evidence has {samples} effective runs "
                f"(need >= {PROMOTION_SAMPLE_MIN} under the current contract key)",
                file=sys.stderr,
            )
            return 1
        if evidence.get("flaky_count"):
            print("ERROR: promotion evidence still shows flaky runs", file=sys.stderr)
            return 1
        if float(evidence.get("p95_minutes") or 0) > BUDGETS["candidate"]["p95_target_minutes"]:
            print(
                f"ERROR: p95 {evidence.get('p95_minutes')}m exceeds "
                f"{BUDGETS['candidate']['p95_target_minutes']}m",
                file=sys.stderr,
            )
            return 1
    review_days = PROMOTION_REVIEW_DAYS.get(args.to)
    promo_map[args.id] = {
        "state": args.to,
        "review_by": _iso(_now() + timedelta(days=review_days)) if review_days else None,
        "effective_runs": 0,
    }
    save_promotion(promotion, Path(args.promotion))
    print(f"promote: {args.id} -> {args.to}")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Build ci/e2e-state.json from >=3 same-SHA same-contract reference runs.

    Refuses (infra count must be 0) when any run carries an
    infrastructure_error classification - infra is never legalized as debt.
    """
    try:
        from .comparator import classify_three_way
    except ImportError:  # pragma: no cover - exercised via CLI
        from comparator import classify_three_way  # type: ignore[no-redef]

    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.runs]
    errors = validate_reference_runs(runs)
    if errors:
        for line in errors:
            print(f"ERROR: {line}", file=sys.stderr)
        return 1
    by_id: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        for record in run.get("outcomes") or []:
            by_id.setdefault(record["nodeid"], []).append(record)
    state_map: dict[str, Any] = {}
    for item_id, item_runs in sorted(by_id.items()):
        try:
            debt = classify_three_way(item_runs)
        except GovernanceError as exc:
            # e.g. a nodeid present in fewer than 3 reference runs
            print(f"ERROR: {item_id}: {exc}", file=sys.stderr)
            return 1
        entry: dict[str, Any] = {"debt": debt}
        if debt == "deterministic-known-fail":
            entry["fingerprint"] = item_runs[0].get("fingerprint")
        state_map[item_id] = entry
    save_state(
        {"description": "E2E debt state (issue #2491)", "entries": state_map},
        Path(args.state),
    )
    counts: dict[str, int] = {}
    for entry in state_map.values():
        counts[entry["debt"]] = counts.get(entry["debt"], 0) + 1
    print(f"classify: {len(state_map)} items {json.dumps(counts, sort_keys=True)}")
    return 0


def cmd_rehome(args: argparse.Namespace) -> int:
    inventory = load_inventory(Path(args.inventory))
    for row in entries(inventory):
        if row.get("path") == args.path:
            row["home_lane"] = args.lane
            row["cadence"] = "pr" if args.lane == "pr-critical" else args.lane
            issues = validate_inventory(inventory, Path(args.root))
            if issues:
                for line in issues:
                    print(f"ERROR: {line}", file=sys.stderr)
                return 1
            dump_artifact(Path(args.inventory), inventory, INVENTORY_SCHEMA_NAME)
            print(f"rehome: {args.path} -> {args.lane}")
            return 0
    print(f"ERROR: {args.path} not in inventory", file=sys.stderr)
    return 1


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove one nodeid/entry from state AND promotion atomically."""
    state = _load_or_default(Path(args.state), "openace-e2e-state", "E2E debt state")
    promotion = _load_or_default(
        Path(args.promotion), "openace-e2e-promotion", "E2E promotion state"
    )
    removed = []
    for artifact, path, saver, name in (
        (state, Path(args.state), save_state, "state"),
        (promotion, Path(args.promotion), save_promotion, "promotion"),
    ):
        mapping = _entries(artifact)
        if args.id in mapping:
            del mapping[args.id]
            removed.append(name)
            saver(artifact, path)
    if not removed:
        print(f"ERROR: {args.id} found in neither state nor promotion", file=sys.stderr)
        return 1
    print(f"remove: {args.id} from {', '.join(removed)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    val = sub.add_parser("validate", help="read-only fail-closed governance validation")
    val.add_argument("--root", type=Path, default=PROJECT_ROOT)
    val.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    val.add_argument("--state", type=Path, default=DEFAULT_STATE)
    val.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    val.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    val.add_argument("--json-output", type=Path, default=None)
    val.set_defaults(func=lambda a: _run_validate(a))

    sd = sub.add_parser("set-disposition", help="set a file's inventory disposition")
    sd.add_argument("--path", required=True)
    sd.add_argument(
        "--mode", choices=["pytest-automated", "standalone-automated", "manual-demo"], required=True
    )
    sd.add_argument("--home-lane", default="")
    sd.add_argument("--note", default="")
    sd.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    sd.add_argument("--root", type=Path, default=PROJECT_ROOT)
    sd.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sd.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    sd.set_defaults(func=cmd_set_disposition)

    q = sub.add_parser(
        "quarantine", help="quarantine + atomic demotion (required+flaky never rests)"
    )
    q.add_argument("--id", required=True)
    q.add_argument("--owner", required=True)
    q.add_argument("--issue", type=int, required=True)
    q.add_argument(
        "--days", type=int, required=True, help=f"expiry window (<={QUARANTINE_MAX_DAYS}d)"
    )
    q.add_argument("--reason", default="")
    q.add_argument("--demote-to", choices=["observing", "candidate"], default="observing")
    q.add_argument("--state", type=Path, default=DEFAULT_STATE)
    q.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    q.set_defaults(func=cmd_quarantine)

    p = sub.add_parser("promote", help="observing->candidate->required with evidence gate")
    p.add_argument("--id", required=True)
    p.add_argument("--to", choices=list(PROMOTION_STATES), required=True)
    p.add_argument("--evidence", default="", help="JSON evidence (required only for --to required)")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE)
    p.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    p.set_defaults(func=cmd_promote)

    c = sub.add_parser("classify", help="build state from 3 reference runs (infra=0 hard gate)")
    c.add_argument("--runs", nargs="+", required=True)
    c.add_argument("--state", type=Path, default=DEFAULT_STATE)
    c.set_defaults(func=cmd_classify)

    r = sub.add_parser("rehome", help="move a file's home lane")
    r.add_argument("--path", required=True)
    r.add_argument("--lane", choices=["pr-critical", "nightly", "weekly"], required=True)
    r.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    r.add_argument("--root", type=Path, default=PROJECT_ROOT)
    r.set_defaults(func=cmd_rehome)

    rm = sub.add_parser("remove", help="delete one nodeid/entry from state+promotion atomically")
    rm.add_argument("--id", required=True)
    rm.add_argument("--state", type=Path, default=DEFAULT_STATE)
    rm.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    rm.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    return args.func(args)


def _run_validate(args: argparse.Namespace) -> int:
    errors, report = validate_all(
        project_root=args.root,
        inventory_path=args.inventory,
        state_path=args.state,
        promotion_path=args.promotion,
        manifest_path=args.manifest,
    )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    for line in errors:
        print(f"ERROR: {line}", file=sys.stderr)
    print(f"governance validation: {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

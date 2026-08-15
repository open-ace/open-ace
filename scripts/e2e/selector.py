"""Mutually-exclusive lane selection (Issue #2491 §互斥 lane selection).

The quadrant table below is the normative definition; unit tests generate
assertions cell-by-cell. For every event the four buckets (normal / advisory /
probe / invalid) are pairwise disjoint and their union equals the *applicable*
automated set. Active quarantined-flaky items are explicitly **not applicable**
for PR and Nightly events (quarantine answers to the Weekly probe only), so
they sit outside the closure rather than in an ambiguous bucket.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import (
        DEBT_STATES,
        EVENTS,
        HOME_LANES,
        MANIFEST_SCHEMA_NAME,
        PR_REQUIRED_DEBT,
        PROMOTION_STATES,
        SCHEDULED_REQUIRED_DEBT,
        SELECTION_BUCKETS,
        GovernanceError,
        load_artifact,
    )
    from .inventory import DEFAULT_INVENTORY, entries, load_inventory
    from .manifest import DEFAULT_MANIFEST
    from .state import (
        DEFAULT_PROMOTION,
        DEFAULT_STATE,
        load_promotion,
        load_state,
        quarantine_status,
    )
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        DEBT_STATES,
        EVENTS,
        HOME_LANES,
        MANIFEST_SCHEMA_NAME,
        PR_REQUIRED_DEBT,
        PROMOTION_STATES,
        SCHEDULED_REQUIRED_DEBT,
        SELECTION_BUCKETS,
        GovernanceError,
        load_artifact,
    )
    from inventory import DEFAULT_INVENTORY, entries, load_inventory  # type: ignore[no-redef]
    from manifest import DEFAULT_MANIFEST  # type: ignore[no-redef]
    from state import (  # type: ignore[no-redef]
        DEFAULT_PROMOTION,
        DEFAULT_STATE,
        load_promotion,
        load_state,
        quarantine_status,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Item:
    """One selectable automated unit: a pytest nodeid or standalone entry id."""

    id: str
    home_lane: str
    promotion: str = "observing"
    debt: str = "unclassified"
    quarantine: dict[str, Any] | None = None

    @property
    def quarantine_active(self) -> bool:
        return quarantine_status(self.quarantine) == "active"

    @property
    def quarantine_expired(self) -> bool:
        return quarantine_status(self.quarantine) == "expired"


@dataclass
class Selection:
    event: str
    normal: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)
    probe: list[str] = field(default_factory=list)
    invalid: dict[str, str] = field(default_factory=dict)  # id -> reason
    # Items outside this event's closure by definition (active quarantines on
    # PR/Nightly, non-home items). Tracked for audit, never a bucket.
    not_applicable: dict[str, str] = field(default_factory=dict)

    def closure_errors(self, applicable_ids: set[str]) -> list[str]:
        """Pairwise-disjoint + union-covers-applicable self-check."""
        errors: list[str] = []
        buckets = {
            "normal": set(self.normal),
            "advisory": set(self.advisory),
            "probe": set(self.probe),
            "invalid": set(self.invalid),
        }
        names = list(buckets)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                overlap = buckets[a] & buckets[b]
                if overlap:
                    errors.append(f"buckets {a}/{b} overlap on {sorted(overlap)[:3]}")
        union = set().union(*buckets.values())
        missing = set(applicable_ids) - union
        if missing:
            errors.append(f"applicable items in no bucket: {sorted(missing)[:3]}")
        extra = union - set(applicable_ids)
        if extra:
            errors.append(f"bucket items outside applicable set: {sorted(extra)[:3]}")
        return errors


def _debt_allowed_for_required(event: str, debt: str) -> bool:
    allowed = PR_REQUIRED_DEBT if event == "pr" else SCHEDULED_REQUIRED_DEBT
    return debt in allowed


def classify_item(event: str, item: Item) -> tuple[str, str]:
    """Return ``(bucket, reason)`` for one item under one event.

    Deterministic rule order (Issue #2491): 1) legality/validity -> invalid;
    2) active quarantine + weekly -> probe; 3) required + legal debt ->
    normal; 4) observing/candidate + non-active-flaky -> advisory.
    """
    if item.promotion not in PROMOTION_STATES:
        return "invalid", f"unknown promotion state {item.promotion!r}"
    if item.home_lane not in HOME_LANES:
        return "invalid", f"unknown home lane {item.home_lane!r}"
    if item.debt not in DEBT_STATES:
        return "invalid", f"unknown debt state {item.debt!r}"
    if item.quarantine_expired:
        return "invalid", "quarantine expired without a review-PR decision"
    if item.promotion == "required":
        if item.quarantine_active:
            # required + flaky is stopped at rule 1 and NEVER falls into probe;
            # the accepting review PR must atomically demote + quarantine.
            return (
                "invalid",
                "required + quarantined-flaky: review PR must atomically demote "
                "and quarantine (owner/issue/expiry<=30d)",
            )
        if not _debt_allowed_for_required(event, item.debt):
            if event == "pr":
                return (
                    "invalid",
                    f"PR required lane only allows stable-pass, debt={item.debt!r} "
                    "(N3: accepting review PR must atomically demote or rehome)",
                )
            return "invalid", f"debt {item.debt!r} not legal for required on {event}"
    if item.quarantine_active:
        if event == "weekly":
            return "probe", "active quarantine: weekly flaky probe (only cross-lane redirect)"
        # PR/Nightly: caller excluded active quarantines from the applicable
        # set; reaching here means the closure input was inconsistent.
        return "invalid", "active quarantine reached PR/nightly classification"
    if item.promotion == "required":
        return "normal", f"required + {item.debt} on {event}"
    return "advisory", f"{item.promotion} + {item.debt}: observation lane"


def applicable_ids(event: str, items: list[Item]) -> tuple[set[str], dict[str, str]]:
    """Closure definition: which items this event governs at all.

    PR       -> pr-critical home items (minus active quarantines)
    Nightly  -> pr-critical + nightly home items (minus active quarantines;
                PR-critical is re-run)
    Weekly   -> weekly home items PLUS every active quarantine from any lane
                (the probe is the only allowed cross-lane redirect)
    """
    excluded: dict[str, str] = {}
    applicable: set[str] = set()
    for item in items:
        if event in ("pr", "nightly"):
            if item.home_lane == "weekly":
                excluded[item.id] = "weekly home lane not governed by this event"
                continue
            if event == "pr" and item.home_lane != "pr-critical":
                excluded[item.id] = "not in the PR-critical set"
                continue
            if item.quarantine_active:
                excluded[item.id] = "active quarantine: outside PR/nightly closure"
                continue
        else:  # weekly: weekly-home items + every active quarantine (probe
            # is the only allowed cross-lane redirect); nightly/pr-critical
            # home items are NOT re-run here
            if item.home_lane != "weekly" and not item.quarantine_active:
                excluded[item.id] = "non-weekly home item without an active quarantine"
                continue
        applicable.add(item.id)
    return applicable, excluded


def select(event: str, items: list[Item]) -> Selection:
    """Classify every item into exactly one bucket for ``event``."""
    if event not in EVENTS:
        raise GovernanceError(f"unknown event {event!r}")
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise GovernanceError(f"duplicate selectable item {item.id}")
        seen.add(item.id)

    applicable, excluded = applicable_ids(event, items)
    selection = Selection(event=event, not_applicable=excluded)
    by_id = {item.id: item for item in items}
    for item_id in sorted(applicable):
        bucket, reason = classify_item(event, by_id[item_id])
        if bucket == "normal":
            selection.normal.append(item_id)
        elif bucket == "advisory":
            selection.advisory.append(item_id)
        elif bucket == "probe":
            selection.probe.append(item_id)
        else:
            selection.invalid[item_id] = reason
    return selection


def build_items(
    inventory: dict[str, Any],
    state: dict[str, Any],
    promotion: dict[str, Any],
    manifest_nodeids: list[str],
) -> list[Item]:
    """Compose file-level inventory with nodeid/entry-level governance state."""
    inv_by_path = {e.get("path"): e for e in entries(inventory)}
    state_entries = state.get("entries") or {}
    promo_entries = promotion.get("entries") or {}
    items: list[Item] = []

    def _home_for(path: str) -> str:
        entry = inv_by_path.get(path)
        return entry.get("home_lane", "nightly") if entry else "nightly"

    def _mode_for(path: str) -> str:
        entry = inv_by_path.get(path)
        return entry.get("mode", "pytest-automated") if entry else "pytest-automated"

    # pytest nodeids
    for nodeid in manifest_nodeids:
        path = nodeid.split("::")[0]
        if _mode_for(path) == "manual-demo":
            continue  # manual items never count as automated coverage
        state_e = state_entries.get(nodeid) or {}
        promo_e = promo_entries.get(nodeid) or {}
        items.append(
            Item(
                id=nodeid,
                home_lane=_home_for(path),
                promotion=promo_e.get("state", "observing"),
                debt=state_e.get("debt", "unclassified"),
                quarantine=state_e.get("quarantine"),
            )
        )
    # standalone entries declared by the inventory
    for path, entry in sorted(inv_by_path.items()):
        if entry.get("mode") != "standalone-automated":
            continue
        entry_ids = entry.get("entry_ids") or [f"standalone::{path}"]
        for entry_id in entry_ids:
            state_e = state_entries.get(entry_id) or {}
            promo_e = promo_entries.get(entry_id) or {}
            items.append(
                Item(
                    id=entry_id,
                    home_lane=entry.get("home_lane", "weekly"),
                    promotion=promo_e.get("state", "observing"),
                    debt=state_e.get("debt", "unclassified"),
                    quarantine=state_e.get("quarantine"),
                )
            )
    return items


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=list(EVENTS), required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--promotion", type=Path, default=DEFAULT_PROMOTION)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=None, help="write selection.json")
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="record only: invalid items do not fail (P1-P3 shadow window)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_artifact(args.manifest, MANIFEST_SCHEMA_NAME)
        inventory = load_inventory(args.inventory)
        state = load_state(args.state)
        promotion = load_promotion(args.promotion)
        # build/select also fail closed on malformed state entries (e.g. an
        # unparseable quarantine expiry inside Item.quarantine_active)
        items = build_items(inventory, state, promotion, manifest.get("nodeids", []))
        selection = select(args.event, items)
        applicable, _ = applicable_ids(args.event, items)
    except GovernanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    closure = selection.closure_errors(applicable)

    payload = {
        "event": selection.event,
        "counts": {
            "normal": len(selection.normal),
            "advisory": len(selection.advisory),
            "probe": len(selection.probe),
            "invalid": len(selection.invalid),
            "not_applicable": len(selection.not_applicable),
        },
        "normal": selection.normal,
        "advisory": selection.advisory,
        "probe": selection.probe,
        "invalid": selection.invalid,
        "closure_errors": closure,
    }
    if args.out:
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], indent=2))

    for err in closure:
        print(f"ERROR: closure: {err}", file=sys.stderr)
    for item_id, reason in sorted(selection.invalid.items()):
        print(f"INVALID: {item_id}: {reason}", file=sys.stderr)
    if closure:
        return 1
    if selection.invalid and not args.shadow:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_cli())

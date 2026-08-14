"""Debt-state and promotion-state artifacts plus the recovery state machine.

``ci/e2e-state.json`` is keyed by normalized nodeid / standalone entry id and
holds per-item debt (three-way classification, quarantine, expected
skip/xfail). ``ci/e2e-promotion.json`` is orthogonal and holds
observing/candidate/required with the dual governance clocks. Items absent
from either file default to ``unclassified`` / ``observing`` (Issue #2491:
new automated items start observing + unclassified in the observation lane).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import (
        PROMOTION_SCHEMA_NAME,
        RECOVERY_THRESHOLDS,
        STATE_SCHEMA_NAME,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        PROMOTION_SCHEMA_NAME,
        RECOVERY_THRESHOLDS,
        STATE_SCHEMA_NAME,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = PROJECT_ROOT / "ci" / "e2e-state.json"
DEFAULT_PROMOTION = PROJECT_ROOT / "ci" / "e2e-promotion.json"

QUARANTINE_REQUIRED_FIELDS = ("owner", "issue", "expiry")
EXPECTED_SKIP_REQUIRED_FIELDS = ("reason", "owner", "issue", "expiry")


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def load_state(path: Path = DEFAULT_STATE) -> dict[str, Any]:
    return load_artifact(path, STATE_SCHEMA_NAME)


def load_promotion(path: Path = DEFAULT_PROMOTION) -> dict[str, Any]:
    return load_artifact(path, PROMOTION_SCHEMA_NAME)


def save_state(state: dict[str, Any], path: Path = DEFAULT_STATE) -> None:
    dump_artifact(path, state, STATE_SCHEMA_NAME)


def save_promotion(promotion: dict[str, Any], path: Path = DEFAULT_PROMOTION) -> None:
    dump_artifact(path, promotion, PROMOTION_SCHEMA_NAME)


def quarantine_status(quarantine: dict[str, Any] | None, now: datetime | None = None) -> str:
    """Return ``active`` / ``expired`` / ``none`` for a quarantine record."""
    if not quarantine:
        return "none"
    now = now or datetime.now(timezone.utc)
    try:
        expiry = _parse_date(quarantine["expiry"])
    except (KeyError, ValueError) as exc:
        raise GovernanceError(f"quarantine has invalid expiry: {quarantine!r}") from exc
    return "active" if expiry > now else "expired"


def is_expired(value: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    try:
        return _parse_date(value) <= now
    except ValueError as exc:
        raise GovernanceError(f"invalid ISO date {value!r}") from exc


def max_quarantine_window(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


# ---------------------------------------------------------------------------
# Recovery state machine (Issue #2491 §Flaky 与恢复规则)
# ---------------------------------------------------------------------------


def apply_scheduled_result(
    entry: dict[str, Any], first_attempt_clean: bool
) -> tuple[dict[str, Any], str]:
    """Advance one state entry by one scheduled first-attempt result.

    Single recoveries never shrink the baseline: a known failure needs 3 and a
    flaky item 5 consecutive scheduled first-attempt cleans to become
    ``resolved`` (which then forces a review PR via the comparator). Any
    failure resets the streak and returns the item to its pre-recovery debt.
    """
    entry = dict(entry)
    debt = entry.get("debt")
    streak = int(entry.get("clean_streak") or 0)
    if debt == "resolved":
        return entry, "resolved-awaiting-shrink"
    if debt == "recovering":
        # provenance is mandatory: the origin debt decides the threshold
        origin = entry.get("recovered_from")
        if origin not in RECOVERY_THRESHOLDS:
            raise GovernanceError("recovering entry missing recovered_from provenance")
        debt = origin
    elif debt not in RECOVERY_THRESHOLDS:
        if first_attempt_clean:
            return entry, "clean"
        return entry, "fail"
    if not first_attempt_clean:
        entry["debt"] = debt
        entry["clean_streak"] = 0
        return entry, "fail-reset"
    streak += 1
    threshold = RECOVERY_THRESHOLDS[debt]
    if streak >= threshold:
        entry["debt"] = "resolved"
        entry["clean_streak"] = streak
        entry["resolved_from"] = debt
        return entry, "resolved"
    entry["debt"] = "recovering"
    entry["clean_streak"] = streak
    entry["recovered_from"] = debt
    return entry, "recovering"


def state_entry(
    debt: str,
    *,
    fingerprint: str | None = None,
    quarantine: dict[str, Any] | None = None,
    expected_skip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"debt": debt}
    if fingerprint:
        entry["fingerprint"] = fingerprint
    if quarantine:
        entry["quarantine"] = quarantine
    if expected_skip:
        entry["expected_skip"] = expected_skip
    return entry

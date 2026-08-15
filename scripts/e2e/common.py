"""Shared constants, artifact schemas, and normalization helpers (Issue #2491).

Everything here is pure stdlib so local checkouts and CI runners share one
implementation. The four governance artifacts (inventory / state / promotion /
contract) plus the derived expected-nodeid manifest all carry ``schema_name``
and ``schema_version``; an unknown name or version fails closed everywhere it
is read.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

INVENTORY_SCHEMA_NAME = "openace-e2e-inventory"
STATE_SCHEMA_NAME = "openace-e2e-state"
PROMOTION_SCHEMA_NAME = "openace-e2e-promotion"
CONTRACT_SCHEMA_NAME = "openace-e2e-contract"
MANIFEST_SCHEMA_NAME = "openace-e2e-expected-nodeids"

MODES = ("pytest-automated", "standalone-automated", "manual-demo")
EXECUTORS = ("pytest", "standalone")
EXECUTOR_NONE = "none"

# CI events that drive lane selection.
EVENTS = ("pr", "nightly", "weekly")
HOME_LANES = ("pr-critical", "nightly", "weekly")
SELECTION_BUCKETS = ("normal", "advisory", "probe", "invalid")

CAPABILITIES = ("browser", "server", "remote", "external", "secret")

# Debt states (per nodeid / standalone entry). ``unclassified`` is the default
# for new automated items; ``recovering`` covers known-fail items with 1..N-1
# consecutive scheduled first-attempt cleans; ``resolved`` means the recovery
# threshold was reached and a review PR must shrink the baseline.
DEBT_STATES = (
    "stable-pass",
    "deterministic-known-fail",
    "quarantined-flaky",
    "recovering",
    "resolved",
    "unclassified",
)

PROMOTION_STATES = ("observing", "candidate", "required")

# ``resolved`` behaves like ``stable-pass`` for lane legality (the item is
# clean); the comparator still fails closed until the baseline is shrunk.
PR_REQUIRED_DEBT = frozenset({"stable-pass", "resolved"})
SCHEDULED_REQUIRED_DEBT = frozenset(
    {"stable-pass", "resolved", "deterministic-known-fail", "recovering"}
)

# Recovery rules: consecutive scheduled first-attempt cleans needed before a
# known item becomes ``resolved``.
RECOVERY_THRESHOLDS = {"deterministic-known-fail": 3, "quarantined-flaky": 5}

# Dual-clock governance: calendar days and effective-run caps per promotion
# state. Exceeding either fails the scheduled governance validator.
PROMOTION_REVIEW_DAYS = {"observing": 14, "candidate": 30}
PROMOTION_MAX_RUNS = {"observing": 5, "candidate": 25}
QUARANTINE_MAX_DAYS = 30
PROMOTION_SAMPLE_MIN = 20

# Duration budgets (Issue #2491 §时长预算). ``p95_target_minutes`` of ``None``
# means the lane has no p95 target (samples are still reported).
BUDGETS = {
    "pr-critical": {
        "hard_timeout_minutes": 30,
        "per_item_seconds": 120,
        "p95_target_minutes": None,
    },
    "candidate": {"hard_timeout_minutes": 15, "per_item_seconds": 120, "p95_target_minutes": 15},
    "nightly": {"hard_timeout_minutes": 120, "per_item_seconds": 240, "p95_target_minutes": 90},
    "weekly": {"hard_timeout_minutes": 180, "per_item_seconds": 240, "p95_target_minutes": 150},
}

# Failure categories. ``infrastructure_error`` is decided from run-envelope
# server-level evidence (see comparator.py) and is never allowed into any
# baseline write path.
OUTCOME_CATEGORIES = (
    "collection_error",
    "setup_error",
    "timeout",
    "assertion_failure",
    "test_body_exception",
    "infrastructure_error",
    "environment_missing",
)


class GovernanceError(RuntimeError):
    """A deterministic governance configuration or validation failure."""


# ---------------------------------------------------------------------------
# Artifact IO (fail-closed on schema mismatch)
# ---------------------------------------------------------------------------


def load_artifact(path: Path, expected_name: str) -> dict[str, Any]:
    """Load a governance JSON artifact, failing closed on schema drift."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GovernanceError(f"missing governance artifact: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GovernanceError(f"corrupt governance artifact {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GovernanceError(f"corrupt governance artifact {path}: not an object")
    if data.get("schema_name") != expected_name:
        raise GovernanceError(
            f"{path}: schema_name {data.get('schema_name')!r} != {expected_name!r}"
        )
    version = data.get("schema_version")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise GovernanceError(f"{path}: unsupported schema_version {version!r}")
    return data


def dump_artifact(path: Path, artifact: dict[str, Any], schema_name: str) -> None:
    """Write a governance artifact with the canonical header fields."""
    out = dict(artifact)
    out["schema_name"] = schema_name
    out["schema_version"] = SCHEMA_VERSION
    Path(path).write_text(
        json.dumps(out, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Identity normalization
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_PORT_RE = re.compile(r"(?<=:)\d{4,5}(?=/|\b)")
_DIGITS_RE = re.compile(r"\d+")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_TMP_HOME_RE = re.compile(r"/(tmp|var/folders)/\S+?/(openace|pytest)[^/\s]*/")


def normalize_nodeid(nodeid: str, runner_root: str | None = None) -> str:
    """Return a repo-relative nodeid anchored under ``tests/e2e/...``.

    Strips absolute runner roots, temp-HOME prefixes, ports, UUIDs and
    timestamps. Parametrized ``[...]`` suffixes are identity-bearing and kept.
    """
    n = (nodeid or "").strip()
    if runner_root and n.startswith(runner_root):
        n = n[len(runner_root) :].lstrip("/")
    m = re.search(r"(tests/e2e/.*)$", n, re.S)
    if m:
        n = m.group(1)
    n = _TMP_HOME_RE.sub("<home>/", n)
    n = _UUID_RE.sub("<uuid>", n)
    n = _PORT_RE.sub("<port>", n)
    return n.strip()


def normalize_message(message: str) -> str:
    """Normalize an exception message into a stable signature component."""
    msg = (message or "").splitlines()[0] if message else ""
    msg = _TMP_HOME_RE.sub("<home>/", msg)
    msg = _UUID_RE.sub("<uuid>", msg)
    msg = _HEX_RE.sub("<hex>", msg)
    msg = _PORT_RE.sub("<port>", msg)
    msg = _DIGITS_RE.sub("<n>", msg)
    return msg.strip()[:200]


def failure_fingerprint(
    exception_class: str | None, message: str | None, frames: list[str] | None = None
) -> str:
    """Stable failure fingerprint: exception class + normalized signature.

    ``frames`` is the (already repo-relative) ``file:func`` list of repo stack
    frames. Raw tracebacks are never stored.
    """
    parts = [exception_class or "<none>", normalize_message(message or "")]
    parts.extend(frames or [])
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def contract_key_identity(contract: dict[str, Any]) -> str:
    """Derive the current contract key identity from a contract artifact."""
    fields = contract.get("current") or {}
    if not isinstance(fields, dict) or not fields:
        raise GovernanceError("contract artifact has no current contract fields")
    canonical = json.dumps(fields, sort_keys=True)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def new_id(prefix: str = "entry") -> str:
    """Standalone entry ids are stable strings, not random UUIDs."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

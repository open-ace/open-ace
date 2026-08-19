"""Merge sharded Full E2E run envelopes into one comparator input."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import RUN_ENVELOPE_SCHEMA_NAME, GovernanceError, dump_artifact, load_artifact
except ImportError:  # pragma: no cover - exercised via CLI
    from common import RUN_ENVELOPE_SCHEMA_NAME, GovernanceError, dump_artifact, load_artifact  # type: ignore[no-redef]


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merge(envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge disjoint shard outcomes while preserving the wall-clock budget."""
    if not envelopes:
        raise GovernanceError("cannot merge zero run envelopes")
    for field in ("category", "commit_sha", "contract_key"):
        values = {envelope.get(field) for envelope in envelopes}
        if len(values) != 1:
            raise GovernanceError(f"shard envelopes disagree on {field}: {sorted(values, key=str)}")

    selected_targets: set[str] = set()
    outcomes_by_id: dict[str, dict[str, Any]] = {}
    started = []
    completed = []
    for envelope in envelopes:
        started.append(_timestamp(str(envelope["started_at"])))
        completed.append(_timestamp(str(envelope["completed_at"])))
        for target in envelope.get("selected_targets") or []:
            target = str(target)
            if target in selected_targets:
                raise GovernanceError(f"selected target appears in multiple shards: {target}")
            selected_targets.add(target)
        for outcome in envelope.get("outcomes") or []:
            item_id = str(outcome.get("nodeid", ""))
            if not item_id:
                raise GovernanceError("shard envelope contains an outcome without nodeid")
            if item_id in outcomes_by_id:
                raise GovernanceError(f"outcome appears in multiple shards: {item_id}")
            outcomes_by_id[item_id] = outcome

    all_success = all(envelope.get("job_conclusion") == "success" for envelope in envelopes)
    max_duration = max(float(envelope.get("duration_seconds") or 0) for envelope in envelopes)
    return {
        "category": envelopes[0].get("category"),
        "base_url": "sharded-full-e2e",
        "started_at": min(started).isoformat().replace("+00:00", "Z"),
        "completed_at": max(completed).isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(max_duration, 3),
        "duration_minutes": round(max_duration / 60.0, 3),
        "commit_sha": envelopes[0].get("commit_sha"),
        "contract_key": envelopes[0].get("contract_key"),
        "job_conclusion": "success" if all_success else "failure",
        "return_code": 0 if all_success else 1,
        "error": None if all_success else "one or more Full E2E shards failed",
        "python": envelopes[0].get("python"),
        "playwright_browsers_path": envelopes[0].get("playwright_browsers_path"),
        "isolated_home": None,
        "selected_targets": sorted(selected_targets),
        "pytest_command": [],
        "artifacts": {"shard_count": len(envelopes)},
        "server": {
            "readiness_achieved": all(
                bool((envelope.get("server") or {}).get("readiness_achieved"))
                for envelope in envelopes
            ),
            "exit": {"abnormal": False, "code": None},
            "shard_count": len(envelopes),
        },
        "outcomes": [outcomes_by_id[item_id] for item_id in sorted(outcomes_by_id)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("envelopes", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    merged = merge([load_artifact(path, RUN_ENVELOPE_SCHEMA_NAME) for path in args.envelopes])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    dump_artifact(args.out, merged, RUN_ENVELOPE_SCHEMA_NAME)
    print(f"merged {len(args.envelopes)} Full E2E shard envelopes")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

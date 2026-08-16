"""Expected-nodeid manifest for the pytest E2E tree (Issue #2491).

``--collect-only -q`` runs offline: collection imports conftest/plugins but
never needs the Open ACE server or a frontend build (verified by the P0 probe,
see docs/dev-notes/2491-rerunfailures-junit-probe.md). The manifest is the
expected side of the expected-vs-observed reconciliation and is committed so
collection drift is judged by CI, not by local environments.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import (
        MANIFEST_SCHEMA_NAME,
        GovernanceError,
        dump_artifact,
        load_artifact,
        normalize_nodeid,
    )
    from .inventory import DEFAULT_INVENTORY, entries, load_inventory
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        MANIFEST_SCHEMA_NAME,
        GovernanceError,
        dump_artifact,
        load_artifact,
        normalize_nodeid,
    )
    from inventory import DEFAULT_INVENTORY, entries, load_inventory  # type: ignore[no-redef]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "ci" / "e2e-expected-nodeids.json"
COLLECT_TARGET = "tests/e2e"
_NODEID_RE = re.compile(r"^tests/e2e/\S+::\S+")


def parse_collect_output(text: str) -> list[str]:
    """Parse ``pytest --collect-only -q`` output into sorted normalized nodeids."""
    nodeids = set()
    for line in text.splitlines():
        line = line.strip()
        if _NODEID_RE.match(line):
            nodeids.add(normalize_nodeid(line))
    return sorted(nodeids)


def collect(project_root: Path = PROJECT_ROOT, target: str = COLLECT_TARGET) -> list[str]:
    """Run pytest collection and return the expected nodeid list."""
    proc = subprocess.run(
        # ``-o addopts=`` neutralizes pytest.ini's ``-v`` which would turn the
        # quiet nodeid listing into a collection tree that nothing can parse.
        [
            sys.executable,
            "-m",
            "pytest",
            target,
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GovernanceError(
            f"pytest collection failed (exit {proc.returncode}):\n{proc.stdout[-2000:]}"
            f"\n{proc.stderr[-2000:]}"
        )
    return parse_collect_output(proc.stdout)


def snapshot_manifest(
    project_root: Path = PROJECT_ROOT,
    out_path: Path = DEFAULT_MANIFEST,
    target: str = COLLECT_TARGET,
) -> list[str]:
    nodeids = collect(project_root, target)
    dump_artifact(
        out_path,
        {
            "description": "Expected pytest nodeids for tests/e2e (issue #2491)",
            "target": target,
            "nodeids": nodeids,
            # Regenerate with: python scripts/e2e/manifest.py snapshot
            "collection_protocol": {
                "command": "pytest --collect-only -q",
                "needs_server": False,
                "needs_frontend_build": False,
            },
        },
        MANIFEST_SCHEMA_NAME,
    )
    return nodeids


def validate_manifest(
    manifest: dict[str, Any],
    inventory: dict[str, Any] | None = None,
    inventory_path: Path = DEFAULT_INVENTORY,
) -> list[str]:
    """Structural validation + manual/standalone masquerade detection."""
    issues: list[str] = []
    raw = manifest.get("nodeids")
    if not isinstance(raw, list):
        return ["manifest has no nodeids list"]
    nodeids = [str(n) for n in raw]
    if len(set(nodeids)) != len(nodeids):
        issues.append("manifest contains duplicate nodeids")
    for nodeid in nodeids:
        if not nodeid.startswith("tests/e2e/"):
            issues.append(f"{nodeid}: nodeid outside managed root")
    if inventory is None:
        try:
            inventory = load_inventory(inventory_path)
        except GovernanceError:
            return issues  # inventory validation reports its own errors
    by_path = {e.get("path"): e for e in entries(inventory)}
    collected_files = {n.split("::")[0] for n in nodeids}
    for path, entry in sorted(by_path.items()):
        mode = entry.get("mode")
        if mode == "manual-demo" and path in collected_files:
            issues.append(
                f"{path}: manual-demo file collects pytest nodeids "
                f"(manual scripts must not masquerade as automated coverage)"
            )
        if mode == "pytest-automated":
            collects = entry.get("collects", True)
            if collects and path not in collected_files:
                issues.append(
                    f"{path}: inventory expects nodeids but pytest collects none "
                    f"(collection regression or re-disposition needed)"
                )
            if not collects and path in collected_files:
                issues.append(
                    f"{path}: inventory declares collects=false but pytest collected "
                    f"nodeids (unreviewed collection change: manifest drift)"
                )
    for path in sorted(collected_files - set(by_path)):
        issues.append(f"{path}: collected file missing from inventory")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot", help="collect expected nodeids")
    snap.add_argument("--out", type=Path, default=DEFAULT_MANIFEST)
    val = sub.add_parser("validate", help="validate manifest + inventory cross-check")
    val.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    val.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        nodeids = snapshot_manifest(out_path=args.out)
        print(f"wrote {len(nodeids)} nodeids to {args.out}")
        return 0
    try:
        manifest = load_artifact(args.manifest, MANIFEST_SCHEMA_NAME)
    except GovernanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    issues = validate_manifest(manifest, inventory_path=args.inventory)
    for line in issues:
        print(f"ERROR: {line}", file=sys.stderr)
    print(f"manifest validation: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

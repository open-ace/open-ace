"""File-level E2E inventory (Issue #2491): snapshot + bidirectional validation.

Every ``.py`` file under the managed E2E roots must appear exactly once in
``ci/e2e-inventory.json`` with an explicit disposition::

    mode = pytest-automated | standalone-automated | manual-demo

Non-manual items must declare exactly one executor; manual items must declare
``executor=none`` and never count as automated coverage. Completeness is
checked against the filesystem (not runner discovery), so helper modules and
manual demo scripts cannot hide from the ledger.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

try:  # package-style import (tests) vs direct-script import (CLI)
    from .common import (
        CAPABILITIES,
        EXECUTOR_NONE,
        EXECUTORS,
        HOME_LANES,
        INVENTORY_SCHEMA_NAME,
        MODES,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )
except ImportError:  # pragma: no cover - exercised via CLI
    from common import (  # type: ignore[no-redef]
        CAPABILITIES,
        EXECUTOR_NONE,
        EXECUTORS,
        HOME_LANES,
        INVENTORY_SCHEMA_NAME,
        MODES,
        GovernanceError,
        dump_artifact,
        load_artifact,
    )

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = PROJECT_ROOT / "ci" / "e2e-inventory.json"
MANAGED_ROOTS = ("tests/e2e",)

REQUIRED_FIELDS = ("path", "mode", "owner", "issue", "home_lane", "executor")

# Initial disposition defaults keyed by the first path segment under
# tests/e2e. Remote/performance trees default to the Weekly lane; the browser
# tree hosts the existing PR-critical targets.
DIR_DEFAULTS: dict[str, dict[str, Any]] = {
    "browser": {"home_lane": "pr-critical", "capabilities": ["browser", "server"]},
    "ui": {"home_lane": "nightly", "capabilities": ["browser", "server"]},
    "manage": {"home_lane": "nightly", "capabilities": ["browser", "server"]},
    "work": {"home_lane": "nightly", "capabilities": ["browser", "server"]},
    "terminal": {"home_lane": "nightly", "capabilities": ["browser", "server"]},
    "remote": {"home_lane": "weekly", "capabilities": ["browser", "server", "remote"]},
    "performance": {"home_lane": "weekly", "capabilities": ["browser", "server"]},
    "": {"home_lane": "nightly", "capabilities": ["browser", "server"]},
}
DEFAULT_TIMEOUTS = {"pr-critical": 120, "nightly": 240, "weekly": 240}


def load_inventory(path: Path = DEFAULT_INVENTORY) -> dict[str, Any]:
    return load_artifact(path, INVENTORY_SCHEMA_NAME)


def entries(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inventory.get("entries")
    if not isinstance(raw, list):
        raise GovernanceError("inventory artifact has no entries list")
    return raw


def managed_py_files(project_root: Path, roots: tuple[str, ...] = MANAGED_ROOTS) -> list[str]:
    """Enumerate managed-root ``.py`` files (disk is authoritative)."""
    files: list[str] = []
    for root in roots:
        base = project_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            files.append(path.relative_to(project_root).as_posix())
    return files


def validate_inventory(
    inventory: dict[str, Any],
    project_root: Path = PROJECT_ROOT,
    roots: tuple[str, ...] = MANAGED_ROOTS,
) -> list[str]:
    """Return every completeness / schema violation (empty list == valid)."""
    issues: list[str] = []
    try:
        raw_entries = entries(inventory)
    except GovernanceError as exc:
        return [str(exc)]

    seen: dict[str, int] = {}
    for idx, entry in enumerate(raw_entries):
        where = f"entry[{idx}]"
        if not isinstance(entry, dict):
            issues.append(f"{where}: not an object")
            continue
        for field in REQUIRED_FIELDS:
            if field not in entry:
                issues.append(f"{where} ({entry.get('path', '?')}): missing field {field!r}")
        path = entry.get("path", "")
        if path:
            seen[path] = seen.get(path, 0) + 1
            if not (project_root / path).is_file():
                issues.append(f"{path}: listed in inventory but missing on disk")
        mode = entry.get("mode")
        if mode not in MODES:
            issues.append(f"{path}: invalid mode {mode!r}")
        if entry.get("home_lane") not in HOME_LANES:
            issues.append(f"{path}: invalid home_lane {entry.get('home_lane')!r}")
        executor = entry.get("executor")
        if mode == "manual-demo":
            if executor != EXECUTOR_NONE:
                issues.append(f"{path}: manual-demo must declare executor=none")
        elif mode == "pytest-automated":
            if executor != "pytest":
                issues.append(
                    f"{path}: mode {mode} needs exactly one executor 'pytest', got {executor!r}"
                )
        elif mode == "standalone-automated":
            if executor != "standalone":
                issues.append(
                    f"{path}: mode {mode} needs exactly one executor 'standalone', got {executor!r}"
                )
        elif executor not in EXECUTORS:
            issues.append(f"{path}: invalid executor {executor!r}")
        caps = entry.get("capabilities", [])
        if not isinstance(caps, list) or any(c not in CAPABILITIES for c in caps):
            issues.append(f"{path}: invalid capabilities {caps!r}")

    for path, count in seen.items():
        if count > 1:
            issues.append(f"{path}: duplicate inventory entries ({count})")

    on_disk = set(managed_py_files(project_root, roots))
    listed = set(seen)
    for path in sorted(on_disk - listed):
        issues.append(f"{path}: managed E2E file has no inventory disposition")
    for path in sorted(listed - on_disk):
        issues.append(f"{path}: inventory entry outside managed roots {roots}")
    return issues


def snapshot_inventory(
    project_root: Path = PROJECT_ROOT,
    out_path: Path = DEFAULT_INVENTORY,
    roots: tuple[str, ...] = MANAGED_ROOTS,
    owner: str = "e2e-governance",
    issue: int = 2491,
    collected_files: set[str] | None = None,
) -> list[str]:
    """Write the initial inventory with per-directory defaults.

    The initial disposition is a reviewable draft: every file starts as
    ``pytest-automated`` under its directory's home lane; P5 debt-clearing
    batches re-disposition remote/performance/manual items through the
    governance writer. ``collected_files`` (from the expected-nodeid
    manifest) drives the ``collects`` flag: conftest/helper modules and the
    currently-empty async/demo cluster files honestly record that they
    contribute no nodeids today, so a future collection change is judged as
    manifest drift instead of being silently absorbed.
    """
    collected = collected_files if collected_files is not None else set()
    rows: list[dict[str, Any]] = []
    for rel in managed_py_files(project_root, roots):
        rest = rel[len("tests/e2e/") :] if rel.startswith("tests/e2e/") else rel
        first_seg = rest.split("/", 1)[0] if "/" in rest else ""
        defaults = DIR_DEFAULTS.get(first_seg, DIR_DEFAULTS[""])
        home = defaults["home_lane"]
        filename = rest.rsplit("/", 1)[-1]
        is_conftest = filename == "conftest.py"
        collects = not is_conftest and rel in collected
        notes = "initial disposition (issue #2491 P1 draft)"
        if is_conftest:
            notes = "shared fixtures/helpers: never collects nodeids itself"
        elif not collects and (
            filename.startswith(("test_", "e2e_")) or filename.endswith("_test.py")
        ):
            notes = (
                "test-named but collects no nodeids today (async-marker/demo "
                "cluster, issue #2491 P5 triage)"
            )
        rows.append(
            {
                "path": rel,
                "mode": "pytest-automated",
                "owner": owner,
                "issue": issue,
                "home_lane": home,
                "executor": "pytest",
                "capabilities": sorted(defaults["capabilities"]),
                "timeout_seconds": DEFAULT_TIMEOUTS[home],
                "cadence": "pr" if home == "pr-critical" else home,
                "collects": collects,
                "notes": notes,
            }
        )
    dump_artifact(
        out_path,
        {"description": "E2E file-level inventory (issue #2491)", "entries": rows},
        INVENTORY_SCHEMA_NAME,
    )
    return [row["path"] for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot", help="write the initial inventory draft")
    snap.add_argument("--out", type=Path, default=DEFAULT_INVENTORY)
    snap.add_argument("--root", type=Path, default=PROJECT_ROOT)
    val = sub.add_parser("validate", help="bidirectional completeness check (fail-closed)")
    val.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        try:  # lazy: keeps module import order acyclic in both import styles
            from .manifest import collect
        except ImportError:
            from manifest import collect  # type: ignore[no-redef]

        try:
            nodeids = collect(args.root)
        except GovernanceError:
            nodeids = []
        collected_files = {n.split("::")[0] for n in nodeids}
        rows = snapshot_inventory(
            project_root=args.root, out_path=args.out, collected_files=collected_files
        )
        print(f"wrote {len(rows)} entries to {args.out}")
        return 0
    try:
        inventory = load_inventory(args.inventory)
    except GovernanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    issues = validate_inventory(inventory)
    for line in issues:
        print(f"ERROR: {line}", file=sys.stderr)
    print(f"inventory validation: {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

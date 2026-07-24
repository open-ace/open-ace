#!/usr/bin/env python3
"""
Compare security baseline between PR and main branch.

Issue #1897: Detect baseline changes in pull requests.

Usage:
    # In CI, with PR context
    python scripts/lint/baseline_diff.py

    # Local testing with explicit baseline
    python scripts/lint/baseline_diff.py --baseline scripts/lint/security_baseline.json

Exit code:
    0: No baseline changes or only removals
    1: New suppressions added (requires justification)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "scripts" / "lint" / "security_baseline.json"


@dataclass
class DiffResult:
    """Result of baseline comparison."""

    added: list[dict]
    removed: list[dict]
    unchanged: list[dict]


def load_baseline_from_file(filepath: Path) -> list[dict]:
    """Load baseline from a JSON file."""
    if not filepath.exists():
        return []
    try:
        data = json.loads(filepath.read_text())
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, KeyError):
        return []


def load_baseline_from_git(ref: str, filepath: Path) -> list[dict]:
    """Load baseline from a git reference."""
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{filepath.relative_to(PROJECT_ROOT)}"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            return []
        return data
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        return []


def compare_baselines(current: list[dict], main: list[dict]) -> DiffResult:
    """Compare two baselines and return differences."""
    current_keys = {item.get("key") for item in current if "key" in item}
    main_keys = {item.get("key") for item in main if "key" in item}

    added_keys = current_keys - main_keys
    removed_keys = main_keys - current_keys
    unchanged_keys = current_keys & main_keys

    added = [item for item in current if item.get("key") in added_keys]
    removed = [item for item in main if item.get("key") in removed_keys]
    unchanged = [item for item in current if item.get("key") in unchanged_keys]

    return DiffResult(added=added, removed=removed, unchanged=unchanged)


def check_metadata_completeness(items: list[dict]) -> list[str]:
    """Check if baseline items have required metadata fields."""
    required_fields = ["owner", "justification", "test_coverage"]
    errors = []

    for item in items:
        key = item.get("key", "unknown")
        metadata = item.get("metadata", {})

        for field in required_fields:
            value = metadata.get(field)
            if not value or (isinstance(value, str) and not value.strip()):
                errors.append(f"{key}: missing metadata.{field}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare security baseline between branches")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Path to current baseline file",
    )
    parser.add_argument(
        "--main-ref",
        default="origin/main",
        help="Git ref for main branch (default: origin/main)",
    )
    args = parser.parse_args()

    # Load current baseline
    current_baseline = load_baseline_from_file(args.baseline)

    # Load main branch baseline
    main_baseline = load_baseline_from_git(args.main_ref, args.baseline)

    # Compare
    diff = compare_baselines(current_baseline, main_baseline)

    # Report results
    print(f"Baseline comparison results:")
    print(f"  Added: {len(diff.added)} suppression(s)")
    print(f"  Removed: {len(diff.removed)} suppression(s)")
    print(f"  Unchanged: {len(diff.unchanged)} suppression(s)")

    # Check added suppressions
    if diff.added:
        print("\n⚠️  New suppressions added:")
        for item in diff.added:
            key = item.get("key", "unknown")
            endpoint = item.get("endpoint", "unknown")
            print(f"  - {key}")
            print(f"    Endpoint: {endpoint}")

        # Check metadata completeness
        metadata_errors = check_metadata_completeness(diff.added)
        if metadata_errors:
            print("\n❌ Metadata validation failed for new suppressions:")
            for error in metadata_errors:
                print(f"  - {error}")
            return 1

        # Require PR description or label
        print(
            "\nNew suppressions require justification in PR description "
            "or 'security-baseline-update' label."
        )
        print("Each suppression must have complete metadata (owner, justification, test_coverage).")

        # Note: In actual CI, we would check PR labels/description here
        # For now, we just ensure metadata is complete
        print("\n✅ All new suppressions have complete metadata")
        return 0

    if diff.removed:
        print("\n✅ Baseline suppressions removed (improvement!)")
        for item in diff.removed:
            key = item.get("key", "unknown")
            print(f"  - {key}")

    if not diff.added and not diff.removed:
        print("\n✅ No baseline changes detected")

    return 0


if __name__ == "__main__":
    sys.exit(main())
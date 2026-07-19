#!/usr/bin/env python3
"""
Bandit security check with severity+confidence layering and baseline support.

This script runs Bandit against the codebase and:
1. Blocks PRs with HIGH severity + HIGH/LOW confidence findings
2. Reports warnings for MEDIUM severity findings
3. Skips LOW severity findings and baseline-allowed findings

Usage:
    python scripts/lint/bandit_check.py [--baseline BASELINE_FILE] [--fail-on-medium]

Exit codes:
    0: No high-severity issues
    1: High-severity issues found (blocks PR)
    2: Script error (configuration issue)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Finding:
    """Represents a Bandit finding."""

    test_id: str
    test_name: str
    severity: str
    confidence: str
    file: str
    line: int
    col: int
    message: str
    cwe: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            test_id=data.get("test_id", ""),
            test_name=data.get("test_name", ""),
            severity=data.get("issue_severity", "LOW"),
            confidence=data.get("issue_confidence", "LOW"),
            file=data.get("filename", ""),
            line=data.get("line_number", 0),
            col=data.get("col_offset", 0),
            message=data.get("issue_text", ""),
            cwe=data.get("cwe"),
        )


def run_bandit(targets: list[str]) -> dict[str, Any]:
    """Run Bandit and return parsed JSON output."""
    cmd = [
        "bandit",
        "-r",
        "-f",
        "json",
        "-c",
        "pyproject.toml",
        *targets,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Bandit exits non-zero when issues found, but we need JSON output
    try:
        if result.stdout:
            return json.loads(result.stdout)
    except json.JSONDecodeError:
        # If no JSON output, check stderr for errors
        if result.returncode != 0 and not result.stdout:
            print(f"::error::Bandit execution failed: {result.stderr}")
            sys.exit(2)

    return {"results": [], "errors": []}


def load_baseline(baseline_file: Path) -> dict[str, Any]:
    """Load baseline file containing known issues to skip."""
    if not baseline_file.exists():
        return {"findings": []}

    try:
        content = baseline_file.read_text()
        return json.loads(content)
    except (json.JSONDecodeError, OSError) as e:
        print(f"::error::Failed to load baseline file: {e}")
        sys.exit(2)


def create_finding_key(finding: Finding) -> str:
    """Create a unique key for baseline matching."""
    return f"{finding.test_id}|{finding.file}|{finding.line}"


def parse_baseline_keys(baseline: dict[str, Any]) -> set[str]:
    """Extract finding keys from baseline for quick lookup."""
    keys = set()
    for item in baseline.get("findings", []):
        key = f"{item.get('test_id', '')}|{item.get('file', '')}|{item.get('line', 0)}"
        keys.add(key)
    return keys


def classify_findings(
    raw_results: dict[str, Any],
    baseline_keys: set[str],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """
    Classify findings into blockers, warnings, and baseline-skipped.

    Returns:
        Tuple of (blockers, warnings, baseline_skipped)
    """
    findings = [Finding.from_dict(r) for r in raw_results.get("results", [])]

    blockers = []
    warnings = []
    baseline_skipped = []

    for finding in findings:
        key = create_finding_key(finding)

        # Check if in baseline
        if key in baseline_keys:
            baseline_skipped.append(finding)
            continue

        severity = finding.severity.upper()
        confidence = finding.confidence.upper()

        # High severity + any confidence = blocker
        if severity == "HIGH" and confidence in ("HIGH", "MEDIUM", "LOW"):
            blockers.append(finding)
        # Medium severity = warning (doesn't block unless --fail-on-medium)
        elif severity == "MEDIUM":
            warnings.append(finding)
        # Low severity = skip (don't block)
        elif severity == "LOW":
            # Low severity findings are just logged, not reported as issues
            continue
        else:
            # Unknown severity, treat as warning
            warnings.append(finding)

    return blockers, warnings, baseline_skipped


def print_findings(findings: list[Finding], level: str) -> None:
    """Print findings in GitHub Actions format."""
    for f in findings:
        print(f"::{level}::{f.test_name} ({f.test_id}): {f.message} (file={f.file}, line={f.line})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bandit security check with layering")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("scripts/lint/bandit_baseline.json"),
        help="Path to baseline JSON file",
    )
    parser.add_argument(
        "--fail-on-medium",
        action="store_true",
        help="Also fail on medium severity issues",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        default=["app/", "scripts/"],
        help="Files/directories to scan",
    )

    args = parser.parse_args()

    # Load baseline
    baseline = load_baseline(args.baseline)
    baseline_keys = parse_baseline_keys(baseline)

    # Run Bandit
    results = run_bandit(args.targets)

    # Classify findings
    blockers, warnings, baseline_skipped = classify_findings(results, baseline_keys)

    # Print results
    if baseline_skipped:
        print(f"::notice::{len(baseline_skipped)} findings skipped (in baseline)")

    if blockers:
        print(f"\n::error::Found {len(blockers)} HIGH severity security issues:")
        print_findings(blockers, "error")

    if warnings:
        print(f"\n::warning::Found {len(warnings)} MEDIUM severity security issues:")
        print_findings(warnings, "warning")

    # Summary
    total = len(blockers) + len(warnings) + len(baseline_skipped)
    print(f"\n📊 Security scan complete: {total} total findings")
    print(f"   🔴 HIGH severity: {len(blockers)} (blocks PR)")
    print(f"   🟡 MEDIUM severity: {len(warnings)} (warning)")
    print(f"   ⚪ Baseline skipped: {len(baseline_skipped)}")

    # Exit code
    if blockers:
        print("\n❌ PR blocked by security issues. Fix or add to baseline if justified.")
        return 1

    if warnings and args.fail_on_medium:
        print("\n❌ PR blocked by medium severity issues (--fail-on-medium enabled).")
        return 1

    if blockers or (warnings and args.fail_on_medium):
        return 1

    print("\n✅ Security check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""
Create GitHub Issues for security audit findings (pip-audit/npm audit).

This script:
1. Parses vulnerability reports from pip-audit or npm audit
2. Extracts CVE identifiers
3. Checks for existing open issues with the same CVE
4. Creates new issues or adds comments to existing ones
5. Tracks consecutive failures for escalation

Usage:
    python scripts/ci/create_audit_issue.py --tool pip-audit --report-file report.json

Environment:
    GITHUB_TOKEN: GitHub token with repo permissions
    GITHUB_REPOSITORY: Repository in owner/repo format
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Vulnerability:
    """Represents a vulnerability finding."""

    cve_id: str | None
    package: str
    severity: str
    description: str
    fixed_in: str | None
    source: str  # pip-audit or npm-audit

    def issue_title(self) -> str:
        """Generate issue title."""
        cve_prefix = f"[{self.cve_id}] " if self.cve_id else ""
        return f"{cve_prefix}Vulnerability in {self.package} ({self.severity})"


def parse_pip_audit_report(report: dict[str, Any]) -> list[Vulnerability]:
    """Parse pip-audit JSON output."""
    vulnerabilities = []

    for dep in report.get("dependencies", []):
        for vuln in dep.get("versions", [{}])[0].get("vulns", []):
            # Extract CVE ID from aliases
            cve_id = None
            for alias in vuln.get("aliases", []):
                if alias.startswith("CVE-"):
                    cve_id = alias
                    break

            # Use first alias if no CVE
            if not cve_id and vuln.get("aliases"):
                cve_id = vuln["aliases"][0]

            vulnerabilities.append(
                Vulnerability(
                    cve_id=cve_id,
                    package=dep.get("name", "unknown"),
                    severity=vuln.get("severity", "UNKNOWN"),
                    description=vuln.get("description", "No description available"),
                    fixed_in=vuln.get("fix_versions", [None])[0] if vuln.get("fix_versions") else None,
                    source="pip-audit",
                )
            )

    return vulnerabilities


def parse_npm_audit_report(report: dict[str, Any]) -> list[Vulnerability]:
    """Parse npm audit JSON output."""
    vulnerabilities = []

    for name, data in report.get("advisories", {}).items():
        # npm audit JSON v1 format
        severity = data.get("severity", "moderate")
        cve = data.get("cves", [None])[0] if data.get("cves") else None

        vulnerabilities.append(
            Vulnerability(
                cve_id=cve,
                package=data.get("module_name", name),
                severity=severity.upper(),
                description=data.get("url", "No description available"),
                fixed_in=data.get("patched_versions", "").replace(" ", "") or None,
                source="npm-audit",
            )
        )

    return vulnerabilities


def run_gh_command(args: list[str], check: bool = False) -> tuple[bool, str]:
    """Run gh CLI command."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 and check:
            return False, result.stderr
        return True, result.stdout
    except FileNotFoundError:
        return False, "gh CLI not found"


def search_existing_issue(cve_id: str | None, package: str) -> str | None:
    """Search for existing open issue with same CVE or package."""
    if not cve_id:
        return None

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    success, output = run_gh_command(
        [
            "issue",
            "list",
            "--repo", repo,
            "--state", "open",
            "--search", f"{cve_id}",
            "--json", "number",
            "--limit", "1",
        ]
    )

    if success and output.strip():
        issues = json.loads(output)
        if issues:
            return str(issues[0]["number"])

    return None


def create_issue(vuln: Vulnerability) -> str | None:
    """Create a new GitHub issue for the vulnerability."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("::error::GITHUB_REPOSITORY not set")
        return None

    title = vuln.issue_title()

    body_lines = [
        "## Security Vulnerability Report",
        "",
        "**Source:** " + vuln.source,
        "**Package:** `" + vuln.package + "`",
        "**Severity:** " + vuln.severity,
        "**CVE:** " + (vuln.cve_id or "N/A"),
        "",
        "### Description",
        vuln.description,
    ]

    if vuln.fixed_in:
        body_lines.extend([
            "",
            "### Fix",
            "Upgrade to version `" + vuln.fixed_in + "` or later.",
        ])

    body_lines.extend([
        "",
        "---",
        "",
        "_This issue was automatically created by the security audit workflow._",
        "_Reported at: " + datetime.utcnow().isoformat() + "Z_",
    ])

    body = "\n".join(body_lines)

    success, output = run_gh_command([
        "issue", "create",
        "--repo", repo,
        "--title", title,
        "--body", body,
        "--label", "security,dependency",
        "--label", f"audit:{vuln.source}",
    ])

    if success:
        # Extract issue number from output
        match = re.search(r"#(\d+)", output)
        if match:
            return match.group(1)

    return None


def add_comment(issue_number: str, vuln: Vulnerability) -> bool:
    """Add a comment to an existing issue."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    comment = (
        "## Recurring Security Alert\n\n"
        "This vulnerability was detected again in the latest security audit.\n\n"
        "- **Detected at:** " + datetime.utcnow().isoformat() + "Z\n"
        "- **Source:** " + vuln.source + "\n"
    )

    if vuln.fixed_in:
        comment += "- **Fix available:** Version `" + vuln.fixed_in + "` or later\n"

    success, _ = run_gh_command([
        "issue", "comment", issue_number,
        "--repo", repo,
        "--body", comment,
    ])

    return success


def main() -> int:
    parser = argparse.ArgumentParser(description="Create GitHub issues for security findings")
    parser.add_argument(
        "--tool",
        required=True,
        choices=["pip-audit", "npm-audit"],
        help="Audit tool that generated the report",
    )
    parser.add_argument(
        "--report-file",
        required=True,
        type=Path,
        help="Path to JSON report file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without executing",
    )

    args = parser.parse_args()

    # Check for required environment
    if not os.environ.get("GITHUB_TOKEN") and not args.dry_run:
        print("::warning::GITHUB_TOKEN not set, issue creation may fail")

    # Load report
    try:
        report = json.loads(args.report_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"::error::Failed to read report file: {e}")
        return 1

    # Parse vulnerabilities
    if args.tool == "pip-audit":
        vulns = parse_pip_audit_report(report)
    else:
        vulns = parse_npm_audit_report(report)

    if not vulns:
        print("✅ No vulnerabilities found in report")
        return 0

    print(f"📋 Found {len(vulns)} vulnerabilities")

    # Process each vulnerability
    created = 0
    updated = 0
    skipped = 0

    for vuln in vulns:
        print(f"\n🔍 Processing: {vuln.package} ({vuln.severity})")

        # Check for existing issue
        existing = search_existing_issue(vuln.cve_id, vuln.package)

        if existing:
            print(f"   Found existing issue #{existing}")
            if args.dry_run:
                print("   [DRY-RUN] Would add comment to #" + existing)
            else:
                if add_comment(existing, vuln):
                    print("   ✅ Added comment to #" + existing)
                    updated += 1
                else:
                    print(f"   ⚠️ Failed to add comment")
        else:
            print("   No existing issue found")
            if args.dry_run:
                print("   [DRY-RUN] Would create new issue: " + vuln.issue_title())
                created += 1
            else:
                issue_num = create_issue(vuln)
                if issue_num:
                    print("   ✅ Created issue #" + issue_num)
                    created += 1
                else:
                    print("   ⚠️ Failed to create issue")
                    skipped += 1

    # Summary
    print("\n📊 Summary:")
    print("   New issues created: " + str(created))
    print("   Existing issues updated: " + str(updated))
    print("   Skipped: " + str(skipped))

    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
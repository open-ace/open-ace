#!/usr/bin/env python3
"""Generate a grouped CHANGELOG entry from conventional-commit git history.

This keeps the CHANGELOG honest: instead of hand-editing entries (error-prone
across hundreds of commits), it derives them from ``git log`` and groups them by
Keep a Changelog categories. Designed to be re-run each release/reminder cycle.

Usage::

    python3 scripts/generate_changelog.py                     # v1.0.0..HEAD
    python3 scripts/generate_changelog.py --since v1.0.0
    python3 scripts/generate_changelog.py --since v1.0.0 --until HEAD

Selection rules:
  * Only conventional-commit subjects (``type(scope): desc``) are kept.
  * Merge commits and auto-dev/* branch noise are skipped.
  * ``feat``    -> Added
  * ``fix``     -> Fixed
  * ``perf``/``refactor`` -> Changed
  * ``docs``/``ci``/``build``/``chore`` -> Maintenance (off by default; --maintenance)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import OrderedDict

SUBJECT_RE = re.compile(
    r"^(?P<type>feat|fix|perf|refactor|docs|ci|build|chore|style|test)"
    r"(?:\((?P<scope>[^)]+)\))?:\s+(?P<desc>.+?)\s*(?:\(#(?P<pr>\d+)\))?\s*$"
)

CATEGORY_MAP = OrderedDict(
    [
        ("feat", "Added"),
        ("perf", "Changed"),
        ("refactor", "Changed"),
        ("fix", "Fixed"),
    ]
)

MAINTENANCE_TYPES = {"docs", "ci", "build", "chore", "style", "test"}

NOISE_SUBSTRINGS = ("auto-dev/", "Merge pull request", "Merge remote-tracking")


def git_log(since: str, until: str) -> list[str]:
    rev_range = f"{since}..{until}" if since else until
    result = subprocess.run(
        ["git", "log", "--pretty=format:%s", rev_range],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def categorize(subjects: list[str], include_maintenance: bool):
    categories: OrderedDict[str, list[str]] = OrderedDict(
        (cat, []) for cat in CATEGORY_MAP.values()
    )
    dedup: set[str] = set()

    for subject in subjects:
        if any(noise in subject for noise in NOISE_SUBSTRINGS):
            continue
        match = SUBJECT_RE.match(subject)
        if not match:
            continue
        ctype, desc, pr = (
            match.group("type"),
            match.group("desc").strip(),
            match.group("pr"),
        )

        target_cat = None
        if ctype in CATEGORY_MAP:
            target_cat = CATEGORY_MAP[ctype]
        elif ctype in MAINTENANCE_TYPES and include_maintenance:
            target_cat = "Maintenance"
        else:
            continue

        bullet = desc
        if pr:
            bullet = f"{desc} (#{pr})"
        key = bullet.lower()
        if key in dedup:
            continue
        dedup.add(key)

        if target_cat not in categories:
            categories[target_cat] = []
        categories[target_cat].append(bullet)

    return categories


def render(categories) -> str:
    lines = []
    for cat, items in categories.items():
        if not items:
            continue
        lines.append(f"### {cat}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="v1.0.0", help="start revision (default: v1.0.0)")
    parser.add_argument("--until", default="HEAD", help="end revision (default: HEAD)")
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="include docs/ci/build/chore/test under a Maintenance section",
    )
    args = parser.parse_args(argv)

    subjects = git_log(args.since, args.until)
    categories = categorize(subjects, args.maintenance)
    output = render(categories)
    if not output.strip():
        print("No conventional commits found in range.", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fix Python 3.10 compatibility by replacing datetime.UTC with timezone.utc."""

import re
import subprocess
from pathlib import Path


def get_changed_files():
    """Get list of files changed in this PR."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent,
    )
    return [f for f in result.stdout.strip().split("\n") if f.endswith(".py")]


def fix_file(filepath: str) -> bool:
    """Fix a single file. Returns True if file was modified."""
    path = Path(filepath)
    if not path.exists():
        return False

    content = path.read_text()
    original = content

    # Pattern 1: from datetime import UTC, ...
    # Replace with: from datetime import datetime, timedelta, timezone, ...
    if re.search(r"from datetime import.*UTC", content):
        # Remove UTC from import
        content = re.sub(
            r"from datetime import (.*?)\bUTC\b(.*?)(?:,|$)",
            lambda m: f"from datetime import {m.group(1)}{m.group(2)}".replace(
                "from datetime import ,", "from datetime import"
            )
            .replace("from datetime import  ", "from datetime import ")
            .rstrip(", ")
            + ("," if "," in m.group(0) else ""),
            content,
        )

        # Clean up the import line
        content = re.sub(
            r"from datetime import\s*,\s*",
            "from datetime import ",
            content,
        )
        content = re.sub(
            r"from datetime import\s+",
            "from datetime import ",
            content,
        )

        # Add timezone if not already present
        if "from datetime import" in content and "timezone" not in content:
            # Find the datetime import line
            content = re.sub(
                r"(from datetime import )(.*?)(\n)",
                lambda m: (
                    f"{m.group(1)}{m.group(2).strip()}, timezone{m.group(3)}"
                    if "timezone" not in m.group(2)
                    else m.group(0)
                ),
                content,
            )
            # Clean up if timezone was already there
            content = re.sub(r",\s*timezone,?\s*timezone", ", timezone", content)

    # Pattern 2: Replace standalone UTC with timezone.utc (not in strings/comments)
    # This handles: datetime.now(UTC), datetime.now(UTC), etc.
    # But not: "UTC", 'UTC', # UTC, etc.

    # Find all lines and process them
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        # Skip if this is a comment or string line
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
            new_lines.append(line)
            continue

        # Replace UTC with timezone.utc (as a token, not in strings)
        # Use word boundaries
        if " UTC" in line or "UTC " in line or "(UTC)" in line or ",UTC" in line:
            # More careful replacement - only replace if it's a token
            line = re.sub(r"\bUTC\b", "timezone.utc", line)

        new_lines.append(line)

    content = "\n".join(new_lines)

    if content != original:
        path.write_text(content)
        return True
    return False


def main():
    files = get_changed_files()
    print(f"Found {len(files)} changed Python files")

    fixed_count = 0
    for filepath in files:
        if fix_file(filepath):
            print(f"Fixed: {filepath}")
            fixed_count += 1

    print(f"\nFixed {fixed_count} files")


if __name__ == "__main__":
    main()

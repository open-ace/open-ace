#!/usr/bin/env python3
"""Fix Python 3.10 compatibility by replacing datetime.UTC with timezone.utc."""

import re
import subprocess
from pathlib import Path


def get_changed_files():
    """Get list of Python files changed in this PR."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "origin/main"],
        capture_output=True,
        text=True,
        cwd="/home/rhuang/open-ace/.worktrees/2d0c317d-3109-4071-a437-bd0ed54100d1",
    )
    return [f for f in result.stdout.strip().split("\n") if f.endswith(".py") and Path(f).exists()]


def fix_imports_and_usage(content: str) -> str:
    """Fix datetime imports and usage in a file."""

    # Step 1: Fix imports
    # Pattern: from datetime import ... UTC ...
    # Replace: from datetime import ... timezone ...

    lines = content.split("\n")
    new_lines = []

    for line in lines:
        if "from datetime import" in line and "UTC" in line:
            # Remove UTC from the import
            # Handle various cases: "UTC, ", ", UTC", "UTC", etc.
            modified = line

            # Remove UTC from import
            modified = re.sub(r"\bUTC\s*,\s*", "", modified)  # "UTC, " -> ""
            modified = re.sub(r",\s*UTC\b", "", modified)  # ", UTC" -> ""
            modified = re.sub(r"\bUTC\b", "", modified)  # standalone UTC

            # Clean up double commas and trailing commas
            modified = re.sub(r",\s*,", ",", modified)
            modified = re.sub(r",\s*$", "", modified)

            # Add timezone if not already present
            if "timezone" not in modified:
                modified = modified.rstrip()
                if not modified.endswith("import "):
                    modified += ", timezone"
                else:
                    # Handle "from datetime import " case
                    modified += "timezone"

            # Clean up whitespace
            modified = re.sub(r"import\s+", "import ", modified)
            modified = re.sub(r"\s+", " ", modified)

            line = modified

        new_lines.append(line)

    content = "\n".join(new_lines)

    # Step 2: Replace usage of UTC with timezone.utc
    # Pattern: datetime.now(UTC) or similar
    # Replace: datetime.now(timezone.utc)

    # Only replace if UTC is used as a standalone token (not in strings)
    lines = content.split("\n")
    new_lines = []

    for line in lines:
        # Skip comments and docstrings
        stripped = line.lstrip()
        if stripped.startswith("#"):
            new_lines.append(line)
            continue

        # Replace UTC token with timezone.utc
        # This handles: datetime.now(UTC), datetime.now(UTC), etc.
        # But preserves: "UTC", 'UTC', etc.
        if re.search(r"\bUTC\b", line):
            # Check if we're inside a string
            # Simple heuristic: if line has more quotes before UTC than after, we're in a string
            # This is imperfect but works for most cases

            # Split by quotes to handle strings properly
            parts = re.split(r'(["\'])', line)
            in_string = False
            quote_char = None

            result_parts = []
            for part in parts:
                if part in ('"', "'"):
                    if in_string and part == quote_char:
                        in_string = False
                        quote_char = None
                    elif not in_string:
                        in_string = True
                        quote_char = part
                    result_parts.append(part)
                elif in_string:
                    result_parts.append(part)
                else:
                    # Not in string, replace UTC
                    result_parts.append(re.sub(r"\bUTC\b", "timezone.utc", part))

            line = "".join(result_parts)

        new_lines.append(line)

    return "\n".join(new_lines)


def fix_file(filepath: str) -> bool:
    """Fix a single file. Returns True if file was modified."""
    path = Path(filepath)
    if not path.exists():
        print(f"  Skipping non-existent: {filepath}")
        return False

    content = path.read_text()
    original = content

    new_content = fix_imports_and_usage(content)

    if new_content != original:
        path.write_text(new_content)
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

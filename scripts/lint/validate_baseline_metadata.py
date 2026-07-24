#!/usr/bin/env python3
"""
Validate security baseline metadata completeness.

Issue #1897: Ensure all baseline suppressions have required metadata fields.

Usage:
    python scripts/lint/validate_baseline_metadata.py

Exit code: 1 if validation fails, 0 otherwise.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "scripts" / "lint" / "security_baseline.json"

REQUIRED_METADATA_FIELDS = [
    "owner",
    "justification",
    "test_coverage",
]

OPTIONAL_METADATA_FIELDS = [
    "reviewed_at",
    "expires_at",
    "risk_level",
    "alternative_controls",
    "automated_check",
]


def validate_baseline() -> tuple[bool, list[str]]:
    """Validate baseline metadata completeness.

    Returns:
        Tuple of (success, list of error messages).
    """
    errors: list[str] = []

    if not BASELINE_PATH.exists():
        # No baseline file is valid (means no suppressions)
        return True, []

    try:
        data = json.loads(BASELINE_PATH.read_text())
    except json.JSONDecodeError as e:
        return False, [f"Failed to parse baseline JSON: {e}"]

    if not isinstance(data, list):
        return False, ["Baseline must be a JSON array"]

    now = datetime.now(timezone.utc)

    for i, item in enumerate(data):
        item_id = f"item {i}" if "key" not in item else item["key"]

        # Check for metadata field
        metadata = item.get("metadata")

        if metadata is None:
            errors.append(f"{item_id}: missing 'metadata' field")
            continue

        if not isinstance(metadata, dict):
            errors.append(f"{item_id}: 'metadata' must be an object")
            continue

        # Check required fields
        for field in REQUIRED_METADATA_FIELDS:
            value = metadata.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"{item_id}: metadata.{field} is required and cannot be empty")

        # Check expires_at is not in the past
        expires_at = metadata.get("expires_at")
        if expires_at:
            try:
                exp_date = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if exp_date < now:
                    errors.append(
                        f"{item_id}: metadata.expires_at ({expires_at}) has expired"
                    )
            except (ValueError, TypeError):
                errors.append(f"{item_id}: metadata.expires_at is not a valid ISO date")

    return len(errors) == 0, errors


def main() -> int:
    """Run validation and print results."""
    success, errors = validate_baseline()

    if success:
        print("✅ Baseline metadata validation passed")
        return 0

    print("❌ Baseline metadata validation failed:")
    for error in errors:
        print(f"  - {error}")

    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Issue #2443 PR-B: retry must reset the full CI-repair counter set.

A CI-repair-exhausted workflow accumulates several counters + a failure
signature. ``retry_workflow`` reset only ``status``/``error_message``/
``retry_count``, so a retried failed workflow's first CI-repair round was
instantly re-exhausted by residual counts and a stale signature guard (#2443
plan review N2). PR-B resets the whole set.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2443)]

AUTONOMOUS = Path(__file__).resolve().parents[2] / "app/routes/autonomous.py"
SRC = AUTONOMOUS.read_text(encoding="utf-8")

# Every field a CI-repair-exhausted workflow accumulates. retry must zero/clear
# ALL of them — resetting only ci_repair_attempts leaves the transient/no-change/
# diagnostics counts and the stale signature, so the retried round re-exhausts.
RESET_FIELDS = [
    "ci_repair_attempts",
    "ci_repair_transient_retries",
    "ci_repair_no_change_retries",
    "ci_diagnostics_attempts",
    "last_ci_failure_signature",
    "last_ci_failure_head_sha",
]


def _retry_updates_block() -> str:
    match = re.search(
        r"retry_updates\s*=\s*\{(?P<body>.*?)\n    _get_repo\(\)\.update_workflow",
        SRC,
        re.S,
    )
    assert match, "retry_updates block not found in autonomous.py"
    return match.group("body")


def test_retry_resets_full_ci_repair_counter_set():
    body = _retry_updates_block()
    missing = [f for f in RESET_FIELDS if f not in body]
    assert not missing, f"retry_updates missing reset of: {missing}"


def test_retry_resets_counters_to_zero_and_signature_to_empty():
    body = _retry_updates_block()
    for counter in (
        "ci_repair_attempts",
        "ci_repair_transient_retries",
        "ci_repair_no_change_retries",
        "ci_diagnostics_attempts",
    ):
        assert re.search(
            rf'"{counter}"\s*:\s*0\b', body
        ), f"{counter} must reset to 0, not just be present"
    assert re.search(r'"last_ci_failure_signature"\s*:\s*""', body)
    assert re.search(r'"last_ci_failure_head_sha"\s*:\s*""', body)

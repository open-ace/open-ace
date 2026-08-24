"""Issue #2442: both preserve sites must use the fail-closed helper.

Source assertions (wiring locks), in the style of
``tests/unit/test_reclaim_wiring_2403.py``. The behavioural proof that the
helper itself works is in ``test_preserve_nesting.py``; these pin that the
script actually calls it at both the startup and reclaim sites, with the
right fail-closed policy at each (startup aborts; reclaim logs because it
runs inside an EXIT trap where errexit would rewrite the exit code).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2442)]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "openace-run-as.sh"
SRC = SCRIPT.read_text(encoding="utf-8")
LINES = SRC.splitlines()


def test_helper_is_defined():
    assert re.search(r"_move_to_preserve\(\) \{", SRC), "_move_to_preserve must be defined"


def test_both_preserve_sites_call_helper():
    n = SRC.count('_move_to_preserve "$task_home/.claude"')
    assert n >= 2, f"expected >=2 call sites (startup + reclaim), found {n}"


def test_no_bare_rm_rf_of_preserve_dir_remains():
    offending = [
        i + 1 for i, line in enumerate(LINES) if "rm -rf" in line and "preserve_claude_dir" in line
    ]
    assert not offending, (
        f"bare `rm -rf ... preserve_claude_dir` at line(s) {offending}; "
        "must go through _move_to_preserve"
    )


def test_helper_contains_chmod_700():
    start = SRC.index("_move_to_preserve() {")
    region = SRC[start:]
    end = region.index("\n}") + 2
    assert "chmod 700" in region[:end], "helper must chmod 700 the preserve dir (#2403)"


def test_startup_aborts_with_exit_70_on_failure():
    assert "exit 70" in SRC, "startup must exit 70 when the preserve dir cannot be cleared"


def test_reclaim_logs_instead_of_exiting_on_failure():
    # reclaim runs inside an EXIT trap; errexit there would rewrite the exit
    # code (per the trap comments), so it must `|| log_audit`, not exit.
    assert re.search(
        r"_move_to_preserve.*preserve_claude_dir.*\|\| log_audit",
        SRC,
        re.S,
    ), "reclaim site must `|| log_audit` on failure (trap must not exit)"

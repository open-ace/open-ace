"""Issue #2438: setup-cgroup-v2.sh must preserve agent_task_preserve_max_age_days.

The script regenerates agent-launcher.conf by stripping every ``agent_task_``
line and re-emitting only the resource keys it manages. It never re-emitted
``agent_task_preserve_max_age_days``, so an operator's setting (the #2403 stale
``.claude-preserve`` reaper window) was silently dropped on the next run and
reset to the wrapper default.

The script must run as root and probes ``/sys/fs/cgroup``, so it cannot be
exercised behaviourally in CI. These are source assertions (wiring locks) in
the style of ``tests/unit/test_reclaim_wiring_2403.py``: they pin that the
preserve key is read, fail-safed, and re-emitted, and that the strip still
drops the prefix so the key is emitted exactly once.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(2438)]

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup-cgroup-v2.sh"
SRC = SCRIPT.read_text(encoding="utf-8")
LINES = SRC.splitlines()


def _line_of(pattern: str, *, start: int = 0) -> int:
    for index in range(start, len(LINES)):
        if re.search(pattern, LINES[index]):
            return index
    raise AssertionError(f"pattern not found in {SCRIPT.name}: {pattern!r}")


def test_resource_block_emits_preserve_age_key():
    """The regenerated block must re-emit the preserve-age key.

    Without it the strip-then-emit cycle deletes the key forever (the
    regression). Mutation: delete the line → test fails.
    """
    assert "agent_task_preserve_max_age_days=$existing_preserve" in SRC


def test_existing_preserve_read_from_conf():
    """The operator's current value must be read from the conf before the strip
    removes it."""
    assert "agent_task_preserve_max_age_days=//p" in SRC
    assert re.search(
        r"sed -n .*agent_task_preserve_max_age_days=//p.*CONF_PATH", SRC, re.S
    ), "expected a sed read of agent_task_preserve_max_age_days from $CONF_PATH"


def test_preserve_age_fail_safe_rejects_zero_and_non_numeric():
    """0 would reap every session's history each run; non-numeric is corruption.

    Both must fall back to a default rather than abort the install, mirroring
    the guard in openace-run-as.sh.
    """
    assert re.search(r"case .*existing_preserve", SRC), "expected a case guard on existing_preserve"
    assert re.search(r"\*\[!0-9\]\*", SRC), "expected a non-digit rejection guard"
    assert "PRESERVE_MAX_AGE_DAYS" in SRC


def test_strip_still_removes_agent_task_prefix():
    """The strip must keep dropping ``agent_task_`` so the key is emitted once,
    not duplicated. The fix relies on re-emission, not on sparing the key.
    """
    assert re.search(r"grep -vE.*agent_task_", SRC)


def test_fail_safe_normalizes_before_resource_block():
    """existing_preserve must be normalized before the heredoc captures it."""
    failsafe = _line_of(r"case .*existing_preserve")
    heredoc = _line_of(r"<<EOF")
    assert failsafe < heredoc, (
        f"fail-safe at line {failsafe + 1} must precede the resource_block "
        f"heredoc at line {heredoc + 1}"
    )

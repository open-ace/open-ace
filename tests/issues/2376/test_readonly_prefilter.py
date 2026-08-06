"""Read-only pre-filter + token-based flag exclusion (#2376 PR-1).

Two pre-existing fail-open / fail-closed defects in ``_has_test_tool_call``:

D1 (fail-open) — the pattern loop substring-matches the *whole* command, so a
command that merely reads or searches a test file counts as a test invocation.
On ``main`` these all return True, and because ``_parse_generic`` /
``_parse_pytest`` judge on the exit code alone, a ``grep`` can satisfy the
authoritative test gate:

    grep -rn "pytest" tests/
    cat tests/unit/test_pytest_helpers.py
    git log --grep=pytest

Fix E (fail-closed) — the ``--help``/``--version``/``-h`` exclusion is a raw
substring test over the whole command, so a legitimate run is discarded when
its path or an unrelated flag happens to contain ``-h``.

The filter is applied per shell segment, and so is the pattern match: a
surviving segment must not lend its recognition to a filtered one.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call


def _tc(command: str) -> list:
    return [{"tool": {"name": "Bash", "input": {"command": command}}}]


# --- D1: read-only commands must never count as test invocations -------------


@pytest.mark.parametrize(
    "command",
    [
        'grep -rn "pytest" tests/',
        "cat tests/unit/test_pytest_helpers.py",
        "git log --grep=pytest",
        "rg vitest frontend/",
        "head -n 50 tests/unit/test_pytest_helpers.py",
        "sed -n '1,20p' tests/test_pytest_thing.py",
        "git diff tests/",
        "ls -la tests/",
        "find . -name 'test_*.py'",
        "wc -l tests/test_pytest_helpers.py",
    ],
)
def test_read_only_commands_are_not_test_invocations(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


def test_read_only_head_behind_a_wrapper_is_still_filtered():
    # The effective head is the first recognized command token, so wrapper
    # prefixes and their operands cannot smuggle a read-only command through.
    assert _has_test_tool_call(_tc("sudo -u openace grep -rn pytest tests/"), "mixed") is False
    assert _has_test_tool_call(_tc("timeout 60 cat tests/test_pytest_x.py"), "mixed") is False


def test_surviving_segment_does_not_lend_recognition_to_a_filtered_one():
    # Per-segment matching: segment 1 survives the read-only filter but carries
    # no test pattern; segment 2 carries the pattern but is read-only. Matching
    # the whole command string would let a one-token prefix defeat the filter.
    command = 'python -c "print(1)" && grep -rn pytest tests/'
    assert _has_test_tool_call(_tc(command), "mixed") is False


def test_read_only_segment_does_not_suppress_a_genuine_one():
    # The filter is per segment, not per command: a genuine test run alongside
    # a read-only command must still be recognized.
    command = "cat tests/conftest.py && python -m pytest tests/test_a.py -q"
    assert _has_test_tool_call(_tc(command), "mixed") is True


# --- Genuine invocations must keep working (no regression) -------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest -q",
        "pytest tests/ -v",
        "python -m pytest tests/test_a.py -q",
        "python -m pytest tests -q --ignore tests/test_a.py",
        "ONLY_FAST=1 python -m pytest tests/test_a.py -q",
        "/usr/bin/python3 -m pytest",
        "python3.12 -m pytest tests/test_a.py tests/test_b.py -q",
        "unittest discover",
        "cd /w/frontend && npm test",
        "pytest || true",
    ],
)
def test_genuine_test_invocations_still_recognized(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


# --- Fix E: flag exclusion must be token-based, not substring ----------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest --help",
        "pytest --version",
        "pytest -h",
    ],
)
def test_help_and_version_are_still_excluded(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # "-h" appears only inside a longer flag, never as its own token.
        "pytest -q --no-header tests/",
        # "-h" appears only inside a path component.
        "python -m pytest tests/e2e/test-helper-cases.py -q",
    ],
)
def test_substring_h_does_not_exclude_a_real_run(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


# --- Non-Bash tool names are unaffected --------------------------------------


def test_dedicated_test_tool_names_still_recognized():
    for name in ("pytest", "run_tests", "test"):
        assert _has_test_tool_call([{"tool": {"name": name, "input": {}}}], "mixed") is True

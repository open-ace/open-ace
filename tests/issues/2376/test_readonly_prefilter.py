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


# --- Multi-line commands: newlines separate commands (PR-1 review #1) --------
#
# A Bash tool call is routinely a multi-line script. Treating it as one segment
# let a read-only command on any line veto a genuine run on any other line —
# reintroducing, on a new axis, exactly the fail-closed bug #2376 exists to fix.


@pytest.mark.parametrize(
    "command",
    [
        "cd /w && git status\npython -m pytest tests/ -q",
        'echo "running tests"\npython -m pytest tests/ -q',
        "ls -la\ncat foo\npytest tests/ -q",
        # The runner runs FIRST; a later read-only line must not retract it.
        "pytest tests/ -q\ngit status",
        # Agent greps for context, then actually runs the suite.
        "grep -rn pytest tests/\npython -m pytest tests/ -q",
        "#!/bin/bash\nset -e\ncd /w\npytest tests/ -q",
    ],
)
def test_newline_separated_commands_are_separate_segments(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command",
    [
        'python -c "print(1)"\ngrep -rn pytest tests/',
        'node -e "1"\ncat tests/test_pytest_x.py',
        'python -c "1" & grep -rn pytest tests/',
    ],
)
def test_segment_bypass_closed_for_every_separator(command):
    # The eligible segment carries no test pattern; the segment that does is
    # read-only. True here would mean the filter is defeated by a prefix.
    assert _has_test_tool_call(_tc(command), "mixed") is False


# --- Quote-aware splitting (PR-1 review #2) ----------------------------------
#
# A regex split cuts inside quoted strings and hands the tail to the filter as a
# headless fragment, so `|unittest` inside a grep pattern re-opened D1 entirely.


@pytest.mark.parametrize(
    "command",
    [
        'grep -E "pytest|unittest" tests/',
        'grep -rn "npm test|pytest" .',
        'git log --grep="pytest|unittest"',
        "sed -n '/pytest/p; /jest/p' f.py",
    ],
)
def test_operators_inside_quotes_do_not_split_a_read_only_command(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


def test_redirections_are_not_treated_as_separators():
    # "2>&1" and "&>" are redirections, not backgrounding.
    assert _has_test_tool_call(_tc("pytest tests/ -q 2>&1"), "mixed") is True
    assert _has_test_tool_call(_tc("pytest tests/ -q &> out.log"), "mixed") is True


def test_pipeline_keeps_the_runner_segment():
    # Splitting on "|" must not drop the runner: the tee/tail segment is
    # filtered, the pytest segment survives.
    assert _has_test_tool_call(_tc("pytest tests/ -q 2>&1 | tee out.log"), "mixed") is True
    assert _has_test_tool_call(_tc("pytest tests/ | tail -50"), "mixed") is True


# --- An argument must never veto its runner (PR-1 review #3) -----------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/cat",
        "pytest -k git tests/",
        'pytest -k "git" tests/',
        "pytest --deselect tests/find tests/",
        "pytest tests/ --rootdir tests/git",
        "npm test -- tests/diff",
        "go test ./cmd/find",
        "cargo test --test file",
    ],
)
def test_argument_basename_does_not_veto_a_genuine_run(command):
    # The head scan stops at the runner, so a path or flag value whose basename
    # happens to be a non-executing command name cannot disable the gate.
    assert _has_test_tool_call(_tc(command), "mixed") is True


# --- Non-Bash tool names are unaffected --------------------------------------


def test_dedicated_test_tool_names_still_recognized():
    for name in ("pytest", "run_tests", "test"):
        assert _has_test_tool_call([{"tool": {"name": name, "input": {}}}], "mixed") is True

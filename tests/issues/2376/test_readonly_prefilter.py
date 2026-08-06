"""Non-executing-command pre-filter + token-based flag exclusion (#2376 PR-1).

Two pre-existing fail-open / fail-closed defects in ``_has_test_tool_call``:

D1 (fail-open) — the pattern loop substring-matches the *whole* command, so a
command that merely reads, searches or writes a test file counts as a test
invocation.
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

from app.modules.workspace.autonomous.orchestrator import (
    _command_segments,
    _has_test_tool_call,
    _is_package_manager_install,
    _shell_tokens,
)


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


@pytest.mark.parametrize(
    "command,expected",
    [
        # Redirections are not separators. Asserted on the segmenter directly:
        # going through _has_test_tool_call would pass even if "&" did split,
        # because the runner sits in the first fragment either way.
        ("pytest tests/ -q 2>&1", ["pytest tests/ -q 2>&1"]),
        ("pytest tests/ -q &> out.log", ["pytest tests/ -q &> out.log"]),
        ("pytest tests/ >& out.log", ["pytest tests/ >& out.log"]),
        # A lone "&" backgrounds, so it *is* a separator.
        ("pytest tests/ & echo done", ["pytest tests/ ", " echo done"]),
        # Operators inside quotes are literal.
        ('grep -E "pytest|unittest" tests/', ['grep -E "pytest|unittest" tests/']),
        ("sed -n '/pytest/p; /jest/p' f.py", ["sed -n '/pytest/p; /jest/p' f.py"]),
        # Newlines separate commands.
        ("git status\npytest tests/", ["git status", "pytest tests/"]),
        # A heredoc body is data: consumed through its terminator, never a
        # segment, and commands after the terminator are still segmented.
        ("cat > tests/x.py <<'EOF'\nimport pytest\nEOF", ["cat > tests/x.py <<'EOF'"]),
        (
            "cat > tests/x.py <<'EOF'\nimport pytest\nEOF\npytest tests/",
            ["cat > tests/x.py <<'EOF'", "pytest tests/"],
        ),
        # The rest of the heredoc's own line is command text.
        ("cat <<EOF && pytest tests/\nbody\nEOF", ["cat <<EOF ", " pytest tests/"]),
        # Not a heredoc: no valid delimiter word follows.
        ("echo $((1<<2))", ["echo $((1<<2))"]),
        # Comments are stripped, not turned into headless segments.
        ("# run pytest later\nls tests/", ["ls tests/"]),
        ("ls tests/ # then pytest", ["ls tests/ "]),
        # "#" mid-word is not a comment.
        ("pytest tests/test_a.py::test_x#frag", ["pytest tests/test_a.py::test_x#frag"]),
    ],
)
def test_command_segments_structure(command, expected):
    assert _command_segments(command) == expected


def test_pipeline_keeps_the_runner_segment():
    # Splitting on "|" must not drop the runner: the tee/tail segment is
    # filtered, the pytest segment survives.
    assert _has_test_tool_call(_tc("pytest tests/ -q 2>&1 | tee out.log"), "mixed") is True
    assert _has_test_tool_call(_tc("pytest tests/ | tail -50"), "mixed") is True


# --- Heredocs, comments, installs (PR-1 re-review N1/N2/N3/N4) ---------------


@pytest.mark.parametrize(
    "command",
    [
        # Writing a test file with a heredoc must not satisfy the gate: without
        # a heredoc guard the body line `import pytest` becomes its own segment.
        "cat > tests/test_new.py <<'EOF'\nimport pytest\n\n\ndef test_x():\n    assert 1\nEOF",
        "cat >> conftest.py <<EOF\nimport pytest\nEOF",
        "cat <<-EOF > tests/x.py\nimport pytest\nEOF",
        # Comments are not commands.
        "# TODO: run pytest after the refactor\nls -la tests/",
        "ls tests/ ; # pytest is what we would run",
        "# first, look at the suite\ngrep -rn TODO tests/\n# then run pytest\n",
        # A runner basename inside an env assignment must not win the scan.
        "PYTHONPATH=/opt/tox cat tests/test_pytest_x.py",
        # Installing a runner is not running it.
        "pip install -U pytest pytest-cov",
        "uv pip install pytest",
        "npm install --save-dev jest",
        "npm ci",
        "poetry add --group dev pytest",
    ],
)
def test_data_and_dependency_commands_are_not_test_runs(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # Write the test file, then run it — the most common heredoc shape in
        # agent work. Consuming to the terminator (rather than swallowing the
        # whole remainder) is what keeps the run after it visible.
        "cat > tests/test_x.py <<'EOF'\nimport pytest\n\n\ndef test_x():\n    assert 1\n"
        "EOF\npython -m pytest tests/test_x.py -q",
        "cat > /tmp/x.py <<EOF\nprint(1)\nEOF\npytest tests/ -q",
        "cat <<EOF > tests/x.sh\necho hi\nEOF\nbash tests/x.sh && pytest tests/ -q",
        # The rest of the heredoc's own line is still command text.
        "cat <<EOF && pytest tests/ -q\nbody\nEOF",
        # Two heredocs in sequence.
        "cat > a.txt <<A\nx\nA\ncat > b.txt <<B\ny\nB\npytest tests/",
        # Tab-stripped terminator (<<-).
        "cat <<-EOF > tests/x.py\n\timport pytest\n\tEOF\npytest tests/ -q",
        # An arithmetic left shift is not a heredoc: no valid delimiter word.
        "echo $((1<<2)) && pytest tests/ -q",
    ],
)
def test_heredoc_body_is_skipped_but_later_commands_are_not(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


def test_run_before_a_heredoc_is_still_seen():
    command = "pytest tests/ -q\ncat > tests/x.py <<'EOF'\nimport pytest\nEOF"
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command",
    [
        "npm test",
        "cd /w/frontend && npm test",
        # A test-name filter that happens to equal an install subcommand must
        # not veto the run: only the first non-flag token after the manager
        # decides.
        "npm test -- --grep install",
        "npm test -- -t install",
        "npm test -- --testNamePattern install",
        "npm test -- --install-deps",
    ],
)
def test_package_manager_run_is_not_mistaken_for_an_install(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command,is_install",
    [
        ("pip install -U pytest pytest-cov", True),
        ("uv pip install pytest", True),
        ("python -m pip install pytest", True),
        ("sudo pip install pytest", True),
        ("npm install --save-dev jest", True),
        ("npm ci", True),
        ("poetry add --group dev pytest", True),
        # Only the first non-flag token after the manager decides, so a filter
        # argument equal to a subcommand is not an install. Asserted on the
        # predicate directly: `yarn test` is not a recognized pattern until
        # PR-3's Fix B, so the public function cannot express this case yet.
        ("yarn test --grep add", False),
        ("pnpm test -- --filter remove", False),
        ("npm test -- --grep install", False),
        ("npm test", False),
        ("python -m pytest tests/", False),
    ],
)
def test_is_package_manager_install(command, is_install):
    assert _is_package_manager_install(_shell_tokens(command)) is is_install


@pytest.mark.parametrize(
    "command",
    [
        # The manager is found by scanning, so wrapper and interpreter prefixes
        # cannot hide it. `python -m pip install` is the form this repo's docs use.
        "python -m pip install pytest",
        "python3 -m pip install -U pytest pytest-cov",
        "sudo pip install pytest",
        "sudo -H pip3 install pytest",
        "sudo apt-get install -y python3-pytest",
        "env PIP_NO_CACHE=1 pip install pytest",
    ],
)
def test_wrapped_installs_are_still_detected(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


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

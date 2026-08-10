"""Contract tests for the test-evidence gate's tool-name/input-key handling (#2401).

These are NOT command-corpus tests. #2376's six review rounds hardened
``_has_test_tool_call`` against every *command string* that could satisfy the
gate without running tests. #2401 is orthogonal: two structural gaps that never
touch the command string, so no command corpus can see them —

  (a) fail-open — a dedicated tool name (``pytest``/``run_tests``/``test``)
      returned ``True`` on the *name alone*, so ``name="test"`` running
      ``helm install`` reached the authoritative PASSED verdict;
  (b) fail-closed — the shell tool-name whitelist was closed + case-sensitive, so
      ``bash``/``terminal``/``execute_bash``/... voided the gate for any command;
  (c) argv-blind — recognition and persistence read only ``command``/``cmd``, so
      an argv-only provider was invisible.

The fix makes the tool name part of the recognized command (option 1 of the
issue's "要么...要么"): the dedicated tool name is prepended as ``argv[0]`` and run
through the SAME per-segment recognition, so ``test``/``run_tests`` only pass when
a real runner is present while ``pytest`` (a runner token) still passes. Verified
against the recognizer, not a mock.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.orchestrator import _has_test_tool_call


def _call(name: str, tool_input: dict, framework_type: str = "python") -> bool:
    return _has_test_tool_call([{"tool": {"name": name, "input": tool_input}}], framework_type)


class TestDedicatedToolNameIsNotFailOpen:
    """(a) A dedicated tool name must not pass on the name alone (#2401)."""

    @pytest.mark.parametrize(
        "name,command,framework",
        [
            # The issue's exact examples: a tool literally named ``test`` running a
            # non-test command, and running nothing at all.
            ("test", "helm install mocha ./chart", "python"),
            # open-ace itself infers ``mixed`` (py + js markers), whose pattern
            # union includes ``mocha`` — so this exercises the _is_artifact_operation
            # veto (``helm install mocha`` is a chart op, not the mocha runner).
            ("test", "helm install mocha ./chart", "mixed"),
            ("test", "", "python"),
            ("test", "echo done", "python"),
            ("test", "ls -la", "python"),
        ],
    )
    def test_dedicated_name_without_a_real_runner_is_rejected(self, name, command, framework):
        # Only the ambiguous ``test`` name is checked here — it is the generic
        # name a provider might reuse for a shell. ``pytest``/``run_tests`` are
        # unambiguous runner names (trusted residual, below).
        assert _call(name, {"command": command}, framework) is False

    @pytest.mark.parametrize(
        "name,command,framework",
        [
            # ``pytest`` is itself a runner token, so a bare pytest tool is a run.
            ("pytest", "tests/ -k auth", "python"),
            ("pytest", "", "python"),
            # ``run_tests`` invoked bare runs the suite (unambiguous runner name).
            ("run_tests", "", "python"),
            # ``test`` passes only when the command carries a real runner — checked
            # against the framework's own pattern set (pytest for python, npm/vitest
            # for a mixed repo).
            ("test", "pytest tests/test_auth.py", "python"),
            ("test", "npm test", "mixed"),
            ("test", "vitest run", "mixed"),
        ],
    )
    def test_dedicated_name_with_a_real_runner_is_recognized(self, name, command, framework):
        assert _call(name, {"command": command}, framework) is True

    def test_unambiguous_runner_name_is_the_accepted_residual(self):
        # Issue-sanctioned residual: ``pytest``/``run_tests`` are unambiguous
        # runner names, so they are trusted whatever the command. A provider names
        # a tool ``pytest``/``run_tests`` only when it runs tests; the drift the
        # issue targets is the ambiguous ``test`` (closed above), not these.
        assert _call("pytest", {"command": "echo hi"}) is True
        assert _call("run_tests", {"command": "deploy.sh --prod"}) is True

    def test_prepend_does_not_defeat_the_read_only_prefilter(self):
        # Prepending the tool name must not lend eligibility to a read-only
        # command: ``grep pytest tests/`` reads a test file, it does not run it.
        assert _call("test", {"command": "grep -rn pytest tests/"}) is False


class TestShellToolNamesAreNotFailClosed:
    """(b) The shell tool-name set is case-insensitive and consolidated (#2401)."""

    @pytest.mark.parametrize("name", ["bash", "Bash", "BASH", "bAsH"])
    def test_shell_tool_names_are_case_insensitive(self, name):
        # Before the fix only the exact-case ``Bash`` matched; a lower-case
        # ``bash`` returned False for any command — a fail-closed drift.
        assert _call(name, {"command": "pytest tests/"}) is True

    @pytest.mark.parametrize(
        "name",
        [
            "sh",
            "shell",
            "zsh",
            "terminal",
            "execute_bash",
            "local_shell",
            "container.exec",
            "run_terminal_cmd",
            "run_shell_command",
            "exec_command",
        ],
    )
    def test_wider_shell_tool_names_reach_the_command_check(self, name):
        # These provider/normalized shell tool names all voided the gate before
        # (closed whitelist); each must now reach the per-segment recognition.
        assert _call(name, {"command": "pytest tests/"}) is True
        # ...and must still reject a non-test command (they are shells, checked).
        assert _call(name, {"command": "ls -la"}) is False

    def test_every_provider_shell_tool_name_is_recognized(self):
        # Contract guard (#2401 b): a shell tool name any provider emits into
        # evidence must be a member of the gate's set, else a drift silently voids
        # the gate. Derived from the REAL registry (not a hardcoded map) so a
        # future provider added to AUTONOMOUS_DEV_ALLOWED_TOOLS with a shell tool
        # name trips this test rather than passing unnoticed.
        import re

        from app.modules.workspace.autonomous.constants import AUTONOMOUS_DEV_ALLOWED_TOOLS
        from app.modules.workspace.autonomous.orchestrator import _SHELL_TOOL_NAMES

        # Explicit truth for today's providers (documents the mapping).
        assert "bash" in _SHELL_TOOL_NAMES  # claude-code emits "Bash"
        assert "run_shell_command" in _SHELL_TOOL_NAMES  # qwen-code-cli

        # Conservative shell markers — a `sh`/`bash`/`zsh` suffix, or `shell`/
        # `terminal` — so a non-shell tool cannot false-trip this.
        shell_marker = re.compile(r"(?:^|[_.])(?:ba|z)?sh$|shell|terminal", re.IGNORECASE)
        registry_shell_tools = {
            tool
            for tools in AUTONOMOUS_DEV_ALLOWED_TOOLS.values()
            for tool in tools
            if shell_marker.search(tool)
        }
        # Guard the guard: the marker must detect the known shells, so an empty
        # set never passes vacuously.
        assert {"Bash", "run_shell_command"} <= registry_shell_tools
        for tool in registry_shell_tools:
            assert (
                tool.lower() in _SHELL_TOOL_NAMES
            ), f"registry shell tool {tool!r} not in _SHELL_TOOL_NAMES"


class TestArgvIsNotInvisible:
    """(c) Recognition reads argv/args, not just command/cmd (#2401)."""

    @pytest.mark.parametrize("key", ["argv", "args"])
    def test_argv_only_input_is_recognized(self, key):
        # An argv-style provider carries the command as a token list with no
        # joined ``command``/``cmd`` string. Before the fix the recognizer read
        # only command/cmd, so this invocation was invisible.
        assert _call("Bash", {key: ["pytest", "tests/"]}) is True

    def test_argv_only_non_test_command_is_still_rejected(self):
        assert _call("Bash", {"argv": ["ls", "-la"]}) is False

    def test_command_string_takes_precedence_over_argv(self):
        # When both are present the joined command string wins (it is what the
        # provider actually executed); argv is the fallback shape.
        assert _call("Bash", {"command": "pytest tests/", "argv": ["ls"]}) is True

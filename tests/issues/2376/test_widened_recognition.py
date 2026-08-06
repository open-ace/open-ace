"""Widened test-command recognition for polyglot repos (#2376 PR-3, D3).

The original defect. ``_infer_test_framework`` returns ``"mixed"`` for any repo
with markers from two languages — open-ace always does, having both
``pyproject.toml`` and ``frontend/package.json``. But ``_has_test_tool_call``'s
``test_commands`` dict has no ``"mixed"`` key, so it fell back to
``generic_patterns``, which is *weaker than every single-language list*: it
lacks ``vitest`` and ``npm run test``, both of which are in the javascript list.

Two production workflows were killed by this while their agents ran appropriate,
passing verification:

    wf 220 (#2343)  cd frontend && npm run test:coverage        exit 0
    wf 221 (#2349)  bash tests/integration/test_sudoers_security.sh  exit 0

Fix B restores mixed to the union of every language's patterns. Fix C adds the
repo-convention rule: executing a file under ``tests/`` is a test run whatever
the interpreter. Fix C is an *allowlist* of runners, not a denylist of
non-runners — a denylist has to be exhaustive to be sound, and this project's
own pre-commit tools (ruff/black/mypy) were slipping through one.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.orchestrator import (
    _has_test_tool_call,
    _infer_test_framework,
    _is_test_path_execution,
    _shell_tokens,
)


def _tc(command: str) -> list:
    return [{"tool": {"name": "Bash", "input": {"command": command}}}]


# --- The two production failures ---------------------------------------------


def test_wf220_npm_run_test_coverage_is_recognized():
    assert _has_test_tool_call(_tc("cd /w/frontend && npm run test:coverage"), "mixed") is True


def test_wf221_shell_test_suite_is_recognized():
    assert (
        _has_test_tool_call(_tc("bash tests/integration/test_sudoers_security.sh"), "mixed") is True
    )


# --- Fix B: mixed is the union, not the weakest fallback ---------------------


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/ -q",  # python
        "npm run test:unit",  # javascript (npm run test prefix)
        "npx vitest run",  # javascript (vitest)
        "yarn test",  # javascript
        "pnpm test",  # javascript
        "go test ./...",  # go
        "gotestsum ./...",  # go
        "cargo test",  # rust
        "mvn test",  # java
        "./gradlew test",  # java
        "tox -e py312",  # python
        "nox -s tests",  # python
    ],
)
def test_mixed_covers_every_language(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize("command", ["npx vitest run", "npm run test:unit", "yarn test"])
def test_generic_fallback_strengthened_for_unknown_repos(command):
    # A repo whose framework cannot be inferred at all still gets the js runners.
    assert _has_test_tool_call(_tc(command), "unknown") is True


def test_single_language_lists_are_unchanged():
    # Fix B must not widen the per-language paths — only "mixed" and "unknown".
    assert _has_test_tool_call(_tc("python -m pytest tests/"), "python") is True
    assert _has_test_tool_call(_tc("cargo test"), "python") is False


# --- Fix C: executing a file under tests/ is a test run ----------------------


@pytest.mark.parametrize(
    "command",
    [
        "bash tests/integration/test_sudoers_security.sh",
        "sh tests/run.sh",
        "zsh tests/run.sh",
        "./tests/e2e/run.sh",
        "tests/e2e/run.sh",
        "python tests/manual/probe.py",
        "python3.12 tests/manual/probe.py",
        "cd /w && bash tests/x.sh",
        "sudo bash tests/x.sh",
        "sudo -u openace bash tests/x.sh",
        "timeout 60 bash tests/x.sh",
        "nice -n 10 bash tests/x.sh",
        "env FOO=1 bash tests/x.sh",
        "bash 'tests/x.sh'",
        'bash "tests/my test.sh"',
        "node tests/e2e/run.js",
    ],
)
def test_test_path_execution_is_recognized(command):
    assert _is_test_path_execution(command) is True
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command",
    [
        # Syntax checks: exit 0 without running anything.
        "bash -n tests/x.sh",
        "bash -n tests/x.sh && echo ok",
        "sh -en tests/x.sh",
        "zsh -n tests/x.sh",
        "bash --norc -n tests/x.sh",
        "node --check tests/x.js",
        # `python -m <mod>` is an allowlist: only real runners count. These are
        # this project's own pre-commit tools and a denylist kept missing them.
        "python -m ruff check tests/x.py",
        "python -m black --check tests/x.py",
        "python -m mypy tests/x.py",
        "python -m flake8 tests/x.py",
        "python -m isort --check tests/x.py",
        "python -m py_compile tests/x.py",
        # `npx <tool>` likewise.
        "npx prettier --check tests/x.ts",
        "npx eslint tests/x.ts",
        "npx tsc --noEmit tests/x.ts",
        # Not under tests/.
        "bash scripts/deploy.sh",
        "python scripts/migrate.py",
    ],
)
def test_non_execution_and_non_test_paths_are_rejected(command):
    assert _is_test_path_execution(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/x.py",
        "python -m unittest tests.test_x",
        "python -m nose2 tests",
        "npx vitest run tests/x.ts",
        "npx jest tests/x.ts",
        "npx playwright test tests/e2e",
    ],
)
def test_module_and_npx_runner_allowlist(command):
    assert _is_test_path_execution(command) is True


def test_read_only_prefilter_still_wins_over_the_path_rule():
    # PR-1's filter runs first: reading a test file is not executing it, even
    # though the path matches.
    assert _has_test_tool_call(_tc("cat tests/integration/test_x.sh"), "mixed") is False
    assert _has_test_tool_call(_tc("grep -rn foo tests/e2e/run.sh"), "mixed") is False


def test_heredoc_body_does_not_trigger_the_path_rule():
    command = "cat > tests/x.sh <<'EOF'\nbash tests/other.sh\nEOF"
    assert _has_test_tool_call(_tc(command), "mixed") is False


# --- Fix H: the framework walk must skip .worktrees --------------------------


def test_infer_framework_skips_worktrees(tmp_path):
    # A worktree checkout under the repo must not contribute markers: it is a
    # copy of the same project, so it both slows the walk and can flip the
    # inferred framework.
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    wt = tmp_path / ".worktrees" / "wf-1"
    wt.mkdir(parents=True)
    (wt / "package.json").write_text("{}")
    assert _infer_test_framework(str(tmp_path), "claude-code") == "python"


def test_infer_framework_still_reports_mixed_for_a_real_polyglot_repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n")
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    assert _infer_test_framework(str(tmp_path), "claude-code") == "mixed"


# --- shlex robustness --------------------------------------------------------


def test_unbalanced_quotes_do_not_raise():
    assert _shell_tokens("bash 'tests/x.sh") is not None
    assert _is_test_path_execution("bash 'tests/x.sh") in (True, False)


# --- Fix G: vitest output must parse into counts, not exit-code guesswork ----


def test_vitest_output_parses_with_counts():
    from app.modules.workspace.autonomous.command_evidence.test_evidence import (
        parse_test_evidence,
    )
    from app.modules.workspace.autonomous.command_evidence.types import (
        CommandExecutionEvidence,
    )

    # Real vitest summary shape: no colon, count follows the label. Both
    # _parse_jest regexes missed it, so this repo's own frontend suite fell to
    # the exit-code-only generic parser.
    evidence = CommandExecutionEvidence(
        command_id="c1",
        id=1,
        tool_name="Bash",
        shell_command="cd /w/frontend && npm run test:coverage",
        exit_code=0,
        output_excerpt=" Test Files  3 passed (3)\n      Tests  12 passed (12)\n   Duration  1.20s",
        session_id="s",
    )
    parsed = parse_test_evidence(evidence, framework_hint="mixed")
    assert parsed.framework == "javascript"
    assert parsed.passed == 12
    assert parsed.parser_confidence == "high"


def test_npm_run_test_resolves_to_javascript():
    from app.modules.workspace.autonomous.command_evidence.test_evidence import _resolve_framework
    from app.modules.workspace.autonomous.command_evidence.types import (
        CommandExecutionEvidence,
    )

    for cmd in ("npm run test:coverage", "yarn test:unit", "pnpm run test"):
        evidence = CommandExecutionEvidence(
            command_id="c", shell_command=cmd, exit_code=0, output_excerpt="x", session_id="s"
        )
        assert _resolve_framework(evidence, "mixed") == "javascript", cmd

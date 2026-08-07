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
    from app.modules.workspace.autonomous.command_evidence.test_evidence import parse_test_evidence
    from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence

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
    from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence

    for cmd in ("npm run test:coverage", "yarn test:unit", "pnpm run test"):
        evidence = CommandExecutionEvidence(
            command_id="c", shell_command=cmd, exit_code=0, output_excerpt="x", session_id="s"
        )
        assert _resolve_framework(evidence, "mixed") == "javascript", cmd


# --- Negative cases from the PR-3 review (D1-D6, D11) ------------------------
#
# The first cut of Fix C had none of these, and each is a fail-open that reached
# ExecutionVerdict.PASSED end to end.


@pytest.mark.parametrize(
    "command",
    [
        # D1: an unrecognized tool with a tests/ argument is not a test run.
        # The path must BE the command, not merely appear among its arguments;
        # PR-1's non-executing filter is a denylist and cannot be the only guard.
        "ruff check tests/x.py",
        "black --check tests/x.py",
        "mypy tests/x.py",
        "eslint tests/x.ts",
        "prettier --check tests/x.ts",
        "tsc --noEmit tests/x.ts",
        "shellcheck tests/integration/test_sudoers_security.sh",
        "pre-commit run --files tests/x.py",
        "tar czf out.tgz tests/x.sh",
        # D2: a syntax check stays one behind any wrapper prefix.
        "sudo bash -n tests/x.sh",
        "sudo -u openace bash -n tests/x.sh",
        "timeout 60 bash -n tests/x.sh",
        "nice -n 10 bash -n tests/x.sh",
        "env FOO=1 bash -n tests/x.sh",
        "sudo node --check tests/x.js",
        "bash -o noexec tests/x.sh",
        # D3: installing a runner is not running it — the hole the plan deleted
        # Fix F' to avoid, and frontend/package.json ships this exact command.
        "npx playwright install",
        "npx playwright install --with-deps",
        "yarn playwright install",
        "npx cypress install",
        "npx cypress verify",
        "npx playwright codegen",
        "npx playwright show-report",
        # D4: bare runner names must not match as substrings of other commands.
        "cargo tree",
        "cargo tomlfmt",
        "pip download tox",
        "kubectl apply -f nox.yaml",
        "python -c 'import equinox'",
        "helm install mocha ./chart",
        "docker build -t mocha .",
        # D6: an npm script merely starting with "test" is not a test run.
        "npm run testdata:seed",
        "npm run test-utils:build",
        # A leading env assignment must not shift the artifact-verb window off
        # the verb. Enumerating the stripped token list while slicing the
        # unstripped one moved it left by the number of assignments, so the
        # `install` in `FOO=1 helm install mocha` fell outside it.
        "FOO=1 helm install mocha ./chart",
        "HELM_NS=prod helm install mocha ./chart",
        "CI=1 pip download tox",
        "A=1 B=2 pip download tox",
        "DOCKER_BUILDKIT=1 docker build -t mocha .",
    ],
)
def test_review_fail_open_cases_are_rejected(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # D11: agents work inside .worktrees/<id> checkouts and use absolute
        # paths, so the anchored-at-start regex rejected wf221's own command.
        "bash /home/openace/auto-dev-221/tests/integration/test_sudoers_security.sh",
        "bash ../tests/x.sh",
        "bash frontend/tests/x.sh",
        "bash tests/x.bats",
        # D3's positive twin: the test subcommands DO count.
        "npx playwright test tests/e2e",
        "npx cypress run",
    ],
)
def test_review_fail_closed_cases_are_recognized(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


def test_vitest_counts_do_not_override_a_nonzero_exit():
    # D5: before the vitest patterns no counts parsed and the exit code decided.
    # Letting counts silently win turns a failing `npm run test:coverage` — a
    # coverage threshold, a failing posttest, a teardown crash — into PASSED.
    #
    # The assertion is on the *run* verdict, not `!= "passed"` on the evidence.
    # The first cut downgraded to (INCONCLUSIVE, MEDIUM), which satisfies
    # `!= "passed"` while still letting the run reach PASSED, so the weaker
    # assertion passed against a fix that did not hold (#2376 PR-3 re-review N2).
    from app.modules.workspace.autonomous.command_evidence.test_evidence import parse_test_evidence
    from app.modules.workspace.autonomous.command_evidence.test_verdict import compute_run_verdict
    from app.modules.workspace.autonomous.command_evidence.types import (
        CommandExecutionEvidence,
        ExecutionVerdict,
    )

    evidence = CommandExecutionEvidence(
        command_id="c",
        id=1,
        tool_name="Bash",
        shell_command="cd /w/frontend && npm run test:coverage",
        exit_code=1,
        output_excerpt=(
            " Test Files  3 passed (3)\n      Tests  12 passed (12)\n"
            "ERROR: Coverage for lines (41.2%) does not meet global threshold (80%)"
        ),
        session_id="s",
    )
    parsed = parse_test_evidence(evidence, framework_hint="mixed")
    assert parsed.verdict == "failed"
    assert compute_run_verdict([parsed]) is ExecutionVerdict.FAILED


def test_test_files_count_is_not_a_test_count():
    # D7: "Test Files 8 passed" is a FILE count. Recording it as `passed` claims
    # 8 passing tests for a run that collected none — the very thing the gate
    # exists to catch.
    from app.modules.workspace.autonomous.command_evidence.test_evidence import parse_test_evidence
    from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence

    evidence = CommandExecutionEvidence(
        command_id="c",
        id=1,
        tool_name="Bash",
        shell_command="npx vitest run",
        exit_code=0,
        output_excerpt=" Test Files  8 passed (8)\n      Tests  no tests",
        session_id="s",
    )
    assert parse_test_evidence(evidence, framework_hint="mixed").passed is None


# --- Re-review N1: token equality must not lose wrapped invocations ----------
#
# Requiring the runner in *command position* closed D4 but broke every wrapped
# form. All of these are True on the PR-2 tip, where matching was substring
# based, and all of them are ordinary — the fail-closed direction is the more
# dangerous one here, because a workflow that ran its tests correctly gets
# killed for it (that is the whole reason #2376 exists).


@pytest.mark.parametrize(
    "command",
    [
        "sudo pytest",
        "sudo -u openace pytest tests/",
        "timeout 600 pytest tests/",
        "nice -n 10 pytest",
        "stdbuf -oL pytest tests/",
        "env FOO=1 pytest",
        "poetry run pytest",
        "uv run pytest",
        "pipenv run pytest tests/",
        "docker compose run --rm app pytest tests/",
        "/usr/local/bin/pytest tests/",
        # A shell -c body is a single token, so token equality cannot see the
        # runner inside it unless the body is re-split.
        'bash -c "pytest tests/"',
        "sh -c 'pytest tests/ -q'",
        'bash -lc "npm test"',
        'docker compose run --rm app sh -c "pytest -q"',
        'sudo -u openace bash -c "bash tests/integration/test_sudoers_security.sh"',
        # `_` is a genuine script-name separator; only `-` and alphanumerics
        # continue a pattern.
        "npm run test_unit",
    ],
)
def test_wrapped_invocations_are_still_recognized(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command",
    [
        # The wrapper is cleared before the body is expanded, so a read-only
        # command holding a shell string does not launder it.
        'echo bash -c "pytest tests/"',
        'bash -c "grep -rn pytest tests/"',
        'bash -c "cat tests/x.sh"',
        # -n parses without running, whether or not -c follows.
        'bash -n -c "pytest tests/"',
    ],
)
def test_shell_c_expansion_does_not_launder_non_executing_commands(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # N4: collection flags exit 0 having asserted nothing, and the structured
        # layer's exit-code fallback would call that a pass. `--collect-only` was
        # reachable before PR-3; `npx playwright test --list` is new with D3's
        # positive twin.
        "npx playwright test --list",
        "pytest --collect-only tests/",
        "python -m pytest --co -q",
        "npx jest --listTests",
    ],
)
def test_collection_only_runs_are_not_test_runs(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


# --- Re-review 3: patterns match TOKENS, never raw segment text --------------
#
# HIGH-1. Multi-word patterns were a substring scan over the segment, so they
# read inside quoted argument bodies. PR-3 put every language's patterns in
# front of every polyglot repo, and the shape that broke it is the one this
# workflow itself emits: its PR-creation step writes a test plan into --body.
# `git` is in the read-only pre-filter; `gh` is not, and no denylist can be
# exhaustive. The same hole bypassed the syntax-check guard and the exclude
# flags, and every one of these reached an authoritative PASSED.


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title "x" --body "## Test plan\n- [x] npm run test:coverage — 12 passed"',
        'gh issue comment 1 --body "ran npm test, 40 passed"',
        'echo "go test ./... all green"',
        'git commit -m "npm run test passes"',
        'curl -d "mvn test" http://x',
        # The quoted body also defeated _is_syntax_check_only and the exclude
        # flags, because it never had to survive them as tokens.
        'bash -n -c "npm test"',
        'bash --help -c "npm test"',
    ],
)
def test_quoted_argument_bodies_are_not_test_invocations(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


# HIGH-2. The artifact-verb veto was a set intersection over every preceding
# token, but the verbs are ordinary English words that appear as wrapper
# *operands* — usernames, container names, conda envs, hostnames. All of these
# work on the PR-2 tip; rejecting them is the fail-closed direction that killed
# wf 220/221 in the first place.


@pytest.mark.parametrize(
    "command",
    [
        "sudo -u build pytest tests/",
        "docker run --rm --name build img pytest",
        "docker run --rm -w build img pytest tests/",
        "docker run --rm -u build img pytest",
        "conda run -n build pytest tests/",
        "poetry run --directory build pytest",
        "kubectl exec -it add -- pytest tests/",
        "nix-shell -p tag --run pytest",
        "timeout 600 sudo -u build /opt/venv/bin/pytest tests/",
        # ssh/su take a host or user in first position, not a subcommand.
        "ssh build pytest tests/",
        "su build -c pytest",
    ],
)
def test_artifact_verbs_as_wrapper_operands_do_not_veto(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


def test_a_verb_in_command_position_still_vetoes():
    # The coreutils `install` copying a file named pytest is not a test run.
    assert _has_test_tool_call(_tc("install -m 755 pytest /usr/bin"), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # MEDIUM: `.+` swallowed a traversal back OUT of tests/.
        "python tests/../scripts/deploy.py",
        "bash /home/x/tests/../scripts/prod_migrate.sh",
    ],
)
def test_paths_that_traverse_out_of_tests_are_rejected(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize("command", ["bash ../tests/x.sh", "bash ../../tests/e2e/run.sh"])
def test_dotdot_before_the_tests_component_is_fine(command):
    # Agents run from a subdirectory routinely; only the tail is checked.
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # pnpm/yarn only ever matched by riding along inside `npm test` /
        # `yarn test` as substrings — the old matcher had no *leading* boundary.
        # Token matching removes that accident, so they are listed explicitly.
        ("pnpm test", True),
        ("pnpm run test:unit", True),
        ("yarn run test:unit", True),
        # ...and the same accident made `xgo test` match `go test`.
        ("xgo test ./...", False),
    ],
)
def test_leading_boundary_now_applies_to_multiword_patterns(command, expected):
    assert _has_test_tool_call(_tc(command), "mixed") is expected


@pytest.mark.parametrize(
    "command",
    [
        # Leading words compare by basename, so an absolute interpreter or
        # wrapper path does not lose the match.
        "/usr/bin/python -m pytest tests/",
        "/w/gradlew test",
    ],
)
def test_multiword_patterns_resolve_leading_basenames(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


# --- Re-review 4: the veto is anchored on the TOOL, not the verb's neighbours -
#
# Round 3 excused a verb whose left neighbour was an option, to stop
# `sudo -u build pytest` being rejected. But docker/helm/pip/kubectl/aws all
# accept global options BEFORE the subcommand, so any of them defeats the veto.
# Both valueless and `--opt=value` forms do it, so "skip only valueless flags"
# is not a fix either — you cannot tell `-u build` from `--debug build` without
# an option table.


@pytest.mark.parametrize(
    "command",
    [
        "docker --debug build -t mocha .",
        "docker --log-level=debug build -t mocha .",
        "docker --tls build --tag mocha .",
        "helm --debug install mocha ./chart",
        "helm --kube-context=prod install mocha .",
        "pip -q download tox",
        "pip --quiet download tox",
        "pip --disable-pip-version-check download tox",
        # Scanning for the tool rather than reading token 0 covers this too.
        "sudo docker build -t mocha .",
        # The veto is per segment, so the multi-word patterns get it as well;
        # it used to apply only to bare runner names.
        "helm install mvn test",
        "pip download gradle test",
        "docker build -t go test",
    ],
)
def test_artifact_operations_are_vetoed_whatever_the_flags(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


# --- Re-review 5: the veto is the UNION of two rules ------------------------
#
# Round 4 replaced the bare-word rule with the tool-anchored one. Measured
# against the previous tip that traded 12 fail-opens for 30, because the two
# rules fail in opposite directions and neither subsumes the other:
#
#   bare-word rule   tool-agnostic, catches noun levels and unlisted tools;
#                    misses global options (`docker --debug build`)
#   tool-anchored    immune to global options; misses noun levels
#                    (`docker image build`) and anything not enumerated
#
# Both must run. These two batteries are the ones the replacement opened.


@pytest.mark.parametrize(
    "command",
    [
        # A noun level between the tool and the verb hides it from the
        # tool-anchored rule, which reads the first non-flag argument as THE
        # subcommand. All ordinary commands.
        "docker image build -t mocha .",
        "docker buildx build -t mocha .",
        "docker image pull mocha",
        "docker container create --name mocha img",
        "docker volume create tox",
        "docker network create nox",
        "helm repo add mocha https://example.invalid",
        "helm plugin install mocha",
        "gh release create --title tox v1",
        "gh repo create mocha --public",
        "gcloud compute instances create tox",
        "npm cache add mocha",
    ],
)
def test_noun_level_subcommands_are_still_artifact_operations(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # Anchoring on a tool set makes every unlisted tool a fail-open. The
        # bare-word rule needs no enumeration, which is exactly why it stays.
        "dnf install -y tox",
        "yum install -y tox",
        "apk add tox",
        "zypper install tox",
        "pipx install tox",
        "snap install mocha",
        "asdf install tox",
        "rustup component add tox",
        "bundle add mocha",
        "dotnet add package tox",
        "nuget install mocha",
        "flatpak install mocha",
        "docker-compose build mocha",
        "podman-compose build mocha",
        "choco install mocha",
        "scoop install tox",
        "port install tox",
        "guix install tox",
    ],
)
def test_unenumerated_tools_are_still_artifact_operations(command):
    assert _has_test_tool_call(_tc(command), "mixed") is False


def test_both_veto_rules_are_load_bearing():
    # Neither rule may be dropped: each of these is caught by exactly one.
    from app.modules.workspace.autonomous.orchestrator import (
        _artifact_tool_subcommand_is_a_verb,
        _artifact_verb_after_a_bare_word,
        _shell_tokens,
    )

    only_bare_word = _shell_tokens("dnf install -y tox")
    assert _artifact_verb_after_a_bare_word(only_bare_word) is True
    assert _artifact_tool_subcommand_is_a_verb(only_bare_word) is False

    only_tool_anchor = _shell_tokens("docker --debug build -t mocha .")
    assert _artifact_verb_after_a_bare_word(only_tool_anchor) is False
    assert _artifact_tool_subcommand_is_a_verb(only_tool_anchor) is True


def test_the_path_rule_is_positional_and_needs_no_veto():
    # An artifact operation naming a test script is already rejected, because
    # the path rule requires the path to BE the command or to follow an
    # interpreter — not merely to appear as an argument.
    assert _has_test_tool_call(_tc("helm install mocha ./tests/run.sh"), "mixed") is False
    assert _has_test_tool_call(_tc("docker build -t mocha . tests/run.sh"), "mixed") is False


@pytest.mark.parametrize(
    "command",
    [
        # ...which is why the veto must NOT gate the path door. The bare-word
        # rule fires on any verb following a bare word, and a test script's own
        # ARGUMENT qualifies. Gating both doors looked tidier and killed these
        # seven — the last is wf221's own command with a mode argument, and
        # runner scripts routinely take one (#2376 PR-3 review-6).
        "bash tests/integration/run.sh build",
        "bash tests/integration/run.sh install",
        "bash tests/e2e/run.sh create",
        "./tests/e2e/run.sh build",
        "sudo -u openace bash tests/deploy_test.sh install",
        "bash tests/x.sh add",
        "bash tests/integration/test_sudoers_security.sh remove",
    ],
)
def test_test_scripts_may_take_an_argument_that_looks_like_a_verb(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    "command",
    [
        # ...while the tool's subcommand being something other than an artifact
        # verb keeps the wrapper cases working. `run` is the subcommand here and
        # `build` is merely an option's operand.
        "docker run --rm --name build img pytest",
        "conda run -n build pytest tests/",
        "poetry run --directory build pytest",
        "docker compose run --rm app pytest tests/",
        # sudo/ssh/su are not artifact tools at all.
        "sudo -u build pytest tests/",
        "ssh build pytest tests/",
        "su build -c pytest",
        "kubectl exec -it add -- pytest tests/",
    ],
)
def test_wrapper_operands_are_not_artifact_operations(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A shell whose -c body is expanded must also be recognized as an
        # interpreter and as a syntax-check shell, or the two come apart:
        # dash/ksh had their body expanded but their `-n` ignored, and could not
        # execute a tests/ file.
        ('dash -n -c "npm test"', False),
        ('ksh -n -c "npm test"', False),
        ('dash -n -c "pytest tests/"', False),
        ("dash tests/integration/test_x.sh", True),
        ("ksh tests/integration/test_x.sh", True),
        ('dash -c "pytest tests/"', True),
    ],
)
def test_shell_c_shells_are_also_interpreters_and_syntax_check_shells(command, expected):
    assert _has_test_tool_call(_tc(command), "mixed") is expected


def test_shell_sets_stay_in_step():
    # Structural twin of the single-word-pattern invariant, and unlike that one
    # these were actually violated: adding a shell to _SHELL_C_COMMANDS without
    # adding it to both of the others is a silent fail-open plus a fail-closed.
    from app.modules.workspace.autonomous.orchestrator import (
        _EXECUTING_INTERPRETERS,
        _SHELL_C_COMMANDS,
        _SYNTAX_CHECK_SHELLS,
    )

    assert not _SHELL_C_COMMANDS - _SYNTAX_CHECK_SHELLS
    assert not _SHELL_C_COMMANDS - _EXECUTING_INTERPRETERS


@pytest.mark.parametrize(
    "command",
    [
        # A flag between the pattern's words. `npm --prefix frontend test` is
        # wf220's command written the other standard way, and nothing caught it.
        # Only flags are skipped, never bare operands.
        "npm --silent test",
        "npm -s test",
        "npm --workspace=frontend test",
        "cargo -q test",
        "cargo --offline test",
        "mvn -q test",
        "gradle --offline test",
        "./gradlew --no-daemon test",
        "npm --if-present run test",
    ],
)
def test_flags_may_separate_the_words_of_a_pattern(command):
    assert _has_test_tool_call(_tc(command), "mixed") is True


def test_bare_operands_may_not_separate_the_words_of_a_pattern():
    # The reason skipping is limited to flags: a `-x value` form cannot be told
    # from a subcommand, so skipping operands would make an install a test run.
    assert _has_test_tool_call(_tc("npm ci --prefix test"), "mixed") is False


def test_every_single_word_pattern_is_registered_as_a_bare_runner():
    # Structural invariant, not a behaviour case. _pattern_matches_segment routes
    # on _BARE_RUNNER_PATTERNS membership: registered names need whole-token
    # equality plus the artifact-verb veto, everything else takes the multi-word
    # path, whose final word may be continued by ":"/"_". A one-word pattern that
    # slips through there would match `nox:build` and skip the veto entirely —
    # a silent fail-open introduced by a one-line edit to _TEST_COMMAND_PATTERNS.
    from app.modules.workspace.autonomous.orchestrator import (
        _ALL_TEST_PATTERNS,
        _BARE_RUNNER_PATTERNS,
    )

    stray = [
        p for p in _ALL_TEST_PATTERNS if len(p.split()) == 1 and p not in _BARE_RUNNER_PATTERNS
    ]
    assert stray == [], f"single-word patterns must be registered as bare runners: {stray}"


def test_resolve_framework_does_not_steal_go_from_a_trailing_npm_command():
    # D8: the npm regex scans the whole command, so placing it before the go/
    # cargo head checks sent `go test ./... && npm run test` to _parse_jest,
    # which never saw the FAIL lines.
    from app.modules.workspace.autonomous.command_evidence.test_evidence import _resolve_framework
    from app.modules.workspace.autonomous.command_evidence.types import CommandExecutionEvidence

    evidence = CommandExecutionEvidence(
        command_id="c",
        shell_command="go test ./... 2>&1 | tail -5 && npm run test",
        exit_code=0,
        output_excerpt="ok  x  0.31s\nFAIL  y  0.02s",
        session_id="s",
    )
    assert _resolve_framework(evidence, "mixed") == "go"

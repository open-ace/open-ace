"""Tests for Issue #2334: Sudoers Hardening.

Tests verify:
1. GIT_SAFE/GH_SAFE use (ALL) runas for cross-user operations
2. git/gh removed from OPENACE_UTILS
3. No shell syntax in generated sudoers
4. WebUI launcher has no fallback
5. Dangerous verbs are blocked
6. Audit logging is present
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Key files
DOCKER_ENTRYPOINT = PROJECT_ROOT / "docker-entrypoint.sh"
INSTALL_SH = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
GENERATE_SUDOERS_SH = PROJECT_ROOT / "scripts" / "generate-sudoers.sh"
GITHUB_OPS_PY = PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "github_ops.py"
GIT_WORKSPACE_PY = (
    PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "git_workspace.py"
)

# Test markers for Issue #2334 (sudoers hardening regression)
pytestmark = [pytest.mark.issue(2334), pytest.mark.regression, pytest.mark.security]


def _extract_cmnd_alias(text: str, alias_name: str) -> list[str]:
    """Extract commands from a Cmnd_Alias definition."""
    # Match multi-line Cmnd_Alias definitions
    pattern = rf"Cmnd_Alias\s+{alias_name}\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return []

    # Parse the commands (they may span multiple lines with \ continuation)
    content = match.group(1)
    # Remove line continuations
    content = re.sub(r"\\\s*\n\s*", " ", content)
    # Split by comma, handling whitespace
    commands = [c.strip() for c in content.split(",") if c.strip()]
    return commands


def _get_runas_for_alias(text: str, alias_name: str) -> list[str]:
    """Get runas targets for user rules referencing an alias."""
    # Match patterns like: user ALL=(target) NOPASSWD: ALIAS_NAME
    pattern = rf"ALL=\(([A-Za-z_][A-Za-z0-9_-]*|\*)\)\s*NOPASSWD:\s*{alias_name}\b"
    return re.findall(pattern, text)


class TestGitSafeRunas:
    """Tests for GIT_SAFE runas configuration."""

    def test_git_safe_has_all_runas_in_docker(self):
        """GIT_SAFE must have (ALL) runas in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()
        targets = _get_runas_for_alias(text, "GIT_SAFE")
        assert targets, "No GIT_SAFE user-rule found in docker-entrypoint.sh"
        assert "ALL" in targets, (
            f"GIT_SAFE runas is {targets!r} in Docker; "
            f"must be (ALL) for github_ops cross-user git (#2280, #2334)"
        )

    def test_git_safe_has_all_runas_in_package(self):
        """GIT_SAFE must have (ALL) runas in Package install.sh."""
        text = INSTALL_SH.read_text()
        targets = _get_runas_for_alias(text, "GIT_SAFE")
        # Package install.sh may not have GIT_SAFE yet if using OPENACE_UTILS
        # Check that if GIT_SAFE exists, it has (ALL) runas
        if targets:
            assert "ALL" in targets, (
                f"GIT_SAFE runas is {targets!r} in Package; "
                f"must be (ALL) for github_ops cross-user git (#2280, #2334)"
            )

    def test_git_safe_has_all_runas_in_generator(self):
        """GIT_SAFE must have (ALL) runas in unified generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        targets = _get_runas_for_alias(text, "GIT_SAFE")
        assert targets, "No GIT_SAFE user-rule found in generate-sudoers.sh"
        assert "ALL" in targets, (
            f"GIT_SAFE runas is {targets!r} in generator; "
            f"must be (ALL) for github_ops cross-user git (#2280, #2334)"
        )


class TestGhSafeRunas:
    """Tests for GH_SAFE runas configuration."""

    def test_gh_safe_has_all_runas_in_docker(self):
        """GH_SAFE must have (ALL) runas in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()
        targets = _get_runas_for_alias(text, "GH_SAFE")
        assert targets, "No GH_SAFE user-rule found in docker-entrypoint.sh"
        assert "ALL" in targets, (
            f"GH_SAFE runas is {targets!r} in Docker; "
            f"must be (ALL) for github_ops cross-user gh (#2280, #2334)"
        )

    def test_gh_safe_has_all_runas_in_generator(self):
        """GH_SAFE must have (ALL) runas in unified generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        targets = _get_runas_for_alias(text, "GH_SAFE")
        assert targets, "No GH_SAFE user-rule found in generate-sudoers.sh"
        assert "ALL" in targets, (
            f"GH_SAFE runas is {targets!r} in generator; "
            f"must be (ALL) for github_ops cross-user gh (#2280, #2334)"
        )


class TestGitGhRemovedFromOpenaceUtils:
    """Tests for git/gh removal from OPENACE_UTILS."""

    def test_git_not_in_openace_utils_docker(self):
        """git must NOT be in OPENACE_UTILS in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()
        # Find OPENACE_UTILS definition
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL,
        )
        if utils_match:
            utils_content = utils_match.group(1)
            # Check for git wildcards
            assert (
                "git *" not in utils_content
            ), "git * must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
            assert (
                "/usr/bin/git" not in utils_content
            ), "/usr/bin/git must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"

    def test_gh_not_in_openace_utils_docker(self):
        """gh must NOT be in OPENACE_UTILS in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL,
        )
        if utils_match:
            utils_content = utils_match.group(1)
            assert (
                "gh *" not in utils_content
            ), "gh * must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
            assert (
                "/usr/bin/gh" not in utils_content
            ), "/usr/bin/gh must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"

    def test_git_not_in_openace_utils_generator(self):
        """git must NOT be in OPENACE_UTILS in generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL,
        )
        assert utils_match, "OPENACE_UTILS definition not found in generator"
        utils_content = utils_match.group(1)
        assert (
            "git *" not in utils_content
        ), "git * must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
        assert (
            "/usr/bin/git" not in utils_content
        ), "/usr/bin/git must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"

    def test_gh_not_in_openace_utils_generator(self):
        """gh must NOT be in OPENACE_UTILS in generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL,
        )
        assert utils_match, "OPENACE_UTILS definition not found in generator"
        utils_content = utils_match.group(1)
        assert (
            "gh *" not in utils_content
        ), "gh * must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
        assert (
            "/usr/bin/gh" not in utils_content
        ), "/usr/bin/gh must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"


class TestNoShellSyntax:
    """Tests for no shell syntax in sudoers."""

    def test_no_shell_syntax_in_docker_heredoc(self):
        """No shell control statements in Docker heredoc content."""
        text = DOCKER_ENTRYPOINT.read_text()
        # Find the heredoc content (between cat > ... << SUDOERS_EOF and SUDOERS_EOF)
        heredoc_match = re.search(
            r"cat\s+>\s+\S+\s+<<\s*\w+\s*\n(.*?)\n\w+\s*$",
            text,
            re.DOTALL,
        )
        if heredoc_match:
            heredoc_content = heredoc_match.group(1)
            # Check for shell control statements
            # Note: We check for standalone 'if', 'then', 'else', 'fi' as sudoers keywords
            # not inside ${} variable expansions
            lines = heredoc_content.split("\n")
            for i, line in enumerate(lines, 1):
                # Skip comment lines
                if line.strip().startswith("#"):
                    continue
                # Check for shell control flow that shouldn't be in sudoers
                # Pattern: line starting with 'if' or containing 'else' as command
                assert not re.match(
                    r"^\s*if\s+\[", line
                ), f"Shell syntax 'if [' found in sudoers heredoc at line {i}: {line}"
                assert not re.match(
                    r"^\s*then\s*$", line
                ), f"Shell syntax 'then' found in sudoers heredoc at line {i}: {line}"
                assert not re.match(
                    r"^\s*else\s*$", line
                ), f"Shell syntax 'else' found in sudoers heredoc at line {i}: {line}"
                assert not re.match(
                    r"^\s*fi\s*$", line
                ), f"Shell syntax 'fi' found in sudoers heredoc at line {i}: {line}"

    def test_no_shell_syntax_in_generator_output(self):
        """Generator output must not contain shell control statements."""
        result = subprocess.run(
            ["bash", str(GENERATE_SUDOERS_SH), "--dry-run", "--output", "/dev/null"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        output = result.stdout
        # Check for shell control statements
        assert "if [" not in output, "Shell syntax 'if [' found in generator output"
        assert (
            "\nthen\n" not in output or "\nthen " not in output
        ), "Shell 'then' found in generator output"
        assert (
            "\nelse\n" not in output or "\nelse " not in output
        ), "Shell 'else' found in generator output"
        assert (
            "\nfi\n" not in output or "\nfi " not in output
        ), "Shell 'fi' found in generator output"


class TestDangerousVerbsBlocked:
    """Tests that dangerous verbs are blocked from whitelists."""

    def test_gh_repo_delete_not_in_whitelist(self):
        """gh repo delete must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        for cmd in gh_safe_commands:
            assert "repo delete" not in cmd.lower(), f"gh repo delete must NOT be in GH_SAFE: {cmd}"

    def test_gh_repo_fork_not_in_whitelist(self):
        """gh repo fork must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        for cmd in gh_safe_commands:
            assert "repo fork" not in cmd.lower(), f"gh repo fork must NOT be in GH_SAFE: {cmd}"

    def test_arbitrary_gh_api_not_in_whitelist(self):
        """Arbitrary gh api must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        # Check that all gh api commands have specific paths
        for cmd in gh_safe_commands:
            if "gh api" in cmd and "--jq" not in cmd:
                # Allow specific whitelisted paths
                assert (
                    "api user" in cmd or "api repos/*" in cmd
                ), f"Arbitrary gh api must NOT be in GH_SAFE: {cmd}"

    def test_git_force_push_not_in_whitelist(self):
        """git push --force must NOT be in GIT_SAFE (except force-with-lease)."""
        text = GENERATE_SUDOERS_SH.read_text()
        git_safe_commands = _extract_cmnd_alias(text, "GIT_SAFE")

        # Filter out allowed --force-with-lease variants
        forbidden_commands = [
            cmd
            for cmd in git_safe_commands
            if "push" in cmd and "--force" in cmd and "--force-with-lease" not in cmd
        ]
        assert (
            not forbidden_commands
        ), f"git push --force must NOT be in GIT_SAFE: {forbidden_commands}"


class TestWebuiLauncherNoFallback:
    """Tests that WebUI launcher has no fallback to broad wildcard."""

    def test_no_webui_fallback_in_docker(self):
        """No fallback to ${WEBUI_PATH} * in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()

        # Look for the problematic fallback pattern
        # The old code had: else open-ace ALL=(ALL) NOPASSWD: ${WEBUI_PATH} *
        # This should NOT exist in the heredoc content

        # Check for fallback rule patterns
        fallback_patterns = [
            r"else\s*\n\s*open-ace\s+ALL=\(ALL\)\s+NOPASSWD:\s+\$\{WEBUI_PATH\}\s*\*",
            r"else\s*\n\s*openace\s+ALL=\(ALL\)\s+NOPASSWD:\s+\$\{WEBUI_PATH\}\s*\*",
        ]

        for pattern in fallback_patterns:
            match = re.search(pattern, text)
            assert match is None, (
                "WebUI fallback rule found in Docker entrypoint; "
                "wrapper must be required per #2334"
            )

    def test_generator_requires_webui_wrapper(self):
        """Generator must require WebUI launcher wrapper."""
        text = GENERATE_SUDOERS_SH.read_text()

        # Check that generator checks for wrapper existence
        assert "WEBUI_LAUNCH_WRAPPER" in text, "Generator must check for webui-launch wrapper"
        assert (
            'if [[ ! -x "$WEBUI_LAUNCH_WRAPPER" ]]' in text
            or 'if [ ! -x "$WEBUI_LAUNCH_WRAPPER" ]' in text
        ), "Generator must validate webui-launch wrapper is executable"


class TestCredentialLeakPrevention:
    """Tests for credential leak prevention via env_keep."""

    def test_gh_token_not_in_env_keep_docker(self):
        """GH_TOKEN must NOT be in env_keep in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()

        # Find env_keep lines (excluding comments)
        env_keep_lines = [
            line
            for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            assert "GH_TOKEN" not in line, f"GH_TOKEN must NOT be in env_keep: {line}"

    def test_api_keys_not_in_env_keep_docker(self):
        """API keys must NOT be in env_keep in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()

        sensitive_vars = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "OPENCLAW_TOKEN",
        ]

        env_keep_lines = [
            line
            for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            for var in sensitive_vars:
                assert var not in line, f"{var} must NOT be in env_keep: {line}"

    def test_gh_token_not_in_env_keep_generator(self):
        """GH_TOKEN must NOT be in env_keep in generator."""
        text = GENERATE_SUDOERS_SH.read_text()

        env_keep_lines = [
            line
            for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            assert "GH_TOKEN" not in line, f"GH_TOKEN must NOT be in env_keep: {line}"


class TestAuditLogging:
    """Tests for audit logging in github_ops."""

    def test_github_ops_has_audit_log_imports(self):
        """github_ops.py should have audit logging capability."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging import
        assert (
            "import logging" in text or "from logging" in text
        ), "github_ops.py should have logging import for audit"

    def test_github_ops_logs_git_operations(self):
        """github_ops.py should log git operations."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging in _run_git
        run_git_match = re.search(
            r"def _run_git\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        if run_git_match:
            run_git_body = run_git_match.group(1)
            # Should have some form of logging
            has_logging = (
                "logger.info" in run_git_body
                or "logger.warning" in run_git_body
                or "logger.error" in run_git_body
                or "logger.debug" in run_git_body
            )
            assert has_logging, "_run_git should log operations for audit trail"

    def test_github_ops_logs_gh_operations(self):
        """github_ops.py should log gh operations."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging in _run_gh
        run_gh_match = re.search(
            r"def _run_gh\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        if run_gh_match:
            run_gh_body = run_gh_match.group(1)
            has_logging = (
                "logger.info" in run_gh_body
                or "logger.warning" in run_gh_body
                or "logger.error" in run_gh_body
                or "logger.debug" in run_gh_body
            )
            assert has_logging, "_run_gh should log operations for audit trail"

    def test_github_ops_git_calls_audit_log(self):
        """github_ops._run_git should call _log_sudo_audit."""
        text = GITHUB_OPS_PY.read_text()

        run_git_match = re.search(
            r"def _run_git\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert run_git_match, "_run_git method not found"
        run_git_body = run_git_match.group(1)
        assert (
            "_log_sudo_audit" in run_git_body
        ), "_run_git should call _log_sudo_audit for audit trail (Issue #2334)"

    def test_github_ops_gh_calls_audit_log(self):
        """github_ops._run_gh should call _log_sudo_audit."""
        text = GITHUB_OPS_PY.read_text()

        # Match _run_gh method with multi-line signature
        run_gh_match = re.search(
            r"def _run_gh\(.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL,
        )
        assert run_gh_match, "_run_gh method not found"
        run_gh_body = run_gh_match.group(1)
        assert (
            "_log_sudo_audit" in run_gh_body
        ), "_run_gh should call _log_sudo_audit for audit trail (Issue #2334)"


class TestGeneratorSyntax:
    """Tests for generator script syntax."""

    def test_generator_passes_bash_syntax_check(self):
        """Generator script must pass bash syntax check."""
        result = subprocess.run(
            ["bash", "-n", str(GENERATE_SUDOERS_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator has syntax errors: {result.stderr}"

    def test_generator_produces_valid_sudoers(self):
        """Generator output must pass visudo syntax check (if visudo available)."""
        # Check if visudo is available
        result = subprocess.run(
            ["which", "visudo"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # visudo not available, skip this test
            return

        # Generate sudoers content
        result = subprocess.run(
            ["bash", str(GENERATE_SUDOERS_SH), "--dry-run", "--output", "/dev/null"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        # Validate with visudo
        validate_result = subprocess.run(
            ["visudo", "-c", "-f", "-"],
            input=result.stdout,
            capture_output=True,
            text=True,
        )
        assert (
            validate_result.returncode == 0
        ), f"Generated sudoers invalid: {validate_result.stderr}"


class TestGithubOpsCommandShapeCoverage:
    """Issue #2635: GIT_SAFE/GH_SAFE must match github_ops command shapes.

    github_ops._run_git's sudo path builds commands where git GLOBAL OPTIONS
    precede the subcommand (mandatory git syntax):

        git -c core.hooksPath=/dev/null -c core.fsmonitor=false
            -c safe.directory=<p> [-C <repo>] <subcommand> ...
        git --git-dir=<...> --work-tree=<...> -c core.hooksPath=/dev/null
            -c core.fsmonitor=false -c safe.directory=<p> <subcommand> ...

    and _run_gh's sudo path always inserts ``-R owner/repo`` before the
    subcommand, plus bare ``gh api repos/...`` forms. The #2334 verb-first
    whitelist (``/usr/bin/git fetch *`` etc.) never matched these, so every
    cross-user orchestrator git/gh call was rejected after the narrowed
    whitelist was deployed. These tests lock the prefix-anchored entries that
    cover those shapes and fail loudly if github_ops changes its prefix
    construction without updating the sudoers generators.
    """

    # The four prefix-anchored entries (as they appear in the generator
    # sources, pre-expansion). All three generators must carry them.
    GIT_PREFIX_ENTRIES = [
        "${GIT_PATH} -c core.hooksPath=/dev/null *",
        "${GIT_PATH} --git-dir=*",
    ]
    GH_PREFIX_ENTRIES = [
        "${GH_PATH} -R *",
        "${GH_PATH} api *",
    ]

    GENERATOR_FILES = [
        ("scripts/generate-sudoers.sh", GENERATE_SUDOERS_SH),
        ("scripts/install-central/package-method/install.sh", INSTALL_SH),
        ("docker-entrypoint.sh", DOCKER_ENTRYPOINT),
    ]

    @pytest.mark.parametrize("entry", GIT_PREFIX_ENTRIES + GH_PREFIX_ENTRIES)
    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_prefix_entries_present_in_all_generators(self, entry, label, path):
        """All three generators must carry the four prefix-anchored entries."""
        assert entry in path.read_text(), (
            f"{label} is missing prefix-anchored entry {entry!r}; "
            f"github_ops._run_git/_run_gh sudo-path commands start with "
            f"global options / -R and cannot match the verb-first whitelist "
            f"(Issue #2635). Keep the three generators consistent."
        )

    # Bare-wildcard shapes the #2635 acceptance criterion forbids: an entry
    # that is just the binary plus ``*`` — whether written with a resolved
    # path, the unexpanded ${GIT_PATH}/${GH_PATH} variable, or a bare
    # ``git``/``gh`` name — after stripping line-continuation backslashes.
    BARE_WILDCARD_RE = re.compile(
        r"^(?:\\\s*)?"
        r"(\$\{GIT_PATH\}|\$\{GH_PATH\}|/usr/bin/git|/usr/local/bin/gh|git|gh)"
        r"\s+\*\s*$"
    )

    def test_no_bare_git_or_gh_wildcard_reintroduced(self):
        """The prefix entries must not degenerate into bare `git *`/`gh *`.

        Acceptance criterion of #2635: no re-introduction of the pre-#2334
        unprefixed wildcards. The guard runs against BOTH the generator's
        expanded dry-run output (where ${GIT_PATH}/${GH_PATH} resolve to the
        host's binary path — a reintroduction appears as ``<bin> *``) and the
        raw Cmnd_Alias entries of all three generator files (covering the
        natural unexpanded ``${GIT_PATH} *`` source form, which the previous
        text-only check silently let through), with leading line-continuation
        ``\\`` prefixes stripped.
        """
        # 1. Expanded generator output: a bare wildcard shows up as
        #    ``<absolute-binary-path> *``.
        try:
            content = self._generate_sudoers()
        except (FileNotFoundError, OSError):
            pytest.skip("bash unavailable; cannot run generate-sudoers.sh --dry-run")
        for alias_name in ("GIT_SAFE", "GH_SAFE"):
            for cmd in _extract_cmnd_alias(content, alias_name):
                stripped = cmd.strip()
                rest = re.sub(r"^\S*/(?:git|gh)\s+", "", stripped)
                assert rest != "*", (
                    f"generate-sudoers.sh dry-run output: bare wildcard "
                    f"{stripped!r} in {alias_name} re-introduces the "
                    f"pre-#2334 `git *`/`gh *` wildcard "
                    f"(Issue #2635 forbids this)"
                )

        # 2. Raw generator sources (docker-entrypoint.sh and install.sh are
        #    not exercised by the dry-run): catch the unexpanded
        #    ``${GIT_PATH} *``/``${GH_PATH} *`` and bare ``git *``/``gh *``
        #    forms in the extracted alias entries.
        for label, path in self.GENERATOR_FILES:
            for alias_name in ("GIT_SAFE", "GH_SAFE"):
                for cmd in _extract_cmnd_alias(path.read_text(), alias_name):
                    stripped = cmd.strip()
                    assert not self.BARE_WILDCARD_RE.match(stripped), (
                        f"{label}: bare wildcard {stripped!r} in {alias_name} "
                        f"re-introduces the pre-#2334 `git *`/`gh *` wildcard "
                        f"(Issue #2635 forbids this)"
                    )

    @staticmethod
    def _generate_sudoers() -> str:
        """Run the unified generator in dry-run mode and return its stdout."""
        result = subprocess.run(
            ["bash", str(GENERATE_SUDOERS_SH), "--dry-run", "--output", "/dev/null"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator failed: {result.stderr}"
        return result.stdout

    @staticmethod
    def _strip_binary(entries: list[str], binary: str) -> list[str]:
        """Strip the leading absolute binary path from whitelist entries.

        Entries embed the host-resolved path (${GIT_PATH}/${GH_PATH}, e.g.
        /usr/bin/git or /usr/local/bin/gh); stripping it lets us fnmatch the
        command shapes github_ops builds (which start after the binary).
        """
        stripped = []
        pattern = re.compile(rf"^\S*/{binary}\s+")
        for entry in entries:
            stripped.append(pattern.sub("", entry))
        return stripped

    def test_real_git_commands_match_generated_whitelist(self):
        """Representative _run_git sudo-path shapes must match GIT_SAFE.

        Uses fnmatch, which (like sudoers wildcard matching) lets ``*``
        span spaces — mirroring how sudo matches the full argv string.
        """
        content = self._generate_sudoers()
        entries = self._strip_binary(_extract_cmnd_alias(content, "GIT_SAFE"), "git")

        real_shapes = [
            # No trusted-git-dir: -c hooksPath/fsmonitor/safe.directory, then -C
            "-c core.hooksPath=/dev/null -c core.fsmonitor=false "
            "-c safe.directory=/x -C /x fetch origin main",
            # Trusted-git-dir: --git-dir/--work-tree precede the -c options
            "--git-dir=/x/.git/worktrees/w --work-tree=/x/.worktrees/w "
            "-c core.hooksPath=/dev/null -c core.fsmonitor=false "
            "-c safe.directory=/a -c safe.directory=/b status --porcelain",
        ]
        for shape in real_shapes:
            matching = [e for e in entries if fnmatch.fnmatch(shape, e)]
            assert matching, (
                f"_run_git sudo-path command {shape!r} matches no GIT_SAFE "
                f"entry; cross-user orchestrator git calls would be rejected "
                f"by sudoers (Issue #2635). GIT_SAFE entries: {entries!r}"
            )

    def test_real_gh_commands_match_generated_whitelist(self):
        """Representative _run_gh sudo-path shapes must match GH_SAFE."""
        content = self._generate_sudoers()
        entries = self._strip_binary(_extract_cmnd_alias(content, "GH_SAFE"), "gh")

        real_shapes = [
            # sudo path always inserts -R owner/repo before the subcommand
            "-R owner/repo pr checks 123 --json name,state",
            # gh api rejects -R (repo_scoped=False) -> plain `gh api repos/...`
            "api repos/owner/repo/pulls/123",
        ]
        for shape in real_shapes:
            matching = [e for e in entries if fnmatch.fnmatch(shape, e)]
            assert matching, (
                f"_run_gh sudo-path command {shape!r} matches no GH_SAFE "
                f"entry; cross-user orchestrator gh calls would be rejected "
                f"by sudoers (Issue #2635). GH_SAFE entries: {entries!r}"
            )

    def test_github_ops_prefix_construction_locked(self):
        """Drift lock: github_ops must keep the exact prefix construction.

        Deliberately brittle source-grep. If github_ops.py changes how it
        builds the sudo-path prefixes (the ``-c core.hooksPath=/dev/null``
        lead-in, the ``--git-dir=`` trusted-context build, or the ``-R``
        insertion), the prefix-anchored sudoers entries added for #2635
        silently stop matching and every cross-user orchestrator git/gh call
        is rejected again. Fail here with a pointer to the entries.
        """
        text = GITHUB_OPS_PY.read_text()

        # 1. git prefixes (both sudo and non-sudo paths) lead with
        #    core.hooksPath=/dev/null before any subcommand.
        assert '"core.hooksPath=/dev/null"' in text, (
            "github_ops._run_git no longer builds the "
            "'-c core.hooksPath=/dev/null' prefix; the sudoers entry "
            "'${GIT_PATH} -c core.hooksPath=/dev/null *' (scripts/"
            "generate-sudoers.sh, scripts/install-central/package-method/"
            "install.sh, docker-entrypoint.sh) must be updated in lockstep "
            "(Issue #2635)"
        )

        # 2. trusted-git-dir path builds --git-dir=/--work-tree= first.
        assert 'f"--git-dir={self._trusted_git_dir}"' in text, (
            "github_ops._run_git no longer builds the '--git-dir=' trusted "
            "prefix; the sudoers entry '${GIT_PATH} --git-dir=*' (scripts/"
            "generate-sudoers.sh, scripts/install-central/package-method/"
            "install.sh, docker-entrypoint.sh) must be updated in lockstep "
            "(Issue #2635)"
        )

        # 3. gh sudo path inserts -R owner/repo before the subcommand.
        assert 'cmd += ["gh", "-R", owner_repo] + args' in text, (
            "github_ops._run_gh no longer inserts '-R owner/repo' on the "
            "sudo path; the sudoers entry '${GH_PATH} -R *' (scripts/"
            "generate-sudoers.sh, scripts/install-central/package-method/"
            "install.sh, docker-entrypoint.sh) must be updated in lockstep "
            "(Issue #2635)"
        )

        # 4. Ordering (M1): within _run_git's sudo branch, the
        #    '-c core.hooksPath=/dev/null' lead-in must be built BEFORE the
        #    '-C'/positional part of the command. The sudoers prefix entry
        #    '${GIT_PATH} -c core.hooksPath=/dev/null *' only matches because
        #    the sudo-path command starts with those -c options; if '-C' (or
        #    anything else) ever moves ahead of them, sudo silently stops
        #    matching the prefix entry.
        assert text.index('"core.hooksPath=/dev/null"') < text.index('["-C", self.repo_path]'), (
            "github_ops._run_git builds '-C <repo>' (or positional args) "
            "before the '-c core.hooksPath=/dev/null' options; the command "
            "no longer starts with the prefix the sudoers entry "
            "'${GIT_PATH} -c core.hooksPath=/dev/null *' anchors on "
            "(scripts/generate-sudoers.sh, scripts/install-central/"
            "package-method/install.sh, docker-entrypoint.sh) — update the "
            "sudoers entries in lockstep (Issue #2635)"
        )


class TestMkdirShapeCoverage:
    """Issue #2674: cross-user mkdir whitelist must match github_ops shape.

    github_ops.create_verification_worktree_dir emits
    ``sudo -u <account> mkdir -p -- <path>`` and ``mkdir -m 700 -- <path>``
    (bare mkdir, NOT the openace-mkdir wrapper: the wrapper is (root)-runas
    and creates root-owned directories, while the verifier worktree must be
    owned by the identity that runs git). The #2334 tightening removed mkdir
    from the sudoers generators in favor of that wrapper, so after prod
    regenerated sudoers every acceptance verifier checkout died with
    "sudo: a password is required" -> "merged-main checkout failed" (#2674).
    These tests lock the MKDIR_SAFE entries that cover the emitted shape in
    all three generators and fail loudly if github_ops changes its mkdir
    construction without updating them.
    """

    # The cross-user mkdir entries (as they appear in the generator sources).
    # sudo resolves the bare ``mkdir`` argv through the invoking user's PATH,
    # so both /usr/bin/mkdir and /bin/mkdir resolutions must be whitelisted.
    MKDIR_ENTRIES = [
        "/usr/bin/mkdir *",
        "/bin/mkdir *",
    ]

    GENERATOR_FILES = [
        ("scripts/generate-sudoers.sh", GENERATE_SUDOERS_SH),
        ("scripts/install-central/package-method/install.sh", INSTALL_SH),
        ("docker-entrypoint.sh", DOCKER_ENTRYPOINT),
    ]

    @pytest.mark.issue(2674)
    @pytest.mark.parametrize("entry", MKDIR_ENTRIES)
    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_mkdir_entries_present_in_all_generators(self, entry, label, path):
        """All three generators must carry both mkdir whitelist entries."""
        assert entry in _extract_cmnd_alias(path.read_text(), "MKDIR_SAFE"), (
            f"{label} is missing the MKDIR_SAFE entry {entry!r}; "
            f"github_ops.create_verification_worktree_dir emits "
            f"'sudo -u <account> mkdir ...' (bare mkdir, not the (root)-runas "
            f"openace-mkdir wrapper), so cross-user verifier worktree creation "
            f"is rejected by sudoers (Issue #2674). Keep the three generators "
            f"consistent."
        )

    @pytest.mark.issue(2674)
    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_mkdir_safe_has_all_runas_in_all_generators(self, label, path):
        """MKDIR_SAFE user rules must use (ALL) runas for cross-user mkdir."""
        targets = _get_runas_for_alias(path.read_text(), "MKDIR_SAFE")
        assert targets, (
            f"No MKDIR_SAFE user-rule found in {label}; cross-user "
            f"'sudo -u <owner> mkdir' cannot match a (root)-runas rule "
            f"(Issue #2674)"
        )
        assert "ALL" in targets, (
            f"MKDIR_SAFE runas is {targets!r} in {label}; must be (ALL) for "
            f"github_ops cross-user mkdir (Issue #2674)"
        )

    @pytest.mark.issue(2674)
    def test_generated_sudoers_matches_verifier_mkdir_shapes(self):
        """Verifier mkdir shapes must fnmatch the generated MKDIR_SAFE entries."""
        result = subprocess.run(
            ["bash", str(GENERATE_SUDOERS_SH), "--dry-run", "--output", "/dev/null"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator failed: {result.stderr}"
        content = result.stdout

        targets = _get_runas_for_alias(content, "MKDIR_SAFE")
        assert targets and "ALL" in targets, (
            f"Generated sudoers MKDIR_SAFE runas is {targets!r}; must be (ALL) "
            f"for github_ops cross-user mkdir (Issue #2674)"
        )

        entries = [
            re.sub(r"^\S*/mkdir\s+", "", cmd) for cmd in _extract_cmnd_alias(content, "MKDIR_SAFE")
        ]
        real_shapes = [
            # create_verification_worktree_dir: worktrees root
            "-p -- /srv/repos/x/.worktrees",
            # unique verifier child dir
            "-m 700 -- /srv/repos/x/.worktrees/verify-deadbeef",
        ]
        for shape in real_shapes:
            matching = [e for e in entries if fnmatch.fnmatch(shape, e)]
            assert matching, (
                f"create_verification_worktree_dir sudo-path command "
                f"{shape!r} matches no MKDIR_SAFE entry; cross-user verifier "
                f"worktree creation would be rejected by sudoers "
                f"(Issue #2674). MKDIR_SAFE entries: {entries!r}"
            )

    @pytest.mark.issue(2674)
    def test_github_ops_mkdir_construction_locked(self):
        """Drift lock: github_ops must keep the bare cross-user mkdir shape.

        Deliberately brittle source-grep, mirroring the #2635 prefix locks.
        If github_ops.create_verification_worktree_dir stops emitting
        ``sudo -u <account> mkdir ...`` (e.g. switches to the openace-mkdir
        wrapper), the MKDIR_SAFE entries added for #2674 silently stop
        matching — or the wrapper's (root) runas rejects the call. Fail here
        with a pointer to the entries.
        """
        text = GITHUB_OPS_PY.read_text()
        assert 'cmd = ["sudo", "-u", account, "mkdir", *args]' in text, (
            "github_ops.create_verification_worktree_dir no longer emits "
            "'sudo -u <account> mkdir ...'; the MKDIR_SAFE entries "
            "'/usr/bin/mkdir *' and '/bin/mkdir *' under (ALL) runas "
            "(scripts/generate-sudoers.sh, scripts/install-central/"
            "package-method/install.sh, docker-entrypoint.sh) must be "
            "updated in lockstep (Issue #2674)"
        )


class TestNmShimShapeCoverage:
    """#2694: the node_modules shim's cross-user execution shape.

    git_workspace._ensure_frontend_node_modules_shim emits
    ``sudo -u <account> /usr/local/bin/openace-nm-shim <wt_fe> <main_nm>``
    (root-owned wrapper). The historical ``sudo -u <account> bash -c <script>``
    shape is rejected by sudoers by design (#2650: multi-step bash-as-owner is
    a root-RCE surface), so without these entries the shim NEVER succeeds on
    multi-user prod — it fail-softs on every advance and spammed 5.2k duplicate
    ``frontend_node_modules_shim_failed`` milestones in four days.
    """

    NM_SHIM_ENTRIES = [
        "/usr/local/bin/openace-nm-shim *",
    ]

    GENERATOR_FILES = [
        ("scripts/generate-sudoers.sh", GENERATE_SUDOERS_SH),
        ("scripts/install-central/package-method/install.sh", INSTALL_SH),
        ("docker-entrypoint.sh", DOCKER_ENTRYPOINT),
    ]

    @pytest.mark.issue(2694)
    @pytest.mark.parametrize("entry", NM_SHIM_ENTRIES)
    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_nm_shim_entries_present_in_all_generators(self, entry, label, path):
        """All three generators must carry the nm-shim wrapper whitelist."""
        assert entry in _extract_cmnd_alias(path.read_text(), "NM_SHIM_SAFE"), (
            f"{label} is missing the NM_SHIM_SAFE entry {entry!r}; "
            f"git_workspace._ensure_frontend_node_modules_shim emits "
            f"'sudo -u <account> /usr/local/bin/openace-nm-shim ...' on "
            f"multi-user deployments, which sudoers rejects without this "
            f"entry (Issue #2694). Keep the three generators consistent."
        )

    @pytest.mark.issue(2694)
    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_nm_shim_safe_has_all_runas_in_all_generators(self, label, path):
        """NM_SHIM_SAFE user rules must use (ALL) runas (cross-owner shim)."""
        targets = _get_runas_for_alias(path.read_text(), "NM_SHIM_SAFE")
        assert targets, f"No NM_SHIM_SAFE user-rule found in {label} (Issue #2694)"
        assert "ALL" in targets, (
            f"NM_SHIM_SAFE runas is {targets!r} in {label}; must be (ALL) for "
            f"the cross-user node_modules shim (Issue #2694)"
        )

    @pytest.mark.issue(2694)
    def test_generated_sudoers_matches_shim_invocation_shape(self):
        """git_workspace's emitted wrapper invocation must fnmatch the entries."""
        result = subprocess.run(
            ["bash", str(GENERATE_SUDOERS_SH), "--dry-run", "--output", "/dev/null"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Generator failed: {result.stderr}"

        entries = _extract_cmnd_alias(result.stdout, "NM_SHIM_SAFE")
        real_shapes = [
            "/usr/local/bin/openace-nm-shim /srv/repos/x/.worktrees/wid/frontend /srv/repos/x/frontend/node_modules",
        ]
        for shape in real_shapes:
            matching = [e for e in entries if fnmatch.fnmatch(shape, e)]
            assert matching, (
                f"git_workspace shim command {shape!r} matches no NM_SHIM_SAFE "
                f"entry; the node_modules shim would be rejected by sudoers "
                f"(Issue #2694). NM_SHIM_SAFE entries: {entries!r}"
            )

    @pytest.mark.issue(2694)
    def test_git_workspace_wrapper_invocation_locked(self):
        """Drift lock: git_workspace must keep invoking the whitelisted wrapper
        (not a bare ``bash -c``) on cross-user deployments, mirroring the
        #2635/#2674 source-grep locks."""
        text = GIT_WORKSPACE_PY.read_text()
        assert "_NM_SHIM_WRAPPER" in text, (
            "git_workspace no longer references _NM_SHIM_WRAPPER; the "
            "NM_SHIM_SAFE entries in scripts/generate-sudoers.sh, "
            "scripts/install-central/package-method/install.sh and "
            "docker-entrypoint.sh must be updated in lockstep (Issue #2694)"
        )
        assert '"bash", "-c"' in text, "same-user bash -c fallback should remain (dev/CI)"


class TestSudoersDrift:
    """Tests for Docker/Package sudoers drift detection."""

    def test_runas_consistency_openace_utils(self):
        """OPENACE_UTILS runas must be consistent between Docker and Package."""
        docker_text = DOCKER_ENTRYPOINT.read_text()
        package_text = INSTALL_SH.read_text()

        # Extract OPENACE_UTILS runas from both
        docker_targets = _get_runas_for_alias(docker_text, "OPENACE_UTILS")
        package_targets = _get_runas_for_alias(package_text, "OPENACE_UTILS")

        # Both should have same runas (either ALL or root, but consistent)
        if docker_targets and package_targets:
            assert set(docker_targets) == set(package_targets), (
                f"OPENACE_UTILS runas drift: Docker={docker_targets!r}, "
                f"Package={package_targets!r}"
            )

    def test_both_use_git_safe(self):
        """Both Docker and Package should use GIT_SAFE for git commands."""
        docker_text = DOCKER_ENTRYPOINT.read_text()

        # Docker should have GIT_SAFE
        assert "Cmnd_Alias GIT_SAFE" in docker_text, "Docker entrypoint should define GIT_SAFE"

        # Generator should also have GIT_SAFE
        generator_text = GENERATE_SUDOERS_SH.read_text()
        assert "Cmnd_Alias GIT_SAFE" in generator_text, "Generator should define GIT_SAFE"

    def test_both_use_gh_safe(self):
        """Both Docker and Package should use GH_SAFE for gh commands."""
        docker_text = DOCKER_ENTRYPOINT.read_text()

        # Docker should have GH_SAFE
        assert "Cmnd_Alias GH_SAFE" in docker_text, "Docker entrypoint should define GH_SAFE"

        # Generator should also have GH_SAFE
        generator_text = GENERATE_SUDOERS_SH.read_text()
        assert "Cmnd_Alias GH_SAFE" in generator_text, "Generator should define GH_SAFE"

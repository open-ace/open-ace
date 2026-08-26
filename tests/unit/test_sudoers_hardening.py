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
DOCKER_METHOD_INSTALL_SH = (
    PROJECT_ROOT / "scripts" / "install-central" / "docker-method" / "install.sh"
)
GENERATE_SUDOERS_SH = PROJECT_ROOT / "scripts" / "generate-sudoers.sh"
GITHUB_OPS_PY = PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "github_ops.py"
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

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
    """Issue #2650: sudoers must delegate git/gh grammar to wrappers."""

    GIT_WRAPPER_ENTRY = "/usr/local/bin/openace-git *"
    GH_WRAPPER_ENTRY = "/usr/local/bin/openace-gh *"

    GENERATOR_FILES = [
        ("scripts/generate-sudoers.sh", GENERATE_SUDOERS_SH),
        ("scripts/install-central/package-method/install.sh", INSTALL_SH),
        ("scripts/install-central/docker-method/install.sh", DOCKER_METHOD_INSTALL_SH),
        ("docker-entrypoint.sh", DOCKER_ENTRYPOINT),
    ]

    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_git_safe_uses_only_wrapper_entries_in_all_generators(self, label, path):
        """GIT_SAFE must contain only the validating wrapper entry."""
        assert _extract_cmnd_alias(path.read_text(), "GIT_SAFE") == [self.GIT_WRAPPER_ENTRY], (
            f"{label}: GIT_SAFE must authorize only {self.GIT_WRAPPER_ENTRY!r} "
            "so prefix-anchored git wildcards cannot bypass validation (Issue #2650)"
        )

    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_gh_safe_uses_only_wrapper_entries_in_all_generators(self, label, path):
        """GH_SAFE must contain only the validating wrapper entry."""
        assert _extract_cmnd_alias(path.read_text(), "GH_SAFE") == [self.GH_WRAPPER_ENTRY], (
            f"{label}: GH_SAFE must authorize only {self.GH_WRAPPER_ENTRY!r} "
            "so gh -R/api wildcards cannot bypass validation (Issue #2650)"
        )

    def test_docker_gh_safe_has_no_line_continuation_tail(self):
        """PR #2665 regression: no dangling backslash may retain old gh entries."""
        gh_safe = _extract_cmnd_alias(DOCKER_ENTRYPOINT.read_text(), "GH_SAFE")
        assert "${GH_PATH} -R *" not in gh_safe
        assert "${GH_PATH} api *" not in gh_safe

    def test_github_ops_sudo_paths_call_wrappers(self):
        """Cross-user github_ops paths must invoke wrapper binaries."""
        text = GITHUB_OPS_PY.read_text()
        assert '"/usr/local/bin/openace-git"' in text
        assert '"/usr/local/bin/openace-gh"' in text

    def test_dockerfile_installs_git_gh_wrappers_and_config(self):
        """Docker builds must install wrappers and config files."""
        text = DOCKERFILE.read_text()
        for needle in ("openace-git.py", "openace-gh.py", "config/openace", "/etc/openace"):
            assert needle in text

    def test_package_installer_installs_git_gh_wrappers_and_config(self):
        """Package installs must install wrappers and config files."""
        text = INSTALL_SH.read_text()
        for needle in ("openace-git.py", "openace-gh.py", "config/openace", "/etc/openace"):
            assert needle in text
        assert "Cmnd_Alias OPENACE_UTILS" in text

    def test_docker_method_installer_installs_git_gh_wrappers_and_config(self):
        """Docker-method installs must install wrappers and config files."""
        text = DOCKER_METHOD_INSTALL_SH.read_text()
        for needle in ("openace-git.py", "openace-gh.py", "config/openace", "/etc/openace"):
            assert needle in text

    @pytest.mark.parametrize(
        "label,path",
        [
            ("scripts/install-central/package-method/install.sh", INSTALL_SH),
            ("scripts/install-central/docker-method/install.sh", DOCKER_METHOD_INSTALL_SH),
        ],
    )
    def test_git_gh_wrapper_install_failures_block_sudoers_rewrite(self, label, path):
        """Installers must not write wrapper-only sudoers when wrappers are absent."""
        text = path.read_text()
        call_lines = [
            line.strip()
            for line in text.splitlines()
            if "install_git_gh_wrappers" in line
            and not line.strip().startswith("install_git_gh_wrappers()")
            and not line.strip().startswith("#")
        ]
        assert call_lines, f"{label}: expected at least one install_git_gh_wrappers call"
        for line in call_lines:
            assert line.startswith("if ! install_git_gh_wrappers"), (
                f"{label}: git/gh wrapper install failure must block configure_sudoers, "
                f"not continue from call {line!r}"
            )

    def test_package_incremental_update_probes_git_gh_wrappers_before_early_return(self):
        """Package upgrades must not keep old direct git/gh sudoers aliases."""
        text = INSTALL_SH.read_text()
        incremental_start = text.index("# ===== Incremental update logic =====")
        early_return = text.index('if [ "$need_update" = false ]', incremental_start)
        probe_block = text[incremental_start:early_return]

        for needle in (
            "Cmnd_Alias[[:space:]]+GIT_SAFE",
            "/usr/local/bin/openace-git[[:space:]]+\\*",
            "Cmnd_Alias[[:space:]]+GH_SAFE",
            "/usr/local/bin/openace-gh[[:space:]]+\\*",
            "NOPASSWD: GIT_SAFE",
            "NOPASSWD: GH_SAFE",
        ):
            assert needle in probe_block, (
                f"Package sudoers incremental probes must check {needle!r} before "
                "the 'already correct' return, otherwise upgrades can preserve "
                "the old direct git/gh wildcard aliases from Issue #2650."
            )

    @staticmethod
    def _git_gh_wrapper_upgrade_probe_passes(sudoers_text: str, run_user: str = "openace") -> bool:
        """Mirror the package upgrade gate for git/gh wrapper-only sudoers."""
        checks = [
            r"^Cmnd_Alias[ \t]+GIT_SAFE[ \t]*=[ \t]*/usr/local/bin/openace-git[ \t]+\*[ \t]*$",
            r"^Cmnd_Alias[ \t]+GH_SAFE[ \t]*=[ \t]*/usr/local/bin/openace-gh[ \t]+\*[ \t]*$",
            rf"^{re.escape(run_user)} ALL=\(ALL\) NOPASSWD: GIT_SAFE([ \t]|$)",
            rf"^{re.escape(run_user)} ALL=\(ALL\) NOPASSWD: GH_SAFE([ \t]|$)",
        ]
        return all(re.search(pattern, sudoers_text, re.MULTILINE) for pattern in checks)

    def test_package_incremental_upgrade_rejects_old_git_gh_wildcard_sudoers(self):
        """An otherwise-current upgrade file with old aliases must be rewritten."""
        old_sudoers = """
Defaults secure_path = /usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
Cmnd_Alias GIT_SAFE = /usr/bin/git -c core.hooksPath=/dev/null *, /usr/bin/git --git-dir=*
Cmnd_Alias GH_SAFE = /usr/bin/gh -R *, /usr/bin/gh api *
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *
Cmnd_Alias MKDIR_SAFE = /usr/bin/mkdir *, /bin/mkdir *
openace ALL=(ALL) NOPASSWD: /usr/local/bin/openace-webui-launch * "/usr/bin/qwen-code-webui" *
openace ALL=(ALL) NOPASSWD: OPENACE_UTILS
openace ALL=(ALL) NOPASSWD: GIT_SAFE
openace ALL=(ALL) NOPASSWD: GH_SAFE
openace ALL=(ALL) NOPASSWD: MKDIR_SAFE
"""
        new_sudoers = old_sudoers.replace(
            "Cmnd_Alias GIT_SAFE = /usr/bin/git -c core.hooksPath=/dev/null *, /usr/bin/git --git-dir=*",
            "Cmnd_Alias GIT_SAFE = /usr/local/bin/openace-git *",
        ).replace(
            "Cmnd_Alias GH_SAFE = /usr/bin/gh -R *, /usr/bin/gh api *",
            "Cmnd_Alias GH_SAFE = /usr/local/bin/openace-gh *",
        )

        assert not self._git_gh_wrapper_upgrade_probe_passes(old_sudoers)
        assert self._git_gh_wrapper_upgrade_probe_passes(new_sudoers)

    def test_docker_method_incremental_update_probes_git_gh_wrappers_before_early_return(self):
        """Docker-method upgrades must not keep old direct git/gh sudoers aliases."""
        text = DOCKER_METHOD_INSTALL_SH.read_text()
        incremental_start = text.index("# Check if sudoers file already exists")
        early_return = text.index('if [ "$needs_update" = false ]', incremental_start)
        probe_block = text[incremental_start:early_return]

        for needle in (
            "Cmnd_Alias[[:space:]]+GIT_SAFE",
            "/usr/local/bin/openace-git[[:space:]]+\\*",
            "Cmnd_Alias[[:space:]]+GH_SAFE",
            "/usr/local/bin/openace-gh[[:space:]]+\\*",
            "NOPASSWD: GIT_SAFE",
            "NOPASSWD: GH_SAFE",
        ):
            assert needle in probe_block, (
                f"Docker-method sudoers incremental probes must check {needle!r} "
                "before the 'already correct' return, otherwise upgrades can "
                "preserve old direct git/gh wildcard aliases from Issue #2650."
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

    def test_no_direct_git_or_gh_wildcard_user_rules_reintroduced(self):
        """Sudoers user rules must not bypass wrappers with direct git/gh wildcards."""
        direct_rule_re = re.compile(
            r"NOPASSWD:.*(?:/usr/bin/git|/usr/local/bin/git|/usr/bin/gh|/usr/local/bin/gh)" r"\s+\*"
        )
        for label, path in self.GENERATOR_FILES:
            for line in path.read_text().splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                assert not direct_rule_re.search(stripped), (
                    f"{label}: direct git/gh wildcard sudoers user rule "
                    f"{stripped!r} bypasses openace-git/openace-gh validation "
                    "(Issue #2650)"
                )

    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_all_sudoers_generators_set_secure_path_for_wrappers(self, label, path):
        """Wrapper-only sudoers files must not preserve caller PATH for wrapper execution."""
        text = path.read_text()
        assert "Defaults secure_path = /usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin" in text, (
            f"{label}: sudoers must set secure_path so openace-git/openace-gh "
            "cannot be entered through a caller-controlled PATH"
        )

    @pytest.mark.parametrize(
        "label,path",
        GENERATOR_FILES,
        ids=[label for label, _ in GENERATOR_FILES],
    )
    def test_all_sudoers_generators_drop_caller_path_from_env_keep(self, label, path):
        """PATH must not be preserved via env_keep (Issue #2650).

        secure_path already governs command lookup, so keeping PATH is dead
        config; worse, if secure_path is ever removed the caller-controlled
        PATH would flow into the target-user process and git would resolve
        ssh/remote helpers through it — re-opening the PATH attack surface the
        wrapper closes.
        """
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "env_keep" not in stripped:
                continue
            # Token match so substrings like GH_PATH would not false-trip.
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped)
            assert "PATH" not in tokens, (
                f"{label}: env_keep must not preserve caller PATH "
                f"(secure_path governs lookup): {stripped!r}"
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

    def test_generated_git_alias_is_wrapper_only(self):
        """Dry-run sudoers output must authorize only the git wrapper."""
        content = self._generate_sudoers()
        assert _extract_cmnd_alias(content, "GIT_SAFE") == [self.GIT_WRAPPER_ENTRY]

    def test_generated_gh_alias_is_wrapper_only(self):
        """Dry-run sudoers output must authorize only the gh wrapper."""
        content = self._generate_sudoers()
        assert _extract_cmnd_alias(content, "GH_SAFE") == [self.GH_WRAPPER_ENTRY]

    def test_github_ops_wrapper_construction_locked(self):
        """Drift lock: sudo-path github_ops must call wrapper binaries."""
        text = GITHUB_OPS_PY.read_text()
        assert f'OPENACE_GIT_WRAPPER = "{self.GIT_WRAPPER_ENTRY.removesuffix(" *")}"' in text
        assert f'OPENACE_GH_WRAPPER = "{self.GH_WRAPPER_ENTRY.removesuffix(" *")}"' in text
        assert 'cmd += [OPENACE_GH_WRAPPER, "-R", owner_repo] + args' in text
        assert "git_bin = (" in text
        assert (
            'os.environ.get("OPENACE_REAL_GIT", "git") if not needs_sudo else OPENACE_GIT_WRAPPER'
            in text
        )
        assert 'f"--git-dir={self._trusted_git_dir}"' in text, (
            "github_ops._run_git must keep passing trusted git-dir/work-tree "
            "arguments so openace-git can validate them before executing git"
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
        ("scripts/install-central/docker-method/install.sh", DOCKER_METHOD_INSTALL_SH),
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

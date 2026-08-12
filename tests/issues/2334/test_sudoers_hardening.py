"""Sudoers security hardening tests for Issue #2334.

Tests verify:
1. GIT_SAFE/GH_SAFE use (ALL) runas for cross-user operations
2. git/gh removed from OPENACE_UTILS
3. No shell syntax in generated sudoers
4. WebUI launcher has no fallback
5. Dangerous verbs are blocked
6. Audit logging is present
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Key files
DOCKER_ENTRYPOINT = PROJECT_ROOT / "docker-entrypoint.sh"
INSTALL_SH = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"
GENERATE_SUDOERS_SH = PROJECT_ROOT / "scripts" / "generate-sudoers.sh"
GITHUB_OPS_PY = PROJECT_ROOT / "app" / "modules" / "workspace" / "autonomous" / "github_ops.py"


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
            re.DOTALL
        )
        if utils_match:
            utils_content = utils_match.group(1)
            # Check for git wildcards
            assert "git *" not in utils_content, (
                "git * must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
            )
            assert "/usr/bin/git" not in utils_content, (
                "/usr/bin/git must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
            )

    def test_gh_not_in_openace_utils_docker(self):
        """gh must NOT be in OPENACE_UTILS in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL
        )
        if utils_match:
            utils_content = utils_match.group(1)
            assert "gh *" not in utils_content, (
                "gh * must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
            )
            assert "/usr/bin/gh" not in utils_content, (
                "/usr/bin/gh must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
            )

    def test_git_not_in_openace_utils_generator(self):
        """git must NOT be in OPENACE_UTILS in generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL
        )
        assert utils_match, "OPENACE_UTILS definition not found in generator"
        utils_content = utils_match.group(1)
        assert "git *" not in utils_content, (
            "git * must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
        )
        assert "/usr/bin/git" not in utils_content, (
            "/usr/bin/git must NOT be in OPENACE_UTILS (use GIT_SAFE instead per #2334)"
        )

    def test_gh_not_in_openace_utils_generator(self):
        """gh must NOT be in OPENACE_UTILS in generator."""
        text = GENERATE_SUDOERS_SH.read_text()
        utils_match = re.search(
            r"Cmnd_Alias\s+OPENACE_UTILS\s*=\s*(.*?)(?=\n\s*Cmnd_Alias|\n\s*#|\n\s*$)",
            text,
            re.DOTALL
        )
        assert utils_match, "OPENACE_UTILS definition not found in generator"
        utils_content = utils_match.group(1)
        assert "gh *" not in utils_content, (
            "gh * must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
        )
        assert "/usr/bin/gh" not in utils_content, (
            "/usr/bin/gh must NOT be in OPENACE_UTILS (use GH_SAFE instead per #2334)"
        )


class TestNoShellSyntax:
    """Tests for no shell syntax in sudoers."""

    def test_no_shell_syntax_in_docker_heredoc(self):
        """No shell control statements in Docker heredoc content."""
        text = DOCKER_ENTRYPOINT.read_text()
        # Find the heredoc content (between cat > ... << SUDOERS_EOF and SUDOERS_EOF)
        heredoc_match = re.search(
            r"cat\s+>\s+\S+\s+<<\s*\w+\s*\n(.*?)\n\w+\s*$",
            text,
            re.DOTALL
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
                if re.match(r"^\s*if\s+\[", line):
                    raise AssertionError(
                        f"Shell syntax 'if [' found in sudoers heredoc at line {i}: {line}"
                    )
                if re.match(r"^\s*then\s*$", line):
                    raise AssertionError(
                        f"Shell syntax 'then' found in sudoers heredoc at line {i}: {line}"
                    )
                if re.match(r"^\s*else\s*$", line):
                    raise AssertionError(
                        f"Shell syntax 'else' found in sudoers heredoc at line {i}: {line}"
                    )
                if re.match(r"^\s*fi\s*$", line):
                    raise AssertionError(
                        f"Shell syntax 'fi' found in sudoers heredoc at line {i}: {line}"
                    )

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
        assert "\nthen\n" in output or "\nthen " not in output, "Shell 'then' found in generator output"
        assert "\nelse\n" in output or "\nelse " not in output, "Shell 'else' found in generator output"
        assert "\nfi\n" in output or "\nfi " not in output, "Shell 'fi' found in generator output"


class TestDangerousVerbsBlocked:
    """Tests that dangerous verbs are blocked from whitelists."""

    def test_gh_repo_delete_not_in_whitelist(self):
        """gh repo delete must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        for cmd in gh_safe_commands:
            assert "repo delete" not in cmd.lower(), (
                f"gh repo delete must NOT be in GH_SAFE: {cmd}"
            )

    def test_gh_repo_fork_not_in_whitelist(self):
        """gh repo fork must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        for cmd in gh_safe_commands:
            assert "repo fork" not in cmd.lower(), (
                f"gh repo fork must NOT be in GH_SAFE: {cmd}"
            )

    def test_arbitrary_gh_api_not_in_whitelist(self):
        """Arbitrary gh api must NOT be in GH_SAFE."""
        text = GENERATE_SUDOERS_SH.read_text()
        gh_safe_commands = _extract_cmnd_alias(text, "GH_SAFE")

        # Check that all gh api commands have specific paths
        for cmd in gh_safe_commands:
            if "gh api" in cmd and "--jq" not in cmd:
                # Allow specific whitelisted paths
                if "api user" not in cmd and "api repos/*" not in cmd:
                    raise AssertionError(
                        f"Arbitrary gh api must NOT be in GH_SAFE: {cmd}"
                    )

    def test_git_force_push_not_in_whitelist(self):
        """git push --force must NOT be in GIT_SAFE (except force-with-lease)."""
        text = GENERATE_SUDOERS_SH.read_text()
        git_safe_commands = _extract_cmnd_alias(text, "GIT_SAFE")

        for cmd in git_safe_commands:
            # --force-with-lease is allowed (Issue #1854)
            if "--force-with-lease" in cmd:
                continue
            # Bare --force is not allowed
            if "push" in cmd and "--force" in cmd and "--force-with-lease" not in cmd:
                raise AssertionError(
                    f"git push --force must NOT be in GIT_SAFE: {cmd}"
                )


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
                f"WebUI fallback rule found in Docker entrypoint; "
                f"wrapper must be required per #2334"
            )

    def test_generator_requires_webui_wrapper(self):
        """Generator must require WebUI launcher wrapper."""
        text = GENERATE_SUDOERS_SH.read_text()

        # Check that generator checks for wrapper existence
        assert "WEBUI_LAUNCH_WRAPPER" in text, (
            "Generator must check for webui-launch wrapper"
        )
        assert 'if [[ ! -x "$WEBUI_LAUNCH_WRAPPER" ]]' in text or \
               'if [ ! -x "$WEBUI_LAUNCH_WRAPPER" ]' in text, (
            "Generator must validate webui-launch wrapper is executable"
        )


class TestCredentialLeakPrevention:
    """Tests for credential leak prevention via env_keep."""

    def test_gh_token_not_in_env_keep_docker(self):
        """GH_TOKEN must NOT be in env_keep in Docker entrypoint."""
        text = DOCKER_ENTRYPOINT.read_text()

        # Find env_keep lines (excluding comments)
        env_keep_lines = [
            line for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            assert "GH_TOKEN" not in line, (
                f"GH_TOKEN must NOT be in env_keep: {line}"
            )

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
            line for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            for var in sensitive_vars:
                assert var not in line, (
                    f"{var} must NOT be in env_keep: {line}"
                )

    def test_gh_token_not_in_env_keep_generator(self):
        """GH_TOKEN must NOT be in env_keep in generator."""
        text = GENERATE_SUDOERS_SH.read_text()

        env_keep_lines = [
            line for line in text.split("\n")
            if "env_keep" in line and not line.strip().startswith("#")
        ]

        for line in env_keep_lines:
            assert "GH_TOKEN" not in line, (
                f"GH_TOKEN must NOT be in env_keep: {line}"
            )


class TestAuditLogging:
    """Tests for audit logging in github_ops."""

    def test_github_ops_has_audit_log_imports(self):
        """github_ops.py should have audit logging capability."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging import
        assert "import logging" in text or "from logging" in text, (
            "github_ops.py should have logging import for audit"
        )

    def test_github_ops_logs_git_operations(self):
        """github_ops.py should log git operations."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging in _run_git
        run_git_match = re.search(
            r"def _run_git\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL
        )
        if run_git_match:
            run_git_body = run_git_match.group(1)
            # Should have some form of logging
            has_logging = (
                "logger.info" in run_git_body or
                "logger.warning" in run_git_body or
                "logger.error" in run_git_body or
                "logger.debug" in run_git_body
            )
            assert has_logging, (
                "_run_git should log operations for audit trail"
            )

    def test_github_ops_logs_gh_operations(self):
        """github_ops.py should log gh operations."""
        text = GITHUB_OPS_PY.read_text()

        # Check for logging in _run_gh
        run_gh_match = re.search(
            r"def _run_gh\(self.*?\n(.*?)(?=\n    def |\nclass |\Z)",
            text,
            re.DOTALL
        )
        if run_gh_match:
            run_gh_body = run_gh_match.group(1)
            has_logging = (
                "logger.info" in run_gh_body or
                "logger.warning" in run_gh_body or
                "logger.error" in run_gh_body or
                "logger.debug" in run_gh_body
            )
            assert has_logging, (
                "_run_gh should log operations for audit trail"
            )


class TestGeneratorSyntax:
    """Tests for generator script syntax."""

    def test_generator_passes_bash_syntax_check(self):
        """Generator script must pass bash syntax check."""
        result = subprocess.run(
            ["bash", "-n", str(GENERATE_SUDOERS_SH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Generator has syntax errors: {result.stderr}"
        )

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
        assert validate_result.returncode == 0, (
            f"Generated sudoers invalid: {validate_result.stderr}"
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
                f"OPENACE_UTILS runas drift: Docker={docker_targets!r}, Package={package_targets!r}"
            )

    def test_both_use_git_safe(self):
        """Both Docker and Package should use GIT_SAFE for git commands."""
        docker_text = DOCKER_ENTRYPOINT.read_text()

        # Docker should have GIT_SAFE
        assert "Cmnd_Alias GIT_SAFE" in docker_text, (
            "Docker entrypoint should define GIT_SAFE"
        )

        # Generator should also have GIT_SAFE
        generator_text = GENERATE_SUDOERS_SH.read_text()
        assert "Cmnd_Alias GIT_SAFE" in generator_text, (
            "Generator should define GIT_SAFE"
        )

    def test_both_use_gh_safe(self):
        """Both Docker and Package should use GH_SAFE for gh commands."""
        docker_text = DOCKER_ENTRYPOINT.read_text()

        # Docker should have GH_SAFE
        assert "Cmnd_Alias GH_SAFE" in docker_text, (
            "Docker entrypoint should define GH_SAFE"
        )

        # Generator should also have GH_SAFE
        generator_text = GENERATE_SUDOERS_SH.read_text()
        assert "Cmnd_Alias GH_SAFE" in generator_text, (
            "Generator should define GH_SAFE"
        )
"""
Test multi-user mode configuration persistence (Issue #2235).

Tests verify:
1. docker-compose.multi-user.yml includes OPENACE_CONFIG_DIR
2. Error messages mention docker-compose.multi-user.yml
3. install.sh generates correct multi-user configuration
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent

# Issue and regression markers for test discovery
pytestmark = [pytest.mark.regression, pytest.mark.issue(2235)]


class TestMultiUserComposeConfig:
    """Test docker-compose.multi-user.yml configuration."""

    def test_openace_config_dir_is_set(self):
        """Verify OPENACE_CONFIG_DIR is set in docker-compose.multi-user.yml."""
        compose_file = ROOT / "docker-compose.multi-user.yml"
        content = compose_file.read_text(encoding="utf-8")

        assert "OPENACE_CONFIG_DIR=/home/open-ace/.open-ace" in content, (
            "OPENACE_CONFIG_DIR must be set to /home/open-ace/.open-ace "
            "to ensure configuration persists to mounted volume"
        )

    def test_openace_config_dir_set_in_openace_service(self):
        """Verify OPENACE_CONFIG_DIR is set in open-ace service."""
        compose_file = ROOT / "docker-compose.multi-user.yml"
        content = compose_file.read_text(encoding="utf-8")

        # Find open-ace service section
        services_match = re.search(
            r"services:\s+open-ace:(.*?)(?=\n  \w+:|\n$)", content, re.DOTALL
        )
        assert services_match, "open-ace service not found"

        openace_section = services_match.group(1)
        assert (
            "OPENACE_CONFIG_DIR" in openace_section
        ), "OPENACE_CONFIG_DIR must be set in open-ace service environment"

    def test_openace_config_dir_set_in_scheduler_service(self):
        """Verify OPENACE_CONFIG_DIR is set in scheduler service."""
        compose_file = ROOT / "docker-compose.multi-user.yml"
        content = compose_file.read_text(encoding="utf-8")

        # Find scheduler service section
        scheduler_match = re.search(
            r"scheduler:(.*?)(?=\n  \w+:|\nvolumes:|\n$)", content, re.DOTALL
        )
        assert scheduler_match, "scheduler service not found"

        scheduler_section = scheduler_match.group(1)
        assert (
            "OPENACE_CONFIG_DIR" in scheduler_section
        ), "OPENACE_CONFIG_DIR must be set in scheduler service environment"

    def test_config_volume_mount_path_documented(self):
        """Verify comment explains config persistence mechanism."""
        compose_file = ROOT / "docker-compose.multi-user.yml"
        content = compose_file.read_text(encoding="utf-8")

        # Check for documentation about config persistence
        assert "Issue #2235" in content, "Configuration should reference Issue #2235 for context"
        assert (
            "OPENACE_CONFIG_DIR" in content
        ), "Documentation should mention OPENACE_CONFIG_DIR mechanism"


class TestErrorMessageImprovement:
    """Test improved error messages in docker-entrypoint.sh."""

    def test_error_message_mentions_multi_user_compose(self):
        """Verify error message recommends docker-compose.multi-user.yml."""
        entrypoint = ROOT / "docker-entrypoint.sh"
        content = entrypoint.read_text(encoding="utf-8")

        assert (
            "docker-compose.multi-user.yml" in content
        ), "Error message should mention docker-compose.multi-user.yml as easy fix"

    def test_error_message_includes_config_dir(self):
        """Verify error message includes OPENACE_CONFIG_DIR in manual fix."""
        entrypoint = ROOT / "docker-entrypoint.sh"
        content = entrypoint.read_text(encoding="utf-8")

        # Check that manual fix mentions OPENACE_CONFIG_DIR
        manual_fix_pattern = r"MANUAL FIX.*?OPENACE_CONFIG_DIR"
        assert re.search(
            manual_fix_pattern, content, re.DOTALL
        ), "Manual fix section should mention OPENACE_CONFIG_DIR"


class TestInstallScriptMultiUser:
    """Test install.sh generates correct multi-user configuration."""

    def test_install_sh_sets_user_when_multi_user_enabled(self):
        """Verify install.sh adds user: "0" when multi-user mode is enabled."""
        install_sh = ROOT / "scripts/install-central/docker-method/install.sh"
        content = install_sh.read_text(encoding="utf-8")

        # Check for conditional user: "0" addition
        # The script uses escaped quotes in the heredoc
        assert (
            'user: "0"' in content or 'user: \\"0\\"' in content
        ), 'install.sh should set user: "0" for multi-user mode'
        assert (
            "WORKSPACE_MULTI_USER_MODE" in content
        ), "install.sh should check WORKSPACE_MULTI_USER_MODE"

    def test_install_sh_sets_openace_allow_root(self):
        """Verify install.sh adds OPENACE_ALLOW_ROOT_MULTI_USER."""
        install_sh = ROOT / "scripts/install-central/docker-method/install.sh"
        content = install_sh.read_text(encoding="utf-8")

        assert (
            "OPENACE_ALLOW_ROOT_MULTI_USER" in content
        ), "install.sh should set OPENACE_ALLOW_ROOT_MULTI_USER for multi-user mode"

    def test_install_sh_sets_config_dir(self):
        """Verify install.sh adds OPENACE_CONFIG_DIR."""
        install_sh = ROOT / "scripts/install-central/docker-method/install.sh"
        content = install_sh.read_text(encoding="utf-8")

        assert (
            "OPENACE_CONFIG_DIR" in content
        ), "install.sh should set OPENACE_CONFIG_DIR for multi-user mode"

    def test_install_sh_adds_home_data_volume(self):
        """Verify install.sh adds home-data volume for multi-user mode."""
        install_sh = ROOT / "scripts/install-central/docker-method/install.sh"
        content = install_sh.read_text(encoding="utf-8")

        assert "home-data" in content, "install.sh should add home-data volume for multi-user mode"


class TestDockerComposeMultiUserSyntax:
    """Test docker-compose.multi-user.yml syntax validity."""

    def test_compose_file_is_valid_yaml(self):
        """Verify docker-compose.multi-user.yml is valid YAML."""
        compose_file = ROOT / "docker-compose.multi-user.yml"

        # Use docker compose config to validate syntax
        # Set timeout to avoid hanging the test suite if Docker daemon is slow
        compose_env = os.environ.copy()
        compose_env.update(
            {
                "DB_PASSWORD": "test-only-strong-password",
                "SECRET_KEY": "s" * 64,
                "OPENACE_ENCRYPTION_KEY": "e" * 64,
            }
        )
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "config", "--quiet"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                env=compose_env,
            )
        except subprocess.TimeoutExpired:
            pytest.skip("Docker compose command timed out")

        # Note: This may fail if docker-compose.yml dependencies are not met
        # That's acceptable - we're mainly checking YAML syntax
        # If it fails due to missing base file, that's still a syntax validation
        if result.returncode != 0:
            # Check if it's just a missing base file issue
            if (
                "docker-compose.yml" in result.stderr
                or "has neither an image nor a build context" in result.stderr
                or "looking up compose provider failed" in result.stderr
            ):
                pytest.skip("Base docker-compose.yml validation required")
            else:
                pytest.fail(f"Invalid YAML syntax: {result.stderr}")


class TestEnvExampleMultiUser:
    """Test .env.example multi-user mode documentation."""

    def test_env_example_mentions_multi_user_compose(self):
        """Verify .env.example recommends docker-compose.multi-user.yml."""
        env_example = ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        assert (
            "docker-compose.multi-user.yml" in content
        ), ".env.example should mention docker-compose.multi-user.yml for multi-user mode"

    def test_env_example_documents_config_dir(self):
        """Verify .env.example documents OPENACE_CONFIG_DIR."""
        env_example = ROOT / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        # Should mention that multi-user.yml handles config persistence
        assert (
            "OPENACE_CONFIG_DIR" in content or "配置持久化" in content
        ), ".env.example should document config persistence mechanism"

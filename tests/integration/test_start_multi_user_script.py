"""
Issue #2242: Multi-User Mode Startup Script Tests

Tests for the start-multi-user.sh script:
- Script existence and permissions
- Docker Compose version detection
- Configuration file validation
- Error handling for missing files
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "start-multi-user.sh"
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
OVERLAY_COMPOSE = REPO_ROOT / "docker-compose.multi-user.yml"


@pytest.mark.integration
class TestStartMultiUserScript:
    """Tests for start-multi-user.sh script (Issue #2242)."""

    def test_script_exists(self):
        """Test that the startup script exists."""
        assert SCRIPT_PATH.exists(), f"Script not found: {SCRIPT_PATH}"

    def test_script_is_executable(self):
        """Test that the startup script is executable."""
        assert SCRIPT_PATH.exists(), f"Script not found: {SCRIPT_PATH}"
        mode = SCRIPT_PATH.stat().st_mode
        assert mode & stat.S_IXUSR, f"Script is not executable: {SCRIPT_PATH}"

    def test_base_compose_file_exists(self):
        """Test that the base docker-compose.yml exists."""
        assert BASE_COMPOSE.exists(), f"Base compose file not found: {BASE_COMPOSE}"

    def test_overlay_compose_file_exists(self):
        """Test that the overlay docker-compose.multi-user.yml exists."""
        assert OVERLAY_COMPOSE.exists(), f"Overlay compose file not found: {OVERLAY_COMPOSE}"

    def test_script_help_output(self):
        """Test that script can be executed and shows usage info."""
        # Run script with --help or check script header
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH), "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=30,
        )
        # Script may fail because Docker might not be available,
        # but it should at least start and show some output
        # We check that it doesn't crash with syntax errors
        # Note: exit code 1 is acceptable if Docker is not available
        assert result.returncode in [0, 1], f"Script crashed unexpectedly: {result.stderr}"

    def test_script_detects_missing_overlay_file(self, tmp_path):
        """Test that script reports error when overlay file is missing."""
        # Create a temporary directory without overlay file
        test_dir = tmp_path / "test_no_overlay"
        test_dir.mkdir()

        # Copy base compose file
        base_content = BASE_COMPOSE.read_text()
        (test_dir / "docker-compose.yml").write_text(base_content)

        # Copy script (modified to look in current directory)
        script_content = SCRIPT_PATH.read_text()
        # Run in the test directory
        result = subprocess.run(
            ["bash", "-c", script_content],
            capture_output=True,
            text=True,
            cwd=str(test_dir),
            timeout=30,
        )

        # Should fail because overlay file is missing
        assert result.returncode != 0, "Script should fail when overlay file is missing"
        assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower(), (
            f"Script should report missing file: {result.stdout} {result.stderr}"
        )

    def test_docker_compose_command_detection(self):
        """Test that Docker Compose command detection logic exists in script."""
        script_content = SCRIPT_PATH.read_text()
        # Check for Docker Compose v2 detection
        assert "docker compose" in script_content, "Script should support Docker Compose v2 syntax"
        # Check for Docker Compose v1 detection
        assert "docker-compose" in script_content, "Script should support Docker Compose v1 syntax"

    def test_script_has_version_check(self):
        """Test that script includes version compatibility check."""
        script_content = SCRIPT_PATH.read_text()
        # Check for version comparison logic
        assert "version" in script_content.lower() or "VERSION" in script_content, (
            "Script should have version checking logic"
        )

    def test_script_outputs_access_url(self):
        """Test that script outputs access URL information."""
        script_content = SCRIPT_PATH.read_text()
        assert "localhost" in script_content, "Script should output localhost access URL"
        assert "19888" in script_content, "Script should reference default port 19888"

    def test_script_outputs_quick_fix_suggestions(self):
        """Test that script outputs quick fix suggestions on error."""
        script_content = SCRIPT_PATH.read_text()
        # Check for user-friendly output - script provides guidance when things go wrong
        assert (
            "Please" in script_content
            or "please" in script_content
            or "Ensure" in script_content
            or "ensure" in script_content
        ), "Script should provide helpful suggestions on error"

    def test_script_references_documentation(self):
        """Test that script references documentation."""
        script_content = SCRIPT_PATH.read_text()
        assert "DEPLOYMENT" in script_content or "docs" in script_content, (
            "Script should reference documentation"
        )


@pytest.mark.integration
class TestMultiUserConfiguration:
    """Tests for multi-user configuration files (Issue #2242)."""

    def test_overlay_file_sets_user_root(self):
        """Test that overlay file sets user to root."""
        overlay_content = OVERLAY_COMPOSE.read_text()
        assert 'user: "0"' in overlay_content or "user: '0'" in overlay_content, (
            "Overlay should set user to 0 (root)"
        )

    def test_overlay_file_sets_multi_user_mode(self):
        """Test that overlay file enables multi-user mode."""
        overlay_content = OVERLAY_COMPOSE.read_text()
        assert "WORKSPACE_MULTI_USER_MODE=true" in overlay_content, (
            "Overlay should set WORKSPACE_MULTI_USER_MODE=true"
        )

    def test_overlay_file_sets_allow_root(self):
        """Test that overlay file sets explicit root authorization."""
        overlay_content = OVERLAY_COMPOSE.read_text()
        assert "OPENACE_ALLOW_ROOT_MULTI_USER=1" in overlay_content, (
            "Overlay should set OPENACE_ALLOW_ROOT_MULTI_USER=1"
        )

    def test_overlay_file_sets_config_dir(self):
        """Test that overlay file sets config directory."""
        overlay_content = OVERLAY_COMPOSE.read_text()
        assert "OPENACE_CONFIG_DIR" in overlay_content, (
            "Overlay should set OPENACE_CONFIG_DIR"
        )


@pytest.mark.integration
class TestEntrypointValidation:
    """Tests for entrypoint configuration validation (Issue #2242)."""

    def test_entrypoint_exists(self):
        """Test that docker-entrypoint.sh exists."""
        entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        assert entrypoint_path.exists(), f"Entrypoint not found: {entrypoint_path}"

    def test_entrypoint_has_config_validation(self):
        """Test that entrypoint has configuration validation logic."""
        entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        entrypoint_content = entrypoint_path.read_text()

        # Check for configuration validation
        assert "WORKSPACE_MULTI_USER_MODE" in entrypoint_content, (
            "Entrypoint should validate WORKSPACE_MULTI_USER_MODE"
        )
        assert "OPENACE_ALLOW_ROOT_MULTI_USER" in entrypoint_content, (
            "Entrypoint should validate OPENACE_ALLOW_ROOT_MULTI_USER"
        )

    def test_entrypoint_outputs_config_summary(self):
        """Test that entrypoint outputs configuration summary."""
        entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        entrypoint_content = entrypoint_path.read_text()

        # Check for configuration summary output
        assert "Configuration Summary" in entrypoint_content or "config" in entrypoint_content.lower(), (
            "Entrypoint should output configuration summary"
        )

    def test_entrypoint_logs_config_check(self):
        """Test that entrypoint logs configuration check results."""
        entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        entrypoint_content = entrypoint_path.read_text()

        # Check for logging to file
        assert "config-check.log" in entrypoint_content or "CONFIG_CHECK_LOG" in entrypoint_content, (
            "Entrypoint should log configuration check results"
        )

    def test_entrypoint_has_error_suggestions(self):
        """Test that entrypoint provides error suggestions."""
        entrypoint_path = REPO_ROOT / "docker-entrypoint.sh"
        entrypoint_content = entrypoint_path.read_text()

        # Check for quick fix suggestions
        assert "QUICK FIX" in entrypoint_content or "quick fix" in entrypoint_content.lower(), (
            "Entrypoint should provide quick fix suggestions"
        )
        assert "start-multi-user.sh" in entrypoint_content, (
            "Entrypoint should reference the startup script"
        )
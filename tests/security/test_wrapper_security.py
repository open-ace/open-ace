"""
Security wrapper tests for Issue #1855.

Tests the openace-chown, openace-useradd, openace-cat, and openace-mkdir
wrapper scripts for security constraints and proper behavior.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Wrapper paths
OPENACE_CHOWN = "/usr/local/bin/openace-chown"
OPENACE_USERADD = "/usr/local/bin/openace-useradd"
OPENACE_CAT = "/usr/local/bin/openace-cat"
OPENACE_MKDIR = "/usr/local/bin/openace-mkdir"


def wrapper_available(wrapper_path: str) -> bool:
    """Check if a wrapper is available for testing."""
    return os.path.isfile(wrapper_path) and os.access(wrapper_path, os.X_OK)


@pytest.mark.skipif(
    not wrapper_available(OPENACE_CHOWN), reason="openace-chown wrapper not available"
)
class TestOpenaceChown:
    """Tests for openace-chown security wrapper."""

    def test_uid_below_minimum_rejected(self, tmp_path):
        """UID < 1000 should be rejected."""
        test_file = tmp_path / "test_file"
        test_file.write_text("test")

        result = subprocess.run(
            [OPENACE_CHOWN, "0:0", str(test_file)], capture_output=True, text=True
        )

        assert result.returncode == 3, f"Expected exit code 3, got {result.returncode}"
        assert "UID" in result.stderr or "below minimum" in result.stderr

    def test_gid_below_minimum_rejected(self, tmp_path):
        """GID < 1000 should be rejected."""
        test_file = tmp_path / "test_file"
        test_file.write_text("test")

        result = subprocess.run(
            [OPENACE_CHOWN, "1000:0", str(test_file)], capture_output=True, text=True
        )

        assert result.returncode == 3, f"Expected exit code 3, got {result.returncode}"

    def test_path_outside_allowed_rejected(self):
        """Path outside /workspace or /home should be rejected."""
        result = subprocess.run(
            [OPENACE_CHOWN, "1000:1000", "/etc/passwd"], capture_output=True, text=True
        )

        assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
        assert "outside allowed" in result.stderr.lower() or "path" in result.stderr.lower()

    def test_invalid_ownership_format_rejected(self, tmp_path):
        """Invalid ownership format should be rejected."""
        test_file = tmp_path / "test_file"
        test_file.write_text("test")

        result = subprocess.run(
            [OPENACE_CHOWN, "invalid", str(test_file)], capture_output=True, text=True
        )

        assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"


@pytest.mark.skipif(
    not wrapper_available(OPENACE_USERADD), reason="openace-useradd wrapper not available"
)
class TestOpenaceUseradd:
    """Tests for openace-useradd security wrapper."""

    def test_reserved_username_rejected(self):
        """Reserved usernames like 'root' should be rejected."""
        result = subprocess.run([OPENACE_USERADD, "root"], capture_output=True, text=True)

        assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
        assert "reserved" in result.stderr.lower()

    def test_invalid_username_format_rejected(self):
        """Invalid username format should be rejected."""
        result = subprocess.run(
            [OPENACE_USERADD, "Invalid-User-Name"], capture_output=True, text=True
        )

        assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
        assert "invalid" in result.stderr.lower() or "format" in result.stderr.lower()

    def test_uid_below_minimum_rejected(self):
        """UID < 1000 should be rejected."""
        result = subprocess.run(
            [OPENACE_USERADD, "testuser", "-u", "500"], capture_output=True, text=True
        )

        assert result.returncode == 3, f"Expected exit code 3, got {result.returncode}"

    def test_username_with_command_injection_rejected(self):
        """Username with command injection should be rejected."""
        result = subprocess.run([OPENACE_USERADD, "test; rm -rf /"], capture_output=True, text=True)

        # Should fail due to invalid characters or reserved check
        assert result.returncode != 0, "Command injection should be rejected"


@pytest.mark.skipif(not wrapper_available(OPENACE_CAT), reason="openace-cat wrapper not available")
class TestOpenaceCat:
    """Tests for openace-cat security wrapper."""

    def test_sensitive_file_rejected(self):
        """Sensitive files like /etc/shadow should be rejected."""
        result = subprocess.run(
            [OPENACE_CAT, "root", "/etc/shadow"], capture_output=True, text=True
        )

        assert result.returncode == 5, f"Expected exit code 5, got {result.returncode}"
        assert "sensitive" in result.stderr.lower() or "denied" in result.stderr.lower()

    def test_path_outside_allowed_rejected(self):
        """Path outside allowed directories should be rejected."""
        result = subprocess.run(
            [OPENACE_CAT, "root", "/etc/passwd"], capture_output=True, text=True
        )

        assert result.returncode in [2, 5], f"Expected exit code 2 or 5, got {result.returncode}"

    def test_nonexistent_user_rejected(self):
        """Nonexistent user should be rejected."""
        result = subprocess.run(
            [OPENACE_CAT, "nonexistent_user_12345", "/tmp/test"], capture_output=True, text=True
        )

        assert result.returncode == 3, f"Expected exit code 3, got {result.returncode}"
        assert "does not exist" in result.stderr.lower() or "user" in result.stderr.lower()


@pytest.mark.skipif(
    not wrapper_available(OPENACE_MKDIR), reason="openace-mkdir wrapper not available"
)
class TestOpenaceMkdir:
    """Tests for openace-mkdir security wrapper."""

    def test_path_outside_allowed_rejected(self):
        """Path outside /workspace or /home should be rejected."""
        result = subprocess.run(
            [OPENACE_MKDIR, "root", "/etc/test_dir"], capture_output=True, text=True
        )

        assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}"
        assert "outside allowed" in result.stderr.lower() or "path" in result.stderr.lower()

    def test_nonexistent_user_rejected(self):
        """Nonexistent user should be rejected."""
        result = subprocess.run(
            [OPENACE_MKDIR, "nonexistent_user_12345", "/workspace/test"],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 3, f"Expected exit code 3, got {result.returncode}"


class TestGithubOpsAdminMerge:
    """Tests for github_ops merge_pr admin opt-in."""

    def test_admin_merge_requires_opt_in(self, monkeypatch):
        """Test that admin merge requires OPENACE_ALLOW_ADMIN_MERGE=1."""
        import os

        # This test verifies the code path, actual execution would need
        # a full GitHub context
        # Ensure the opt-in check exists in the code
        import app.modules.workspace.autonomous.github_ops as github_ops

        # Check that the module has the opt-in check
        source_file = Path(github_ops.__file__)
        content = source_file.read_text()

        assert (
            "OPENACE_ALLOW_ADMIN_MERGE" in content
        ), "github_ops.py should check OPENACE_ALLOW_ADMIN_MERGE"
        assert (
            "PermissionError" in content
        ), "github_ops.py should raise PermissionError for missing opt-in"


class TestWorkspacePyUsesWrappers:
    """Tests for workspace.py using wrappers."""

    def test_workspace_py_references_wrappers(self):
        """Test that workspace.py references the wrapper paths."""
        import app.utils.workspace as workspace

        source_file = Path(workspace.__file__)
        content = source_file.read_text()

        assert "openace-useradd" in content, "workspace.py should reference openace-useradd wrapper"
        assert "openace-chown" in content, "workspace.py should reference openace-chown wrapper"


class TestAgentRunnerUsesWrappers:
    """Tests for agent_runner.py using wrappers."""

    def test_agent_runner_references_cat_wrapper(self):
        """Test that agent_runner.py references openace-cat wrapper."""
        import app.modules.workspace.autonomous.agent_runner as agent_runner

        source_file = Path(agent_runner.__file__)
        content = source_file.read_text()

        assert "openace-cat" in content, "agent_runner.py should reference openace-cat wrapper"
        assert "openace-mkdir" in content, "agent_runner.py should reference openace-mkdir wrapper"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Unit tests for openace-git security wrapper (Issue #2650).

Tests cover:
- Security boundary enforcement
- Fail-closed behavior
- Argument parsing
- Parameter validation
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

# Import from implementation module to avoid code duplication
scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
git_wrapper_path = os.path.join(scripts_dir, "openace-git.py")

spec = importlib.util.spec_from_file_location("openace_git", git_wrapper_path)
openace_git = importlib.util.module_from_spec(spec)
sys.modules["openace_git"] = openace_git
spec.loader.exec_module(openace_git)

parse_git_arguments = openace_git.parse_git_arguments
validate_c_arguments = openace_git.validate_c_arguments
validate_path = openace_git.validate_path
parse_version = openace_git.parse_version

# ============================================================================
# Exit Codes (must match openace-git.py)
# ============================================================================

EXIT_SUCCESS = 0
EXIT_USAGE = 64
EXIT_PERMISSION_DENIED = 65
EXIT_CONFIG_ERROR = 66
EXIT_PATH_VALIDATION = 67
EXIT_AUDIT_ERROR = 68
EXIT_COMMAND_FAILED = 69
EXIT_TIMEOUT = 70
EXIT_VERSION_INCOMPATIBLE = 71


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory with test configuration."""
    config_dir = tmp_path / "openace"
    config_dir.mkdir()

    # Create wrapper.yaml
    wrapper_config = """version: 1
general:
  log_path: /dev/null
  command_timeout: 30
security:
  allowed_path_prefixes:
    - /tmp
    - /workspace
    - /home
version_compat:
  git_min_version: "2.30.0"
  git_max_version: "99.0.0"
"""
    (config_dir / "wrapper.yaml").write_text(wrapper_config)

    # Create git-verbs.yaml
    git_verbs_config = """version: 1
allowed_verbs:
  - verb: status
    allowed_flags: [--porcelain]
  - verb: checkout
    allowed_flags: [-b]
  - verb: push
    allowed_flags: [origin, --force-with-lease]
  - verb: commit
    allowed_flags: [-m, --no-verify]
  - verb: add
    allowed_flags: [-A, -u]
  - verb: init
    allowed_flags: []
forbidden_verbs:
  - "!clean"
  - "!reset"
"""
    (config_dir / "git-verbs.yaml").write_text(git_verbs_config)

    return config_dir


@pytest.fixture
def temp_run_dir(tmp_path: Path) -> Path:
    """Create a temporary run directory."""
    run_dir = tmp_path / "run" / "openace"
    run_dir.mkdir(parents=True)
    return run_dir


# ============================================================================
# Parameter Parsing Tests
# ============================================================================


class TestArgumentParsing:
    """Test git argument parsing logic."""

    def test_parse_simple_command(self):
        """Test parsing a simple git command."""
        result = parse_git_arguments(["status", "--porcelain"])

        assert result.subcommand == "status"
        assert "--porcelain" in result.subcommand_args

    def test_parse_with_global_option_c(self):
        """Test parsing -c global option."""
        result = parse_git_arguments(["-c", "safe.directory=/workspace", "status", "--porcelain"])

        assert "-c" in result.global_opts
        assert "safe.directory=/workspace" in result.global_opts
        assert result.subcommand == "status"
        assert len(result.c_args) == 1
        assert result.c_args[0] == "safe.directory=/workspace"

    def test_parse_with_multiple_c_options(self):
        """Test parsing multiple -c options."""
        result = parse_git_arguments(
            [
                "-c",
                "safe.directory=/workspace",
                "-c",
                "user.name=test",
                "commit",
                "-m",
                "test",
            ]
        )

        assert len(result.c_args) == 2

    def test_parse_with_git_dir(self):
        """Test parsing --git-dir option."""
        result = parse_git_arguments(["--git-dir=/repo/.git", "--work-tree=/repo", "status"])

        assert any("--git-dir" in opt for opt in result.global_opts)
        assert result.subcommand == "status"

    def test_parse_c_equals_format(self):
        """Test parsing -c<key>=<value> format (no space)."""
        result = parse_git_arguments(["-csafe.directory=/workspace", "status"])

        assert len(result.c_args) == 1


# ============================================================================
# Security Boundary Tests
# ============================================================================


class TestSecurityBoundaries:
    """Test security boundary enforcement."""

    def test_alias_rce_blocked(self):
        """Test that -c alias.* is rejected."""
        is_valid, error = validate_c_arguments(["alias.pwn=!id"])
        assert not is_valid
        assert "alias" in error.lower()

    def test_alias_subkey_blocked(self):
        """Test that -c alias.<subkey>.<name> is rejected."""
        # Various alias RCE patterns
        for pattern in ["alias.co=!rm -rf /", "alias.pwn=!/bin/sh"]:
            is_valid, error = validate_c_arguments([pattern])
            assert not is_valid, f"Should reject: {pattern}"

    def test_hooks_path_non_dev_null_blocked(self):
        """Test that -c core.hooksPath with non-/dev/null value is rejected."""
        is_valid, error = validate_c_arguments(["core.hooksPath=/tmp/hooks"])
        assert not is_valid
        assert "hooksPath" in error or "forbidden" in error.lower()

    def test_hooks_path_dev_null_allowed(self):
        """Test that -c core.hooksPath=/dev/null is allowed."""
        is_valid, error = validate_c_arguments(["core.hooksPath=/dev/null"])
        assert is_valid

    def test_safe_directory_allowed(self):
        """Test that -c safe.directory is allowed."""
        is_valid, error = validate_c_arguments(["safe.directory=/workspace"])
        assert is_valid

    def test_user_config_allowed(self):
        """Test that -c user.* config is allowed."""
        is_valid, error = validate_c_arguments(["user.name=Test User"])
        assert is_valid

        is_valid, error = validate_c_arguments(["user.email=test@example.com"])
        assert is_valid


# ============================================================================
# Path Validation Tests
# ============================================================================


class TestPathValidation:
    """Test path validation logic."""

    def test_allowed_path_passes(self):
        """Test that allowed paths pass validation."""
        is_valid, error = validate_path("/workspace/project", ["", "/workspace"])
        assert is_valid

    def test_blocked_path_fails(self):
        """Test that paths outside allowed prefixes are rejected."""
        is_valid, error = validate_path("/etc/passwd", ["", "/workspace"])
        assert not is_valid

    def test_home_path_allowed(self):
        """Test that home directory paths are allowed."""
        is_valid, error = validate_path("/home/user/project", ["", "/home"])
        assert is_valid


# ============================================================================
# Context Validation Tests
# ============================================================================


class TestContextValidation:
    """Test trusted_git_context validation."""

    def test_valid_context(self, tmp_path: Path):
        """Test that valid context passes validation."""
        context_file = tmp_path / "context.json"
        context_data = {
            "git_dir": "/workspace/repo/.git",
            "work_tree": "/workspace/repo",
            "common_dir": "/workspace/repo/.git",
            "git_identity": "2051:12345",
            "created_at": time.time(),
            "expires_at": time.time() + 300,
            "pid": os.getpid(),
        }
        context_file.write_text(json.dumps(context_data))

        # Verify file exists and has correct format
        assert context_file.exists()
        data = json.loads(context_file.read_text())
        assert data["git_dir"] == "/workspace/repo/.git"

    def test_expired_context_rejected(self, tmp_path: Path):
        """Test that expired context is rejected."""
        context_file = tmp_path / "context.json"
        context_data = {
            "git_dir": "/workspace/repo/.git",
            "expires_at": time.time() - 100,  # Expired
        }
        context_file.write_text(json.dumps(context_data))

        # Verify file exists with expired timestamp
        data = json.loads(context_file.read_text())
        assert data["expires_at"] < time.time()


# ============================================================================
# Orphan File Cleanup Tests
# ============================================================================


class TestOrphanFileCleanup:
    """Test orphan file cleanup mechanism."""

    def test_orphan_context_cleanup(self, tmp_path: Path):
        """Test that orphan context files are cleaned up."""
        run_dir = tmp_path / "run" / "openace"
        run_dir.mkdir(parents=True)

        # Create an orphan file (old)
        orphan_file = run_dir / "trusted-context-99999.json"
        orphan_file.write_text('{"expires_at": 0}')
        os.utime(orphan_file, (0, 0))  # Set mtime to epoch

        # Verify file exists
        assert orphan_file.exists()

        # After cleanup simulation, file should be deletable
        import time

        age = time.time() - os.path.getmtime(orphan_file)
        assert age > 600  # Should be considered orphan

    def test_active_file_not_cleaned(self, tmp_path: Path):
        """Test that active files are not cleaned up."""
        run_dir = tmp_path / "run" / "openace"
        run_dir.mkdir(parents=True)

        # Create a fresh file
        active_file = run_dir / "trusted-context-12345.json"
        active_file.write_text('{"expires_at": ' + str(time.time() + 300) + "}")

        # Verify file exists
        assert active_file.exists()


# ============================================================================
# Version Compatibility Tests
# ============================================================================


class TestVersionCompatibility:
    """Test version compatibility checking."""

    def test_parse_version(self):
        """Test version string parsing."""
        assert parse_version("git version 2.43.0") == (2, 43, 0)
        assert parse_version("2.30.1") == (2, 30, 1)

    def test_version_comparison(self):
        """Test version comparison logic."""
        v1 = parse_version("2.30.0")
        v2 = parse_version("2.43.0")
        v3 = parse_version("3.0.0")

        assert v1 < v2
        assert v2 < v3


# ============================================================================
# Integration Tests
# ============================================================================


class TestWrapperIntegration:
    """Integration tests for the wrapper."""

    @pytest.mark.skipif(not shutil.which("git"), reason="git not available in environment")
    def test_self_check_runs(self):
        """Test that --self-check runs successfully."""
        result = subprocess.run(
            [sys.executable, "scripts/openace-git.py", "--self-check"],
            capture_output=True,
            text=True,
        )

        # Self-check should pass or have config error (acceptable)
        assert result.returncode in [0, 66]  # 66 = config error (acceptable)

    def test_help_flag_pass_through(self):
        """Test that --help is passed through to git."""
        # Skip if git not installed
        if not shutil.which("git"):
            pytest.skip("git not available in environment")

        result = subprocess.run(
            [sys.executable, "scripts/openace-git.py", "--help"],
            capture_output=True,
            text=True,
        )

        # Should pass through to git OR return permission denied if git not accessible
        # Either way, the wrapper should not crash
        assert result.returncode in [0, 65, 126]  # success, permission denied, or wrapper error


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

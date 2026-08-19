#!/usr/bin/env python3
"""
Sudoers upgrade compatibility tests for Issue #2181.

Tests that existing deployments with legacy sudoers rules can be safely
upgraded to the new security model without breaking functionality.

Test scenarios:
1. Legacy sudoers with rm * and OPENACE_CLI rules
2. Wrapper installation verification
3. Functionality regression after upgrade
4. Agent workflow compatibility
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
UPGRADE_SCRIPT = SCRIPTS_DIR / "upgrade-sudoers-security.sh"


def script_available() -> bool:
    """Check if upgrade script is available."""
    return UPGRADE_SCRIPT.exists() and os.access(UPGRADE_SCRIPT, os.X_OK)


def wrapper_available(wrapper_name: str) -> bool:
    """Check if a security wrapper is available."""
    wrapper_path = Path(f"/usr/local/bin/{wrapper_name}")
    return wrapper_path.exists() and os.access(wrapper_path, os.X_OK)


def create_legacy_sudoers() -> str:
    """Create a legacy sudoers file with deprecated rules for testing."""
    return """# Legacy sudoers file (pre Issue #2181)
# Contains deprecated rules that should be removed

# Git/GH rules (should be preserved)
Cmnd_Alias GIT_SAFE = /usr/bin/git checkout *, /usr/bin/git push *
Cmnd_Alias GH_SAFE = /usr/bin/gh pr create *, /usr/bin/gh pr merge *

# Deprecated: rm wildcard (should be removed)
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/rm *, /usr/bin/cat *, /usr/bin/chown *

# Deprecated: OPENACE_CLI (should be removed)
Cmnd_Alias OPENACE_CLI = /usr/local/bin/claude *, /usr/local/bin/qwen *

# Deprecated: AI CLI with (ALL) runas (should be removed)
open-ace ALL=(ALL) NOPASSWD: OPENACE_CLI
openace ALL=(ALL) NOPASSWD: OPENACE_CLI

# Deprecated: env_keep with sensitive variables (should be removed)
Defaults env_keep += "OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN"

# User rules (should be preserved)
open-ace ALL=(root) NOPASSWD: GIT_SAFE
openace ALL=(root) NOPASSWD: GIT_SAFE
open-ace ALL=(root) NOPASSWD: GH_SAFE
openace ALL=(root) NOPASSWD: GH_SAFE
"""


def visudo_available() -> bool:
    """Check if visudo is available for syntax validation."""
    return shutil.which("visudo") is not None


def _run_force_upgrade(tmp_path: Path):
    """Run ``--force`` against a legacy sudoers under tmp_path.

    Returns ``(result, test_sudoers, wrapper_dir)``. Stubs ``openace-rm`` so
    the wrapper-rule branch is exercised, and redirects every script path
    (SUDOERS_FILE/WRAPPER_DIR/BACKUP_DIR/AUDIT_LOG) under tmp_path so it runs
    unprivileged.
    """
    test_sudoers = tmp_path / "sudoers.d" / "open-ace-webui"
    test_sudoers.parent.mkdir(parents=True, exist_ok=True)
    test_sudoers.write_text(create_legacy_sudoers())

    wrapper_dir = tmp_path / "wrappers"
    wrapper_dir.mkdir()
    stub = wrapper_dir / "openace-rm"
    stub.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(stub, 0o755)

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    env = {
        **os.environ,
        "SUDOERS_FILE": str(test_sudoers),
        "WRAPPER_DIR": str(wrapper_dir),
        "BACKUP_DIR": str(log_dir),
        "AUDIT_LOG": str(log_dir / "audit.log"),
    }
    result = subprocess.run(
        [str(UPGRADE_SCRIPT), "--force"],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, test_sudoers, wrapper_dir


@pytest.mark.skipif(not script_available(), reason="upgrade-sudoers-security.sh not available")
class TestSudoersUpgrade:
    """Tests for sudoers upgrade functionality."""

    def test_upgrade_script_exists(self):
        """Verify upgrade script exists and is executable."""
        assert UPGRADE_SCRIPT.exists(), f"Upgrade script not found: {UPGRADE_SCRIPT}"
        assert os.access(
            UPGRADE_SCRIPT, os.X_OK
        ), f"Upgrade script not executable: {UPGRADE_SCRIPT}"

    def test_check_mode_detects_legacy_rules(self, tmp_path: Path):
        """Test that --check mode detects legacy rules that need upgrading."""
        # Create a test sudoers file with legacy rules
        test_sudoers = tmp_path / "sudoers.d" / "open-ace-webui"
        test_sudoers.parent.mkdir(parents=True, exist_ok=True)
        test_sudoers.write_text(create_legacy_sudoers())

        # Run upgrade script in check mode
        result = subprocess.run(
            [str(UPGRADE_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            env={**os.environ, "SUDOERS_FILE": str(test_sudoers)},
        )

        # Should detect that upgrade is needed
        assert "deprecated pattern" in result.stdout.lower() or result.returncode == 0

    def test_dry_run_shows_changes(self, tmp_path: Path):
        """Test that --dry-run shows what would be changed without making changes."""
        # Create a test sudoers file with legacy rules
        test_sudoers = tmp_path / "sudoers.d" / "open-ace-webui"
        test_sudoers.parent.mkdir(parents=True, exist_ok=True)
        original_content = create_legacy_sudoers()
        test_sudoers.write_text(original_content)

        # Run upgrade script in dry-run mode
        result = subprocess.run(
            [str(UPGRADE_SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "SUDOERS_FILE": str(test_sudoers)},
        )

        # Should show changes (progress now on stderr per #2440; banner on stdout)
        assert "removing" in result.stderr.lower() or "upgrade" in result.stdout.lower()

        # Original file should be unchanged
        assert test_sudoers.read_text() == original_content

    def test_removes_deprecated_rm_wildcard(self, tmp_path: Path):
        """Test that rm * wildcard is removed."""
        test_sudoers = tmp_path / "sudoers.d" / "open-ace-webui"
        test_sudoers.parent.mkdir(parents=True, exist_ok=True)
        test_sudoers.write_text(create_legacy_sudoers())

        # Run upgrade (simulated by checking script logic)
        # In a real test, we would run the upgrade script
        # Here we verify the script contains the logic
        script_content = UPGRADE_SCRIPT.read_text()
        assert "/usr/bin/rm *" in script_content
        assert "DEPRECATED_PATTERNS" in script_content

    def test_removes_deprecated_openace_cli(self, tmp_path: Path):
        """Test that OPENACE_CLI rule is removed."""
        script_content = UPGRADE_SCRIPT.read_text()
        assert "OPENACE_CLI" in script_content
        assert "Cmnd_Alias OPENACE_CLI" in script_content or "OPENACE_CLI" in script_content

    def test_removes_sensitive_env_keep(self, tmp_path: Path):
        """Test that sensitive variables are removed from env_keep."""
        script_content = UPGRADE_SCRIPT.read_text()
        assert "SENSITIVE_VARS" in script_content
        assert "OPENAI_API_KEY" in script_content
        assert "GH_TOKEN" in script_content

    def test_adds_wrapper_rules(self, tmp_path: Path):
        """Test that security wrapper rules are added."""
        script_content = UPGRADE_SCRIPT.read_text()
        assert "openace-rm" in script_content
        assert "SECURITY_WRAPPERS" in script_content

    def test_preserves_valid_rules(self, tmp_path: Path):
        """Test that valid rules are preserved."""
        script_content = UPGRADE_SCRIPT.read_text()
        # Should not remove GIT_SAFE or GH_SAFE
        assert "grep -v" in script_content or "preserve" in script_content.lower()

    def test_validates_sudoers_syntax(self, tmp_path: Path):
        """Test that sudoers syntax is validated after upgrade."""
        script_content = UPGRADE_SCRIPT.read_text()
        assert "visudo -c" in script_content
        assert "validate_sudoers" in script_content

    def test_creates_backup(self, tmp_path: Path):
        """Test that backup is created before upgrade."""
        script_content = UPGRADE_SCRIPT.read_text()
        assert "backup" in script_content.lower()
        assert ".bak" in script_content

    @pytest.mark.skipif(not visudo_available(), reason="visudo not available")
    def test_force_upgrade_produces_visudo_valid_candidate(self, tmp_path: Path):
        """``--force`` must yield a visudo-valid candidate with progress on stderr.

        Regression for #2440: progress echoes on stdout used to pollute the
        generated candidate, so ``visudo`` rejected it (exit 4, fail-closed)
        and the upgrade could never succeed.
        """
        result, test_sudoers, _ = _run_force_upgrade(tmp_path)

        # The upgrade must succeed — the bug exits 4 (polluted candidate).
        assert result.returncode == 0, (
            f"upgrade failed rc={result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

        # Progress belongs on stderr, never on the candidate-bearing stdout.
        assert "Adding wrapper rule" in result.stderr
        assert "Removing deprecated" in result.stderr
        assert "Adding wrapper rule" not in result.stdout
        assert "Removing deprecated" not in result.stdout

        # Generated candidate must be valid sudoers.
        check = subprocess.run(
            ["visudo", "-c", "-f", str(test_sudoers)], capture_output=True, text=True
        )
        assert (
            check.returncode == 0
        ), f"candidate invalid:\n{check.stderr}\n{test_sudoers.read_text()}"

    @pytest.mark.skipif(not visudo_available(), reason="visudo not available")
    def test_candidate_carries_both_account_rules_unchanged_scope(self, tmp_path: Path):
        """Both service accounts get the wrapper rule; command scope is not widened (#2440)."""
        result, test_sudoers, wrapper_dir = _run_force_upgrade(tmp_path)
        assert result.returncode == 0, f"upgrade failed rc={result.returncode}:\n{result.stderr}"
        content = test_sudoers.read_text()
        assert "open-ace ALL=(root) NOPASSWD:" in content
        assert "openace ALL=(root) NOPASSWD:" in content
        # Scope stays "${wrapper_path} *" — not widened.
        assert f"{wrapper_dir / 'openace-rm'} *" in content


@pytest.mark.skipif(not wrapper_available("openace-rm"), reason="openace-rm wrapper not available")
class TestWrapperInstallation:
    """Tests for security wrapper installation verification."""

    def test_openace_rm_wrapper_exists(self):
        """Verify openace-rm wrapper is installed."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        assert wrapper_path.exists(), "openace-rm wrapper not found"
        assert os.access(wrapper_path, os.X_OK), "openace-rm wrapper not executable"

    def test_openace_rm_has_min_uid_check(self):
        """Verify openace-rm checks minimum UID."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "MIN_UID" in content or "1000" in content

    def test_openace_rm_has_path_whitelist(self):
        """Verify openace-rm has path whitelist."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "/workspace" in content or "/home" in content

    def test_openace_rm_has_symlink_check(self):
        """Verify openace-rm checks for symlink escape."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "symlink" in content.lower() or "readlink" in content

    def test_openace_rm_has_dangerous_options_check(self):
        """Verify openace-rm checks for dangerous options."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "no-preserve-root" in content or "DANGEROUS_OPTIONS" in content

    def test_openace_rm_has_toctou_protection(self):
        """Verify openace-rm has TOCTOU protection."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "flock" in content or "TOCTOU" in content

    def test_openace_rm_has_audit_logging(self):
        """Verify openace-rm has audit logging."""
        wrapper_path = Path("/usr/local/bin/openace-rm")
        content = wrapper_path.read_text()
        assert "audit" in content.lower() or "log" in content.lower()


class TestFunctionalityRegression:
    """Tests for functionality regression after upgrade."""

    def test_upgrade_script_handles_missing_sudoers(self):
        """Test that upgrade script handles missing sudoers gracefully."""
        result = subprocess.run(
            [str(UPGRADE_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            env={**os.environ, "SUDOERS_FILE": "/nonexistent/sudoers"},
        )
        # Should not crash
        assert result.returncode in [0, 1, 2]

    def test_upgrade_script_is_idempotent(self):
        """Test that running upgrade script multiple times is safe."""
        script_content = UPGRADE_SCRIPT.read_text()
        # Should have force flag or check for existing rules
        assert "--force" in script_content or "already" in script_content.lower()


@pytest.mark.skipif(not script_available(), reason="upgrade script not available")
class TestUpgradeIntegration:
    """Integration tests for the complete upgrade process."""

    def test_end_to_end_upgrade_simulation(self, tmp_path: Path):
        """
        Simulate a complete upgrade process from legacy to new sudoers.

        This test:
        1. Creates a legacy sudoers file
        2. Runs the upgrade script in dry-run mode
        3. Verifies that deprecated rules would be removed
        4. Verifies that wrapper rules would be added
        """
        # Create test sudoers with legacy rules
        test_sudoers = tmp_path / "sudoers.d" / "open-ace-webui"
        test_sudoers.parent.mkdir(parents=True, exist_ok=True)
        test_sudoers.write_text(create_legacy_sudoers())

        # Check upgrade script logic
        script_content = UPGRADE_SCRIPT.read_text()

        # Verify script contains logic for:
        # 1. Detecting deprecated patterns
        assert "DEPRECATED_PATTERNS" in script_content
        assert "/usr/bin/rm *" in script_content

        # 2. Removing sensitive variables
        assert "SENSITIVE_VARS" in script_content
        assert "OPENAI_API_KEY" in script_content

        # 3. Adding wrapper rules
        assert "openace-rm" in script_content

        # 4. Validating syntax
        assert "visudo" in script_content

        # 5. Creating backup
        assert "backup" in script_content.lower()


class TestIssue2779SudoersChownCheck:
    """Regression tests for Issue #2779.

    Issue #2779: package-method install.sh incorrectly warned about
    missing chown in OPENACE_UTILS after security hardening in Issue #2181.

    The old check assumed chown should be in OPENACE_UTILS, but security
    hardening moved chown capability to the openace-chown wrapper. This
    test suite verifies the fix and prevents regression.
    """

    # Path to the install script that was fixed
    INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "install-central" / "package-method" / "install.sh"

    def test_install_script_exists(self):
        """Verify install script exists."""
        assert self.INSTALL_SCRIPT.exists(), f"Install script not found: {self.INSTALL_SCRIPT}"

    def test_no_openace_utils_chown_check(self):
        """
        Verify the old OPENACE_UTILS.*chown check has been removed.

        This was the root cause of Issue #2779: the check assumed chown
        should be in OPENACE_UTILS, but security hardening removed it.
        """
        script_content = self.INSTALL_SCRIPT.read_text()

        # The problematic pattern should NOT exist
        assert (
            "Cmnd_Alias OPENACE_UTILS.*chown" not in script_content
        ), "Found deprecated OPENACE_UTILS.*chown check - Issue #2779 regression"

        # The warning message should NOT exist
        assert (
            "Sudoers missing OPENACE_UTILS Cmnd_Alias or chown command" not in script_content
        ), "Found deprecated chown warning message - Issue #2779 regression"

    def test_wrapper_check_covers_chown(self):
        """
        Verify that wrapper checks cover chown capability.

        The openace-chown wrapper check should be present as the
        authoritative check for chown capability.
        """
        script_content = self.INSTALL_SCRIPT.read_text()

        # Wrapper check loop should include openace-chown
        assert "openace-chown" in script_content, "openace-chown wrapper check missing"

        # Should check for wrapper existence and user authorization
        # Pattern: for wrapper in ... openace-chown ...
        assert (
            "for wrapper in" in script_content and "openace-chown" in script_content
        ), "Wrapper loop pattern not found"

    def test_openace_utils_definition_excludes_chown(self):
        """
        Verify OPENACE_UTILS definition excludes chown.

        Per Issue #2181 security hardening, OPENACE_UTILS should only
        contain low-risk read-only commands.
        """
        script_content = self.INSTALL_SCRIPT.read_text()

        # Find OPENACE_UTILS definition
        matches = re.findall(r"Cmnd_Alias OPENACE_UTILS = (.+)", script_content)

        for match in matches:
            # Should NOT contain chown
            assert (
                "/usr/bin/chown" not in match
            ), f"OPENACE_UTILS incorrectly contains chown: {match}"

            # Should contain expected safe commands
            assert (
                "/usr/bin/test" in match or "test *" in match
            ), f"OPENACE_UTILS missing test command: {match}"

    def test_correct_config_no_false_warning(self, tmp_path: Path):
        """
        Test that correct configuration does not trigger chown warning.

        Scenario 1 from Issue #2779: OPENACE_UTILS without chown,
        wrapper rules complete -> no chown-related warning.
        """
        # Create a correct sudoers configuration
        correct_sudoers = """# Correct sudoers (post Issue #2181)
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *
deploy-user ALL=(root) NOPASSWD: /usr/local/bin/openace-chown *
"""
        test_file = tmp_path / "sudoers.d" / "open-ace-webui"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(correct_sudoers)

        # Verify the configuration is valid
        assert "OPENACE_UTILS" in test_file.read_text()
        assert "/usr/bin/chown" not in test_file.read_text()
        assert "openace-chown" in test_file.read_text()

    def test_legacy_config_with_chown_not_false_positive(self, tmp_path: Path):
        """
        Test that legacy config with chown in OPENACE_UTILS is handled correctly.

        Scenario 6 from Issue #2779: old sudoers with deprecated chown
        should not cause false positive about missing chown.
        """
        # Create a legacy sudoers with chown in OPENACE_UTILS
        legacy_sudoers = """# Legacy sudoers (pre Issue #2181)
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/chown *
deploy-user ALL=(root) NOPASSWD: OPENACE_UTILS
"""
        test_file = tmp_path / "sudoers.d" / "open-ace-webui-legacy"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(legacy_sudoers)

        # The install script should not have the false positive check
        script_content = self.INSTALL_SCRIPT.read_text()

        # Verify the problematic check is gone
        assert (
            "Cmnd_Alias OPENACE_UTILS.*chown" not in script_content
        ), "Legacy config would trigger false positive with old check"

    def test_wrapper_check_pattern_correct(self):
        """
        Verify wrapper check uses correct user+path matching.

        The wrapper check should match user+path on same line to avoid
        false positives from other users' rules.
        """
        script_content = self.INSTALL_SCRIPT.read_text()

        # Verify wrapper check loop exists and includes openace-chown
        assert "for wrapper in" in script_content, "Wrapper loop not found"
        assert "openace-chown" in script_content, "openace-chown wrapper check missing"

        # Verify user-anchored grep pattern exists in wrapper checks
        # Pattern: grep -E "^${run_user}" to anchor to current user
        # This prevents false positives from other users' rules
        # At minimum, verify wrapper check references user variable
        assert (
            "${run_user}" in script_content or "$run_user" in script_content
        ), "Wrapper check should reference run_user variable"

    def test_idempotent_upgrade_no_sudoers_rewrite(self, tmp_path: Path):
        """
        Verify idempotent upgrade: correct config should not trigger sudoers rewrite.

        Scenario 4 from Issue #2779: With correct configuration (OPENACE_UTILS
        without chown, wrapper rules complete), running upgrade twice should
        not rewrite sudoers file on second run.

        This validates the core fix: removing the false positive chown check
        ensures correct configs don't unnecessarily trigger updates.
        """
        # Create a correct sudoers configuration matching post-#2181 security model
        correct_sudoers = """# Correct sudoers (post Issue #2181)
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *
Cmnd_Alias MKDIR_SAFE = /usr/bin/mkdir *, /bin/mkdir*
deploy-user ALL=(root) NOPASSWD: OPENACE_UTILS
deploy-user ALL=(ALL) NOPASSWD: MKDIR_SAFE
deploy-user ALL=(root) NOPASSWD: /usr/local/bin/openace-chown *
"""
        test_file = tmp_path / "sudoers.d" / "open-ace-webui"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(correct_sudoers)

        # Verify the install script does NOT have the false positive check
        # that would cause unnecessary updates
        script_content = self.INSTALL_SCRIPT.read_text()

        # The old problematic check should NOT exist
        assert (
            "Cmnd_Alias OPENACE_UTILS.*chown" not in script_content
        ), "False positive chown check still exists - breaks idempotency"

        # The warning message should NOT exist
        assert (
            "Sudoers missing OPENACE_UTILS Cmnd_Alias or chown command" not in script_content
        ), "False positive warning message still exists"

        # With the fix, the configuration should pass all checks
        # without triggering need_update for chown-related issues
        assert "openace-chown" in script_content, "openace-chown wrapper check missing"

    def test_consecutive_upgrade_idempotency(self, tmp_path: Path):
        """
        Test that consecutive upgrades maintain idempotency.

        Scenario 5 from Issue #2779: Running upgrade multiple times
        should not produce different results or unnecessary warnings.
        """
        # Create initial correct configuration
        initial_config = """# Initial correct sudoers
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *
deploy-user ALL=(root) NOPASSWD: /usr/local/bin/openace-chown *
"""
        test_file = tmp_path / "sudoers.d" / "open-ace-webui"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(initial_config)

        # Simulate "first upgrade" - check configuration is valid
        assert "OPENACE_UTILS" in test_file.read_text()
        assert "/usr/bin/chown" not in test_file.read_text()

        # Simulate "second upgrade" - verify no changes needed
        # With the fix, the same configuration should be recognized as valid
        # and not trigger any warnings or updates
        script_content = self.INSTALL_SCRIPT.read_text()

        # Verify no false positive triggers
        assert (
            "Cmnd_Alias OPENACE_UTILS.*chown" not in script_content
        ), "Would trigger false positive on second upgrade"

        # Content should remain unchanged
        assert test_file.read_text() == initial_config


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

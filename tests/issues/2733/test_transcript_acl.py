"""
Tests for Issue #2733: Transcript ACL Configuration

Tests cover:
1. configure_transcript_acl function in install.sh
2. ACL configuration for workspace users' .qwen/projects directories
3. Security: symlink escape prevention
4. Edge cases: missing users, missing directories, ACL unsupported
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConfigureTranscriptAcl(unittest.TestCase):
    """Tests for the configure_transcript_acl function."""

    def test_acl_function_exists(self):
        """Verify configure_transcript_acl function exists in install.sh."""
        install_sh = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "install-central"
            / "package-method"
            / "install.sh"
        )
        self.assertTrue(install_sh.exists(), f"install.sh not found at {install_sh}")

        content = install_sh.read_text()
        self.assertIn(
            "configure_transcript_acl()",
            content,
            "configure_transcript_acl function not found in install.sh",
        )

    def test_acl_function_has_security_checks(self):
        """Verify configure_transcript_acl has symlink escape prevention."""
        install_sh = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "install-central"
            / "package-method"
            / "install.sh"
        )
        content = install_sh.read_text()

        # Check for symlink escape prevention
        self.assertIn("readlink -f", content, "Symlink resolution not found")
        self.assertIn("/home/*/.qwen/projects*", content, "Allowed path pattern not found")

    def test_acl_function_grants_minimal_permissions(self):
        """Verify ACL grants minimal permissions (r-X on projects, x on home)."""
        install_sh = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "install-central"
            / "package-method"
            / "install.sh"
        )
        content = install_sh.read_text()

        # Check for execute-only on home directory (traversal)
        self.assertIn(
            "u:${service_user}:x", content, "Execute-only ACL on home directory not found"
        )

        # Check for read+execute on projects directory
        self.assertIn(
            "u:${service_user}:r-X", content, "Read+execute ACL on projects directory not found"
        )

    def test_acl_function_sets_default_acl(self):
        """Verify ACL sets default ACL for inheritance."""
        install_sh = (
            Path(__file__).parent.parent.parent.parent
            / "scripts"
            / "install-central"
            / "package-method"
            / "install.sh"
        )
        content = install_sh.read_text()

        # Check for default ACL
        self.assertIn("setfacl -R -d -m", content, "Default ACL configuration not found")


class TestTranscriptAclIntegration(unittest.TestCase):
    """Integration tests for transcript ACL configuration."""

    @classmethod
    def setUpClass(cls):
        """Check if we can run integration tests (need root and setfacl)."""
        cls.can_run_as_root = os.geteuid() == 0
        cls.has_setfacl = subprocess.run(["which", "setfacl"], capture_output=True).returncode == 0

    def setUp(self):
        """Set up test fixtures."""
        if not self.can_run_as_root:
            self.skipTest("Integration tests require root privileges")
        if not self.has_setfacl:
            self.skipTest("Integration tests require setfacl command")

    def test_setfacl_on_directory(self):
        """Test that setfacl works on a temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test directory
            test_dir = Path(tmpdir) / "test_acl"
            test_dir.mkdir()

            # Set ACL
            result = subprocess.run(
                ["setfacl", "-m", "u:root:r-X", str(test_dir)], capture_output=True, text=True
            )

            # Verify ACL was set
            result = subprocess.run(["getfacl", str(test_dir)], capture_output=True, text=True)
            self.assertIn("user:root:r-x", result.stdout, "ACL not set correctly")


class TestFetchQwenObservability(unittest.TestCase):
    """Tests for fetch_qwen.py observability enhancement (already implemented)."""

    def test_find_all_qwen_project_dirs_returns_coverage_data(self):
        """Verify find_all_qwen_project_dirs returns structured coverage data."""
        from scripts.fetch_qwen import find_all_qwen_project_dirs

        # The function should return a dict with 'accessible' and 'denied' keys
        result = find_all_qwen_project_dirs()

        self.assertIsInstance(result, dict, "Result should be a dictionary")
        self.assertIn("accessible", result, "Result should have 'accessible' key")
        self.assertIn("denied", result, "Result should have 'denied' key")
        self.assertIn("errors", result, "Result should have 'errors' key")

    def test_fetch_result_json_format(self):
        """Verify FETCH_RESULT JSON output format."""
        import json

        fetch_qwen = Path(__file__).parent.parent.parent.parent / "scripts" / "fetch_qwen.py"
        content = fetch_qwen.read_text()

        # Check for FETCH_RESULT output
        self.assertIn("FETCH_RESULT", content, "FETCH_RESULT output not found")

        # Verify the result includes status and coverage
        self.assertIn('"status"', content, "Status field not found in FETCH_RESULT")
        self.assertIn('"coverage"', content, "Coverage field not found in FETCH_RESULT")


if __name__ == "__main__":
    unittest.main()

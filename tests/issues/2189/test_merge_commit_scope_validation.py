"""Test merge commit scope validation fix (Issue #2189).

Tests that scope validation correctly excludes changes introduced by
merging upstream main, only counting changes made in the PR branch.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


class TestMergeCommitScopeValidation(unittest.TestCase):
    """Test scope validation for merge commits."""

    def setUp(self):
        """Set up test fixtures."""
        self.orchestrator = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
        self.gh = MagicMock(spec=GitHubOps)

    def test_is_merge_commit_true(self):
        """Test that merge commits are correctly identified."""
        # Mock rev-parse succeeds for ^2 (second parent exists)
        self.gh._run_git.return_value = MagicMock(returncode=0, stdout="abc123")

        result = self.orchestrator._is_merge_commit(self.gh, "merge_commit_sha")

        self.assertTrue(result)
        self.gh._run_git.assert_called_once_with(["rev-parse", "merge_commit_sha^2"], check=False)

    def test_is_merge_commit_false(self):
        """Test that regular commits are correctly identified."""
        # Mock rev-parse fails for ^2 (no second parent)
        self.gh._run_git.return_value = MagicMock(returncode=128, stdout="")

        result = self.orchestrator._is_merge_commit(self.gh, "regular_commit_sha")

        self.assertFalse(result)
        self.gh._run_git.assert_called_once_with(["rev-parse", "regular_commit_sha^2"], check=False)

    def test_is_merge_commit_exception(self):
        """Test exception handling in merge commit detection."""
        # Mock raises exception
        self.gh._run_git.side_effect = Exception("Git error")

        result = self.orchestrator._is_merge_commit(self.gh, "any_sha")

        self.assertFalse(result)

    def test_scope_validation_regular_commit(self):
        """Test that regular commits use original logic."""
        wf = {"base_commit_sha": "commit_before"}  # Same as commit_before, so only 1 range

        # Mock: not a merge commit
        with patch.object(self.orchestrator, "_is_merge_commit", return_value=False):
            # Mock: get_changed_files returns few files
            self.gh.get_changed_files.return_value = ["file1.py", "file2.py"]

            result = self.orchestrator._validate_autonomous_change_scope(
                self.gh, wf, "commit_before", "commit_after"
            )

            # Should pass (only 2 files)
            self.assertEqual(result, "")
            # Should use original commit_before
            self.gh.get_changed_files.assert_called_once_with("commit_before", "commit_after")

    def test_scope_validation_merge_commit(self):
        """Test that merge commits use merge-base."""
        wf = {"base_commit_sha": "merge_base_sha"}  # Same as effective_base, so only 1 range

        # Mock: is a merge commit
        with patch.object(self.orchestrator, "_is_merge_commit", return_value=True):
            # Mock: fetch and resolve_commit
            self.gh._run_git.return_value = MagicMock(returncode=0, stdout="merge_base_sha\n")
            self.gh.resolve_commit.return_value = "fetched_main_head"

            # Mock: get_changed_files returns few files (PR branch only)
            self.gh.get_changed_files.return_value = ["file1.py", "file2.py"]

            result = self.orchestrator._validate_autonomous_change_scope(
                self.gh, wf, "commit_before", "merge_commit_after"
            )

            # Should pass (only 2 files in PR branch)
            self.assertEqual(result, "")
            # Should use merge-base as base
            self.gh.get_changed_files.assert_called_once_with(
                "merge_base_sha", "merge_commit_after"
            )

    def test_scope_validation_merge_commit_excludes_upstream(self):
        """Test that merge commits exclude upstream changes from scope."""
        wf = {"base_commit_sha": ""}  # Legacy workflow without base_commit_sha

        # Mock: is a merge commit
        with patch.object(self.orchestrator, "_is_merge_commit", return_value=True):
            # Mock: fetch succeeds
            self.gh._run_git.return_value = MagicMock(returncode=0, stdout="merge_base_sha\n")
            self.gh.resolve_commit.return_value = "fetched_main_head"

            # Mock: get_changed_files - if we used wrong base, would return 87 files
            # But with merge-base, returns only 10 files (PR branch only)
            self.gh.get_changed_files.return_value = [f"file{i}.py" for i in range(10)]

            # Mock: _update_workflow (for base_commit_sha backfill)
            with patch.object(self.orchestrator, "_update_workflow"):
                result = self.orchestrator._validate_autonomous_change_scope(
                    self.gh, wf, "commit_before", "merge_commit_after"
                )

            # Should pass (only 10 files, under limit of 60)
            self.assertEqual(result, "")

    def test_scope_validation_merge_commit_fallback(self):
        """Test fallback to original logic when merge-base derivation fails."""
        wf = {"base_commit_sha": "commit_before"}  # Same as commit_before, so only 1 range

        # Mock: is a merge commit
        with patch.object(self.orchestrator, "_is_merge_commit", return_value=True):
            # Mock: fetch succeeds but merge-base fails
            self.gh._run_git.return_value = MagicMock(returncode=128, stdout="")
            self.gh.resolve_commit.return_value = "fetched_main_head"

            # Mock: get_changed_files with original base
            self.gh.get_changed_files.return_value = ["file1.py", "file2.py"]

            result = self.orchestrator._validate_autonomous_change_scope(
                self.gh, wf, "commit_before", "merge_commit_after"
            )

            # Should fallback to original logic
            self.assertEqual(result, "")
            # Should use original commit_before (fallback)
            self.gh.get_changed_files.assert_called_once_with("commit_before", "merge_commit_after")


if __name__ == "__main__":
    unittest.main()

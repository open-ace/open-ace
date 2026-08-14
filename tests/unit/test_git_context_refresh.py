"""Tests for trusted Git context refresh at worktree lifecycle points.

Regression coverage for #2565: the class-level _trusted_git_contexts registry
persists across scheduler cycles, so a prior _run_agent's stale pin causes
false-positive "identity changed" failures during the NEXT cycle's worktree
lifecycle ops (ensure_worktree / recreate). The fix re-pins to the CURRENT
gitdir identity at lifecycle entry points.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps, GitHubOpsError
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _git_is_functional() -> bool:
    """Check if git can execute repository operations.

    Returns True only if git can successfully initialize a repository.
    Returns False if git is restricted or not functional.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["git", "init", "--bare", str(Path(tmp) / "test.git")],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# Skip all tests in this module if git is not functional
pytestmark = pytest.mark.skipif(
    not _git_is_functional(),
    reason="git init is restricted or non-functional - required for git context refresh tests",
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return str(repo)


@pytest.fixture
def orchestrator():
    """Create an AutonomousOrchestrator instance for helper access."""
    return AutonomousOrchestrator("test-wf-refresh")


@pytest.fixture(autouse=True)
def clear_trusted_contexts():
    """Ensure the class-level registry is clean before each test."""
    GitHubOps._trusted_git_contexts.clear()
    yield
    GitHubOps._trusted_git_contexts.clear()


@pytest.mark.regression
@pytest.mark.issue(2565)
class TestRefreshTrustedGitContext:
    """Unit tests for AutonomousOrchestrator._refresh_trusted_git_context."""

    def test_refresh_replaces_stale_identity_with_current(self, git_repo, orchestrator):
        """Given a stale registered identity, _refresh_trusted_git_context
        re-pins the registry to the repo's CURRENT gitdir identity."""
        # Register a BOGUS (stale) identity for the repo
        GitHubOps.register_trusted_git_context(
            repo_path=git_repo,
            git_dir=os.path.join(git_repo, ".git"),
            git_identity="999:999",
            common_dir=os.path.join(git_repo, ".git"),
            common_identity="999:999",
        )
        real_key = os.path.realpath(git_repo)
        assert GitHubOps._trusted_git_contexts[real_key]["git_identity"] == "999:999"

        # Refresh should re-pin to the REAL current identity
        orchestrator._refresh_trusted_git_context(git_repo, system_account=None)

        gh = GitHubOps(git_repo)
        real_identity = gh.get_path_identity(os.path.join(git_repo, ".git"))
        refreshed = GitHubOps._trusted_git_contexts[real_key]
        assert refreshed["git_identity"] == real_identity
        assert refreshed["git_identity"] != "999:999"

    def test_refresh_does_not_raise_on_missing_repo(self, orchestrator, tmp_path):
        """If the repo path doesn't exist, refresh silently returns (best-effort)
        WITHOUT registering a trusted context for the bogus path."""
        bogus = str(tmp_path / "nonexistent")
        assert not os.path.exists(bogus)  # precondition
        # Must not raise, and must not register a context for a non-repo path
        orchestrator._refresh_trusted_git_context(bogus, system_account=None)
        assert os.path.realpath(bogus) not in GitHubOps._trusted_git_contexts

    def test_stale_context_does_not_block_git_after_refresh(self, git_repo, orchestrator):
        """Simulate the false-positive: a stale trusted context (from a prior
        cycle) would normally cause _run_git to raise. After refresh, the git
        op proceeds normally."""
        # Pin a STALE identity (simulating a prior cycle's registration)
        GitHubOps.register_trusted_git_context(
            repo_path=git_repo,
            git_dir=os.path.join(git_repo, ".git"),
            git_identity="999:999",
            common_dir=os.path.join(git_repo, ".git"),
            common_identity="999:999",
        )
        gh_stale = GitHubOps(git_repo)
        # Without refresh, _run_git fails (stale identity mismatch)
        with pytest.raises(GitHubOpsError, match="identity changed"):
            gh_stale._run_git(["status", "--porcelain"])

        # Now refresh (as ensure_worktree would do at lifecycle entry)
        orchestrator._refresh_trusted_git_context(git_repo, system_account=None)

        # A NEW GitHubOps instance (as lifecycle code creates) now works
        gh_fresh = GitHubOps(git_repo)
        result = gh_fresh._run_git(["rev-parse", "HEAD"])
        assert result.returncode == 0


@pytest.mark.regression
@pytest.mark.issue(2565)
class TestSecurityGuardStillDetectsTampering:
    """The refresh fix must NOT weaken the anti-tampering guard. An agent
    replacing .git between the pre-agent pin and post-agent verify is still
    detected."""

    def test_agent_window_git_replacement_still_detected(self, git_repo, orchestrator):
        """Simulate: orchestrator pins before agent -> agent replaces .git ->
        post-agent verify still raises."""
        # 1. Orchestrator pins (refresh) the context before the agent
        orchestrator._refresh_trusted_git_context(git_repo, system_account=None)
        gh_pinned = GitHubOps(git_repo)
        assert gh_pinned._trusted_git_dir  # context is pinned

        # 2. Simulate the AGENT replacing the .git directory with a new one
        #    (new inode = different device:inode identity)
        git_dir = os.path.join(git_repo, ".git")
        backup = git_dir + ".bak"
        shutil.copytree(git_dir, backup)
        shutil.rmtree(git_dir)
        os.rename(backup, git_dir)

        # 3. Post-agent verify must STILL detect the change
        with pytest.raises(GitHubOpsError, match="identity changed"):
            gh_pinned._run_git(["status", "--porcelain"])

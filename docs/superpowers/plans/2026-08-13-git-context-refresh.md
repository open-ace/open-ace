# Git Trusted-Context Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the false-positive "Protected Git directory identity changed" failures by refreshing the trusted Git context at worktree lifecycle points, while preserving the anti-tampering guard.

**Architecture:** Add `_refresh_trusted_git_context` to the orchestrator (reuses `_capture_repo_state` to snapshot the current gitdir and re-register it). Call it at `ensure_worktree` entry (main repo) and after each `worktree add` (worktree) in `git_workspace.py`.

**Tech Stack:** Python, pytest, real temp git repos/worktrees for identity-based tests.

---

### Task 1: Write the helper unit test (RED)

**Files:**
- Create: `tests/unit/test_git_context_refresh.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for trusted Git context refresh at worktree lifecycle points.

Regression coverage for #2565: the class-level _trusted_git_contexts registry
persists across scheduler cycles, so a prior _run_agent's stale pin causes
false-positive "identity changed" failures during the NEXT cycle's worktree
lifecycle ops (ensure_worktree / recreate). The fix re-pins to the CURRENT
gitdir identity at lifecycle entry points.
"""

import os
import subprocess

import pytest

from app.modules.workspace.autonomous.github_ops import GitHubOps
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True,
    )
    return str(repo)


@pytest.fixture
def orchestrator():
    """Create an AutonomousOrchestrator instance for helper access."""
    return AutonomousOrchestrator("test-wf-refresh")


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
        # The stale identity is now in the registry
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
        """If the repo path doesn't exist, refresh silently returns (best-effort)."""
        bogus = str(tmp_path / "nonexistent")
        # Must not raise
        orchestrator._refresh_trusted_git_context(bogus, system_account=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_git_context_refresh.py::TestRefreshTrustedGitContext::test_refresh_replaces_stale_identity_with_current -xvs`
Expected: FAIL with `AttributeError: 'AutonomousOrchestrator' object has no attribute '_refresh_trusted_git_context'`

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/unit/test_git_context_refresh.py
git commit -m "test(autonomous): add RED test for _refresh_trusted_git_context helper (#2565)"
```

---

### Task 2: Implement `_refresh_trusted_git_context` (GREEN)

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py` (near line 2683, after `_capture_repo_state`)

- [ ] **Step 1: Add the helper method**

Insert after `_capture_repo_state` (line 2683), before `recover_worktree_branch`:

```python
def _refresh_trusted_git_context(
    self, repo_path: str, system_account: str | None
) -> None:
    """Re-pin the trusted Git context for repo_path to its CURRENT gitdir
    identity.

    Called at worktree lifecycle points (ensure_worktree / recreate), which
    legitimately change the gitdir after the prior _run_agent pinned it.
    The class-level registry persists across scheduler cycles, so without
    this refresh the next cycle's lifecycle git ops verify against a stale
    baseline and produce false-positive "identity changed" failures (#2565).

    Best-effort: if the gitdir cannot be snapshotted (e.g. doesn't exist yet),
    skip silently — _verify_trusted_git_context's empty-context early-return
    handles the unset case.
    """
    try:
        state = self._capture_repo_state(repo_path, system_account)
    except Exception:
        logger.debug(
            "Skipping trusted Git context refresh for %s (capture failed)",
            repo_path,
        )
        return
    git_dir = state.get("git_dir", "")
    git_identity = state.get("git_identity", "")
    common_dir = state.get("common_dir", "")
    common_identity = state.get("common_identity", "")
    if not git_dir or not git_identity or not common_dir or not common_identity:
        logger.debug(
            "Skipping trusted Git context refresh for %s (incomplete state)",
            repo_path,
        )
        return
    GitHubOps.register_trusted_git_context(
        repo_path,
        git_dir,
        git_identity,
        common_dir,
        common_identity,
    )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/unit/test_git_context_refresh.py::TestRefreshTrustedGitContext -xvs`
Expected: PASS (2 tests)

- [ ] **Step 3: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py
git commit -m "fix(autonomous): add _refresh_trusted_git_context helper (#2565)"
```

---

### Task 3: Write the behavioral false-positive test (RED)

**Files:**
- Modify: `tests/unit/test_git_context_refresh.py`

- [ ] **Step 1: Add the behavioral test**

Append to `TestRefreshTrustedGitContext`:

```python
    def test_stale_context_does_not_block_git_after_refresh(
        self, git_repo, orchestrator
    ):
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
        # Without refresh, _run_git would fail (stale identity mismatch)
        with pytest.raises(GitHubOpsError, match="identity changed"):
            gh_stale._run_git(["status", "--porcelain"])

        # Now refresh (as ensure_worktree would do at lifecycle entry)
        orchestrator._refresh_trusted_git_context(git_repo, system_account=None)

        # A NEW GitHubOps instance (as lifecycle code creates) now works
        gh_fresh = GitHubOps(git_repo)
        result = gh_fresh._run_git(["rev-parse", "HEAD"])
        assert result.returncode == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_git_context_refresh.py::TestRefreshTrustedGitContext::test_stale_context_does_not_block_git_after_refresh -xvs`
Expected: FAIL (helper already exists, but this verifies the RED state of the false-positive scenario BEFORE the lifecycle calls are wired — the test itself proves the refresh works, so it should PASS now. To see true RED, temporarily comment out the refresh call.)

Actually, this test verifies the helper works end-to-end. It should PASS now. To verify the RED path: confirm that WITHOUT the refresh call, `gh_stale._run_git` raises. That is the `pytest.raises` part. The test proves both sides.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/unit/test_git_context_refresh.py::TestRefreshTrustedGitContext::test_stale_context_does_not_block_git_after_refresh -xvs`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_git_context_refresh.py
git commit -m "test(autonomous): add behavioral test for stale-context refresh (#2565)"
```

---

### Task 4: Write the security guard test (RED→GREEN)

**Files:**
- Modify: `tests/unit/test_git_context_refresh.py`

- [ ] **Step 1: Add the security guard test**

Append a new test class:

```python
@pytest.mark.regression
@pytest.mark.issue(2565)
class TestSecurityGuardStillDetectsTampering:
    """The refresh fix must NOT weaken the anti-tampering guard. An agent
    replacing .git between the pre-agent pin and post-agent verify is still
    detected."""

    def test_agent_window_git_replacement_still_detected(self, git_repo, orchestrator):
        """Simulate: orchestrator pins before agent → agent replaces .git →
        post-agent verify still raises."""
        # 1. Orchestrator pins (refresh) the context before the agent
        orchestrator._refresh_trusted_git_context(git_repo, system_account=None)
        gh_pinned = GitHubOps(git_repo)
        assert gh_pinned._trusted_git_dir  # context is pinned

        # 2. Simulate the AGENT replacing the .git directory
        #    (remove + recreate .git with a different inode)
        git_dir = os.path.join(git_repo, ".git")
        # Move the real .git aside and recreate it (new inode)
        backup = git_dir + ".bak"
        os.rename(git_dir, backup)
        os.rename(backup, git_dir)
        # On most filesystems, rename is atomic and preserves inode.
        # To force a NEW inode, copy + delete + recreate:
        import shutil
        shutil.copytree(git_dir, backup)
        shutil.rmtree(git_dir)
        os.rename(backup, git_dir)

        # 3. Post-agent verify must STILL detect the change
        with pytest.raises(GitHubOpsError, match="identity changed"):
            gh_pinned._run_git(["status", "--porcelain"])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/unit/test_git_context_refresh.py::TestSecurityGuardStillDetectsTampering -xvs`
Expected: PASS (proves the guard still catches tampering after refresh — no implementation change needed for this test, it validates the invariant)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_git_context_refresh.py
git commit -m "test(autonomous): add security guard test proving tampering still detected (#2565)"
```

---

### Task 5: Wire refresh calls into git_workspace.py lifecycle points

**Files:**
- Modify: `app/modules/workspace/autonomous/git_workspace.py`

- [ ] **Step 1: Add refresh at ensure_worktree entry (after main_gh creation, line 192)**

After:
```python
main_gh = _GitHubOps(project_path, system_account=system_account)
```
Add:
```python
# Refresh the trusted Git context for the main repo so lifecycle ops
# (fetch/show-ref/worktree-add) verify against the CURRENT gitdir, not
# a stale baseline pinned by a prior _run_agent cycle. (#2565)
self._orch._refresh_trusted_git_context(project_path, system_account)
```

- [ ] **Step 2: Add refresh after branch-mismatch worktree add (after line 259)**

After:
```python
main_gh._run_git(["worktree", "add", canonical, expected_branch])
```
Add:
```python
self._orch._refresh_trusted_git_context(canonical, system_account)
```

- [ ] **Step 3: Add refresh after branch-mismatch worktree add -b (after line 276)**

After:
```python
main_gh._run_git(
    ["worktree", "add", "-b", expected_branch, canonical, head_sha]
)
```
Add:
```python
self._orch._refresh_trusted_git_context(canonical, system_account)
```

- [ ] **Step 4: Add refresh after missing-worktree branch-survives add (after line 324)**

After:
```python
main_gh._run_git(["worktree", "add", canonical, branch_name])
```
Add:
```python
self._orch._refresh_trusted_git_context(canonical, system_account)
```

- [ ] **Step 5: Add refresh after missing-worktree branch-gone add -b (after line 349)**

After:
```python
main_gh._run_git(["worktree", "add", "-b", branch_name, canonical, head_sha])
```
Add:
```python
self._orch._refresh_trusted_git_context(canonical, system_account)
```

- [ ] **Step 6: Run the full test suite for the new tests**

Run: `pytest tests/unit/test_git_context_refresh.py -xvs`
Expected: PASS (all tests)

- [ ] **Step 7: Run existing suites for regression**

Run: `pytest tests/issues/2021/ tests/unit/test_autonomous_ci_guardrails.py -xvs`
Expected: PASS (no regression)

- [ ] **Step 8: Commit**

```bash
git add app/modules/workspace/autonomous/git_workspace.py
git commit -m "fix(autonomous): refresh trusted Git context at worktree lifecycle points (#2565)"
```

---

### Task 6: Final regression sweep

- [ ] **Step 1: Run broader orchestrator/github_ops tests**

```bash
pytest tests/unit/test_git_context_refresh.py tests/issues/2021/ tests/autonomous/test_repo_drift_validation.py tests/unit/test_autonomous_ci_guardrails.py -v
```
Expected: All PASS

- [ ] **Step 2: Verify no `closes|fixes|resolves` near issue numbers in commits**

```bash
git log origin/main..HEAD --oneline --format="%s %b" | grep -iE 'closes|fixes|resolves.*#[0-9]' || echo "CLEAN"
```
Expected: CLEAN

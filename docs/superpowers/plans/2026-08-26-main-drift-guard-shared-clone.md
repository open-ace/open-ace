# Main-HEAD-drift Guard Shared-Clone Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the autonomous main-HEAD-drift guard from failing a workflow when the *shared* project clone's main HEAD is moved by a sibling workflow or the developer, in the case where the agent ran cross-user (isolated launcher) and therefore provably could not have moved it.

**Architecture:** In the same-branch main-drift block of `_validate_repo_context_after_run`, short-circuit to "allow (external drift)" with a WARNING when `AutonomousAgentRunner._is_cross_user(system_account)` is True — the same predicate that decided the agent was launched credentialless with a scoped ACL to only its worktree. Same-user mode keeps today's benign-pull probe + fail-closed behavior.

**Tech Stack:** Python, pytest. Spec: `docs/superpowers/specs/2026-08-26-main-drift-guard-shared-clone-design.md`. Issue: #3124.

---

## File Structure

- `app/modules/workspace/autonomous/orchestrator.py` — one added branch in the same-branch main-drift block of `_validate_repo_context_after_run` (currently the fail-closed `return "Detected commits on the main repository…"` at ~line 3378).
- `tests/autonomous/test_repo_drift_validation.py` — extend with cross-user cases (reuses the file's existing `_make_orchestrator`, `_before_state`, `_install_fake_gh` helpers).

`AutonomousAgentRunner` is already imported in `orchestrator.py` (`from app.modules.workspace.autonomous.agent_runner import ... AutonomousAgentRunner`), so no new import.

---

## Task 1: Cross-user allow in the main-drift block

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py` (same-branch main-drift block; the benign-pull `if`/`return ""` and the fail-closed `return (...)` at ~3364-3382)
- Test: `tests/autonomous/test_repo_drift_validation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/autonomous/test_repo_drift_validation.py`. First ensure `import pytest` is present near the top (add it if missing, after `import os`). Then append this class at end of file:

```python
class TestSharedCloneCrossUser:
    """#3124: a cross-user isolated agent cannot touch the shared project
    clone, so a main-HEAD move during its run is external (a sibling workflow
    or the developer) and must not fail the workflow. Same-user mode, where an
    escape is possible, stays fail-closed."""

    pytestmark = [pytest.mark.issue(3124), pytest.mark.regression]

    def test_cross_user_agent_allows_nonbenign_main_drift(self, monkeypatch):
        # #2739: main moved forward but NOT onto origin/main (benign-pull probe
        # would return False → today this blocks). Under the cross-user isolated
        # launcher the agent could not have done it → allow.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_LOCAL,
            effective_head=WORKTREE_HEAD,
            moved_forward=True,
            after_on_remote=False,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)
        with patch.object(
            AutonomousAgentRunner, "_is_cross_user", return_value=True
        ):
            assert o._validate_repo_context_after_run(before, system_account="dwu") == ""

    def test_cross_user_agent_allows_even_when_probe_indeterminate(self, monkeypatch):
        # #2739 exact shape: the benign-pull probe hits a git error under shared-
        # clone contention (indeterminate → fail-closed today). Cross-user must
        # still allow, and must not depend on the probe outcome.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_LOCAL,
            effective_head=WORKTREE_HEAD,
            git_error=True,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)
        with patch.object(
            AutonomousAgentRunner, "_is_cross_user", return_value=True
        ):
            assert o._validate_repo_context_after_run(before, system_account="dwu") == ""

    def test_same_user_nonbenign_main_drift_still_blocks(self, monkeypatch):
        # Same-user host (no isolation): an escape is possible, so a non-benign
        # main move stays fail-closed.
        _install_fake_gh(
            monkeypatch,
            after_main_head=MAIN_AFTER_LOCAL,
            effective_head=WORKTREE_HEAD,
            moved_forward=True,
            after_on_remote=False,
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)
        with patch.object(
            AutonomousAgentRunner, "_is_cross_user", return_value=False
        ):
            err = o._validate_repo_context_after_run(before, system_account="alice")
            assert "Detected commits on the main repository" in err

    def test_cross_user_does_not_suppress_repo_root_escape(self, monkeypatch):
        # The cross-user allow is scoped to the main-drift block only. A genuine
        # worktree-integrity violation (repo root changed) still blocks.
        def factory(repo_path, system_account=None):
            gh = MagicMock()
            gh.get_path_identity.return_value = "1:1"
            gh.get_current_branch.return_value = "auto-dev/wf-drift"
            gh.get_current_commit.return_value = WORKTREE_HEAD

            def run_git(args, check=True):
                if args == ["rev-parse", "--show-toplevel"]:
                    return MagicMock(stdout="/srv/somewhere-else")
                return MagicMock(stdout=repo_path, returncode=0)

            gh._run_git.side_effect = run_git
            return gh

        monkeypatch.setattr(
            "app.modules.workspace.autonomous.orchestrator.GitHubOps", factory
        )
        o = _make_orchestrator()
        before = _before_state(MAIN_BEFORE)
        with patch.object(
            AutonomousAgentRunner, "_is_cross_user", return_value=True
        ):
            err = o._validate_repo_context_after_run(before, system_account="dwu")
            assert "Agent escaped the workflow repository" in err
```

Add the import the tests need (top of file, with the other imports):

```python
from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/autonomous/test_repo_drift_validation.py::TestSharedCloneCrossUser -v`
Expected: `test_cross_user_agent_allows_nonbenign_main_drift` and `test_cross_user_agent_allows_even_when_probe_indeterminate` FAIL (they get the "Detected commits on the main repository" block instead of ""). The same-user and repo-root tests pass (existing behavior).

- [ ] **Step 3: Add the cross-user allow (minimal implementation)**

In `app/modules/workspace/autonomous/orchestrator.py`, inside the same-branch main-drift block, insert the cross-user short-circuit **before** the benign-pull probe. The block currently reads (starting at the `# main HEAD moved but the worktree did not.` comment):

```python
                # main HEAD moved but the worktree did not. This is either an
                # agent operating on the main repo, or an external `git pull`
                # moving HEAD to a remote commit during the agent run. Allow
                # only when the move is a forward update to a remote-sourced
                # commit (a benign pull); a local escape commit (not pushed),
                # a reset/rollback, or a non-fast-forward rewrite is blocked.
                if self._main_drift_is_benign_pull(
```

Insert immediately after that comment block and before `if self._main_drift_is_benign_pull(`:

```python
                # #3124: on multi-user hosts the agent runs under the cross-user
                # isolated launcher — a credentialless principal with a scoped
                # ACL to ONLY its worktree — and CANNOT touch the shared project
                # clone. So a main-HEAD move it could not have caused is external
                # (a sibling workflow's merge/prep or the developer). Attributing
                # it to the agent is a false positive; project clones are shared
                # across concurrent workflows. This mirrors the branch-switch
                # skip below and relies on the same isolation assumption. It
                # short-circuits before the benign-pull probe, which itself runs
                # git on the contended shared clone and can fail closed under
                # concurrency. Same-user (dev/macOS) mode has no isolation and
                # keeps the fail-closed probe.
                if AutonomousAgentRunner._is_cross_user(system_account):
                    logger.warning(
                        "Workflow %s: main repo HEAD moved %s..%s during agent run, "
                        "but the agent ran cross-user (isolated launcher; cannot touch "
                        "the shared project clone). Treating as external drift and allowing.",
                        self._workflow_id,
                        before_main.get("head", "")[:8],
                        after_main.get("head", "")[:8],
                    )
                    return ""
                # main HEAD moved but the worktree did not. This is either an
                # agent operating on the main repo, or an external `git pull`
                # moving HEAD to a remote commit during the agent run. Allow
                # only when the move is a forward update to a remote-sourced
                # commit (a benign pull); a local escape commit (not pushed),
                # a reset/rollback, or a non-fast-forward rewrite is blocked.
                if self._main_drift_is_benign_pull(
```

(This duplicates the existing comment so the benign-pull path keeps its rationale; the net effect is the new `if` block inserted above the existing comment. Implement it as: add the new comment + `if` + `return ""` directly before the existing `# main HEAD moved but the worktree did not.` comment.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/autonomous/test_repo_drift_validation.py -v`
Expected: PASS (all — the 4 new plus every pre-existing drift test unchanged, since they use `system_account=None` → `_is_cross_user(None)` is False → same-user path).

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py tests/autonomous/test_repo_drift_validation.py
git commit -m "fix(#3124): allow shared-clone main drift when agent ran cross-user (isolated)"
```

---

## Task 2: Mutation check (verify the guard clause bites)

- [ ] **Step 1: Mutation — force the cross-user check off**

Temporarily change `if AutonomousAgentRunner._is_cross_user(system_account):` to `if False:`.
Run: `python -m pytest tests/autonomous/test_repo_drift_validation.py::TestSharedCloneCrossUser -v`
Expected: FAIL — `test_cross_user_agent_allows_nonbenign_main_drift` and `test_cross_user_agent_allows_even_when_probe_indeterminate` block instead of allow. Restore; re-run → PASS.

(No commit — verification only. Confirm the tree matches Task 1's committed state afterward.)

---

## Self-Review notes

- **Spec coverage:** cross-user allow (Task 1 Step 3); tests for cross-user-allow (#2739 shape, incl. indeterminate probe), same-user-still-blocks, scoped-not-suppressing-other-violations (Task 1 Step 1); mutation (Task 2). All spec test-strategy items covered.
- **Type/name consistency:** predicate `AutonomousAgentRunner._is_cross_user(system_account)` matches the imported class and its staticmethod signature; tests patch it via `patch.object(AutonomousAgentRunner, "_is_cross_user", ...)`.
- **No behavior change for same-user:** every existing drift test uses `system_account=None` → `_is_cross_user` False → unchanged path.
- **Scope:** only the same-branch main-drift block changes; the auto-dev/*-branch-switch block and all worktree-integrity checks are untouched.

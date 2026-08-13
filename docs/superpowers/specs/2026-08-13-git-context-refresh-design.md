# Git Trusted-Context Refresh at Worktree Lifecycle Points

## Problem

`_verify_trusted_git_context` (github_ops.py:401) pins each repo's gitdir
identity (device:inode via `get_path_identity`) in a **class-level** registry
`_trusted_git_contexts` (github_ops.py:247), keyed by
`os.path.realpath(repo_path)`. This registry **persists across GitHubOps
instances and scheduler cycles**. Every `_run_git` (github_ops.py:701) and
`_run_gh` (github_ops.py:573) call invokes `_verify_trusted_git_context`, which
fails closed ("Protected Git directory identity changed after agent execution")
if the pinned identity differs from the current identity.

A prior `_run_agent` cycle pins the identity (orchestrator.py:6826
`register_trusted_git_context`). The registry **persists**, so the **next**
cycle's worktree lifecycle ops (git_workspace `ensure_worktree` / recreate, which
run **before** `_run_agent`) execute git ops (`main_gh._run_git` fetch /
show-ref / worktree-add, and `wt_gh.get_current_commit`) that verify against the
**stale prior-cycle identity**. When the gitdir identity changed between cycles
(worktree recreate, concurrent main-clone churn, #2505 self-heal
recreate-on-retry), the stale baseline mismatches and produces a false positive,
failing the workflow at preparation/planning.

**Prod impact**: 13 failures today (issues 2565 / 2572 / 2574-2577), blocking a
batch. Yesterday 0 failures (pre-#2505-deploy).

## Root Cause

The trusted-context registry is designed to survive only within a single agent
window (pin before agent, verify after agent). But because it is class-level, it
actually survives across scheduler cycles. Lifecycle ops that legitimately change
the gitdir (worktree recreate) run **before** the next `_run_agent` re-pins, so
they verify against a stale identity from a prior cycle that may no longer match.

## Approved Fix (Approach 1: Refresh at Lifecycle Points)

Add an orchestrator helper that re-snapshots a repo's **current** gitdir identity
and re-registers it, and call it at worktree lifecycle entry points so lifecycle
git ops verify against the current gitdir instead of a stale baseline.

### Helper: `_refresh_trusted_git_context`

**Location**: `orchestrator.py`, near `_capture_repo_state` (~line 2654).

```python
def _refresh_trusted_git_context(
    self, repo_path: str, system_account: str | None
) -> None:
    """Re-pin the trusted Git context for repo_path to its CURRENT gitdir
    identity. Called at worktree lifecycle points (ensure_worktree / recreate),
    which legitimately change the gitdir after the prior _run_agent pinned it.

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

**Implementation**: Reuses `_capture_repo_state(repo_path, system_account)` to get
`git_dir` / `git_identity` / `common_dir` / `common_identity`; if it returns a
usable `git_dir` + identities, calls
`GitHubOps.register_trusted_git_context(repo_path, git_dir, git_identity,
common_dir, common_identity)`. On any exception or missing fields, logs a debug
line and returns (does NOT raise — lifecycle ops must not fail because a refresh
couldn't run).

### Call Points: `git_workspace.py`

#### 1. At `ensure_worktree` entry (before any lifecycle `_run_git`)

Refresh the **main repo** (`project_path`) context — covers `main_gh` verifies
during recreate. Place **after** `main_gh` is created (line 192) and **before**
any `main_gh._run_git` call.

The `system_account` is already resolved at lines 185-191; reuse that local
variable.

```python
main_gh = _GitHubOps(project_path, system_account=system_account)
# Refresh the trusted Git context for the main repo so lifecycle ops
# (fetch/show-ref/worktree-add) verify against the CURRENT gitdir, not a
# stale baseline pinned by a prior _run_agent cycle. (#2565)
self._orch._refresh_trusted_git_context(project_path, system_account)
```

#### 2. After `worktree add` in the recreate flow

Refresh the **worktree** (canonical path) context — covers `wt_gh` verifies
(e.g. `get_current_commit` at line 332, and the branch-mismatch path's
`wt_gh.get_current_branch` at line 210).

**After line 324** (branch-survives `worktree add`):

```python
main_gh._run_git(["worktree", "add", canonical, branch_name])
self._orch._refresh_trusted_git_context(canonical, system_account)
```

**After line 349** (branch-gone `worktree add -b`):

```python
main_gh._run_git(
    ["worktree", "add", "-b", branch_name, canonical, head_sha]
)
self._orch._refresh_trusted_git_context(canonical, system_account)
```

**After line 259 and 276** (branch-mismatch recreate, both sub-paths):

```python
main_gh._run_git(["worktree", "add", canonical, expected_branch])
self._orch._refresh_trusted_git_context(canonical, system_account)
# ... and ...
main_gh._run_git(
    ["worktree", "add", "-b", expected_branch, canonical, head_sha]
)
self._orch._refresh_trusted_git_context(canonical, system_account)
```

### What does NOT change

- `_verify_trusted_git_context` — unchanged (still fail-closed on identity mismatch).
- `register_trusted_git_context` — unchanged (same validation / containment checks).
- `_run_git` / `_run_gh` — unchanged.
- Normal git / session semantics — unchanged.
- `_run_agent` pre/post pin logic — unchanged (still pins before agent, verifies after).

## Security Invariant

The guard's purpose: catch a sandboxed **agent** replacing the worktree `.git` to
redirect pushes. The agent runs ONLY inside `_run_agent`, which re-pins
(orchestrator.py:6826) **immediately before** the agent starts and verifies
**immediately after**. The new refresh ONLY re-pins to the orchestrator's OWN
current gitdir during **lifecycle ops (no agent running)**.

**Therefore**: agent-run tampering is still caught, because the pin is fresh
immediately before the agent runs. The refresh cannot be triggered by the agent
(it lives in the orchestrator's lifecycle code, executed between scheduler
cycles, never inside the sandbox).

### Guard Test

A `.git` replacement during the agent window (change the gitdir identity AFTER
the pre-agent pin, BEFORE the post-agent verify) is still detected:
`_verify_trusted_git_context` raises. This proves the invariant.

## Test Plan

All tests use real temp git repos / worktrees (not mocks of the identity logic)
so `get_path_identity` + the registry are actually exercised. Marked
`@pytest.mark.regression` + `@pytest.mark.issue(2565)`. Placed in `tests/unit/`.

1. **Helper unit test**: `_refresh_trusted_git_context` given a stale/old
   registered identity, after calling it the registry holds the CURRENT gitdir
   identity. RED before the helper exists.

2. **Behavioral false-positive test**: register a STALE trusted context for a
   repo, then exercise the refresh, assert it re-registers to current + proceeds
   instead of raising "directory identity changed".

3. **Security guard test**: an agent-window `.git` replacement (change the gitdir
   identity AFTER the pre-agent pin, BEFORE the post-agent verify) is still
   detected — `_verify_trusted_git_context` raises.

4. Run existing `github_ops` / `git_workspace` / `orchestrator` suites to confirm
   no regression.

## Scope Constraints

- Additive / gated only. No changes to normal git / session semantics.
- The launcher-layer variant (V1, `openace-run-as.sh` exit-68) is OUT OF SCOPE —
  a separate follow-up PR.
- Commit / PR text MUST NOT contain `closes|fixes|resolves` near an issue number.

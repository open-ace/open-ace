# Design: main-HEAD-drift guard must not blame a cross-user isolated agent for shared-clone drift

- **Issue:** #3124
- **Date:** 2026-08-26
- **Origin:** autonomous-workflow monitoring (class-2); workflow `10a24081` / #2739

## Problem

`_validate_repo_context_after_run` (`app/modules/workspace/autonomous/orchestrator.py`) snapshots the **project clone's** main HEAD before a local agent phase and re-checks it after. Its same-branch main-drift block fails the workflow with:

> "Detected commits on the main repository while the workflow worktree HEAD did not move; the agent likely executed git commands outside the workflow worktree."

when, during the agent run: the project clone's main HEAD moved · the clone stayed on the same branch · the workflow's **worktree HEAD did not move** · and `_main_drift_is_benign_pull` returned False (the move was not provably a forward update to an `origin/main` commit).

Project clones are **shared across many concurrent workflows** (and with the human developer). So that HEAD move is routinely caused by a sibling workflow's merge/prep or the developer — not the agent. The benign-pull probe runs its own `fetch origin main` + `merge-base` on the **same contended clone**; under concurrent sibling git activity (lock contention → git error → indeterminate → fail closed) or a stale/racing `origin/main` fetch (`after` not yet on the local `origin/main` ref), it cannot prove the move benign and blocks.

### Evidence (workflow `10a24081` / #2739, prod ai-lab, 2026-08-20)

- The agent was a **read-only plan-refine run** — system prompt: *"只进行分析、阅读代码、输出方案文本，不要修改任何文件或执行写操作"*. Across both agent sessions every tool call was Read/Grep/Glob inside its own worktree; **zero** `git commit/add/checkout/reset/pull`. It could not have moved main.
- `/home/dwu/open-ace-01/open-ace` is shared by **10 workflows**; at the exact failure second (`01:54:27`) three siblings — #2740, #2754, #2756 — were at `acceptance_verification` on the same clone.
- Only 2 workflows ever hit this guard (#2565, #2739), but every project clone is shared (68/41/30/29/25/22/10/…), so the whole fleet is susceptible to the timing race.

## Root cause

The guard assumes **single-tenant ownership** of the project clone, but the clone is **shared**, so an external main-HEAD move during the agent run trips a fail-closed guard that misattributes it to the agent.

## Chosen approach — allow the drift when the agent provably could not have caused it

The codebase **already relies on** this assumption in the adjacent branch-switch skip (orchestrator.py, the `else` branch of the branch-changed case):

> "On multi-user Linux hosts with the launcher, the agent runs as a credentialless principal and CANNOT touch the main repo, so this tradeoff is dev-host-only."

`AutonomousAgentRunner._is_cross_user(system_account)` (`agent_runner.py`) is the **authoritative predicate that decides how the agent was actually launched**: True ⇒ the agent ran under the cross-user isolated `openace-run-as` launcher (a credentialless principal with a scoped ACL to only its worktree) and cannot touch the shared main clone. It returns True when `system_account` is set and differs from the service-process user (and, fail-safe, True when the current user's passwd entry can't be resolved — the same default the launch path uses, so it stays consistent with how the agent was actually isolated).

**Fix:** in the same-branch main-drift block, before failing closed, if `AutonomousAgentRunner._is_cross_user(system_account)` is True, allow the drift and log a WARNING (external move; the isolated agent could not have caused it), mirroring the branch-switch skip. **Same-user mode is unchanged** — it keeps the current `_main_drift_is_benign_pull` probe and fail-closed behavior, because on a same-user host an agent escape is genuinely possible.

Because `_is_cross_user` is the same predicate the launcher used, matching the guard's allow-decision to it is semantically exact: if the agent was launched isolated, it demonstrably could not move main, so the drift is external by construction.

### Where the change goes

Exactly one place: the same-branch main-drift block. Currently:

```python
                if self._main_drift_is_benign_pull(...):
                    logger.info(... "benign external pull"); return ""
                return (
                    "Detected commits on the main repository while the workflow worktree "
                    "HEAD did not move; the agent likely executed git commands outside "
                    "the workflow worktree."
                )
```

becomes: keep the benign-pull allow; then, before the fail-closed `return`, add a cross-user allow that logs a WARNING and returns "" (no violation).

## Blast radius

- One added condition + WARNING log in one block. Same-user behavior byte-for-byte unchanged.
- The auto-dev/*-branch-switch block (the branch-*changed*-to-`auto-dev/*` case) is a stronger escape signal and is **not** #2739's path — left strict, out of scope.
- No dataclass/schema/migration changes.

## Alternatives rejected

- **Harden the benign-pull probe** (retry fetch, tolerate lock-contention git errors). More complex, does not fix the stale-`origin/main` sub-case, and still *guesses* attribution instead of using the definitive cross-user signal.
- **Skip the main-drift check entirely.** Removes real protection on same-user dev hosts where an escape is possible; the cross-user gate is strictly better (keeps that protection).

## Test strategy (TDD, `tests/unit/`, marked `issue(3124)` + `regression`)

Drive `_validate_repo_context_after_run` with a crafted `before_state` (worktree "effective" head unchanged; "main" head moved, same branch) and patch `_capture_repo_state` for the after-snapshots and `AutonomousAgentRunner._is_cross_user` for the mode.

1. **Cross-user isolated agent → allowed.** main HEAD moved + same branch + worktree HEAD unchanged + `_is_cross_user` True → returns "" (no violation), and `_main_drift_is_benign_pull` is **not** relied on (patch it to return False to prove the cross-user path wins). Mutation anchor: this is #2739.
2. **Same-user + non-benign move → still blocks.** `_is_cross_user` False + `_main_drift_is_benign_pull` False → returns the "Detected commits on the main repository…" violation (fail-closed preserved).
3. **Benign pull still allowed** regardless of mode (`_main_drift_is_benign_pull` True → "").
4. **Regression:** an actual worktree-integrity violation (e.g. branch changed) still returns its violation — the cross-user allow only covers the main-drift block, not the other checks.

## Deployment & rollout

Pure logic change; no migration/schema. The guard runs in the scheduler (`openace-scheduler.service`) — deploy = hot-patch `orchestrator.py`, restart that unit. #2739's issue is **CLOSED** (resolved elsewhere), so **no workflow reset is needed**; the fix prevents future recurrences fleet-wide.

# Autonomous Phase Contracts

> Issue #2044 (Phase A). This document is the contract specification for every
> workflow phase in `AutonomousOrchestrator`. It is the authoritative source for
> "when a phase commits, and with what result." Code must match this document;
> changes here require updating the orchestrator and the characterization tests
> in `tests/autonomous/test_orchestrator_characterization.py`.

## Purpose

`AutonomousOrchestrator` (9.6k lines) historically let each `_do_*` phase method
mutate `current_phase` / `status` inline via `_update_workflow`. That made it
possible for a phase to **partially succeed and then write the next phase's
state** before its own side effects were confirmed — the core hazard #2044
targets.

Phase A introduces a contract without moving the phase methods:

- **`WorkflowContext`** — the read-only snapshot a phase receives.
- **`PhaseResult`** — the structured outcome a phase returns.
- **`_commit_phase_result`** — the single authoritative path for phase/status
  transition. Only `outcome="completed"` may advance `current_phase`.

Legacy `_do_*` methods still return `None` and commit inline (unchanged
behaviour). `advance()` routes through `_dispatch_phase`, which forwards any
`PhaseResult` to the commit entrypoint and leaves `None` returns on the legacy
path. Phases migrate one at a time; Phase B (after #2022/#2043 land) will move
the complex ones into handlers.

## Contract fields

Every phase documents these eight properties:

| Field | Meaning |
|---|---|
| **preconditions** | Workflow/DB/git state that must hold before the phase runs. |
| **inputs** | Fields the phase reads from the workflow dict. |
| **authoritative evidence** | The milestone(s) whose status/state decides the outcome. |
| **side effects** | External mutations: git, GitHub API, agent subprocess, DB writes. |
| **postconditions** | State guaranteed after a successful run. |
| **re-entry point** | What a restart resumes from (persisted `current_phase` + milestones). |
| **recovery behavior** | What happens on retry / restart / shutdown mid-phase. |
| **terminal outcomes** | The `PhaseResult` outcomes this phase can produce. |

## Phase ordering

```
preparation → planning → development → pr_review → report → merge
                                                              ↓
                                                        (completed)
```

`PHASE_ORDER` and `PHASE_STATUS_MAP` (in `orchestrator.py`) are the canonical
transition table. `_next_phase("merge") == "merge"` (terminal);
`_next_phase(<unknown>) == "planning"` (recovery default). The commit entrypoint
rejects any `next_phase` not in `PHASE_ORDER`.

---

## preparation

Creates the GitHub repo (new project), the issue, the branch, and the worktree.
The only phase that creates the worktree, so `advance()` skips the worktree
self-heal for it.

- **preconditions**: `current_phase == "preparation"`, `status == "preparing"`.
  No worktree exists yet (the main repo is the only valid repo_path).
- **inputs**: `project_path`, `is_new_project`, `requirements_text` /
  `requirements_issue_url`, `branch_strategy`, `parent_workflow_id` (fork).
- **authoritative evidence**: `branch_created` milestone
  (`status == "completed"`). For new projects also `repo_setup` and
  `issue_created`.
- **side effects**: `gh.create_repo`, `gh.create_issue`, `gh.create_worktree` /
  `gh.add_worktree`, DB milestone/workflow writes.
- **postconditions**: `worktree_path`, `branch_name`, `branch_strategy` set;
  `current_phase == "planning"`, `status == "planning"`.
- **re-entry point**: `current_phase == "preparation"`. The fork fast-path probes
  for a surviving branch (local or remote) and attaches via `add_worktree` rather
  than recreating, so a partial prior attempt is idempotent (#814).
- **recovery behavior**: a `branch_created` milestone with `status == "failed"`
  is recorded before re-raising; `advance()` marks the workflow failed (or
  transient-retries on a network error).
- **terminal outcomes**: `completed` → planning; `failed` on git/GitHub error.

## planning

Runs plan-then-review rounds until the plan is approved or `max_plan_rounds` is
hit. Uses the **main session line** with read-only tools and a capped timeout.

- **preconditions**: worktree exists (`advance()` self-heals it first);
  `current_phase == "planning"`.
- **inputs**: `requirements_text`, `current_round`, `max_plan_rounds`,
  `github_issue_number`, prior `plan_created`/`plan_refined`/`plan_reviewed`
  milestones, `user_feedback`.
- **authoritative evidence**: `plan_finalized` milestone (approved) or
  exhaustion of `max_plan_rounds`. Per-round: `plan_created`/`plan_refined` +
  `plan_reviewed` milestones.
- **side effects**: two agent runs per round (plan, review); issue comment with
  the plan; clears `user_feedback` after injecting it into the prompt.
- **postconditions**: on approval `current_phase == "development"`,
  `status == "developing"`, `current_round == 0`. On timeout
  `status == "planning_timeout"` (user can extend). On other failure
  `status == "failed"`.
- **re-entry point**: `current_round` is persisted; restart resumes the next
  round. `round_num` is derived from `current_round + 1`, never from memory.
- **recovery behavior**: transient errors retry via `advance()`; a `WorkflowPaused`
  (shutdown) leaves status untouched.
- **terminal outcomes**: `completed` → development; `pause` on planning timeout
  (planning_timeout status); `failed` on plan failure.

## development

Implements the change and runs targeted tests. Drives the **main session line**.
Loops on test failure up to `MAX_DEV_RETRIES_ON_TEST_FAIL`, with optional CI
repair and re-development.

- **preconditions**: worktree exists and is on `branch_name`; an approved plan
  exists (`plan_finalized` milestone); `current_phase == "development"`.
- **inputs**: finalized plan content, `dev_round`, retry counters
  (`test_retries`, `dev_retries_on_test_fail`, `skip_retries`), test milestone
  evidence.
- **authoritative evidence**: `dev_completed` milestone + test result milestone
  (`tests_run` with `result_summary`). Tests are judged by structured command
  evidence (#2046), not free-text heuristics.
- **side effects**: agent run (writes code), test execution, CI repair agent run,
  branch push, issue comments.
- **postconditions**: on success `current_phase == "pr_review"`,
  `status == "pr_review"`, `current_round == 0`, retry counters cleared. On
  unrecoverable test failure `status == "failed"`.
- **re-entry point**: retry counters and `dev_round` are persisted; restart
  re-enters at the same development round. A re-entry guard prevents
  double-launching development after a process restart.
- **recovery behavior**: test failure → dev retry (up to limit) → CI repair →
  hard failure. Shutdown raises `WorkflowPaused`.
- **terminal outcomes**: `completed` → pr_review; `failed` on unrecoverable test
  failure; `retry` within the loop.

## pr_review

Creates/updates the PR and runs independent code-review rounds. Uses the
**review session line**. Loops until review passes or `max_pr_review_rounds`.

- **preconditions**: development completed (`dev_completed` milestone); branch
  pushed; `current_phase == "pr_review"`.
- **inputs**: `github_pr_number`, `current_round`, `max_pr_review_rounds`,
  `require_full_review_rounds`, PR diff.
- **authoritative evidence**: `pr_reviewed` milestones; the structured approval
  verdict (`_derive_review_passed`). CI status via the external evidence layer
  (#2045).
- **side effects**: `gh.create_pull_request` / update, review agent run, CI
  polling, issue comments.
- **postconditions**: on approval `current_phase == "report"`,
  `status == "reporting"`. On exhaustion of review rounds → back to development
  (`current_phase == "development"`) for another dev round.
- **re-entry point**: `current_round` persisted; the round_num guard covers both
  re-entry and process-restart resume. Existing PR is updated, not recreated.
- **recovery behavior**: CI pending → wait + poll; CI failure → CI repair
  (re-enters pr_review); review failure → development. Shutdown via
  `WorkflowPaused`.
- **terminal outcomes**: `completed` → report; `completed` → development (review
  failed, new dev round); `failed` on merge/PR error.

## report

Generates the structured progress report and posts it. Thin phase: no agent run,
no git mutation. Reads milestone evidence and renders i18n.

- **preconditions**: `current_phase == "report"`; planning + development +
  pr_review milestones exist.
- **inputs**: `dev_round`, finalized plan, diff stats, test summary, review
  milestones, `content_language`.
- **authoritative evidence**: `progress_reported` + `round_completed` +
  `wait_started` milestones.
- **side effects**: GitHub issue comment (rendered in `content_language`);
  structured `metadata.report` payload (single source of truth, rendered per-
  viewer by the frontend). **No git mutation.**
- **postconditions**: `current_phase == "wait"`, `status == "waiting"`;
  `wait_started_at` recorded for comment filtering.
- **re-entry point**: idempotent — milestones are deduplicated; re-running just
  re-emits the report.
- **recovery behavior**: GitHub comment failure is non-fatal (best-effort post).
- **terminal outcomes**: `wait` (parks in `waiting`, does not advance to merge).

## wait

Polls for new requirements or a completion signal. **Must not mutate the git
working tree** — the scheduler's waiting-bypass assumes this phase only touches
DB/API state.

- **preconditions**: `current_phase == "wait"`, `status == "waiting"`.
- **inputs**: `user_feedback` (from cancel-with-feedback), `auto_merge`,
  `github_pr_number`, `github_issue_number`.
- **authoritative evidence**: `requirement_received` milestone (new feedback);
  absence of new comments (stay waiting).
- **side effects**: GitHub issue comment polling. **No git/agent work.**
- **postconditions**: three branches —
  1. `user_feedback` present → resume from the cancelled milestone's phase,
     `dev_round += 1` (typically back to `development`).
  2. `auto_merge` + PR exists → `current_phase == "merge"`, `status == "merging"`.
  3. Otherwise stay in `waiting` (poll again next cycle).
- **re-entry point**: scheduler re-enters `_do_wait` every cycle (~10s) while
  `status == waiting`. No in-memory state required.
- **recovery behavior**: no failure path; transient GitHub errors retry via
  `advance()`.
- **terminal outcomes**: `completed` → merge (auto_merge); `completed` →
  cancelled_phase (feedback); otherwise stays `wait`.

## merge

Synchronizes the base, resolves conflicts (with the SIGKILL-resilient worktree
transition from #2050), merges the PR, and cleans up. May fork a conflict-
resolution sub-workflow. Uses the **test session line** for conflict resolution.

- **preconditions**: `current_phase == "merge"`; PR exists and is mergeable;
  `current_phase == "merge"`, `status == "merging"`.
- **inputs**: `github_pr_number`, `branch_name`, `worktree_transition_state`
  (mid-flight conflict transition), CI status.
- **authoritative evidence**: `merge_completed` / `merge_failed` milestones;
  conflict-resolution fork milestone.
- **side effects**: `gh.merge_pull_request`, base sync, conflict worktree
  create/remove, branch/worktree cleanup, `completed_at`.
- **postconditions**: on success `status == "completed"`,
  `current_phase == "merge"` (terminal), `completed_at` set. On conflict → fork
  a sub-workflow and pause the parent. On unrecoverable conflict →
  `status == "failed"`.
- **re-entry point**: `worktree_transition_state` is persisted; a SIGKILLed
  transition is reconciled at the top of `advance()` before any phase runs
  (#2050), so a restart never falls back to the main checkout. Cleanup retries
  are tracked separately (#2043).
- **recovery behavior**: conflict → fork → parent paused → child resolves →
  parent resumes merge. Reconciliation fail-closes (status=failed) rather than
  running a phase against the wrong checkout.
- **terminal outcomes**: `completed` → terminal (workflow completed); `pause`
  (conflict fork); `failed` on unrecoverable conflict/merge error.

---

## Migration status (Phase A)

All phases remain on the **legacy path** (`_do_*` returns `None`, commits
inline). The contract and commit entrypoint are in place and tested but no
production phase has been migrated yet. Migration order (Phase B, after #2022
and #2043 land):

1. Thin phases first — `report`, `wait` — to validate the contract end-to-end.
2. `merge` and `pr_review` (the complex ones) once their dependency services are
   wired.
3. `development`, `planning`, `preparation` last.

A phase is migrated when it returns a `PhaseResult` and contains **no direct
`_update_workflow({"current_phase": ...})` / `_create_milestone` calls** — all
state flows through `_commit_phase_result`.

# Autonomous Phase Contracts — 自主阶段契约

[English](#english) | [中文](#中文)

---

## English

> Issue #2044 (Phase A). This document is the contract specification for every
> workflow phase in `AutonomousOrchestrator`. It is the authoritative source for
> "when a phase commits, and with what result." Code must match this document;
> changes here require updating the orchestrator and the characterization tests
> in `tests/unit/test_orchestrator_characterization.py`.

## Purpose

`AutonomousOrchestrator` (13k lines) historically let each `_do_*` phase method
mutate `current_phase` / `status` inline via `_update_workflow`. That made it
possible for a phase to **partially succeed and then write the next phase's
state** before its own side effects were confirmed — the core hazard #2044
targets.

Phase A introduces a contract without moving the phase methods:

- **`WorkflowContext`** — the read-only snapshot a phase receives.
- **`PhaseResult`** — the structured outcome a phase returns.
- **`_commit_phase_result`** — the single authoritative path for phase/status
  transition. Only `outcome="completed"` may advance `current_phase`.

All eight phases now return a `PhaseResult` committed through
`_commit_phase_result`; no phase commits inline. Four live in `phases/*.py`
behind the `PHASE_HANDLERS` registry (development, pr_review, merge,
acceptance_verification — resolved via `resolve_phase_handler`); the other four
(preparation, planning, report, wait) remain `_do_*` methods in the
orchestrator but already sit on the `(ctx, deps) -> PhaseResult` contract. The
`_legacy(...)` wrapper branches in the dispatcher are defensive fallbacks only
and never fire in practice.

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
                                                    acceptance_verification
                                                                ↓
                                                          (completed)
```

`PHASE_ORDER` and `PHASE_STATUS_MAP` (in `orchestrator.py`) are the canonical
transition table. merge's successor is `acceptance_verification` (#2335);
`_next_phase("acceptance_verification") == "acceptance_verification"`
(terminal); `_next_phase(<unknown>) == "planning"` (recovery default). The
commit entrypoint rejects any `next_phase` outside `PHASE_ORDER` and the two
pseudo-phases it also admits (`wait`, `completed`).

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
resolution sub-workflow. Uses a throwaway `fresh` session for conflict
resolution.

- **preconditions**: `current_phase == "merge"`, `status == "merging"`; PR
  exists and is mergeable.
- **inputs**: `github_pr_number`, `branch_name`, `worktree_transition_state`
  (mid-flight conflict transition), CI status.
- **authoritative evidence**: `merge_completed` / `merge_failed` milestones;
  conflict-resolution fork milestone.
- **side effects**: `gh.merge_pull_request`, base sync, conflict worktree
  create/remove, branch/worktree cleanup.
- **postconditions**: on success `current_phase == "acceptance_verification"`,
  `status == "verification_pending"`. The workflow reaches `completed` only
  from the verification side (confirmed settle, human override, or the phase
  being disabled); `completed_at` is written by the confirmed settle patch, the
  override route, or the unified commit of the `completed` pseudo-phase — not
  by merge itself. On conflict → fork a sub-workflow and pause the parent. On
  unrecoverable conflict → `status == "failed"`.
- **re-entry point**: `worktree_transition_state` is persisted; a SIGKILLed
  transition is reconciled at the top of `advance()` before any phase runs
  (#2050), so a restart never falls back to the main checkout. Cleanup retries
  are tracked separately (#2043).
- **recovery behavior**: conflict → fork → parent paused → child resolves →
  parent resumes merge. Reconciliation fail-closes (status=failed) rather than
  running a phase against the wrong checkout.
- **terminal outcomes**: `completed` → acceptance_verification (merge success;
  the terminal `completed` status is reached from the verification side);
  `pause` (conflict fork); `failed` on unrecoverable conflict/merge error.

> **phase_change emit contract**: `_commit_phase_result` does **not** emit
> `phase_change` events. The migrated handlers emit their own via
> `deps.host.emit_phase_change`; merge's success tail emits
> `phase_change{"phase":"completed"}` before returning
> `next_phase="acceptance_verification"`, preserving the legacy event stream
> for UI consumers.

---

## acceptance_verification

Independent post-merge verification (#2335, `phases/acceptance_verification.py`).
Runs a credentialless read-only verifier on the merged main SHA, applies
deterministic mechanical gates plus per-item verdict aggregation, and only a
`confirmed` verdict closes the issue. When disabled
(`autonomous.acceptance_verification_enabled=false`) the handler completes
immediately without running the verifier.

- **preconditions**: `current_phase == "acceptance_verification"`, `status ==
  "verification_pending"`; the PR is merged so a merge SHA resolves.
- **inputs**: merge SHA (`verification_merge_sha`, resolved from the PR's
  merge commit when absent), base SHA (`base_commit_sha`), the issue's
  acceptance snapshot (`issue_acceptance_hash`), `verification_status`,
  `verification_attempt`, prior `acceptance_verification` milestones.
- **authoritative evidence**: the `acceptance_verification` milestone — minted
  `in_progress` ("Acceptance verification: running") at verifier start on the
  `verification` session line, settled in place with the verdict (#3003).
- **side effects**: verifier agent run (read-only tools, temporary merged-main
  checkout on the `verification` session line), issue report comment, issue
  close on `confirmed`, usage writes to the running acceptance row
  (`prior_usage` baseline + in-run deltas), `milestone_updated` events;
  human override via the `verification_override` route records the human
  identity in `verified_by`.
- **postconditions**: `confirmed` → `status == "completed"`, issue closed,
  `completed_at` set. `rejected` / `indeterminate` → `paused` for human review
  (delivered code is never marked failed). Infrastructure failures retry in
  the same phase, up to 3 attempts (deterministic parse failures capped at 2,
  #2867), then pause.
- **re-entry point**: deduplicated on `(merge_sha, issue_acceptance_hash)`;
  a settled `confirmed` result is a terminal no-op. A new merge SHA or an
  edited issue re-runs the verifier naturally. The early milestone row is
  reused for the same attempt and older `in_progress` rows are swept to
  failed("interrupted"); a quota pause mid-verification terminalizes the row
  only after writing its burned usage.
- **recovery behavior**: infrastructure failure → same-phase retry; verifier
  quota pause → workflow `paused` (resumable); shutdown → the row is
  terminalized to `cancelled` and resurrected by the same attempt on resume
  (a hard kill leaves it `in_progress`, also reused).
- **terminal outcomes**: `completed` (confirmed verdict or phase disabled);
  `pause` (rejected / indeterminate / retries exhausted); `retry`
  (infrastructure failure under the cap).

---

## Migration status

All eight phases commit through `_commit_phase_result`; none remains on the
legacy inline-commit path.

- `phases/*.py` + `PHASE_HANDLERS` registry (resolved via
  `resolve_phase_handler`): `development`, `pr_review`, `merge`,
  `acceptance_verification`. The dispatcher's `_legacy(self._do_*)` branches
  for these are defensive fallbacks only.
- Contract-direct `_do_*` methods in the orchestrator (#2044 T6–T9):
  `preparation`, `planning`, `report`, `wait`.

A phase is migrated when it returns a `PhaseResult` and contains **no direct
`_update_workflow({"current_phase": ...})` / `_create_milestone` calls** — all
state flows through `_commit_phase_result`.

---

## 中文

以下为中文参考翻译，权威版本以 English 节为准 / The English section above is authoritative.

### 目的（Purpose）

> Issue #2044（Phase A）。本文档是 `AutonomousOrchestrator` 中每个工作流阶段的契约规范，
> 是"阶段何时提交、以何种结果提交"的权威来源。代码必须与本文档一致；此处的变更需要
> 同步更新 orchestrator 以及 `tests/unit/test_orchestrator_characterization.py`
> 中的特征化测试。

`AutonomousOrchestrator`（约 13k 行）历史上允许每个 `_do_*` 阶段方法通过
`_update_workflow` 内联修改 `current_phase` / `status`。这使得一个阶段可能在自身
副作用尚未确认之前就**部分成功并写入下一阶段的状态**——这正是 #2044 要解决的
核心风险。

Phase A 在不移动阶段方法的前提下引入了一套契约：

- **`WorkflowContext`** —— 阶段接收的只读快照。
- **`PhaseResult`** —— 阶段返回的结构化结果。
- **`_commit_phase_result`** —— 阶段/状态迁移的唯一权威路径。只有
  `outcome="completed"` 才能推进 `current_phase`。

全部八个阶段现在都返回经由 `_commit_phase_result` 提交的 `PhaseResult`；没有任何
阶段再内联提交。其中四个位于 `phases/*.py`、挂在 `PHASE_HANDLERS` 注册表之后
（development、pr_review、merge、acceptance_verification —— 通过
`resolve_phase_handler` 解析）；另外四个（preparation、planning、report、wait）
仍是 orchestrator 中的 `_do_*` 方法，但已经遵循 `(ctx, deps) -> PhaseResult`
契约。调度器中的 `_legacy(...)` 包装分支仅作为防御性兜底，实际永远不会触发。

### 契约字段（Contract fields）

每个阶段都记录以下八个属性：

| 字段 | 含义 |
|---|---|
| **preconditions**（前置条件） | 阶段运行前必须成立的工作流/DB/git 状态。 |
| **inputs**（输入） | 阶段从工作流 dict 中读取的字段。 |
| **authoritative evidence**（权威证据） | 其状态决定结果的里程碑（milestone）。 |
| **side effects**（副作用） | 外部变更：git、GitHub API、agent 子进程、DB 写入。 |
| **postconditions**（后置条件） | 成功运行后保证的状态。 |
| **re-entry point**（重入点） | 重启时从何处恢复（持久化的 `current_phase` + 里程碑）。 |
| **recovery behavior**（恢复行为） | 阶段中途重试/重启/关机时的行为。 |
| **terminal outcomes**（终态结果） | 本阶段可能产生的 `PhaseResult` 结果。 |

### 阶段顺序（Phase ordering）

```
preparation → planning → development → pr_review → report → merge
                                                                ↓
                                                    acceptance_verification
                                                                ↓
                                                          (completed)
```

`PHASE_ORDER` 与 `PHASE_STATUS_MAP`（位于 `orchestrator.py`）是权威迁移表。
merge 的后继阶段是 `acceptance_verification`（#2335）；
`_next_phase("acceptance_verification") == "acceptance_verification"`
（终态）；`_next_phase(<unknown>) == "planning"`（恢复默认值）。提交入口会拒绝
任何不在 `PHASE_ORDER` 及其同样放行的两个伪 phase（`wait`、`completed`）之外的
`next_phase`。

---

### 准备阶段（preparation）

创建 GitHub 仓库（新项目）、issue、分支和 worktree。它是唯一创建 worktree 的
阶段，因此 `advance()` 会对它跳过 worktree 自愈。

- **preconditions**（前置条件）：`current_phase == "preparation"`、
  `status == "preparing"`。此时尚不存在 worktree（主仓库是唯一有效的
  repo_path）。
- **inputs**（输入）：`project_path`、`is_new_project`、`requirements_text` /
  `requirements_issue_url`、`branch_strategy`、`parent_workflow_id`（fork）。
- **authoritative evidence**（权威证据）：`branch_created` 里程碑
  （`status == "completed"`）。新项目还包括 `repo_setup` 与 `issue_created`。
- **side effects**（副作用）：`gh.create_repo`、`gh.create_issue`、
  `gh.create_worktree` / `gh.add_worktree`，DB 里程碑/工作流写入。
- **postconditions**（后置条件）：`worktree_path`、`branch_name`、
  `branch_strategy` 已设置；`current_phase == "planning"`、
  `status == "planning"`。
- **re-entry point**（重入点）：`current_phase == "preparation"`。fork 快速路径
  会探测是否有存活的分支（本地或远端），并通过 `add_worktree` 重新挂载而非重建，
  因此此前不完整的尝试是幂等的（#814）。
- **recovery behavior**（恢复行为）：重新抛出异常前会记录 `status == "failed"`
  的 `branch_created` 里程碑；`advance()` 将工作流标记为失败（网络错误则做
  临时性重试）。
- **terminal outcomes**（终态结果）：`completed` → planning；git/GitHub 错误时
  `failed`。

### 规划阶段（planning）

运行"先规划后审查"的轮次，直到计划获批或达到 `max_plan_rounds`。使用**主会话线
（main session line）**，只读工具且带超时上限。

- **preconditions**（前置条件）：worktree 已存在（`advance()` 会先对其自愈）；
  `current_phase == "planning"`。
- **inputs**（输入）：`requirements_text`、`current_round`、`max_plan_rounds`、
  `github_issue_number`、此前的 `plan_created`/`plan_refined`/`plan_reviewed`
  里程碑、`user_feedback`。
- **authoritative evidence**（权威证据）：`plan_finalized` 里程碑（获批）或
  `max_plan_rounds` 耗尽。每轮：`plan_created`/`plan_refined` + `plan_reviewed`
  里程碑。
- **side effects**（副作用）：每轮两次 agent 运行（规划、审查）；附带计划的
  issue 评论；将 `user_feedback` 注入提示词后清除。
- **postconditions**（后置条件）：获批时 `current_phase == "development"`、
  `status == "developing"`、`current_round == 0`。超时时
  `status == "planning_timeout"`（用户可延长）。其他失败时
  `status == "failed"`。
- **re-entry point**（重入点）：`current_round` 已持久化；重启后从下一轮恢复。
  `round_num` 由 `current_round + 1` 推导，绝不依赖内存。
- **recovery behavior**（恢复行为）：临时性错误通过 `advance()` 重试；
  `WorkflowPaused`（关机）不改动状态。
- **terminal outcomes**（终态结果）：`completed` → development；规划超时时
  `pause`（planning_timeout 状态）；规划失败时 `failed`。

### 开发阶段（development）

实现变更并运行针对性测试。驱动**主会话线（main session line）**。测试失败时最多
循环 `MAX_DEV_RETRIES_ON_TEST_FAIL` 次，并可选用 CI 修复与重新开发。

- **preconditions**（前置条件）：worktree 存在且位于 `branch_name` 上；已存在
  获批计划（`plan_finalized` 里程碑）；`current_phase == "development"`。
- **inputs**（输入）：定稿计划内容、`dev_round`、重试计数器（`test_retries`、
  `dev_retries_on_test_fail`、`skip_retries`）、测试里程碑证据。
- **authoritative evidence**（权威证据）：`dev_completed` 里程碑 + 测试结果
  里程碑（带 `result_summary` 的 `tests_run`）。测试由结构化命令证据判定
  （#2046），而非自由文本启发式。
- **side effects**（副作用）：agent 运行（写代码）、测试执行、CI 修复 agent
  运行、分支推送、issue 评论。
- **postconditions**（后置条件）：成功时 `current_phase == "pr_review"`、
  `status == "pr_review"`、`current_round == 0`、重试计数器清零。不可恢复的
  测试失败时 `status == "failed"`。
- **re-entry point**（重入点）：重试计数器与 `dev_round` 已持久化；重启后在
  同一开发轮次重入。重入防护避免进程重启后重复启动开发。
- **recovery behavior**（恢复行为）：测试失败 → 开发重试（达上限为止）→
  CI 修复 → 硬失败。关机时抛出 `WorkflowPaused`。
- **terminal outcomes**（终态结果）：`completed` → pr_review；不可恢复的测试
  失败时 `failed`；循环内为 `retry`。

### PR 审查阶段（pr_review）

创建/更新 PR 并运行独立的代码审查轮次。使用**审查会话线（review session
line）**。循环直到审查通过或达到 `max_pr_review_rounds`。

- **preconditions**（前置条件）：开发已完成（`dev_completed` 里程碑）；分支已
  推送；`current_phase == "pr_review"`。
- **inputs**（输入）：`github_pr_number`、`current_round`、
  `max_pr_review_rounds`、`require_full_review_rounds`、PR diff。
- **authoritative evidence**（权威证据）：`pr_reviewed` 里程碑；结构化批准
  判定（`_derive_review_passed`）。CI 状态经由外部证据层（#2045）。
- **side effects**（副作用）：`gh.create_pull_request` / 更新、审查 agent
  运行、CI 轮询、issue 评论。
- **postconditions**（后置条件）：批准时 `current_phase == "report"`、
  `status == "reporting"`。审查轮次耗尽 → 退回开发
  （`current_phase == "development"`）进行新一轮开发。
- **re-entry point**（重入点）：`current_round` 已持久化；round_num 防护同时
  覆盖重入与进程重启后的恢复。已存在的 PR 会被更新而非重建。
- **recovery behavior**（恢复行为）：CI pending → 等待并轮询；CI 失败 →
  CI 修复（重入 pr_review）；审查失败 → 回到 development。关机经由
  `WorkflowPaused`。
- **terminal outcomes**（终态结果）：`completed` → report；`completed` →
  development（审查失败，新一轮开发）；merge/PR 错误时 `failed`。

### 报告阶段（report）

生成结构化进度报告并发布。轻量阶段：无 agent 运行、无 git 变更。读取里程碑
证据并渲染 i18n。

- **preconditions**（前置条件）：`current_phase == "report"`；planning +
  development + pr_review 里程碑已存在。
- **inputs**（输入）：`dev_round`、定稿计划、diff 统计、测试摘要、审查里程碑、
  `content_language`。
- **authoritative evidence**（权威证据）：`progress_reported` +
  `round_completed` + `wait_started` 里程碑。
- **side effects**（副作用）：GitHub issue 评论（以 `content_language` 渲染）；
  结构化 `metadata.report` 载荷（唯一事实源，由前端按查看者渲染）。**无 git
  变更。**
- **postconditions**（后置条件）：`current_phase == "wait"`、
  `status == "waiting"`；记录 `wait_started_at` 用于评论过滤。
- **re-entry point**（重入点）：幂等 —— 里程碑去重；重跑只会重新发布报告。
- **recovery behavior**（恢复行为）：GitHub 评论失败为非致命（尽力发布）。
- **terminal outcomes**（终态结果）：`wait`（停驻在 waiting，不推进到
  merge）。

### 等待阶段（wait）

轮询新需求或完成信号。**绝不能改动 git 工作树** —— 调度器的 waiting-bypass
假定本阶段只触碰 DB/API 状态。

- **preconditions**（前置条件）：`current_phase == "wait"`、
  `status == "waiting"`。
- **inputs**（输入）：`user_feedback`（来自 cancel-with-feedback）、
  `auto_merge`、`github_pr_number`、`github_issue_number`。
- **authoritative evidence**（权威证据）：`requirement_received` 里程碑
  （新反馈）；无新评论（保持 waiting）。
- **side effects**（副作用）：GitHub issue 评论轮询。**无 git/agent 操作。**
- **postconditions**（后置条件）：三条分支 ——
  1. 存在 `user_feedback` → 从被取消里程碑所属的阶段恢复，`dev_round += 1`
     （通常回到 `development`）。
  2. `auto_merge` 且 PR 存在 → `current_phase == "merge"`、
     `status == "merging"`。
  3. 否则保持 `waiting`（下一周期再轮询）。
- **re-entry point**（重入点）：只要 `status == waiting`，调度器每个周期
  （约 10 秒）重入 `_do_wait`。不需要任何内存态。
- **recovery behavior**（恢复行为）：没有失败路径；临时性 GitHub 错误经
  `advance()` 重试。
- **terminal outcomes**（终态结果）：`completed` → merge（auto_merge）；
  `completed` → cancelled_phase（反馈）；否则保持 `wait`。

### 合并阶段（merge）

同步基线、解决冲突（含 #2050 的 SIGKILL 韧性 worktree 迁移）、合并 PR 并
清理。可能 fork 出一个冲突解决子工作流。冲突解决使用一次性 `fresh` 会话。

- **preconditions**（前置条件）：`current_phase == "merge"`、
  `status == "merging"`；PR 存在且可合并。
- **inputs**（输入）：`github_pr_number`、`branch_name`、
  `worktree_transition_state`（进行中的冲突迁移）、CI 状态。
- **authoritative evidence**（权威证据）：`merge_completed` / `merge_failed`
  里程碑；冲突解决 fork 里程碑。
- **side effects**（副作用）：`gh.merge_pull_request`、基线同步、冲突
  worktree 创建/移除、分支/worktree 清理。
- **postconditions**（后置条件）：成功时
  `current_phase == "acceptance_verification"`、
  `status == "verification_pending"`。工作流只能从验证侧到达 `completed`
  （确认性落账、人工覆盖或该阶段被禁用）；`completed_at` 由确认性落账补丁、
  覆盖路径或 `completed` 伪 phase 的统一提交写入 —— 而非由 merge 本身写入。
  冲突 → fork 子工作流并暂停父工作流。不可恢复的冲突 →
  `status == "failed"`。
- **re-entry point**（重入点）：`worktree_transition_state` 已持久化；被
  SIGKILL 打断的迁移会在任何阶段运行之前于 `advance()` 顶部完成对账
  （#2050），因此重启绝不会回退到主检出。清理重试单独跟踪（#2043）。
- **recovery behavior**（恢复行为）：冲突 → fork → 父工作流暂停 → 子工作流
  解决 → 父工作流恢复 merge。对账失败会 fail-close（status=failed），而不是
  在错误的检出上运行阶段。
- **terminal outcomes**（终态结果）：`completed` → acceptance_verification
  （合并成功；终态 `completed` 状态从验证侧到达）；`pause`（冲突 fork）；
  不可恢复的冲突/合并错误时 `failed`。

> **phase_change 发射契约**：`_commit_phase_result` **不**发射 `phase_change`
> 事件。已迁移的 handler 通过 `deps.host.emit_phase_change` 自行发射；merge
> 的成功尾部在返回 `next_phase="acceptance_verification"` 之前发射
> `phase_change{"phase":"completed"}`，为 UI 消费者保留遗留事件流。

---

### 验收验证阶段（acceptance_verification）

独立的合并后验证（#2335，`phases/acceptance_verification.py`）。在已合并的
main SHA 上运行无凭据只读验证器，应用确定性的机械门禁加上逐项判定聚合，只有
`confirmed` 判定才会关闭 issue。禁用时
（`autonomous.acceptance_verification_enabled=false`）handler 立即完成，不
运行验证器。

- **preconditions**（前置条件）：`current_phase ==
  "acceptance_verification"`、`status == "verification_pending"`；PR 已合并，
  因此 merge SHA 可解析。
- **inputs**（输入）：merge SHA（`verification_merge_sha`，缺失时从 PR 的
  merge commit 解析）、基线 SHA（`base_commit_sha`）、issue 的验收快照
  （`issue_acceptance_hash`）、`verification_status`、`verification_attempt`、
  此前的 `acceptance_verification` 里程碑。
- **authoritative evidence**（权威证据）：`acceptance_verification` 里程碑
  —— 验证器启动时在 `verification` 会话线上以 `in_progress`
  （"Acceptance verification: running"）铸造，并就地随判定落账（#3003）。
- **side effects**（副作用）：验证器 agent 运行（只读工具、`verification`
  会话线上的临时 merged-main 检出）、issue 报告评论、`confirmed` 时关闭
  issue、向运行中的 acceptance 行写入用量（`prior_usage` 基线 + 运行内增量）、
  `milestone_updated` 事件；人工覆盖经 `verification_override` 路径将人工
  身份记录到 `verified_by`。
- **postconditions**（后置条件）：`confirmed` → `status == "completed"`、
  issue 关闭、`completed_at` 已设置。`rejected` / `indeterminate` →
  `paused` 等待人工审查（已交付的代码绝不会被标记为 failed）。基础设施故障
  在同一阶段重试，最多 3 次（确定性解析失败上限 2 次，#2867），随后暂停。
- **re-entry point**（重入点）：按 `(merge_sha, issue_acceptance_hash)` 去重；
  已落账的 `confirmed` 结果是终态 no-op。新的 merge SHA 或被编辑过的 issue
  会自然地重新运行验证器。同一尝试会复用早期里程碑行，较旧的 `in_progress`
  行会被清扫为 failed("interrupted")；验证中途的配额暂停只有在写入其已消耗
  用量后才终结该行。
- **recovery behavior**（恢复行为）：基础设施故障 → 同阶段重试；验证器配额
  暂停 → 工作流 `paused`（可恢复）；关机 → 该行被终结为 `cancelled` 并在
  恢复时由同一尝试复活（硬 kill 会使其留在 `in_progress`，同样会被复用）。
- **terminal outcomes**（终态结果）：`completed`（confirmed 判定或阶段被
  禁用）；`pause`（rejected / indeterminate / 重试耗尽）；`retry`（未达上限的
  基础设施故障）。

---

### 迁移状态（Migration status）

全部八个阶段都经由 `_commit_phase_result` 提交；没有任何阶段仍停留在遗留的
内联提交路径上。

- `phases/*.py` + `PHASE_HANDLERS` 注册表（经 `resolve_phase_handler` 解析）：
  `development`、`pr_review`、`merge`、`acceptance_verification`。调度器中
  针对它们的 `_legacy(self._do_*)` 分支仅是防御性兜底。
- 直接遵循契约的 orchestrator `_do_*` 方法（#2044 T6–T9）：`preparation`、
  `planning`、`report`、`wait`。

一个阶段被视为已迁移的条件是：它返回 `PhaseResult` 且**不含任何直接的
`_update_workflow({"current_phase": ...})` / `_create_milestone` 调用** ——
所有状态都流经 `_commit_phase_result`。

# 修复方案（v2）：验收 rejected 修复轮在主仓库裸跑并误终止（Bug 5）

- 日期：2026-08-20
- v2 修订（独立审查意见）：① 重建路径推导优先 `get_preferred_worktree_path`（merge cleanup 只清 `worktree_path`/`branch_name`，不清 `preferred_worktree_path`，可精确回到原始 worktree 位置，与 preparation/CI-repair 的既有语义一致）；② 测试计划补"worktree 已清、branch 残留"中间态的 attach 用例；③ 删除 2.2 末段冗余表述（recreation 段 L243 的 branch_name 推导保持原样，无需"改为"）。
- 分支：`fix/resume-without-workspace`（基于 `origin/main`）
- 生产环境：192.168.31.159（openace-scheduler.service，PostgreSQL `openace` 库）
- 受害工作流：#322（9be278b1，issue #2755）、#329（4a2d6e76，PR #2902）、#340（04b0b260，PR #2907）
- 前置：Bug 3 修复（v7，`fix/acceptance-rejected-timing-issue-shortcircuit`，已合并部署）解决了"rejected 重入被 pr_review timing-issue 短路"的误终止；本次 Bug 5 是该修复上线验证时暴露的**更深一层缺陷**：修复轮根本没有拿到隔离工作区。
- **行号基准声明**：文中行号对应工作区当前 `origin/main` 检出；实施时一律以符号（函数名/常量名/语句）定位为准，行号仅作对照参考。

## 一、Bug 描述

### 1.1 生产证据（三个受害工作流的完整事件链）

共同背景：三个工作流均完成了 round 1 交付（PR 合并进 main，merge 阶段 cleanup 清空了 `worktree_path` 与 `branch_name`），随后 acceptance_verification 判定 **rejected**，人工通过 resume-with-feedback 恢复（16:35:44，milestone `requirement_received`）。此后：

| 工作流 | resume 后路由 | 实际行为 | 结果 |
|---|---|---|---|
| #322 | **pr_review**（错误） | `_do_wait` 回溯选中昨日陈旧的 `worktree_restored`(phase=pr_review) 里程碑 → 直接进入 pr_review；branch_name 为空 → `git rev-parse ''` 失败（exit 128，日志 `Failed to check branch status: git rev-parse  failed`）→ `has_changes=False, is_timing_issue=False` → v7 拦截条件（需 `is_timing_issue`）不满足 → 走通用 `no_changes` 终态 | 16:36:01 误终止 completed（resume 后仅 17 秒，开发轮从未运行） |
| #329 | development（碰巧正确） | `ensure_worktree` 对空 `worktree_path` 是 no-op（返回 `project_path`）→ dev agent **直接在主仓库 `/home/dwu/open-ace-01/open-ace` 上开发**（branch_name 为空，所有分支校验被跳过）→ 编排器自动提交 `auto: development changes (round 2)`（7d279598）**落在本地 main 上** → 测试轮跑完 → pr_review 同样因空分支名走 `no_changes` | 16:51:34 误终止 completed，stray commit 留在主仓库 main |
| #340 | development（碰巧正确） | 同 #329，主仓库 `/home/qlfan/auto/a1/open-ace` 上 stray commit `c1eb1932` | 16:53:15 误终止 completed |

日志铁证（journalctl，2026-08-21 00:35:58 CST）：

```
pr_review - WARNING - Failed to check branch status: git rev-parse  failed (exit 128):
致命错误：有歧义的参数 ''：未知的版本或路径不存在于工作区中。
```

主仓库污染证据：

```
# /home/dwu/open-ace-01/open-ace（branch=main）
7d279598 auto: development changes (round 2)   ← #329 的修复轮产出，未推送、未走 PR
# /home/qlfan/auto/a1/open-ace（branch=main）
c1eb1932 auto: development changes (round 2)   ← #340 的修复轮产出
```

### 1.2 根因（三个叠加缺陷）

**缺陷 A —— `_do_wait` user_feedback 路径的里程碑回溯选中基础设施里程碑**（orchestrator.py，`_do_wait` 内 `if user_feedback and user_feedback.strip():` 块）：

```python
cancelled_phase = "development"  # default fallback
milestones = self.repo.list_milestones(self._workflow_id)
for ms in reversed(milestones):
    if status == "completed" and mtype not in (
        "wait_started", "requirement_received", "branch_created",
        "repo_setup", "issue_created",
    ):
        cancelled_phase = ms.get("phase", "development")
        break
```

排除表不含 `worktree_restored`。#322 的最新"completed 且不在排除表"里程碑是昨日 pr_review 阶段的 `worktree_restored`（infra 记录）→ resume 目标成了 pr_review，完全跳过开发轮。更根本的问题是：**对 rejected 场景，回溯本身就是错误的决策机制**——rejected 隐含"上一轮交付已合并"（acceptance 只在 merge 成功后运行），此时最近 completed 里程碑必然落在 merge/acceptance/report 侧或 infra 记录上（cleaned_up、merged、worktree_restored、被 cancel 的 report 三件套……），无论命中哪个都不是正确目标。正确目标只有一个：**带着验收失败项重开 development**。#329/#340 碰巧路由到 development 只是因为它们的里程碑形状不同（历史上恰好有未 cancel 的 development 侧 completed 里程碑），属偶然正确。

**缺陷 B —— `ensure_worktree` 对空 `worktree_path` 是 no-op，修复轮拿不到工作区**（git_workspace.py `ensure_worktree`）：

```python
if strategy != "worktree" or not project_path or not worktree_path:
    # ... transition-state 守卫 ...
    return worktree_path or project_path   # ← 空路径：返回主仓库！
```

该 no-op 的原有理由（注释）：空 `worktree_path` 是 `_do_merge` 最终 cleanup 在 PR 合并后的**有意**清空（"工作流已完成"），把它当"目录丢失"去重建会在重试 merge 时 `git worktree add <主仓库>` 失败。这个假设对**终态**工作流成立，但对**被 resume 拉回活跃阶段**的工作流不成立：merge cleanup 清空了 `worktree_path`/`branch_name` 之后，验收 rejected + 人工 resume 把工作流带回 development/pr_review，`advance()` 每 tick 先调 `_ensure_worktree`（no-op）→ 阶段全部落在 `project_path`（主仓库检出）上执行：

- `_run_development_agent`：`project_path = wf.get("worktree_path") or wf.get("project_path")` → 主仓库；分支校验 `if expected_branch and ...` 因 `expected_branch` 为空**整体跳过**；
- 编排器自动提交（`git_add_all` + `git_commit("auto: development changes (round N)")`）直接落在主仓库**本地 main** 上；
- pr_review 的分支检查用空 branch_name 调 `git rev-parse` → 必然失败。

**缺陷 C —— pr_review 对空 branch_name 静默落入 `no_changes` 终态**（pr_review.py `handle`）：

```python
try:
    branch_sha = gh._run_git(["rev-parse", branch_name]).stdout.strip()  # branch_name="" → exit 128
    ...
except Exception as e:
    logger.warning("Failed to check branch status: %s", e)
    pass   # has_changes=False, is_timing_issue=False 保持初值
```

空分支名是**损坏状态**（所有 branch_strategy 下 preparation 都会设置 branch_name；为空只可能来自 merge cleanup 后未重建），却被当成"无变更"处理 → 误发 issue 评论"Agent did not produce any code changes"、误终止 completed。v7 的 rejected 拦截条件 `is_timing_issue and pr_number and rejected_verification` 因 `is_timing_issue=False` 同样无法兜底。

### 1.3 触发条件（精确）

```
merge cleanup 已清空 worktree_path（PR 合并后的标准路径）
∧ verification_status == 'rejected'
∧ 人工 resume-with-feedback / cancel-with-feedback（_do_wait user_feedback 路径）
∧ branch_strategy == 'worktree'
→ 修复轮在主仓库 main 上裸跑（缺陷 B）
→ 若回溯命中 worktree_restored(pr_review) 则连开发轮都跳过（缺陷 A，#322）
→ pr_review 空分支名 → no_changes 误终止（缺陷 C，三者共同终点）
```

注：缺陷 B 的影响面不止 rejected 修复轮——**任何**"已合并交付 → wait → 新需求评论 → planning → development"流程同样以空工作区进入 development（wait 阶段的新需求路径返回 `next_phase="planning"`，不经过 preparation）。本修复在 development 入口统一重建工作区，顺带覆盖该路径。

## 二、修复设计

### 2.1 改动 A（orchestrator.py `_do_wait`：rejected 强制重开 development；回溯排除 infra 里程碑）

在 user_feedback 路径中，`rejected_acceptance`（v7 已计算的局部变量）为真时**跳过回溯**，直接 `cancelled_phase = "development"`；非 rejected 保持现状，但排除表增加 `"worktree_restored"`：

```python
if user_feedback and user_feedback.strip():
    # A rejected acceptance means the previous delivery ALREADY merged
    # (acceptance only runs after a successful merge), so milestone
    # backtracking can only land on merge/acceptance/report-side or infra
    # milestones — every one a wrong resume target (#322: a stale
    # worktree_restored(pr_review) skipped the repair round entirely and
    # the workflow fell straight into the no-changes terminal). The only
    # meaningful resume is a repair development round; force it.
    if rejected_acceptance:
        cancelled_phase = "development"
    else:
        cancelled_phase = "development"  # default fallback
        milestones = self.repo.list_milestones(self._workflow_id)
        for ms in reversed(milestones):
            status = ms.get("status", "")
            mtype = ms.get("milestone_type", "")
            if status == "completed" and mtype not in (
                "wait_started",
                "requirement_received",
                "branch_created",
                "repo_setup",
                "issue_created",
                "worktree_restored",  # infra bookkeeping, never a resume target
            ):
                cancelled_phase = ms.get("phase", "development")
                break
    ...  # 其余（dev_round+1、emit、PhaseResult.completed）不变
```

设计说明：
- `rejected_acceptance` 为真时工作流必然带验收反馈注入（v7 主路径）或人工反馈，dev prompt 有修复目标；`dev_round+1` 与 v7 语义一致。
- v6 一次性防护不受影响：其守卫（`fresh_human_resume`）只控制**反馈注入**；本改动只控制**阶段路由**。修复轮第二个 wait tick 上 `user_feedback` 已被消费清空 → 不进 user_feedback 路径 → 无重入循环。
- 人工反复 resume 的 dev 轮数无自动上限（与 v7 现状一致，人在环内，`MAX_ACCEPTANCE_DEV_ROUNDS` 只约束 pr_review 拦截路径的自动重开）。

### 2.2 改动 B（git_workspace.py `ensure_worktree`：cleanup 后进入工作区消费阶段时重建）

空路径 no-op 分支增加重建条件：`branch_strategy == 'worktree'` 且 `worktree_path` 为空且 `current_phase ∈ {development, pr_review}` 时，落入既有的"worktree 丢失重建"逻辑（复用 Issue #814/#2042/#1999 的全部安全机制），而不是返回主仓库：

```python
# module-level constant
# Phases that execute agent/git work in the workflow workspace. A workflow
# resumed AFTER merge cleanup (rejected-acceptance repair round, or new
# issue-comment requirements) reaches these with worktree_path/branch_name
# deliberately cleared; running them against project_path (the main
# checkout) commits repair work straight onto local main (#322/#329/#340).
_WORKSPACE_CONSUMING_PHASES = ("development", "pr_review")

# in ensure_worktree, replacing the early-return guard:
if strategy != "worktree" or not project_path or not worktree_path:
    ts = wf.get("worktree_transition_state")
    if ts and ts != "recovery_failed":
        raise RuntimeError(
            "worktree transition in progress " f"(state={ts!r}); reconcile before execution"
        )
    # Post-merge-cleanup resume (#322/#329/#340): the merged delivery's
    # cleanup cleared the fields because the workflow was "done"; a
    # rejected-acceptance / new-requirements resume brings it back into a
    # workspace-consuming phase. Recreate the worktree via the same
    # authoritative-head recovery as the dir-gone path below instead of
    # returning the main checkout. Phases that never touch the workspace
    # (wait/report/acceptance/planning/merge) keep the historical no-op
    # (the merge-retry rationale in the comment above still holds).
    if (
        strategy == "worktree"
        and project_path
        and not worktree_path
        and wf.get("current_phase") in _WORKSPACE_CONSUMING_PHASES
    ):
        # fall through to the recreation section below. Path: prefer the
        # same canonical location preparation/CI-repair use —
        # preferred_worktree_path SURVIVES merge cleanup (cleanup clears
        # only worktree_path/branch_name), so this recreates at the exact
        # original spot. Legacy sibling format is a defensive fallback for
        # the (practically unreachable) case where no preferred path can
        # be derived; an empty branch_name there yields a bogus path that
        # `worktree add` rejects loudly (fail-closed, not silent).
        canonical = self.get_preferred_worktree_path(wf) or os.path.realpath(
            os.path.normpath(
                f"{project_path}/../{(wf.get('branch_name') or '').strip().replace('/', '-')}"
            )
        )
    else:
        return worktree_path or project_path
else:
    canonical = os.path.realpath(worktree_path)
    # ... 既有 valid-worktree / branch-mismatch 逻辑不变（命中时 return canonical）...

# ... 既有 recreation 段（fetch origin main → resolve_recovery_head →
#     branch 存活则 attach，否则 worktree add -b → _update_workflow →
#     worktree_restored 里程碑）不变 ...
```

结构上具体实现为：把现行 `if strategy != "worktree" or not project_path or not worktree_path:` 早退块改为上述形状，让"cleanup 后进入工作区消费阶段"的空路径与"目录丢失"共用下方的重建段（重建段现成的 `branch_name = wf.get("branch_name") or f"auto-dev/{...}"` 推导、分支存活探测、fail-closed 守卫、`_refresh_trusted_git_context`、`_update_workflow`、`worktree_restored` 里程碑全部原样复用，branch_name 推导无需任何改动）。

设计说明：
- **重建基点**：`resolve_recovery_head` 四态决策树（PR head → expected_head_sha → base_commit_sha → fail-closed）。三个受害工作流 `github_pr_number` 仍在（cleanup 不清 PR 号）→ `resolve_verified_pr_head`：merged PR 的 headRefOid 经 `fetch origin main` 后本地 `cat-file -e` 可验证（合并提交的第二父链）→ CONFIRMED → 在旧 PR head 上重建分支。修复轮的新提交使分支不再是 main 祖先 → pr_review 正常走 diff 检查 → 新 PR → merge。
- **重建路径**：`get_preferred_worktree_path` 优先返回 `preferred_worktree_path`（merge cleanup 只清 `worktree_path`/`branch_name`，该字段留存）→ 精确回到原始 worktree 位置（`.worktrees/{workflow_id}` 格式）；无留存字段时推导同款 `.worktrees` 路径——与 preparation 的 pre-generated 优先序（`orchestrator.py` preparation：pre-generated `.worktrees` 路径优先，legacy sibling 格式仅向后兼容 fallback）和 CI-repair 的 `_get_preferred_worktree_path` 用法保持同一语义。空 `branch_name` 不影响 preferred 路径推导（路径只含 workflow_id）。
- **gate 不要求 branch_name 为空**：覆盖 cleanup 部分失败（worktree 已删、branch 残留）的中间态——重建段的"分支存活则 attach"分支恰好处理它（attach 到存活分支，且 #1999 守卫校验分支头与 verified head 一致）。
- **gate 不含 merge**：保留原 no-op 注释针对的"重试 merge"场景（PR 已合并的 merge 重试不应重建工作区）。
- **gate 不含 planning**：planning 是只读分析阶段，维持现状（新需求流程 wait→planning 期间不重建，到 development 入口才重建）。
- **squash-merge 仓库的边界**：PR head 不可达 main 时 `resolve_verified_pr_head` 返回 indeterminate → fail-closed → 工作流 failed（带清晰错误），操作员介入。与 #2042 哲学一致，本仓库（open-ace）使用 merge-commit 风格不受影响。
- **重建后 advance() 已有 `wf = self.workflow` 重读**（ensure_worktree 之后），下游阶段看到的是重建后的 `worktree_path`/`branch_name`；`_run_development_agent` 的分支校验随之生效。

### 2.3 改动 C（pr_review.py `handle`：空 branch_name 大声失败）

在 `handle` 读取 `branch_name` 之后、进入分支检查之前：

```python
branch_name = wf.get("branch_name", "")
if not branch_name.strip():
    # Empty branch_name is a broken state (every branch_strategy sets it in
    # preparation; empty only happens after merge cleanup without
    # recreation). Falling through would run `git rev-parse ''` → exit 128
    # → the except-swallow leaves has_changes=False and the workflow
    # terminates as no_changes — masking the breakage (#322/#329/#340).
    # Fail loudly instead.
    return PhaseResult.failed(
        structured_error={
            "message": (
                "pr_review entered with an empty branch_name (workspace "
                "cleared by merge cleanup and not recreated); refusing to "
                "fall into the no-changes terminal — this is a broken "
                "state, not 'no changes'"
            )
        }
    )
```

防御纵深：改动 A+B 生效后该路径不可达（development/pr_review 入口已重建工作区）；保留它是为了非 worktree 策略或任何未预见的路由把空分支送进 pr_review 时**可见地失败**而非静默误终止。

### 2.4 修复后的完整闭环（验证用）

```
rejected → 人工 resume-with-feedback → _do_wait：
  user_feedback 非空 ∧ verification_status='rejected'
  → 注入验收失败项（v7 主路径，已有）
  → 强制 next_phase=development（改动 A）
→ 下一 tick advance(phase=development)：
  ensure_worktree 发现 worktree_path 空 ∧ phase∈工作区消费集 → 重建
  worktree+branch@旧 PR head（改动 B）
→ dev agent 在隔离 worktree 修复 → auto-commit 落在 auto-dev 分支
→ pr_review：分支有新提交 → 正常建 PR
→ merge → acceptance_verification 重验（verification_merge_sha 已清空）
```

## 三、测试计划

新增/修改测试（沿用 v7 的测试文件与 fake 模式）：

1. `tests/autonomous/test_orchestrator_characterization.py`（`_do_wait` 测试区）：
   - `test_wait_rejected_resume_forces_development`：`verification_status='rejected'` + user_feedback，里程碑列表以 `worktree_restored`(pr_review, completed) 结尾 → 断言 `next_phase == "development"`（修复前为 pr_review）。
   - `test_wait_non_rejected_backtracking_skips_worktree_restored`：非 rejected + user_feedback，里程碑 `[dev_completed(development, completed), worktree_restored(pr_review, completed)]` → 断言 `next_phase == "development"`（修复前 pr_review）。
   - 回归：v7 已有的注入类用例全部保持通过。
2. `tests/autonomous/` 下 ensure_worktree 的既有测试文件（实施时定位，应为 git_workspace/orchestrator 的 worktree 恢复测试）：
   - `test_ensure_worktree_recreates_cleared_workspace_for_development`：strategy=worktree、worktree_path=""/branch_name=""、current_phase=development → 断言执行了 `worktree add -b`、`_update_workflow` 写回 worktree_path+branch_name、创建 `worktree_restored` 里程碑、返回 canonical 路径（= `get_preferred_worktree_path` 的推导值）。
   - `test_ensure_worktree_recreates_cleared_workspace_for_pr_review`：同上，phase=pr_review。
   - `test_ensure_worktree_attaches_surviving_branch_when_worktree_cleared`：strategy=worktree、worktree_path=""、branch_name 非空且 `show-ref` 存活、current_phase=development → 断言走 `worktree add <path> <branch>`（attach 存活分支）而非 `worktree add -b`，写回 worktree_path+branch_name——锁定"gate 不要求 branch_name 为空"声明的分支残留中间态行为。
   - `test_ensure_worktree_noop_for_wait_and_merge_when_cleared`：phase=wait / merge、空路径 → 返回 project_path、无 git 重建调用（保留历史行为）。
   - `test_ensure_worktree_noop_when_not_worktree_strategy`：strategy=new-branch、空路径 → project_path。
   - 回归：既有 "dir gone 重建"、branch-mismatch、transition-state 守卫用例保持通过。
3. `tests/autonomous/phases/test_pr_review.py`：
   - `test_empty_branch_name_fails_loudly`：workflow 无 branch_name → 断言 `PhaseResult` 为 failed（含 structured_error message），且**不是** completed/no_changes 终态。
4. 全量回归：`HOME=/tmp/fakehome python -m pytest tests/autonomous -x -q`（隔离本机 `~/.open-ace/agent-launcher.conf` 对 MAX_CONCURRENT_WORKFLOWS 的覆盖）。

## 四、部署与受害工作流恢复（运维步骤）

1. 合并 PR 后部署：`ssh root@192.168.31.159 "cd /home/openace && git pull && systemctl restart openace-scheduler.service"`。
2. 清理主仓库 stray commit（丢弃误提交，修复轮将重做）：
   - `/home/dwu/open-ace-01/open-ace`：确认无未提交内容后 `git reset --hard origin/main`（丢弃 7d279598）；
   - `/home/qlfan/auto/a1/open-ace`：同样 `git reset --hard origin/main`（丢弃 c1eb1932）。
   - 说明：两个 stray commit 是误在 main 上的修复轮产出，内容将由恢复后的工作流在隔离 worktree 中重做并走正常 PR 流程；不 cherry-pick 以免绕过评审。
3. 恢复三个误终止工作流（#322/#329/#340，当前 status=completed 不在可 resume 状态；resume API 仅接受 waiting/paused）：将其复位为 waiting 并提供修复目标反馈——`UPDATE autonomous_workflows SET status='waiting', current_phase='wait', user_feedback='Resume: fix the rejected acceptance items.', error_message='' WHERE id IN (...)`。
   - 说明：这是修复系统误终止后的状态复位（把工作流交还给调度器重跑），不是代替工作流做事；修复后的 `_do_wait` 会在 user_feedback 路径上自动注入验收失败项并强制 development（改动 A），ensure_worktree 会重建隔离工作区（改动 B），修复轮完整闭环。
   - #322 的 user_feedback 字段仍残留 v7 注入文本，复位时统一覆盖为简短人工指令，避免注入文本重复拼接。
4. 验证：观察三个工作流进入 development 且 `worktree_path`/`branch_name` 非空、`worktree_restored` 里程碑出现、agent 在 worktree 内工作、后续 PR 正常创建合并、acceptance 重验。

## 五、风险与回滚

- **改动 B 的行为面**：唯一语义变化是"worktree 策略 + 空路径 + development/pr_review 阶段"从 no-op 变为重建。该状态此前必然产生错误行为（主仓库裸跑或 no_changes 误终止），不存在依赖旧行为的正确路径；wait/report/acceptance/planning/merge 及非 worktree 策略完全不变。回滚 = revert 单个 PR。
- **重建失败**（fail-closed，如 squash-merge 后 PR head 不可达）：工作流 failed 并带清晰 error_message，操作员可介入；不劣于现状（现状是静默误终止）。
- **既有测试破坏风险**：任何"worktree 策略 + 空路径 + development/pr_review"形状的既有用例会从 no-op 变为重建路径；实施时跑全量 autonomous 测试套件，对这类用例逐个确认是"断言旧的错误行为"（更新断言）还是真实回归（重新设计）。
- **里程碑排除表加 `worktree_restored`**：只影响非 rejected 的 cancel-with-feedback 回溯；`worktree_restored` 是纯 infra 记录，排除后回溯落到真实工作里程碑上，方向必然更正确。

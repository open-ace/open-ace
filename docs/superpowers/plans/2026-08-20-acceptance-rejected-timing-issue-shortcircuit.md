# 修复方案（v7）：验收 rejected 重入被 pr_review timing-issue 短路掩盖

- 日期：2026-08-20（v2 按一轮 9 条意见修订；v3 按二轮 8 条意见修订；v4 按三轮 6 条意见修订；v5 按四轮 2 条意见修订行号基准；v6 按五轮 1 critical 修订 `_do_wait` 注入一次性防护；v7 按 PR 审查 1 major + 4 minor 修订：两处 patch 清空 `verification_merge_sha` + resume patch 清空 `error_message`、守卫重排省一次 DB 查询、注释修正、补 host 回退分支测试）
- 分支：`fix/acceptance-rejected-timing-issue-shortcircuit`（基于 `origin/main` @ 18a0f676；v7 实施时已变基至 `origin/main` @ 0fb32f96）
- 生产环境：192.168.31.159（openace-scheduler.service，PostgreSQL `openace` 库）
- 受害工作流：#331（issue #2765，验收 rejected 后流程终止为 completed，修复未落地）
- **行号基准声明**：文中行号对应工作区 `d015c45d`（`fix/merged-pr-reuse-and-scheduler-starvation` tip）；与 `origin/main` @ 18a0f676 在 orchestrator.py 上有约 60 行偏移（#2867 相关 hunk，不触碰任何本方案改动点）。**实施时一律以符号（函数名/常量名/语句）定位为准，行号仅作对照参考。**

## 一、Bug 描述

### 1.1 生产证据（#331 事件时间线，autonomous_workflows + workflow_milestones）

| 时间 (UTC) | 事件 |
|---|---|
| 08-19 17:14 | PR #2851 merged（merge 阶段完成，分支清理） |
| 08-19 17:19 | acceptance_verification 判定 **rejected**：gate `call-chain:tenant_repo` 被拒（新增 `app/repositories/tenant_repo.py` 无生产调用方，dead code / missing wiring）→ `PhaseResult.pause`（awaiting review） |
| 08-19 18:09 | 人工 resume → `_do_wait` user_feedback 路径 → milestone 回溯选中 development（dev_round+1） |
| 08-19 18:19–18:22 | dev agent 运行；产出与验收指出的 call-chain 问题错位（改了 session.py/user.py 等）—— **verification_report 从未接入 dev prompt，agent 无修复目标** |
| 08-19 20:48 | 昨日路径：pr_review 复用已合并 PR → merge 空提交失败（bug 1，PR #2906 已修） |
| 08-20 13:53 | 今日重置后路径：ensure_worktree 重建分支（PR head，已全部在 main）→ pr_review 分支检查：`merge-base --is-ancestor` 成立 → **timing-issue 短路 → status=completed**，milestone `Branch behind main (timing issue)` |

issue #2765 未被关闭（timing-issue 路径不 close issue），但工作流终止：验收指出的 dead-code 缺陷永远无人修。

### 1.2 根因（代码级）

[pr_review.py](../../../app/modules/workspace/autonomous/phases/pr_review.py#L184-L279)：

```python
is_ancestor = merge-base --is-ancestor <branch> <main>  # 分支是 main 的祖先
if is_ancestor:
    is_timing_issue = True      # 语义假设：工作流创建竞态（Issue #1552）
    has_changes = False
...
if not has_changes:
    if is_timing_issue:
        # post issue 评论 → emit phase_change completed
        return PhaseResult.completed(next_phase="completed", ...)
```

该短路将**所有**"分支落后 main"都判为 Issue #1552 的创建竞态。但存在第二种到达方式：

**验收 rejected → 人工 resume → development 未产生 main 外提交 → pr_review**。此时"分支落后"的事实是"上一轮交付已通过 PR 合并进 main"，语义是 **delivery already landed, acceptance unfinished**，正确行为是继续开发修复，而非终止。

辅助证据（机制缺口）：
- `verification_report`（含被拒项的 rationale）只在 acceptance_verification 内部与 API 展示使用，**从未接入 dev prompt**——resume 后的 dev agent 拿不到修复目标，产出错位（主路径缺口，见 2.5）。
- `MAX_ACCEPTANCE_DEV_ROUNDS` / `PhaseHost.dev_round_cap_remaining()`（orchestrator.py L8857，#2335 预留的 rejected→dev 循环上限）生产代码零调用，未接线。
- 既有 `feedback_prefill_from_report`（acceptance_verification.py L356 工作区基准，#2491）已实现 report→failed-items 文本转换，覆盖 scope/gates/verifier 三类非 confirmed 项——本修复直接复用，不写第三套解析。

### 1.3 触发条件（精确）

```
pr_review 入口
  ∧ merge-base --is-ancestor <branch_sha> <main_sha>   # 分支落后
  ∧ github_pr_number 非空（wf 或 host 双读取）          # 上一轮交付过 PR
  ∧ verification_status == 'rejected'（本地 DB 字段）    # 上一轮验收被拒
→ 误短路 completed
```

三个条件均为本地 DB / 本地 git 判断，无需 GitHub API（无抖动风险）。
`verification_status == 'rejected'` 隐含 PR 已 MERGED：验收只发生在 merge 成功之后（acceptance_verification.py L562-568：merge_sha 取不到只 retry），因此不需要远程探测 PR 状态。

## 二、修复设计

### 2.1 核心改动 A（pr_review.py：兜底路径拦截短路）

在 `not has_changes and is_timing_issue` 短路执行**前**插入判定（示意，实施以源码风格为准）：

```python
if not has_changes:
    pr_number = wf.get("github_pr_number") or host.get_workflow_field("github_pr_number")
    rejected_verification = (
        (wf.get("verification_status") or host.get_workflow_field("verification_status") or "")
        .strip().lower() == "rejected"
    )
    if (
        is_timing_issue
        and pr_number
        and rejected_verification
    ):
        dev_round = wf.get("dev_round", 1)
        new_dev_round = dev_round + 1
        if host.dev_round_cap_remaining(wf) > 0:
            feedback = _rejection_feedback(wf, pr_number)   # 见 2.2
            host.emit_phase_change(                          # handler 必须自行 emit
                {"phase": "development", "dev_round": new_dev_round, "resumed": True}
            )
            return PhaseResult.completed(
                next_phase="development",
                next_status="developing",
                workflow_patch={
                    "dev_round": new_dev_round,
                    "current_round": 0,
                    "verification_status": None,   # 循环防护（见 2.3）
                    "user_feedback": feedback,     # dev prompt 的修复目标（见 2.2）
                    "error_message": "",           # 清 pause 期残留（先例 L277）
                },
                milestone_events=[{
                    "phase": "pr_review",
                    "dev_round": new_dev_round,
                    "milestone_type": "acceptance_rejected_reopened",
                    "status": "completed",
                    "title": f"PR #{pr_number} merged but acceptance rejected; "
                             f"reopening development round {new_dev_round}",
                    "result_summary": "Branch fully merged into main while acceptance "
                                      "is rejected; reopening development with the "
                                      "failed-items feedback.",
                }],
            )
        else:
            # 持久 rejected 且 dev 轮上限耗尽：按 #2335 语义 fail 而非 completed
            # （MAX_ACCEPTANCE_DEV_ROUNDS 注释："a persistent rejection fails the
            # workflow rather than looping forever"）。
            # PhaseResult.failed() 工厂不接受 milestone_events（phase_contract.py
            # L174-186），故直接构造 dataclass；_commit_phase_result 对所有
            # outcome 无差别落库 milestone_events（L5413）。
            # error_message 只写 structured_error["message"]——failed 分支会用它
            # 无条件覆盖 workflow_patch 里的 error_message（L5402-5403），双写
            # 必丢详细文案，故 workflow_patch 不再携带 error_message。
            fail_msg = (
                f"Acceptance rejected after {dev_round} development rounds "
                f"(dev-round cap {MAX_ACCEPTANCE_DEV_ROUNDS} exhausted); "
                f"PR #{pr_number} is merged but failed gates remain unresolved"
            )
            return PhaseResult(
                outcome="failed",
                milestone_events=[{
                    "phase": "pr_review",
                    "dev_round": dev_round,
                    "milestone_type": "acceptance_rejected_cap_exhausted",
                    "status": "failed",
                    "title": f"Acceptance rejection persisted past dev-round cap (PR #{pr_number})",
                }],
                structured_error={"message": fail_msg},
            )
    # （否则维持现有 timing-issue / no-changes 短路行为，一字不改）
```

要点：
- **PR 编号双读取**：对照 L314 先例（`wf.get(...) or host.get_workflow_field(...)`），既是触发条件也用于 milestone/error 文案。
- **`MAX_ACCEPTANCE_DEV_ROUNDS` 常量迁移（防循环导入）**：该常量在 orchestrator.py L1746（工作区基准；`MAX_ACCEPTANCE_DEV_ROUNDS = 3` 定义行及其上方 4 行 #2335 注释块 L1742-1746 整体迁移），而 orchestrator L84 → phases/__init__ L37 → pr_review 的加载链使 pr_review **不能**反向导入 orchestrator（pr_review.py L516-517 注释已明确此约束）。将常量迁入**已存在的** `app/modules/workspace/autonomous/constants.py`（pr_review L71-81 已从该模块导入，`_extract_pr_number_from_error` 等先例在此）：constants 新增定义（含注释）；orchestrator.py 导入列表（L57-76 一带）加 `MAX_ACCEPTANCE_DEV_ROUNDS`（`# noqa: F401` 再导出）并删除本地定义块（注释+定义行）；pr_review.py 导入列表加同名。
- **emit_phase_change**：pr_review.py 模块头与 `_commit_phase_result` 注释（orchestrator.py L5336-5339）明确 handler 自行 emit；现有 timing-issue 路径 L265、`_do_wait` L12480-12482 均有先例，本路径 emit `{"phase": "development", "dev_round": ..., "resumed": True}`。
- **纯本地判定**：三个条件（分支祖先、PR 编号、verification_status）都不触网。
- **接线既有 host 能力**：`PhaseHost.dev_round_cap_remaining()`（phase_host.py L90、orchestrator.py L8857），本修复是它的第一个生产调用点，兑现 #2335 预留。
- **next_phase="development"**：`_do_wait` 同型先例（L12487-12491）；`_commit_phase_result` 对非 completed/wait 的 next_phase 走标准迁移；`PHASE_STATUS_MAP["development"] == "developing"`（L1734）。
- **cap 耗尽终态**：`PhaseResult(outcome="failed", milestone_events=[...], structured_error={"message": fail_msg})`（dataclass 直构——`failed()` 工厂不接受 milestone_events），详细文案只放 `structured_error["message"]`（failed 分支用它无条件覆盖 patch 的 error_message，双写必丢），对齐 #2335 "persistent rejection fails the workflow" 语义；不再以 completed 掩盖（v1 缺陷，一轮意见 4）。

### 2.2 反馈文本：复用 `feedback_prefill_from_report`（不写第三套解析）

`_rejection_feedback(wf, pr_number)` 置于 **acceptance_verification.py**（紧邻 `feedback_prefill_from_report`，单一实现两处消费；已验证 pr_review→acceptance_verification 与 orchestrator→acceptance_verification 均无导入环）：

```python
_MAX_FEEDBACK_CHARS = 4000

def _rejection_feedback(wf: dict, pr_number) -> str:
    """Dev-round repair target derived from the last rejected verification."""
    raw = wf.get("verification_report") or ""
    try:
        report = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        report = {}
    prefill = feedback_prefill_from_report(report) if isinstance(report, dict) else ""
    delivery = f"PR #{pr_number}" if pr_number else "the previous delivery"
    if prefill:
        body = (
            f"Acceptance verification REJECTED the previous delivery ({delivery}). "
            f"Fix these failed items in this development round:\n\n{prefill}"
        )
    else:
        body = (
            f"Acceptance verification REJECTED the previous delivery ({delivery}). "
            "Review the verification report comment on the issue and fix the failed items."
        )
    return body[:_MAX_FEEDBACK_CHARS]
```

导入路径（两处不同，照抄即用）：
- pr_review.py（phases 包内）：`from .acceptance_verification import _rejection_feedback`
- orchestrator.py（autonomous 包）：`from app.modules.workspace.autonomous.phases.acceptance_verification import _rejection_feedback`

从模块外导入下划线私有函数在本库有先例（pr_review.py L76 导入 autonomous/constants 的 `_extract_pr_number_from_error`）。

- `feedback_prefill_from_report` 遍历 **scope/gates/verifier** 三类的非 confirmed 项（acceptance_verification.py `_failed_items` / `feedback_prefill_from_report`，工作区基准 L346-353 / L356），rationale 缺失时回退 evidence[0].note——覆盖 scope-only / verifier-only 拒绝（v1 只解析 gates 的偏置，一轮意见 3）。
- 解析失败 / report 缺失 / 无 failed 项 → 默认文本（指向 issue 上的验收评论）。
- 截断 4000 字符防 prompt 膨胀。
- 生命周期：`_get_user_feedback_prompt` 在 dev prompt 展示，dev 完成后既有逻辑清空（orchestrator.py L4443/L10075/L10652/L12168 读后清空先例），不泄漏到后续轮次。

### 2.3 循环防护（双重）

1. **单次自动重开**：重开时 `verification_status` 清空为 None（autonomous_repo.py L694 白名单仅过滤字段名不过滤 None 值，`verification_status` 在 L123 白名单内，None 真实落库）。若 dev 又无有效提交回到 pr_review，`rejected_verification` 不再成立 → 走现有 timing-issue 短路 completed（issue 收到 timing-issue 评论 + 此前验收 rejected 评论，人工有完整上下文）。每个 rejected 交付最多自动重开一次。
2. **全局 dev 轮上限**：`dev_round_cap_remaining(wf) <= 0`（dev_round 已达 MAX_ACCEPTANCE_DEV_ROUNDS=3）→ `PhaseResult.failed`（见 2.1），持久 rejected 既不无限循环也不静默 completed（#2335 语义）。

### 2.4 核心改动 B（orchestrator.py `_do_wait`：主路径 feedback 注入，v6 增加一次性防护）

v1 只修兜底路径（审查意见 5）：rejected → 人工 resume → `_do_wait` → development 的**主路径**仍盲跑，第一轮 dev 无修复目标、产出错位、空耗 dev_round。在 `_do_wait` 的 user_feedback resume 路径（L12457 起）注入。

**v6 关键修订（五轮审查 critical 意见 1）**：v5 假设"注入只发生在人工 resume 后的第一个 `_do_wait` tick"不成立——`verification_status='rejected'` 跨轮存活（全库唯二清空点：本修复 pr_review 拦截路径仅 dev 无提交时触发；acceptance_verification 要到 merge 之后才重写）。修复轮链路：tick #1 注入 → resume-development → dev 消费并清空 `user_feedback` 且产出修复提交 → pr_review（有提交走正常路径）→ report 无条件 next_phase="wait" → **tick #2**：状态恰为（rejected 陈旧 + feedback 已清 + PR 存在）→ v5 注入再次触发 → resume 分支劫持 auto-merge 直通 → 修复轮 PR 永不 merge、acceptance 永不重跑 → report↔wait 无限 ping-pong（每轮重复贴进度评论、dev_round 无上界递增——cap 只在 pr_review 的 not has_changes 拦截里检查，dev 有提交时永不触发）。

v6 注入条件（守卫 = 新鲜人工 resume 证据）：

```python
user_feedback = wf.get("user_feedback", "")
# A rejected acceptance means the resumed dev round needs the verifier's
# failed-items as its repair target (#331: the blind round drifted off-target).
# One-shot guard (v6): 'rejected' survives across rounds (it is only cleared
# by the pr_review no-changes interception or rewritten after the NEXT merge),
# so a bare rejected check would re-inject on every later wait tick and hijack
# the auto-merge passthrough into an endless report↔wait loop. Inject only on
# evidence of a FRESH human resume:
#   (a) user_feedback is non-empty (both resume routes write it: cancel-with-
#       feedback and resume-with-feedback), or
#   (b) the latest completed milestone is 'requirement_received' (created by
#       the cancel-with-feedback route and by the new-requirements polling
#       path; the polling path immediately moves the workflow to planning,
#       so only the cancel route's can linger on a waiting tick).
# The repair round's second tick has neither: feedback was consumed+cleared by
# the dev prompt and the latest completed milestone is report's round_completed.
fresh_human_resume = bool(user_feedback and user_feedback.strip())
if not fresh_human_resume:
    for ms in reversed(self.repo.list_milestones(self._workflow_id)):
        if ms.get("status") == "completed":
            fresh_human_resume = ms.get("milestone_type") == "requirement_received"
            break
if (wf.get("verification_status") or "").strip().lower() == "rejected" and fresh_human_resume:
    rejection_fb = _rejection_feedback(wf, wf.get("github_pr_number"))
    if rejection_fb:
        user_feedback = (
            f"{user_feedback}\n\n{rejection_fb}"
            if user_feedback and user_feedback.strip()
            else rejection_fb
        )
if user_feedback and user_feedback.strip():
    ...  # 既有 resume 决策逻辑不动，但 return 的 workflow_patch 增补字段（v7）：
    #     workflow_patch={
    #         "dev_round": new_dev_round, "current_round": 0,
    #         "user_feedback": user_feedback,   # ← 持久化注入结果（关键）
    #         "verification_merge_sha": "",     # ← v7（PR 审查 major 1）：丢弃被拒
    #                                          #    交付的陈旧 merge SHA，下一次
    #                                          #    merge 后验收重新解析新 PR 的
    #                                          #    merge commit，避免 replayed-rejected
    #         "error_message": "",             # ← v7（minor 5）：清暂停横幅文案
    #     }
```

- **持久化是关键**（二轮意见 3）：dev prompt 经 `_get_user_feedback_prompt` 读 **DB 字段** `wf["user_feedback"]`——拼接只改局部变量不落库则注入落空。resume 路径的 `workflow_patch` 必须增补 `"user_feedback": user_feedback`（拼接后的完整值）；既有 `dev_round`/`current_round` 字段不动，这是对既有 return 的唯一修改。
- **守卫覆盖两条 resume 路径**（v6）：cancel-milestone-with-feedback（routes/autonomous.py L1527-1548：写 user_feedback + 创建 `requirement_received` milestone，feedback 可为空）与 resume-with-feedback（L1726-1771：只写 user_feedback **必非空**，无 milestone）。(a) 覆盖两者带 feedback 的情形；(b) 专门覆盖 cancel 无 feedback 的空字符串变体。
- **milestone 证据的正确性**：`list_milestones` 按 `created_at ASC, id ASC` 返回（autonomous_repo.py L1167），`reversed()` 后第一个 `status=='completed'` 即最新完成里程碑。cancel-with-feedback 路由原子地（设 wait + 建 requirement_received）后，scheduler 下一个 wait tick 前无其他 phase 运行，故 tick #1 的最新 completed milestone 必是 requirement_received；而修复轮 tick #2 的最新 completed milestone 是 report 的 `round_completed`（phase=report，status=completed，orchestrator.py L12450-12458）。普通等待轮（report→wait，无人工介入）最新 completed 也是 round_completed/wait_started 序列——同样不满足 (b)，不注入，保持纯等待语义。
- **不能改用"注入时清空 verification_status"做一次性防护**（审查意见 1 明确否决）：那会让 dev 无提交的兜底路径（2.1 组件 A 拦截依赖 rejected 状态）退回 #331 原始 bug。
- 人工 feedback 非空 → **追加**（人工意见优先，verifier 项补充）；为空（仅 cancel 无 feedback 变体）→ 兜底填充。
- 既有边界说明：`_do_wait` 的 feedback-resume 路径本身无 cap 检查（既有行为，本方案不扩大范围）；反复 resume 反复 +1 dev_round 的防护由 2.1 的 cap 分支在回到 pr_review 时收口。
- 回溯边界说明（三轮意见 5）：resume 回溯选中哪个 phase 由 milestone 历史决定（L12463-12474）。若人工经 `resume-with-feedback`（不取消 milestone，routes/autonomous.py L1726）resume，回溯可能选中 merge（`merged` milestone 在排除列表外）而非 development——此时注入的 user_feedback 已持久化，延迟到 development 阶段才被 dev prompt 消费（`_get_user_feedback_prompt` L10591），不产生错误行为；#331 实测路径（cancel-milestone-with-feedback）回溯选中 development，即时消费。
- 顺带注记（不在本方案实施）：**非 rejected** 场景（feedback 恒空 + auto_merge=true + PR 存在）`_do_wait` 仍直通 merge 阶段（已合并 PR 的 merge 失败路径，属 merge 阶段加固，另行观察）；rejected + 守卫不满足的**后续 tick** 同样直通 merge（修复轮正常交付路径，v6 语义）；rejected + 守卫满足的第一 tick 由注入改道 resume-development，不再直通。

### 2.5 明确不改的行为

- `verification_status` 为空 / confirmed / **indeterminate** 的 timing-issue：维持现有 completed 短路（Issue #1552 原始场景；indeterminate 有专门守护用例，见三-4）。
- `not has_changes and not is_timing_issue`（no-changes 路径）：不动。
- acceptance_verification 的 rejected → pause 主路径：不动（人工审核仍是第一现场；2.4 只在 resume 时补 feedback，2.1 只接管 resume 后无产出的兜底）。
- `_do_wait` 的 milestone 回溯与 feedback 清空机制：不动。
- timing-issue / no-changes 的 issue 评论与 completed 里程碑文案：不动。

## 三、测试计划

### 新增（tests/autonomous/phases/test_pr_review.py，沿用 `_gh/_host/_deps/_ctx/_workflow` 基建；所有用例**显式** `host.dev_round_cap_remaining.return_value = N`，不依赖 MagicMock 比较的隐式 truthy）

1. `test_timing_issue_with_rejected_verification_reopens_development`
   - 前置：分支是 main 祖先（复用 `merge-base --is-ancestor` 返回 0 的 `_run_git` side_effect）、`github_pr_number=2851`、`verification_status="rejected"`、`dev_round=1`、`host.dev_round_cap_remaining.return_value=2`、`verification_report` 含一个 rejected gate。
   - 断言：`outcome=="completed"`、`next_phase=="development"`、`next_status=="developing"`；patch：`dev_round==2`、`current_round==0`、`verification_status is None`、`user_feedback` 含失败项名；milestone `acceptance_rejected_reopened` 且 `dev_round==2`（与 title 同轮次）；`host.emit_phase_change` 以 `{"phase": "development", ...}` 被调用；**不** emit `{"phase": "completed"}`；`post_github_comment` 未被调用。
2. `test_reopen_respects_dev_round_cap_and_fails`
   - 同用例 1 但 `dev_round=3`、`host.dev_round_cap_remaining.return_value=0` → `outcome=="failed"`、`structured_error["message"]` 含 "dev-round cap" 与 PR 号（error_message 落库源）、milestone `acceptance_rejected_cap_exhausted`；不 emit completed。
3. `test_reopen_with_unparseable_report_uses_default_feedback`
   - `verification_report="{broken json"` → 仍重开 development；`user_feedback` 为含 "REJECTED" 与 PR 号的非空默认文本。
4. `test_reopen_omitted_when_verification_not_rejected`
   - 参数化 `verification_status in (None, "confirmed", "indeterminate")` + 分支祖先 + `github_pr_number=1234` → 维持现有 completed 短路 + timing_issue milestone（守护 indeterminate，审查意见 7a）。
5. `test_reopen_requires_recorded_pr`
   - rejected + 分支祖先 + `github_pr_number=None`（且 host 侧也为 None）→ 现有 completed 短路（守护触发条件第三项）。

### 新增（tests/autonomous/test_orchestrator_characterization.py——`_do_wait` 既有测试基建在此文件 L875 起 `test_wait_phase_returns_phase_result_not_inline_commit`，含 `_active_workflow`/monkeypatch 基建；tests/issues/2335/ 无 `_do_wait` 基建，不适用）

6. `test_wait_injects_rejection_feedback_on_resume`
   - waiting + `verification_status="rejected"` + report 含 failed gate + `user_feedback=""` + milestones 最新 completed 为 `requirement_received`（v6 守卫 (b) 证据）→ resume 到 development 的 patch `user_feedback` 含失败项（**持久化断言**，守护二轮意见 3）；dev_round+1。
7. `test_wait_appends_rejection_feedback_to_human_feedback`
   - 同上但 `user_feedback="use sqlite"` → patch 的 user_feedback 同时含 "use sqlite" 与 verifier 失败项（追加语义）。
8. `test_wait_no_injection_when_not_rejected`
   - `verification_status=None`/`"confirmed"` → patch 无 user_feedback 写入或为原值（不注入）。

### 新增（v6，tests/autonomous/test_orchestrator_characterization.py，守护五轮审查 critical 意见 1 的一次性防护）

9. `test_wait_no_reinjection_on_repair_round_second_tick`
   - 修复轮 report→wait 的第二 tick 形态：`user_feedback=""`、`verification_status="rejected"`、`github_pr_number=2851`、auto_merge 默认 true、milestones 最新 completed 为 `round_completed`（phase=report，status=completed）→ **不注入**：`next_phase=="merge"`（auto-merge 直通回归）且 patch 无 user_feedback。此为 v6 守卫的核心守护用例（v5 实现下必失败：注入会劫持为 resume-development）。
10. `test_wait_injects_on_cancel_without_feedback_via_requirement_milestone`
   - cancel-with-feedback 空 feedback 变体：`user_feedback=""`、rejected、milestones 最新 completed 为 `requirement_received`（status=completed）→ 注入填充 → resume-development（`next_phase=="development"`），patch user_feedback 含失败项。（实现注记：该场景即用例 6 的 v6 前置（同为空 feedback + requirement_received 证据 + 注入持久化断言），实现时并入用例 6，不重复造用例。）
11. `test_wait_no_injection_when_latest_completed_is_wait_started`
   - 普通等待轮（无人工介入）：`user_feedback=""`、rejected、milestones 最新 completed 为 `wait_started` → 不注入 → 保持等待（`outcome=="wait"`）。守护"纯等待轮不注入"语义。

### 回归（已有测试不改即守护；现有 timing-issue/no-changes 用例均传 `github_pr_number=None`，天然不触发新路径——审查意见 7c 的 None 场景由此守护）

- `test_timing_issue_marks_completed_with_timing_milestone`
- `test_no_changes_returns_completed_with_literal_current_phase`
- `test_round1_creates_new_pr_when_recorded_pr_merged` 等 PR #2906 用例（rejected 短路在 PR 检查之前返回，互斥不冲突；非 rejected 场景仍走到 PR 状态检查）

## 四、实施步骤

1. `git checkout -b fix/acceptance-rejected-timing-issue-shortcircuit origin/main`
2. `autonomous/constants.py`（已存在）：新增 `MAX_ACCEPTANCE_DEV_ROUNDS = 3`（含 #2335 注释）；orchestrator.py 导入列表加该名（`# noqa: F401` 再导出）并删除本地定义块（`MAX_ACCEPTANCE_DEV_ROUNDS = 3` 定义行 + 其上方 4 行注释，工作区基准 L1742-1746）
3. acceptance_verification.py：新增 `_rejection_feedback(wf, pr_number)`（复用 `feedback_prefill_from_report`，含 `delivery` None 回退）
4. pr_review.py：短路前判定（2.1）+ 导入 `_rejection_feedback`（`from .acceptance_verification import`）与 `MAX_ACCEPTANCE_DEV_ROUNDS`（`from ..constants import`，与既有 L71-81 导入同源）
5. orchestrator.py：`_do_wait` feedback 注入（2.4，resume return 的 workflow_patch 增补 `"user_feedback"`；导入用绝对路径 `from app.modules.workspace.autonomous.phases.acceptance_verification import _rejection_feedback`）
6. 测试：新增用例 1-8（pr_review 5 个 + test_orchestrator_characterization 3 个）
7. 全量回归：`HOME=/tmp/fakehome python -m pytest tests/autonomous/phases/test_pr_review.py tests/autonomous/test_orchestrator_characterization.py -x -q`（fakehome 隔离本机 agent-launcher.conf 的并发覆盖）
8. 独立 agent 审查代码 → 零意见 → PR → 独立 agent 审查 PR → 零意见 → 合并
9. 部署 192.168.31.159（git pull + rsync + alembic + systemctl restart，选工作流无活跃锁窗口）
10. 重置 #331（显式设置 dev_round，一轮意见 9）：
   ```sql
   UPDATE autonomous_workflows SET status='queued', current_phase='pr_review',
     current_round=0, dev_round=1, verification_status='rejected',
     github_pr_number=2851, error_message='', locked_at=NULL, locked_by=NULL
   WHERE id=331;
   ```
   验证链：pr_review →（分支祖先 + rejected + remaining=2）→ 重开 development（dev_round=2、user_feedback 含 `call-chain:tenant_repo`）→ dev 产出接线修复 → 新 PR → merge → 验收。观察点：dev prompt 实际收到失败项、milestone `acceptance_rejected_reopened` 落库。

## 五、风险与回滚

- 风险 1：rejected 判定误触发（verification_status 残留 rejected 但实际已人工处理）→ 防护：dev_round cap（2.1 failed 分支）+ 单次重开（verification_status 清空）+ issue 评论留痕；最坏多跑一轮 dev 或一次显式 failed（人工可见原因）。
- 风险 2：next_phase="development" 的状态迁移与调度器预期不符 → `_do_wait` 同型先例 + 用例断言 next_status；调度器按 status=developing 正常选中。
- 风险 3：`_do_wait` 注入改变了人工 resume 的 feedback 语义（追加而非覆盖）→ 人工意见保留在前、verifier 项在后；用例 7 守护；dev prompt 读后清空不变。
- 回滚：四文件小改动（constants 迁移 + 一个共享辅助 + 两处消费 + `_do_wait` 注入），revert PR 即可；DB 无 schema 变更、无迁移。

## 六、v1 → v2 → v3 修订记录（独立审查意见对照）

### v1 → v2（一轮，9 条）

| 意见 | 修订 |
|---|---|
| 1（major）PR 编号缺失/未定义变量 | 2.1 补 `pr_number` 双读取并纳入条件与文案 |
| 2（major）遗漏 emit_phase_change | 2.1 补 emit `{"phase": "development", ...}`；用例 1 断言 |
| 3（major）只解析 gates 漏 scope/verifier | 2.2 复用 `feedback_prefill_from_report`（三类遍历） |
| 4（major）cap 耗尽应 fail 而非 completed | 2.1 cap 分支改 failed 终态；用例 2 |
| 5（major）主路径 feedback 缺口 | 新增 2.4 `_do_wait` 注入；用例 6-8 |
| 6（minor）行号 L8857 | 已更正 |
| 7（minor）测试缺口 | 用例 4 参数化 indeterminate；全部用例显式 mock cap；回归节注明 None 场景守护者 |
| 8（minor）milestone 轮次不自洽 | milestone dev_round 与 title 统一取新轮次 |
| 9（minor）重置 SQL 未处理 dev_round | 实施步骤 10 显式 `dev_round=1` 并设观察点 |

### v2 → v3（二轮，8 条）

| 意见 | 修订 |
|---|---|
| 1（critical）`PhaseResult.failed()` 不接受 milestone_events → TypeError | 2.1 cap 分支改 dataclass 直构 `PhaseResult(outcome="failed", ...)` |
| 2（critical）`MAX_ACCEPTANCE_DEV_ROUNDS` 循环导入（orchestrator L84→phases/__init__→pr_review） | 常量迁至 `phases/constants.py`，orchestrator 再导出兼容；要点节与实施步骤 2 |
| 3（major）`_do_wait` 注入不持久化（dev prompt 读 DB 字段） | 2.4 resume return 的 workflow_patch 增补 `"user_feedback"`；用例 6 持久化断言 |
| 4（major）error_message 双写被 structured_error 覆盖 | cap 分支详细文案只放 `structured_error["message"]`，patch 不携带 error_message；用例 2 断言改 structured_error |
| 5（minor）`_rejection_feedback` 归属地文档矛盾 | 2.2 统一为 acceptance_verification.py（已验证无导入环） |
| 6（minor）pr_number None → "PR #None" 进 prompt | 2.2 `delivery` 回退 "the previous delivery" |
| 7（minor）重开分支未清 pause 期 error_message 残留 | 2.1 patch 增补 `"error_message": ""`（先例 L277） |
| 8（minor）用例 6-8 挂载点 tests/issues/2335/ 无 `_do_wait` 基建 | 改为 tests/autonomous/test_orchestrator_characterization.py（L875 起既有基建）；回归命令同步更新 |

### v3 → v4（三轮，6 条）

| 意见 | 修订 |
|---|---|
| 1（major）`phases/constants.py` 不存在、先例指向错误 | 迁移目标改为**已存在的** `autonomous/constants.py`（pr_review L71-81 既有导入源）；要点节与实施步骤 2/4 同步更正 |
| 2（minor）用例 2 断言与 fail_msg 文案不匹配 | fail_msg 措辞改为含 "dev-round cap ... exhausted"（与断言子串一致） |
| 3（minor）2.4 注记与注入行为矛盾 | 注记改写：非 rejected 场景仍直通 merge；rejected 场景由 2.4 兜底改道 |
| 4（minor）orchestrator 相对导入 `from .acceptance_verification` 即 ImportError | 2.2 分别写明两处导入路径（pr_review 相对、orchestrator 绝对） |
| 5（minor）resume-with-feedback 回溯选中 merge 的边界未说明 | 2.4 增补回溯边界说明（feedback 延迟消费，不产生错误行为） |
| 6（minor）行号漂移 | L1745/L354/L344-351 已更正 |

### v4 → v5（四轮，2 条，同根因：行号基准）

| 意见 | 修订 |
|---|---|
| 1（major）行号体系对应工作区 d015c45d 而非声称的 origin/main，系统性偏移约 60 行 | 头部新增**行号基准声明**（工作区 `d015c45d` 基准、与 main 偏移说明、实施一律以符号定位为准）；两版本差异（#2867 hunk）不触碰本方案改动点，逻辑在 origin/main 上结构成立 |
| 2（minor）残留行号失准且文档内自相矛盾（L1745/L354 vs L356） | 统一为工作区基准：`MAX_ACCEPTANCE_DEV_ROUNDS` 定义 L1746（迁移块 L1742-1746，含定义行）、`feedback_prefill_from_report` L356、`_failed_items` L346-353；行号均加"工作区基准"标注 |

### v5 → v6（五轮代码审查，3 条：1 critical / 1 major / 1 minor，均同根因）

| 意见 | 修订 |
|---|---|
| 1（critical）`_do_wait` 注入缺"一次性"防护：`verification_status='rejected'` 跨轮存活（唯二清空点均不在修复轮路径上），修复轮 report→wait 第二 tick 时注入再次触发，劫持 auto-merge 直通成 report↔wait 无限循环（dev_round 无上界递增，cap 只在 pr_review not has_changes 拦截里检查，dev 有提交时永不触发） | 2.4 注入条件增加**新鲜人工 resume 守卫**：`(a) user_feedback 非空 ∨ (b) 最新 completed milestone 是 requirement_received`。(a) 覆盖两条 resume 路由的带 feedback 情形（cancel-with-feedback / resume-with-feedback 后者必非空）；(b) 覆盖 cancel 无 feedback 变体（该路由创建 requirement_received milestone；注意 resume-with-feedback 路由**不创建** milestone，故 (b) 不能单独作守卫）。拒绝审查否决的替代方案"注入时清空 verification_status"（会废掉 2.1 组件 A 的兜底拦截）。新增用例 9-11 |
| 2（major）测试缺口：无用例覆盖"rejected 状态跨轮存活到第二个 wait tick" | 用例 9（v5 实现下必失败，作 v6 守护测试）+ 用例 10/11 覆盖守卫两个分支；用例 6 前置更新（加 requirement_received 证据） |
| 3（minor）`if rejection_fb:` 死守卫（两分支恒返回非空） | 保留（守卫条件改造后该判断语义为"防御 _rejection_feedback 未来引入空返回路径"，不再死代码；v6 代码注释说明） |

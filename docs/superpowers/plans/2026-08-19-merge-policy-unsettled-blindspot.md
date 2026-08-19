# Plan: merge-policy pause 的"未结算盲区"(required 缺席 ≠ 已结算)

日期:2026-08-19 · 分类:class-2(自主系统自身 bug)· 分支:`fix/merge-policy-unsettled-blindspot`(base origin/main @ da452a06)

## 1. 事故(主证据)

- 工作流 9676f33f(issue #2778)/ PR #2804,2026-08-19 01:44:29(服务器本地)在 merge 阶段被 pause:
  `Merge blocked by repository policy: PR #2804 is not merge-ready (state=blocked).`
- 时间线(统一 UTC;journalctl 原文为服务器本地 UTC+8,已换算):
  - 17:38:04 `PR #2804: merge rejected by policy but CI has not settled (state=blocked, any_pending=True); deferring` —— #2503 的守卫正常工作
  - 17:38:37 CI-repair agent 启动;**17:43 推送 `c89d2b39 auto: ci repair (attempt 1)`**(新 head SHA,gh committedDate 为 UTC)
  - 17:44:15 merge 阶段推进;**17:44:29(推送后 ~90s)误 pause**
  - 18:08 人工核验:PR #2804 全绿 CLEAN;reset 后 18:19 一次 merge 成功 → pause 依据纯属瞬态
- 误判链:新 SHA 的检查 run 部分创建(快速检查已有,**required 聚合门 `PR Gate` 尚未创建**)→
  `zero_check_runs_fallback` 因 `if checks: return False` 跳过(它只管"完全零 run"的 #2673 事件丢失场景)→
  无 pending 桶、无 fail 桶 → 尝试 merge 被拒(required 缺席)→ refresh 后仍无 pending →
  `any_pending=False` + `state=blocked` → 进入"真实策略阻塞"分支 → 不可自恢复 pause。

## 2. 根因

`phases/merge.py` 策略-pause 分支把"rollup 中无 pending"等价于"CI 已结算"。但**required 上下文对当前
head SHA 完全缺席**(非 pending、非 fail)有两种语义:

1. 推送后检查 run 创建延迟(瞬态,分钟级自愈)——本例;
2. required 上下文配置了但没有任何 provider(仓库配置错误,永不到来)。

现有代码无法区分,一律 pause。#2503 已修"underlying job pending 被过滤"的窗口,本缺口是其注释
(``aggregate gate ... propagation window``)承认的残余。

## 3. 方案(选定:required 完备性 + head 新鲜度双条件 defer)

在策略-pause 分支、`any_pending`/`unknown` defer 之后、pause 之前增加:

```python
required = _required_contexts(gh, pr_number, base_branch)
if required:
    missing = required - {(c.get("name") or "") for c in refreshed_checks}
    if missing and _head_freshly_pushed(gh, pr_head_sha):   # head commit < 宽限窗
        return PhaseResult.retry()   # 未结算,继续轮询
```

- `_head_freshly_pushed`:新增 `GitHubOps.get_commit_committed_at(sha)`
  (`gh api repos/{repo}/commits/{sha}` → `commit.committer.date`,解析为 aware datetime)。
  在**罕见的被拒路径**才调用,非热路径;取不到时间 → 返回 False(fail-closed,维持 pause,
  监控兜底再分类——与 #1989 fail-closed 精神一致)。
  🔴 **py3.10 兼容**(独立审查 blocking):GitHub 时间戳带 `Z` 后缀,`fromisoformat`
  3.11+ 才支持;必须先 `replace("Z", "+00:00")`,并给解析函数加直接单测(真实 `...Z`
  字符串 + 不可解析 → None),否则修复在 3.10 上静默 no-op。
- 宽限窗常量 `_POLICY_SETTLE_GRACE_SECONDS = 1200`:**对齐 orchestrator 既有的
  `ZERO_CHECK_RUNS_WALL_CLOCK_FLOOR = 1200`**(独立审查 blocking:同一类"检查 run
  创建/provisioning 延迟",代码库经验值就是 20 分钟;取 600 无依据且慢 provisioning
  场景会复发)。超窗仍缺席 → 维持 pause(保留"永不到来的 required = 仓库配置错误,
  需人工修 ruleset"的既有保证)。commit 时间为未来值(时钟偏移)→ 负差值 < 窗 →
  视为 fresh,defer(偏安全方向)。
- 名字匹配为字面比对(与 `_blocking_pending` 同款局限):matrix 展开名/门改名会造成
  永久 missing → 至多多 defer 一个宽限窗后仍 pause,与现状同向,可接受(实现注释注明)。
- `_required_contexts` 返回 None(不可观测)→ 不 defer,维持 pause(现状,降级不越权)。

### 备选与否决理由

- **空 rollup 才 defer**(`if not refreshed_checks`):覆盖不了部分创建场景(本例 rollup 非空)。否决。
- **永久 defer 所有 required 缺席**:破坏"配置错误需人工介入"保证,未配置 provider 的 required 上下文会无限轮询。否决。
- **复用 #2673 的 zero_check_runs milestone 状态机**:那是 orchestrator 侧、针对"完全零 run"的重触发机制(_CLOSE/REOPEN),语义不同且重;本修只需在 pause 前多一次判别。否决。
- **用 refreshed_checks 里最新完成时间推断活跃度**:缺席的 run 没有时间戳,推断不可靠。否决。

## 4. 测试(TDD,先红后绿;tests/autonomous/phases/test_merge.py)

1. `test_merge_policy_block_with_missing_required_on_fresh_head_defers_not_pauses`
   —— checks=[非 required 快速检查 pass],protection required=["PR Gate"],state=blocked,
   merge 抛 policy 拒绝,`get_commit_committed_at` 回 90s 前 → **retry**(现状 pause,先红)。
2. `test_merge_policy_block_with_missing_required_on_stale_head_still_pauses`
   —— 同上但 commit 时间 2h 前 → **pause**(配置错误路径不回归)。
3. `test_merge_policy_block_with_required_present_still_pauses`
   —— required 全在场且无 pending(既有 settled 场景,L323 已有,确保不被新逻辑扰动——跑既有测试即可)。
4. commit 时间不可得(helper 抛异常/返回 None)→ pause(fail-closed)。
5. (审查补充)protection 查询抛异常(required 不可观测)→ 仍 pause,锁住降级契约。
6. (审查补充)required 齐全时 `get_commit_committed_at` **不被调用**(短路顺序)。
7. (审查补充,github_ops 层)`parse_github_iso_datetime`:真实 `2026-08-18T17:43:12Z`
   → aware datetime;空串/乱串 → None。py3.10 下 Z 解析是修复生效的前提。

## 5. 验证 Gap 对应

- lane:`tests/autonomous/phases/` 在 CI `test(3.x)` 收集范围(非 tests/issues opt-in)。
- 本地全量跑 `tests/autonomous/phases/test_merge.py`;对照 clean origin/main worktree 排除环境性失败。
- PR 文本**不含** closes/fixes/resolves + #N(auto-close 陷阱)。

## 6. 部署与收尾

- app/ 热补丁 cp + chown openace(`github_ops.py`、`phases/merge.py`);`systemctl restart openace-scheduler.service`(编排改动);无迁移。
- prod 验证:观察后续 auto-dev merge 周期不再出现同签名误 pause;既有 deferring 日志语义不变。

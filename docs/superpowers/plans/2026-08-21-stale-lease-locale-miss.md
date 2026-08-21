# 修复方案：zh_CN locale 下 missing-remote-ref fetch 错误未被识别，stale-lease 远程分支重建恢复失效（Bug 6）

- 日期：2026-08-21
- v2（独立审查 PASS + 3 条非阻塞 MINOR 已并入）：① 2.1 注释记录 locale 无关的长期判别方案（`git ls-remote --exit-code`）；② 测试计划增加恢复分支 warning 日志断言；③ 部署步骤 4 增加 journalctl 即时验证信号。
- 分支：`fix/stale-lease-locale-keywords`（基于 `origin/main`）
- 生产环境：192.168.31.159（openace-scheduler.service，PostgreSQL `openace` 库）
- 受害工作流：#322（9be278b1，pr_review 阶段 failed）、#340（04b0b260，pr_review 阶段 failed）；#329（4a2d6e76，development r3 运行中，进入 pr_review 后必然撞同一堵墙）
- 前置：Bug 5 修复（PR #2915，已合并部署）使 rejected 修复轮正确重建隔离 worktree 并重开 development；本 bug 是修复轮**交付推送阶段**暴露的下一层缺陷。
- **行号基准声明**：行号对应工作区当前 `origin/main` 检出；实施时一律以符号（函数名/常量名/语句）定位为准。

## 一、Bug 描述

### 1.1 生产证据（journalctl，2026-08-21 02:22 CST = UTC 18:22）

三个工作流 r1 的 PR 合并后，GitHub 删除了远程分支 `auto-dev/<wf>`（merge 后自动删分支）。Bug 5 修复使 r3 修复轮重建了**本地**分支并在隔离 worktree 中完成开发；pr_review 阶段推送时：

```
pr_review - WARNING - force-with-lease stale lease for auto-dev/9be278b1-d63; fetching origin and retrying
github_ops - WARNING - lease-refresh fetch failed: git fetch origin auto-dev/9be278b1-d63 failed (exit 128):
致命错误：无法找到远程引用 auto-dev/9be278b1-d63        ← zh_CN locale 的 git 错误消息
pr_review - WARNING - Transient push failure for branch auto-dev/9be278b1-d63: git push origin auto-dev/9be278b1-d63 --force-with-lease failed (exit 1): To https://github.com/open-ace/open-ace.git
 ! [rejected]          auto-dev/9be278b1-d63 -> auto-dev/9be278b1-d63 (stale info)   ← 拒绝原因仍是英文
orchestrator - ERROR - Transient error retry exhausted for workflow 9be278b1 after 6 attempts
orchestrator - ERROR - Orchestrator error in pr_review: git push ... --force-with-lease failed ...
github_ops - INFO - Removed worktree at /home/dwu/open-ace-01/open-ace/.worktrees/9be278b1-...   ← 失败清理连 worktree 一起删了
```

#340（04b0b260）同型失败（attempt 5/6 → 6/6 → exhausted）。两个工作流均转 `failed`，worktree 目录被失败清理移除（本地分支仍在）。

### 1.2 事件链（每一步都有代码依据）

1. `git push origin auto-dev/X --force-with-lease`：本地陈旧的 `refs/remotes/origin/auto-dev/X`（r1 推送时留下）与远端实际状态（分支已被删除）不一致 → git 拒绝，`(stale info)`。
2. `github_ops.git_push` 的恢复路径（L2766 起）：`_is_stale_lease_rejection("...stale info...")` → True → 执行 `git fetch origin auto-dev/X` 刷新 lease。
3. 远端分支不存在 → fetch 失败 exit 128，stderr 为 **zh_CN**：`致命错误：无法找到远程引用 auto-dev/X`。
4. `_is_missing_remote_ref_fetch_error`（L214）只匹配英文关键词（`_MISSING_REMOTE_REF_FETCH_KEYWORDS`，L206-211：`"couldn't find remote ref"` 等）→ **False** → 走 `raise e from fetch_err`（L2800-2801），原始 push 错误上抛。
5. 编排器将含 `stale info` 的错误判为 transient，Layer-2 重试 6 次——每次都精确重复步骤 1-4（确定性失败，非瞬时）→ 重试耗尽 → 工作流 `failed`。

**本应发生的行为**（既有设计，issue #2870 已实现）：步骤 4 识别出"远端分支已不存在" → 放弃 lease、对已验证的 auto-dev 分支执行**普通 push**（L2789-2799）重建远程分支 → 推送成功 → 继续 PR 流程。该设计在英文 locale 下有回归测试（`tests/unit/test_deleted_remote_branch_push_recovery.py`），在 zh_CN 服务器上因错误消息本地化而从未生效。

### 1.3 根因

`_MISSING_REMOTE_REF_FETCH_KEYWORDS` 是纯英文关键词表；生产服务器 git 运行在 zh_CN locale 下，`fatal: couldn't find remote ref` 被翻译为 `致命错误：无法找到远程引用`，关键词失配，恢复分支被跳过。

**为什么不能从根上强制 git 输出英文**：`_run_git`（L745 起）经 `sudo -u <account> git ...` 执行——sudo 默认 `env_reset` 剥离 `LC_ALL`/`LANG`；sudoers 只白名单裸 `git` 命令名，无法用 `env LC_ALL=C git ...` 前缀（会被 sudoers 拒绝）；修改 sudoers `env_keep` 属服务器配置而非源码修复（且用户约定修复应落在源码）。git 也没有控制输出消息语言的 config 项。因此**关键词表补齐 zh_CN 译文是本仓库内唯一可行的源码级修复**。

**为什么推送侧无需同样处理**：git 的 push 拒绝原因（`stale info` / `fetch first` / `non-fast-forward`）在该服务器 locale 下未被翻译（生产日志证据：`(stale info)` 保持英文），`_FORCE_WITH_LEASE_REFRESH_KEYWORDS` 工作正常。

## 二、修复设计

### 2.1 改动（github_ops.py，唯一改动点）

`_MISSING_REMOTE_REF_FETCH_KEYWORDS`（L206-211）追加 zh_CN 译文关键词：

```python
_MISSING_REMOTE_REF_FETCH_KEYWORDS = (
    "couldn't find remote ref",
    "could not find remote ref",
    "couldn't find remote branch",
    "could not find remote branch",
    # zh_CN locale: the production server's git localizes the missing-ref
    # fetch failure as "致命错误：无法找到远程引用 <ref>" (#322/#340: the
    # English-only list skipped the plain-push recovery and the workflow
    # burned all 6 transient retries on a deterministic failure).
    # _is_missing_remote_ref_fetch_error lowercases the message; Chinese
    # characters are unaffected by .lower().
    "无法找到远程引用",
    "无法找到远程分支",
)
```

长期演进（本次不做）：关键词表本质是 locale 追逐；若将来服务器更换 locale 再次失配，可在普通 push 前改用 locale 无关的判别——`git ls-remote --exit-code origin <branch>`（exit 2 = 无匹配 ref）确认远端缺失。

说明：
- 只加有生产日志证据的 `无法找到远程引用`，以及同源对称的分支变体 `无法找到远程分支`（对应英文表里的 branch 两条）；不加无证据的其他译文（如 zh_TW `無法找到遠端引用`），避免猜测性匹配。
- `.lower()` 对中文无影响，现有判定函数无需改动。
- 恢复分支的其余逻辑（L2785-2802：fetch 失败 → 识别远端缺失 → 对已通过 `auto-dev/` 前缀验证的分支普通 push 重建）**不动**——该逻辑本身正确，只是入口判定被 locale 挡住。

### 2.2 明确的非目标

- `_TRANSIENT_ERROR_KEYWORDS` 等其他关键词表的 locale 覆盖：没有生产证据表明已造成误判（网络类错误消息通常携带英文 URL/主机名片段），不在本次范围；若未来出现再按同模式补齐。
- sudoers / locale 服务器配置调整：见 1.3，不可行且违反"修复落在源码"的约定。

## 三、测试计划

扩展 `tests/unit/test_deleted_remote_branch_push_recovery.py`（该文件即 issue #2870 恢复路径的回归测试）：

1. **新增用例 `test_stale_info_with_zh_cn_missing_remote_ref_plain_pushes`**：三段 `side_effect` 与现有英文用例同构——push 拒绝（英文 stale info，与生产一致）→ fetch 失败 stderr 为生产日志原文 `致命错误：无法找到远程引用 auto-dev/abc12345`（exit 128）→ 普通 push 成功。断言：第三条命令是 push 且**不带** `--force-with-lease`（即恢复生效），不抛异常；并用 caplog 断言出现恢复分支 warning 日志 `plain-pushing validated auto-dev branch to recreate it`（证明走的是恢复路径而非旁路）。
2. 既有 4 个用例保持不动（英文恢复、fetch 成功保 lease 重试、网络错误不上抛恢复、非 auto-dev 分支拒绝）。

运行：`HOME=/tmp/fakehome python3 -m pytest tests/unit/test_deleted_remote_branch_push_recovery.py tests/unit/test_git_push_stale_lease.py -q`（隔离本机 `~/.open-ace/agent-launcher.conf` 对并发配置的覆盖），再跑全量回归确认无副作用。

## 四、部署与恢复步骤

1. 合并 PR 后，rsync 同步 `app/modules/workspace/autonomous/github_ops.py` 到 `/home/openace/`（服务器为文件同步式部署，非 git 仓库），重启 `openace-scheduler.service`。
2. **#329 竞态处理**：其 development r3 完成后进入 pr_review 即触发同型失败（同一 dwu 主仓库、陈旧 remote-tracking ref 同样存在）。若部署先于其撞墙 → 直接成功；若先撞墙 → 与 #322/#340 一并按下一步恢复。
3. **恢复 #322/#340**（通过 API resume，不直接改库）：resume 后 pr_review 重入 → `ensure_worktree` 发现目录被失败清理移除但本地分支存活 → 按存活分支重建 worktree → push 走（已修复的）恢复路径普通 push 重建远程分支 → 继续建 PR/评审流程。#322 的 `github_pr_number=2910` 指向 r1 已合并 PR，pr_review 现有 PR 活性检查（PR #2906 已部署）会在 round 1 对非 OPEN PR 清空复用并新建 PR。
4. 观察三个工作流推进至 merge/acceptance，确认远程分支重建、PR 创建成功。即时验证信号：journalctl 中出现 `plain-pushing validated auto-dev branch to recreate it` 日志即证明修复生效，无需等到 merge/acceptance。

## 五、风险与回退

- 改动为纯关键词追加，无行为分支变化；最坏情况仍是现状（恢复不生效、走 transient 重试），不存在新增恶化路径。
- 误匹配面分析：`无法找到远程引用/分支` 仅在 git fetch 确实报"远端无此引用"时出现；普通 push 的前提是该分支已通过 `auto-dev/` 前缀验证（L2748），且 fetch 已证明远端缺失，不会覆盖任何远端已有内容。
- 回退：还原单文件即可。

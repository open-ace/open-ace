# #2694 计划：回退 worktree node_modules shim（B1）+ 懒加载依赖准备

## 背景与决策（2026-08-15，用户确认）

- shim（commit `e6fba763`，08-11）以 `sudo -u <owner> bash -c` 执行，被 sudoers 有意拒绝
  （#2650 root-RCE 面）→ **跨用户 prod 从未成功**；fail-soft 每 advance 记一条
  `frontend_node_modules_shim_failed` milestone（4 天 5255 条，#2550 wait 轮询贡献 3707）。
- 用户判定 wrapper 方案（PR #2695，加 sudoers 白名单）不具通用性：open-ace 是通用产品，
  shim 硬编码了自身 `frontend/` 布局，别的项目 no-op。走 **B1 回退 + 零配置懒加载**。
- 代码注释里的 "#23" 是**错误 issue 引用**（真实 issue #23 是无关主题）；受害工作流是
  c88afdc0/83ffb529/ee678c63（commit message 记录）。

## B1 回退（本 PR）

纯删除：

- `git_workspace.py`：删 `_FRONTEND_NODE_MODULES_CACHE_DIRS`、`_build_node_modules_shim_script`、
  `_ensure_frontend_node_modules_shim(_impl)`、ensure_worktree 两处调用点；`shlex` import。
- `github_ops.py`：删 `_run_as_account`（唯一调用方是 shim）。
- `tests/autonomous/test_git_workspace_node_modules_shim.py`：删 5 个 shim 测试 +
  相关 import；**保留** cleanup_* 测试（测 #2505 worktree_path 保留，与 shim 无关）。
- `tests/unit (ex tests/issues/814)/test_worktree_path_selfheal.py`：更新两处过时注释（shim 不复存在）。

验证：grep 无残留符号；`tests/autonomous/` + issue-814 全量本地跑；无迁移，无 schema 影响。

## 懒加载依赖准备（后续 PR，新 issue）

1. 开发任务指令规则：跑前端测试/构建前检查 worktree `node_modules`，缺且仓库有锁文件
   → 按锁文件安装（package-lock→npm ci；pnpm-lock→pnpm i --frozen-lockfile；
   yarn.lock→yarn；bun.lockb→bun install）。纯后端任务零成本。
2. 失败签名翻译：测试输出命中 `EACCES ... node_modules/.vite-temp` /
   `Cannot find module ... vitest` 时，重试指令（#2663 fresh-retry 通道）注入
   "worktree 缺依赖，先按锁文件安装"。

## 部署与收尾

- 部署：app/ 热补丁（git_workspace.py、github_ops.py）+ 重启 openace-scheduler。
- 验证：#2550 wait 轮询不再产生 shim_failed milestone（journalctl + DB count 停增）。
- DB 清理：删除存量 `frontend_node_modules_shim_failed`，每工作流保留最新 1 条。
- issue 回填：#2694 记录结论；PR #2695 关闭（wrapper 方案废弃）。

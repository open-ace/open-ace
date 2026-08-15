# #2491 测试修复轮：与本 PR 主体无关的 app/ 修复动机与耦合核验

本文件说明 #2491 PR（E2E 治理基线 P1）中混入的 4 处非治理改动的动机、
根因证据与部署耦合核验。这些改动由自动化测试重试轮（structured evidence 判定
16 failed / 3 errors）强制引入：全量 `tests/unit` 在本地（PG 配置的开发机 +
agent sandbox 环境）系统性失败，其中一组失败连锁污染了无关测试。

## 1. `app/repositories/database.py`：Database 实例方言一致性

**根因**：`Database(db_url="sqlite:///tmp/...")` 的 `connection()` 正确按
实例 URL 选后端，但 `execute`/`fetch_one`/`fetch_all` 调用**模块级**
`is_postgresql()`/`adapt_sql()`（跟随全局 DATABASE_URL）。在全局配置为 PG 的
开发机上，sqlite 实例拿到 sqlite 连接却被套上 `%s` 占位符与
`cursor_factory=RealDictCursor` → `test_dingtalk_org_sync` /
`test_feishu_org_sync` 崩溃。

**修复**：新增实例方法 `_adapt_sql`（仅当 `self._is_postgresql` 才转换），
三个查询方法的 `is_postgresql()` 改为 `self._is_postgresql`。CI（无 PG 配置）
行为不变；这是实例一致性 bug 的最小修复，非测试迁就。

## 2. `app/services/data_fetch_scheduler.py`：fallback 后端归一化

**根因**：`SCHEDULER_IMPLEMENTATION=apscheduler` 但包不可用时，`start()`
静默 fallback 到 threading，而 `is_running()` 仍按 `_implementation=="apscheduler"`
检查 `self._scheduler`（None）→ 已启动的调度器报告未运行
（`test_is_running_when_started` 失败，孤立可复现）。gevent ImportError 分支同病。

**修复**：`__init__` 与 gevent fallback 处把 `_implementation` 归一化为实际
启动的后端。纯一致性修复，默认 threading 路径不受影响。

## 3. `app/modules/workspace/autonomous/github_ops.py`：OPENACE_REAL_GIT

**动机**：agent sandbox 的 PATH 首位是 orchestrator-only git guard shim
（`agent_bin/git`，56 字节），对 `merge-base` 等命令也返回 exit 126；
`test_pr_review_diff_merge_base` 的临时 repo 场景经 `GitHubOps._run_git`
执行 git 全部被拒。harness 通过 `OPENACE_REAL_GIT` 暴露真实二进制
（guardrails 测试本身断言该变量存在）。

**实现**：`_run_git` 非 sudo 分支用 `os.environ.get("OPENACE_REAL_GIT", "git")`。

**sudoers 耦合核验（审查 B2 关切）**：prod sudoers 只白名单字面 `git`
（memory: sudoers-openace-cant-bash-as-owner）。为消除“该变量泄漏进带账号路径
→ `sudo -u <account> /usr/bin/git` 被拒且 fail-soft 掩盖”的风险，**sudo 分支
强制使用字面 `"git"`**，`OPENACE_REAL_GIT` 仅在非 sudo 分支生效——即使变量
泄漏到生产编排进程，sudo 路径也不受影响。默认值未变（additive + gated）。

## 4. 四个测试 hermeticity fixture（sqlite 强制）

`test_api_key_proxy_sqlite_row_2545.py` / `test_api_key_proxy_issue_1811.py`
patch `api_key_proxy.is_postgresql→False`；dingtalk/feishu org sync 的
`sync_env` 额外 patch `app.repositories.database.is_postgresql→False`。
模式与 main 上 `test_run_timeline_repo.py` 的既有注释一致（"tests are correct
regardless of the dev box's configured DATABASE_URL"）。断言未改弱。
这同时消除了 PG 池连接泄漏 → `test_autonomous_ci_guardrails` 的
"connection pool exhausted" 连锁失败（污染复现与修复均在本轮验证）。

## 与 #2491 的关系

理想情况下 1–3 应独立成 PR；因编排器单分支提交无法拆分，在此完整记录动机、
根因证据与耦合核验以便独立 review 与回滚参考。

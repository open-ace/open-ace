# 修复方案：daily_usage 增量 UPSERT 的 PostgreSQL json 拼接操作符错误（Bug 8）

- 日期：2026-08-21
- 状态：待审查
- 影响范围：用量统计（daily_usage）写入、配额核算数据完整性
- 引入来源：commit `bc6e2067`（auto: development changes (round 1)，Issue #2732 原子增量特性），经 PR #2878 合入 main，2026-08-20 21:50 部署至生产服务器

## 1. Bug 描述

`UsageRepository._increment_usage_postgresql()` 的 UPSERT 语句在 `models_used` 合并分支中使用了 PostgreSQL 不存在的 `json || json` 拼接操作符，导致所有携带 `models_used` 的用量增量写入失败。

## 2. 生产证据

1. journalctl（openace-scheduler.service）自 2026-08-21 02:49:58 起持续报错，截至 19:00 共 190 次：
   - `app.repositories.database - WARNING - Rolling back transaction due to error: operator does not exist: json || json`
   - `app.repositories.usage_repo - ERROR - Failed to increment usage (PostgreSQL): operator does not exist: json || json`
   - `LINE 19: || EXCLUDED.models_used:... HINT: No operator matches the given name and argument types.`
2. `daily_usage` 表 `CURRENT_DATE`（2026-08-21）零行记录；最近 5 天仅 2026-08-18 有 2 行（该特性部署前的测试数据）。即该特性上线后没有任何一条用量被成功记录。
3. 服务器代码 `/home/openace/app/repositories/usage_repo.py` 与本地 main 一致（mtime 2026-08-20 21:50，buggy 代码在第 263-273 行）。

## 3. 根因分析

问题 SQL（app/repositories/usage_repo.py L263-273）：

```sql
models_used = CASE
    WHEN EXCLUDED.models_used IS NULL
    THEN daily_usage.models_used
    ELSE (
        SELECT json_agg(DISTINCT elem)
        FROM json_array_elements(
            COALESCE(daily_usage.models_used::json, '[]')
            || EXCLUDED.models_used::json
        ) elem
    )
END
```

两处缺陷：

1. **`json || json` 操作符不存在**：PostgreSQL 的 `||` JSON 拼接操作符仅定义在 `jsonb` 类型上，`json` 类型没有该操作符。`daily_usage.models_used` 列为 `text`，`::json` 转换后无法拼接。CASE 的 ELSE 分支仅在 `EXCLUDED.models_used IS NOT NULL` 时求值，因此只有携带 models 的增量才失败（工作流 AI 调用几乎都携带 model，故全量失败）。
2. **`json_agg(DISTINCT elem)` 非法**（第二处隐藏缺陷，验证时发现）：`json` 类型没有相等操作符，`DISTINCT` 聚合无法用于 `json` 元素。即使修复了 `||`，`json_agg(DISTINCT ...)` 仍会报 `could not identify an equality operator for type json`。

CI 未拦截原因：`tests/unit/test_usage_repo_increment.py` 对 PostgreSQL 分支全部 skip（L17-23），仅覆盖 SQLite 路径；该 SQL 字符串从未在测试中针对 PostgreSQL 执行过。

## 4. 影响面（含放大器）

1. 用量丢失：每次增量失败后整个事务回滚，tokens/requests/models 全部不入库。
2. 放大器（`app/modules/workspace/autonomous/agent_runner.py` L846, L900-919）：`_sync_usage_to_daily_usage()` 不检查 `increment_usage()` 返回值，失败后仍将 session 标记为 `daily_usage_synced=True`，丢失的用量永不重试。
3. 配额核算失真：调度器周期运行 `quota_enforcement` 任务，但 `daily_usage` 无当日数据，租户/用户用量配额无法正确核算。
4. 受影响调用方清单：`agent_runner._sync_usage_to_daily_usage()`（scheduler 进程，不检查返回值）；`usage_sink.DailyUsageSink.consume()`（LLM 代理/Web 进程，已正确处理 False 返回值）；`scripts/backfill_daily_usage.py`（离线脚本，已检查返回值）。仅 agent_runner 需要代码修复。

## 5. 修复设计

### 5.1 主修复（usage_repo.py）

将合并分支改为 jsonb 语义（已在生产服务器用 TEMP TABLE 验证通过）：

```sql
models_used = CASE
    WHEN EXCLUDED.models_used IS NULL
    THEN daily_usage.models_used
    ELSE (
        SELECT jsonb_agg(DISTINCT elem)::text
        FROM jsonb_array_elements(
            COALESCE(daily_usage.models_used::jsonb, '[]'::jsonb)
            || EXCLUDED.models_used::jsonb
        ) elem
    )
END
```

要点：
- `text → jsonb` 转换后 `||` 拼接合法；`'[]'::jsonb` 作为 NULL 缺省。
- `jsonb_array_elements` 直接消费 jsonb；`jsonb` 有相等操作符，`jsonb_agg(DISTINCT elem)` 合法。
- `::text` 显式转回列类型（列为 text，jsonb 赋值需显式转换）。
- 语义与 SQLite 路径一致：EXCLUDED 为 NULL 时保留现有 models；否则做集合并集去重。
- 旧数据风险：增量始终指向 `CURRENT_DATE`，历史行（可能含非法 JSON 文本）不会被触碰；当日行由修复后代码以合法 JSON 创建。

### 5.2 放大器修复（agent_runner.py）

`_sync_usage_to_daily_usage()` 仅在 `increment_usage()` 返回 True 时才标记 `daily_usage_synced=True`；返回 False 时记 warning 并保留未同步标记。注意：失败那次的用量本身不会自动补回（per-milestone 增量随运行结束丢弃），保留标记只保证后续活动的用量仍可写入，与 5.3 的不回填决定一致。此为最小改动，不改变调用时序。

### 5.3 不修复项（明确排除）

- 不迁移列类型 text → jsonb（涉及历史数据与读取方，超出本 Bug 范围）。
- 不回填 08-21 丢失的用量数据（会话级明细仍在 agent_sessions，如需精确补偿属后续独立任务）。

## 6. 测试计划

1. 新增契约测试 `tests/unit/test_usage_repo_increment.py::TestPostgresqlUpsertContract`：
   - 用 mock 捕获 `_increment_usage_postgresql` 发出的 SQL 文本（mock `repo.db.connection()` 的 cursor.execute）；
   - 负向断言（空白归一化后）：不含 `json_array_elements(` 与 `json_agg(`（实现时发现原建议的 `::json(?!\w)\s*\|\|` 正则漏检真实 bug 形态——旧 SQL 中 `::json` 与 `||` 之间隔着 `, '[]')`，故改为直接禁用 json 类型的原语；已验证两条负向断言对旧 buggy SQL 均命中）；
   - 正向断言（SQL 文本先做空白归一化再匹配，防排版变化导致假失败）：含 `jsonb_agg(DISTINCT elem)::text`、`jsonb_array_elements(`、`COALESCE(daily_usage.models_used::jsonb, '[]'::jsonb)`、`|| EXCLUDED.models_used::jsonb`；
   - 断言 `WHEN EXCLUDED.models_used IS NULL THEN daily_usage.models_used` 保留分支仍在。
2. 新增 `agent_runner._sync_usage_to_daily_usage` 失败不置位测试：patch `app.modules.workspace.autonomous.agent_runner.UsageRepository`，mock `increment_usage` 返回 False，断言 session 未被标记 synced；返回 True 时正常标记。
3. 回归：现有 SQLite 增量测试全绿（`HOME=/tmp/fakehome pytest tests/unit/test_usage_repo_increment.py tests/unit/test_usage_sink.py`）。
4. 部署后生产验证：观察 journalctl 不再出现 `json || json`；`daily_usage` 当日出现新行且多次增量后 tokens 累加、models 去重合并。

## 7. 部署与验证步骤

1. PR 合并后同步 `app/repositories/usage_repo.py`、`app/modules/workspace/autonomous/agent_runner.py` 至 `/home/openace`。
2. 重启两个加载 usage_repo 的常驻服务：`systemctl restart openace-scheduler.service`（scheduler 进程，agent_runner 路径）与 `systemctl restart open-ace.service`（Web/LLM 代理进程，DailyUsageSink 路径）。
3. 等待工作流产生一次带 model 的 AI 调用后查 `daily_usage` 当日行与 journalctl 错误消失。
4. 无需 resume 任何工作流（本 Bug 不阻塞工作流推进，仅丢统计）。

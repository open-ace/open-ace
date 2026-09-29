# CLI Reference — CLI 参考

[English](#english) | [中文](#中文)

---

## English

Reference for the operations CLI at the repository root (`cli.py`, "AI Token Usage CLI"). It reads and writes the same usage database as the shared scripts (`scripts/shared/`), so it is safe for daily queries and useful for scheduled maintenance — but three of its commands modify metering data and are covered in a dedicated section below.

## Running

All commands are invoked as modules of `cli.py` from the repository root:

```bash
python3 cli.py <command> [options]
python3 cli.py --help
python3 cli.py <command> --help
```

Shared behavior:

- Running any command initializes the usage database first (`db.init_database()`), so the CLI can also bootstrap a fresh environment.
- Data source: the usage database configured in `~/.open-ace/config.json` (`database` section). Default is SQLite at `~/.open-ace/ace.db`; a `postgresql://` URL switches to PostgreSQL (requires `psycopg2`).
- App configuration is read from `~/.open-ace/config.json` (`OPENACE_CONFIG_DIR` overrides the directory); the `email` section feeds the `report` command.
- Token counts are printed in both human-readable form (e.g. `1.2M`) and the raw number.

## Query Commands

### `today` — usage for today

Shows per-tool usage for the current date: total tokens, input/output/cache split, request count, and models used.

| Option | Description |
|--------|-------------|
| `--tool TOOL` | Filter by tool name (e.g. `claude`, `qwen`) |
| `--host HOST` | Filter by host name |

```bash
python3 cli.py today
python3 cli.py today --tool claude
python3 cli.py today --host dev-machine
```

### `query` — usage for a specific date

Same output shape as `today`, for an arbitrary date.

| Argument / Option | Required | Description |
|-------------------|----------|-------------|
| `date` (positional) | Yes | Date in `YYYY-MM-DD` format (invalid formats are rejected) |
| `--tool TOOL` | No | Filter by tool name |
| `--host HOST` | No | Filter by host name |

```bash
python3 cli.py query 2026-09-01
python3 cli.py query 2026-09-01 --tool qwen
```

### `top` — top tools over the last N days

Aggregates usage by tool over a window ending today, sorted by total tokens. Each tool also shows its request count.

| Option | Description |
|--------|-------------|
| `--tool TOOL` | Restrict the ranking to one tool |
| `--days N` | Window length in days (default `7`) |
| `--host HOST` | Filter by host name |

```bash
python3 cli.py top
python3 cli.py top --days 30
```

### `summary` — all-time summary

Per-tool totals over the full history: days tracked, total tokens, average per day, total requests, average requests per day, and the tracked date range.

| Option | Description |
|--------|-------------|
| `--host HOST` | Filter by host name |

```bash
python3 cli.py summary
```

### `report` — email report

Composes an HTML usage report and sends it over SMTP. The reference date is today, or yesterday when the command runs before 08:00 local time — so an early-morning cron still reports the previous full day.

| Argument | Description |
|--------|-------------|
| `type` (positional, optional) | Report type; only `email` exists (default) |

```bash
python3 cli.py report
```

Notes:

- Requires an `email` section in `~/.open-ace/config.json` (`smtp_server`, `smtp_port`, `smtp_username`, `smtp_password`, `from_email`, `to_email`, `use_tls`); the command exits with a clear error if it is missing.
- The SMTP connection is tested before sending; the subject is `AI Token Usage Report - <date>`.
- The summary table in the email is all-time; the daily table covers the reference date only.

### `config` — configuration management

| Action | Description |
|--------|-------------|
| `show` | Print the current config JSON (with its path) |
| `init` | Create `~/.open-ace/config.json` from `config/settings.json.sample`, or a built-in default; prompts before overwriting |
| `edit` | Open the config in `$EDITOR` (default `nano`); runs `init` first if no config exists |

```bash
python3 cli.py config show
python3 cli.py config init
python3 cli.py config edit
```

## Metering Maintenance Commands

These three commands change billing/metering data. Run them against a backed-up database, prefer narrow date ranges or a single `--tenant-id`, and never run two of them concurrently — `aggregate-quota` takes a 300-second aggregation lock and records idempotency history, but `reset-tenant-period` and `repair-consistency` write directly.

### `aggregate-quota` — rebuild quota and tenant usage aggregates

Two-step pipeline:

1. `daily_messages` → `quota_usage`: counts user messages (`role='user'`) as requests and sums the tokens of the assistant replies linked to them via the `parent_id` chain. `sender_name` is matched to users by username — imported data whose sender does not match any username produces no rows.
2. `quota_usage` → `tenant_usage` / `tenants`: resets expired billing periods, runs a data-quality check, then aggregates per-tenant usage and updates tenant totals under an aggregation lock. A run for an already-aggregated date range is detected and skipped (idempotency history).

| Option | Description |
|--------|-------------|
| `--start DATE` | Start date in `YYYY-MM-DD` (default: all data) |
| `--end DATE` | End date in `YYYY-MM-DD` (default: all data) |

```bash
python3 cli.py aggregate-quota
python3 cli.py aggregate-quota --start 2026-09-01 --end 2026-09-30
```

When to use:

- Backfilling after importing historical messages, or repairing `quota_usage` rows lost to a failed job.
- Re-running a scheduled aggregation that failed partway (safe: lock + idempotency guard).

Risks:

- It rebuilds aggregates that quota checks and reports bill against: a wrong date range or dirty `daily_messages` silently becomes billed usage. A quality score below 90% is logged as a warning — investigate before trusting the run.
- With no `--start/--end`, it reprocesses all data; prefer bounded ranges when repairing.

### `reset-tenant-period` — manually reset one tenant's billing period

Resets the given tenant's billing counters and starts a new cycle (`billing_cycle_start` / `billing_cycle_end` recomputed from the tenant's `billing_day` and `billing_cycle_type`).

| Option | Required | Description |
|--------|----------|-------------|
| `--tenant-id ID` | Yes | Numeric tenant ID (integer) |

```bash
python3 cli.py reset-tenant-period --tenant-id 3
```

When to use:

- A tenant changed their billing day or plan and the operator must cut the cycle over immediately.
- A cycle was corrupted (for example an aggregation ran with wrong dates) and period counters need a clean restart.

Risks:

- This is a billing boundary change: usage accumulated in the old period stops counting toward it. Usage rows are not deleted, but quotas and reports evaluated from the new period will not see the old cycle's consumption. Confirm the tenant ID — there is no confirmation prompt, and the reset is not automatically reversible.

### `repair-consistency` — recompute tenant totals from `tenant_usage`

Rebuilds `tenants.total_tokens_used` and `tenants.total_requests_made` as `SUM(...)` over `tenant_usage` — for one tenant, or every tenant when `--tenant-id` is omitted.

| Option | Required | Description |
|--------|----------|-------------|
| `--tenant-id ID` | No | Limit the repair to one tenant; omit to repair all |

```bash
python3 cli.py repair-consistency --tenant-id 3
python3 cli.py repair-consistency
```

When to use:

- The `consistency_violations` table records detected mismatches between tenant totals and the aggregated usage rows (`violation_type`, `expected_value` vs `actual_value`, `difference`, `status` of `detected` / `repaired` / `ignored`). When rows sit in `detected`, this command performs the repair those rows describe; update the row's `status`/`repaired_at` afterwards to keep the audit trail accurate.
- After interrupted aggregation jobs that updated `tenant_usage` but died before updating `tenants`.

Risks:

- It overwrites tenant totals unconditionally from `tenant_usage`: if the underlying aggregation was wrong, the repair entrenches the wrong numbers. Fix `aggregate-quota` output first, then repair.
- It only reconciles the two aggregate layers; it does not re-derive `tenant_usage` from `quota_usage` (that is `aggregate-quota`'s job).

## Exit Codes

Query commands return 0 even when no data matches (they print a "no usage data" message). The maintenance commands exit non-zero on failure: `aggregate-quota` on tenant-aggregation errors, `reset-tenant-period` and `repair-consistency` on any exception, so they can be used in scripted health checks.

---

## 中文

仓库根目录运维 CLI（`cli.py`，"AI Token Usage CLI"）的参考文档。它与共享脚本（`scripts/shared/`）读写同一个用量数据库，适合日常查询和定时维护——但其中三个命令会修改计量数据，见下方专门章节。

## 运行方式

所有命令都在仓库根目录以 `cli.py` 的子命令形式执行：

```bash
python3 cli.py <command> [options]
python3 cli.py --help
python3 cli.py <command> --help
```

共同行为：

- 执行任何命令前都会先初始化用量数据库（`db.init_database()`），因此 CLI 也可以引导全新环境。
- 数据来源：`~/.open-ace/config.json` 中 `database` 段配置的用量数据库。默认为 SQLite（`~/.open-ace/ace.db`）；配置 `postgresql://` URL 则切换到 PostgreSQL（需要 `psycopg2`）。
- 应用配置读取自 `~/.open-ace/config.json`（可用 `OPENACE_CONFIG_DIR` 覆盖目录）；其中 `email` 段供 `report` 命令使用。
- Token 数量同时以可读形式（如 `1.2M`）和原始数值打印。

## 查询命令

### `today` — 今日用量

按工具展示当天的用量：总 token、输入/输出/缓存拆分、请求数和使用的模型。

| 选项 | 说明 |
|--------|-------------|
| `--tool TOOL` | 按工具名过滤（如 `claude`、`qwen`） |
| `--host HOST` | 按主机名过滤 |

```bash
python3 cli.py today
python3 cli.py today --tool claude
python3 cli.py today --host dev-machine
```

### `query` — 指定日期用量

输出结构与 `today` 相同，日期任意指定。

| 参数 / 选项 | 必需 | 说明 |
|-------------------|----------|-------------|
| `date`（位置参数） | 是 | `YYYY-MM-DD` 格式日期（非法格式会被拒绝） |
| `--tool TOOL` | 否 | 按工具名过滤 |
| `--host HOST` | 否 | 按主机名过滤 |

```bash
python3 cli.py query 2026-09-01
python3 cli.py query 2026-09-01 --tool qwen
```

### `top` — 最近 N 天用量排行

统计截至今天的窗口期内各工具总用量，按总 token 排序，并显示请求数。

| 选项 | 说明 |
|--------|-------------|
| `--tool TOOL` | 只统计某个工具 |
| `--days N` | 窗口天数（默认 `7`） |
| `--host HOST` | 按主机名过滤 |

```bash
python3 cli.py top
python3 cli.py top --days 30
```

### `summary` — 全量汇总

全历史按工具汇总：跟踪天数、总 token、日均、总请求数、日均请求数和统计区间。

| 选项 | 说明 |
|--------|-------------|
| `--host HOST` | 按主机名过滤 |

```bash
python3 cli.py summary
```

### `report` — 邮件报告

组装 HTML 用量报告并通过 SMTP 发送。基准日为今天；本地时间 08:00 之前执行时自动改用昨天——清晨的 cron 也能拿到完整的前一天数据。

| 参数 | 说明 |
|--------|-------------|
| `type`（位置参数，可选） | 报告类型；目前只有 `email`（默认） |

```bash
python3 cli.py report
```

注意事项：

- 需要 `~/.open-ace/config.json` 中的 `email` 段（`smtp_server`、`smtp_port`、`smtp_username`、`smtp_password`、`from_email`、`to_email`、`use_tls`）；缺失时命令会明确报错退出。
- 发送前会先测试 SMTP 连接；邮件主题为 `AI Token Usage Report - <date>`。
- 邮件中的汇总表为全历史数据；每日数据表只覆盖基准日。

### `config` — 配置管理

| 动作 | 说明 |
|--------|-------------|
| `show` | 打印当前配置 JSON（含路径） |
| `init` | 从 `config/settings.json.sample` 创建 `~/.open-ace/config.json`，无样例时创建内置默认值；覆盖前会交互确认 |
| `edit` | 用 `$EDITOR`（默认 `nano`）打开配置；配置不存在时先执行 `init` |

```bash
python3 cli.py config show
python3 cli.py config init
python3 cli.py config edit
```

## 计量维护命令

以下三个命令会修改计费/计量数据。执行前先备份数据库，优先限定日期范围或单个 `--tenant-id`，且不要并发执行——`aggregate-quota` 有 300 秒聚合锁和幂等历史记录，但 `reset-tenant-period` 与 `repair-consistency` 是直接写入。

### `aggregate-quota` — 重建配额与租户用量聚合

两步流水线：

1. `daily_messages` → `quota_usage`：把用户消息（`role='user'`）计为请求，累加通过 `parent_id` 链关联的助手回复的 token。`sender_name` 按用户名匹配用户——导入数据中无法匹配用户名的发送者不会生成记录。
2. `quota_usage` → `tenant_usage` / `tenants`：重置已过期的计费周期，执行数据质量检查，然后在聚合锁下汇总各租户用量并更新租户总计。已聚合过的日期范围会被识别并跳过（幂等历史）。

| 选项 | 说明 |
|--------|-------------|
| `--start DATE` | 起始日期 `YYYY-MM-DD`（默认：全部数据） |
| `--end DATE` | 结束日期 `YYYY-MM-DD`（默认：全部数据） |

```bash
python3 cli.py aggregate-quota
python3 cli.py aggregate-quota --start 2026-09-01 --end 2026-09-30
```

使用场景：

- 导入历史消息后回填，或修复因任务失败丢失的 `quota_usage` 记录。
- 重跑中途失败的定时聚合（安全：聚合锁 + 幂等保护）。

风险：

- 它重建的是配额检查与账单报表所依赖的聚合：日期范围错误或 `daily_messages` 脏数据会直接变成计费用量。质量分数低于 90% 时仅记录警告——先排查再信任结果。
- 不带 `--start/--end` 时会重新处理全部数据；修复场景优先限定范围。

### `reset-tenant-period` — 手动重置租户计费周期

重置指定租户的计费计数器并开始新周期（按租户的 `billing_day` 与 `billing_cycle_type` 重新计算 `billing_cycle_start` / `billing_cycle_end`）。

| 选项 | 必需 | 说明 |
|--------|----------|-------------|
| `--tenant-id ID` | 是 | 数字租户 ID（整数） |

```bash
python3 cli.py reset-tenant-period --tenant-id 3
```

使用场景：

- 租户变更计费日或套餐，运维需要立即切换周期。
- 周期数据损坏（例如聚合用错日期），周期计数器需要干净重启。

风险：

- 这是计费边界变更：旧周期内累计的用量不再计入该周期。用量行不会被删除，但按新周期评估的配额与报表看不到旧周期消耗。执行前确认租户 ID——没有确认提示，且重置无法自动回滚。

### `repair-consistency` — 从 `tenant_usage` 重算租户总计

把 `tenants.total_tokens_used` 和 `tenants.total_requests_made` 重建为对 `tenant_usage` 的 `SUM(...)`——可限定单个租户，省略 `--tenant-id` 时修复全部租户。

| 选项 | 必需 | 说明 |
|--------|----------|-------------|
| `--tenant-id ID` | 否 | 只修复指定租户；省略则修复全部 |

```bash
python3 cli.py repair-consistency --tenant-id 3
python3 cli.py repair-consistency
```

使用场景：

- `consistency_violations` 表记录检测到的租户总计与聚合用量不一致（`violation_type`、`expected_value` 与 `actual_value`、`difference`、状态 `detected` / `repaired` / `ignored`）。当表中存在 `detected` 记录时，本命令执行这些记录所描述的修复；修复后应更新对应行的 `status`/`repaired_at`，保持审计轨迹准确。
- 聚合任务更新了 `tenant_usage` 但在更新 `tenants` 前中断之后。

风险：

- 它无条件用 `tenant_usage` 覆盖租户总计：如果底层聚合本身是错的，修复会把错误固化。先用 `aggregate-quota` 修正数据，再执行修复。
- 它只对齐两个聚合层；不会从 `quota_usage` 重新推导 `tenant_usage`（那是 `aggregate-quota` 的职责）。

## 退出码

查询命令即使没有匹配数据也返回 0（打印 "no usage data" 提示）。维护命令失败时以非零码退出：`aggregate-quota` 在租户聚合出错时、`reset-tenant-period` 与 `repair-consistency` 在任何异常时，因此可以用于脚本化健康检查。

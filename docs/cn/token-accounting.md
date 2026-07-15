# Claude / Codex / ZCode / Qwen Token 统计链路说明

本文档说明 Open ACE 如何为 `claude`、`codex`、`zcode`、`qwen` 这 4 个本地工具抓取 token、计算每日/消息级统计、写入数据库，并被 WebUI、配额、分析与报表模块消费。

目标读者：

- 用户：理解为什么 Open ACE 的 token 数与官方控制台可能不同
- 项目维护者：排查统计异常、修 fetcher、接入新工具
- 二次开发者：明确该改哪一层，避免重复累计或字段误用

## 1. 总体链路

```text
本地 JSONL / SQLite
  -> scripts/fetch_*.py
  -> scripts/shared/db.py
     -> daily_usage
     -> daily_messages
     -> agent_sessions
     -> session_messages
     -> daily_stats / hourly_stats
     -> user_daily_stats
  -> app/repositories/* / app/services/*
  -> Work / Manage 页面、报表、配额与分析接口
```

可以把这条链路理解为两层事实表、三层摘要：

- `daily_usage`：按日、按工具、按主机的聚合事实表，适合总量和趋势
- `daily_messages`：消息级事实表，适合小时粒度、时间线、sender/project 归因
- `agent_sessions` / `session_messages`：Workspace 会话视图和 transcript 镜像
- `daily_stats` / `hourly_stats` / `user_daily_stats`：从事实表再派生的预聚合表

## 2. 统一口径

### 2.1 核心字段

| 字段 | 含义 | 主要存储位置 |
|------|------|--------------|
| `tokens_used` | Open ACE 认为该记录的总 token | `daily_usage`、`daily_messages`、`agent_sessions`、`session_messages` |
| `input_tokens` | 该记录的非 cache 输入 token | `daily_usage`、`daily_messages`、`agent_sessions`、`session_messages` |
| `output_tokens` | 输出 token | `daily_usage`、`daily_messages`、`agent_sessions`、`session_messages` |
| `cache_tokens` | cache token 总和 | 只在 `daily_usage` 中单独存列 |
| `request_count` | Open ACE 定义下的请求数 | `daily_usage`、`agent_sessions`、`user_daily_stats` |

关键注意点：

1. `daily_messages` 没有单独的 `cache_tokens` 列。
2. cache 只在 `daily_usage.cache_tokens` 中单独保留，消息级只能看到 `tokens_used`、`input_tokens`、`output_tokens`。
3. 下游消费方如果已经拿到了 `tokens_used`，通常不应该再做 `tokens_used + cache_tokens`，否则很容易 double count。

### 2.2 当前推荐理解

对这 4 个工具，Open ACE 的目标是尽量让：

```text
tokens_used == 非 cache input_tokens + output_tokens + cache_tokens
```

但要注意 provider 原始语义并不完全相同：

- Claude：cache 单独给出，`tokens_used` 在 Open ACE 中显式把 cache 加进去
- Codex：provider `total_tokens` 已经包含 cached input，Open ACE 保留 provider total，并把 `input_tokens` 存成去 cache 后的输入
- Qwen：`totalTokenCount` 走 provider 口径，`promptTokenCount` 里包含 cache，Open ACE 会把 `input_tokens` 改写为去 cache 后的输入
- ZCode：以 `turn_usage` 为权威源，`computed_total_tokens`、`input_tokens`、`output_tokens`、`cache_*` 一起使用

## 3. 各工具如何抓取与计算

### 3.1 Claude

**源数据**

- 路径：`~/.claude/projects/**.jsonl`
- 脚本：`scripts/fetch_claude.py`

**抓取方式**

- Claude 本地日志按 JSONL 存储
- usage 主要从 `entry["usage"]` 或 `entry["message"]["usage"]` 提取
- 入口函数：
  - `extract_tokens_from_entry()`
  - `process_jsonl_file()`
  - `_merge_messages_by_id()`

**字段映射**

- `input_tokens` <- `input_tokens`
- `output_tokens` <- `output_tokens`
- `cache_read_tokens` <- `cache_read_input_tokens`
- `cache_creation_tokens` <- `cache_creation_input_tokens`
- `tokens_used` <- `input + output + cache_read + cache_creation`

**为什么要 merge message id**

- Claude 同一个逻辑消息可能被拆成多行写入 JSONL
- 如果逐行直接入库，既会重复算 token，也会丢结构化内容
- 当前做法是先按逻辑 `message_id` 合并，再归入 `daily_messages` / `session_messages`

**request_count**

- 按 assistant 逻辑消息计数
- 有稳定 message id 时去重
- 即使某条 assistant 消息 token 为 0，也可能仍计为一次请求

### 3.2 Codex

**源数据**

- 路径：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- 脚本：`scripts/fetch_codex.py`

**关键事件**

- `task_started`
- `turn_context`
- `response_item`
- `token_count`
- `task_complete`

**抓取方式**

- Codex 不按“消息自带 usage”存账单数据，而是通过事件流重建 turn
- `task_started` 建 turn
- `response_item` 收集 user / assistant message
- `token_count` 累加该 turn 的 token 使用
- `task_complete` 结束该 turn

**为什么按 turn，而不是按 session**

- 一个 Codex session 可能跨多个小时甚至多天
- 如果把整场 session token 挂到第一条 assistant message，会扭曲每日/每小时统计
- 当前逻辑按 turn 重建，再把每个 turn 的 token 归给发起该 turn 的 user message

**字段映射**

- `tokens_used` <- `last_token_usage.total_tokens` 的逐事件累加值
- `cache_tokens` <- `cached_input_tokens`
- `input_tokens` <- `input_tokens - cached_input_tokens`
- `output_tokens` <- `output_tokens`
- `thoughts_tokens` 只在抓取阶段参与日汇总，不单独入 `daily_messages`

**重要语义**

- 当前本地 Codex 源日志中，`token_count.last_token_usage` 表现为事件级增量，应累加
- `cached_input_tokens` 是 provider total 的组成部分，不是额外再加的一层

**request_count**

- 按 `task_started` 计数
- 不是按 assistant message 数量计数

**重抓为什么会先删旧消息**

- token 归因可能因为 parser 修复从 assistant message 挪到 user message
- `delete_messages_for_agent_sessions()` 会先按 `tool_name + host_name + agent_session_id` 清理旧行，再整体重写
- 这样可以避免历史脏行残留造成双算

### 3.3 ZCode

**源数据**

- 路径：`~/.zcode/cli/db/db.sqlite`
- 脚本：`scripts/fetch_zcode.py`
- 原始表：`session`、`message`、`part`、`turn_usage`

**为什么和其他 3 个工具不同**

- ZCode 原始源不是 JSONL，而是 SQLite 关系型结构
- `message` / `part` 更适合拿 transcript
- `turn_usage` 才是 token 统计的权威来源

**抓取方式**

- 用 `remote-agent/session_sync.py` 里的 `ZcodeSession` 解析消息和项目路径
- 再单独查 `turn_usage` 做 token 归因
- 关键函数：
  - `_get_turn_usage_rows()`
  - `_get_turn_usage_by_date()`
  - `process_zcode_session()`

**字段映射**

- `tokens_used` <- `computed_total_tokens`
- `input_tokens` <- `turn_usage.input_tokens`
- `output_tokens` <- `turn_usage.output_tokens`
- `cache_tokens` <- `cache_creation_input_tokens + cache_read_input_tokens`

**为什么按 `turn_usage.started_at` 分日期**

- 一个 session 可能跨日
- ZCode 的 token 权威时间戳在 `turn_usage.started_at`
- 所以 `daily_usage` 的分日逻辑应基于 turn，而不是 session 的创建时间或“消息最多的那一天”

**消息级归因**

- 每个 turn 会优先归给 `turn_usage.user_message_id` 对应的 user message
- 这样 `daily_messages` / `hourly_stats` 才能和真实 turn 发起时间对齐

**匹配失败时怎么办**

- 部分 turn 匹配失败：会打印告警
  - `daily_usage` 仍然可信
  - 但 `daily_messages` / `agent_sessions` 可能缺少部分 turn token
- 全部 turn 匹配失败：会退回旧逻辑，把整场 session token 注入第一条 assistant message，并打印 fallback 告警

### 3.4 Qwen

**源数据**

- 路径：`~/.qwen/projects/**/chats/*.jsonl`
- 兼容某些旧布局下的直接 `*.jsonl`
- 脚本：`scripts/fetch_qwen.py`

**抓取方式**

- usage 来自 `usageMetadata`
- 入口函数：
  - `extract_tokens_from_entry()`
  - `process_jsonl_file()`

**字段映射**

- `prompt_tokens` <- `promptTokenCount`
- `candidates_tokens` <- `candidatesTokenCount`
- `thoughts_tokens` <- `thoughtsTokenCount`
- `cached_tokens` <- `cachedContentTokenCount`
- `tokens_used` <- `totalTokenCount`

**重要语义**

- `promptTokenCount` 包含 cache
- Open ACE 会额外计算 `actual_input_tokens = promptTokenCount - cachedContentTokenCount`
- 最终写入 `daily_messages.input_tokens` 的是 `actual_input_tokens`
- `tokens_used` 仍保留 provider `totalTokenCount` 口径

**为什么 thoughts 不再额外加进 total**

- 当前逻辑把 `thoughtsTokenCount` 视为额外观测维度，而不是一定要叠加进 `totalTokenCount`
- 否则会把 provider 已经给出的 total 再重复放大

**request_count**

- 按 assistant message 计数
- 有 message id 时做去重

## 4. 如何落库

### 4.1 `daily_usage`

**职责**

- 每日、每工具、每主机的聚合事实表
- 适合做趋势、总量、配额、ROI、成本估算

**写入入口**

- `scripts/shared/db.py` 的 `save_usage()`

**主要列**

- `date`
- `tool_name`
- `host_name`
- `tokens_used`
- `input_tokens`
- `output_tokens`
- `cache_tokens`
- `request_count`
- `models_used`

### 4.2 `daily_messages`

**职责**

- 消息级分析事实表
- 适合做小时统计、时间线、sender 归属、conversation/project 维度分析

**写入入口**

- `scripts/shared/db.py` 的 `save_messages_batch()`

**重要限制**

- 没有单独的 `cache_tokens` 列
- 它不是 Workspace 运行时 transcript 权威表，更多是分析事实表
- 直接 `SUM(tokens_used)` 时必须先确认工具归因语义，不要想当然地把它当成“官方账单逐条镜像”

### 4.3 `agent_sessions`

**职责**

- session 级摘要
- Work 模式 / 远程会话列表 / session 详情头部统计

**更新入口**

- 各 fetcher 的 `update_agent_sessions_stats()`

**特点**

- 聚合 `message_count`
- 聚合 `total_tokens`
- 聚合 `request_count`
- 记录 `model`、`project_path`、`updated_at`

### 4.4 `session_messages`

**职责**

- session 详情页使用的 transcript 镜像
- 便于从 fetcher 导入后直接按 session 回放

**更新入口**

- 各 fetcher 的 `update_agent_sessions_stats()` 在更新 session 摘要时顺带插入

### 4.5 `daily_stats` / `hourly_stats`

**职责**

- 从 `daily_messages` 派生出的预聚合表

**刷新机制**

- `save_messages_batch()` 成功后会调用 `_refresh_daily_stats_for_messages(messages)`
- 它会按受影响日期重建：
  - `daily_stats`
  - `hourly_stats`

### 4.6 `user_daily_stats`

**职责**

- 面向用户维度的日聚合表
- 供配额、趋势和某些快速查询路径使用

**刷新机制**

- `save_messages_batch()` 完成后，会经 `scripts/shared/user_stats_helper.py`
- 调用 `app/services/user_stats_aggregator.py`
- 把 `daily_messages` 和 `agent_sessions` 汇总到 `user_daily_stats`

## 5. 下游如何读取这些数据

| 模块 | 主要读取层 | 说明 |
|------|------------|------|
| `app/repositories/usage_repo.py` | `daily_usage` 优先 | 总量、按工具统计、CSV、请求数等优先读聚合表，避免 JOIN `daily_messages` 放大 `request_count` |
| `app/repositories/message_repo.py` | `daily_messages` | 小时模式、时间线、消息检索、按 sender / project / conversation 分析 |
| `app/services/analysis_service.py` | `message_repo` + `hourly_stats` | 部分趋势直接走消息聚合，部分按日小时视图走预聚合表 |
| `app/services/user_stats_aggregator.py` | `daily_messages` + `agent_sessions` | 生成用户日级摘要 |
| Work 模式 session 详情 | `agent_sessions` + `session_messages` | 不是直接从 `daily_messages` 读 |
| Manage 模式 usage / analysis | `daily_usage`、`daily_messages`、`daily_stats`、`hourly_stats` | 取决于页面是看总量、趋势还是明细 |

可操作的经验规则：

1. 看“每天总共用了多少 token”，先查 `daily_usage`
2. 看“某个小时发生了什么”，查 `daily_messages` 或 `hourly_stats`
3. 看“某个 session 的 transcript”，查 `session_messages`
4. 看“某个用户今天已用多少”，优先查 `user_daily_stats`

## 6. 常见误区

### 6.1 为什么 Open ACE 和官方控制台不完全一致？

常见原因：

- 官方与本地日志的刷新延迟不同
- 官方统计可能带有本地日志中没有落下来的模型桶
- session / turn / message 的归因口径不同
- 某些 provider 的 total 是否含 cache / thoughts 语义不同

这类差异不一定是 bug，先看差异属于哪一层：

- 源日志缺失
- fetcher 归因不同
- `daily_usage` 与 `daily_messages` 被拿去做了不同用途的对比

### 6.2 cache 算进 total 吗？

对这 4 个工具，Open ACE 当前目标是把 cache 视为 total 的组成部分。区别只在于：

- 有的 provider 直接给出含 cache 的 total
- 有的 provider 需要 Open ACE 自己把 cache 补进 `tokens_used`

### 6.3 为什么 `daily_usage` 和 `SUM(daily_messages.tokens_used)` 可能不同？

原因包括：

- `daily_usage` 是按工具定义的聚合权威层
- `daily_messages` 是消息级归因层
- 某些工具按 turn 归给 user message，而不是 assistant message
- 某些 fallback / 匹配失败场景下，`daily_usage` 仍然完整，但 `daily_messages` 只是一种近似映射

### 6.4 为什么重抓后数字会变化？

重抓并不只是“重新插一遍数据”，还可能包含：

- message-id merge 修复
- turn 归因修复
- stale assistant token 清理
- 旧 session message 删除后重建

如果 parser 逻辑变了，历史 session 的归因也可能随之修正。

### 6.5 为什么不建议某些页面直接 `sum(daily_messages.tokens_used)`？

因为 `daily_messages` 的职责是“分析事实”，不是“统一账单事实”。

它非常适合：

- 时间线
- 小时分布
- sender / project / conversation 维度

但如果页面想展示：

- 今日总 token
- 工具间总量对比
- 配额扣减

应该先使用 `daily_usage` 或 `user_daily_stats`。

## 7. 维护建议

后续如果要新增工具或修改 fetcher，建议按下面顺序检查：

1. 源日志里 provider usage 的语义是什么
2. `tokens_used` 是否已经包含 cache / thoughts
3. 该工具更适合按 session、turn 还是 message 归因
4. `daily_usage` 和 `daily_messages` 的职责是否被混淆
5. 是否需要清理旧 session 明细，避免历史脏行保留
6. 是否补充 `tests/unit/test_fetch_*.py` 或 issue 定向回归测试

相关代码入口：

- `scripts/fetch_claude.py`
- `scripts/fetch_codex.py`
- `scripts/fetch_zcode.py`
- `scripts/fetch_qwen.py`
- `scripts/shared/db.py`
- `scripts/shared/user_stats_helper.py`
- `app/repositories/usage_repo.py`
- `app/repositories/message_repo.py`
- `app/services/analysis_service.py`
- `app/services/user_stats_aggregator.py`

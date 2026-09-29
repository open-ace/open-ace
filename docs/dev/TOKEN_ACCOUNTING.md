# Token Accounting — Token 统计链路

[English](#english) | [中文](#中文)

---

## English

# Token Accounting for Claude / Codex / ZCode / Qwen

This document explains how Open ACE collects token usage for the four local tools `claude`, `codex`, `zcode`, and `qwen`, how it computes daily and message-level metrics, how those metrics are stored, and which downstream services/pages consume which table.

Target readers:

- users who want to understand why Open ACE numbers can differ from provider dashboards
- maintainers who need to debug or change fetchers
- contributors adding a new local tool or adjusting usage semantics

## Concepts

This opening section replaces the former `CONCEPTS.md` and restates its four core concepts against the actual code model.

### 1. Request (API call)

An independent model invocation attributed to one billing unit. Open ACE's `request_count` is tool-specific:

- Claude / Qwen: counted per logical **assistant message**, deduplicated by stable message id when available; a zero-token assistant message can still count as a request
- Codex: counted per **`task_started`** event (one per reconstructed turn), not per assistant row
- ZCode: attributed per **`turn_usage`** row (the authoritative billing record)

See section 3 for the per-tool pipelines.

### 2. Message

Every persisted row in `daily_messages` / `session_messages` carries a normalized `role` from the canonical set:

| Role | Meaning |
|------|---------|
| `user` | Messages sent by the user |
| `assistant` | AI-generated responses |
| `system` | System/context messages |
| `tool` | Tool execution results |

The canonical values are defined in `app/utils/roles.py` (`normalize_message_role`), and every write path into `daily_messages` funnels through it at the write boundary (`app/repositories/message_repo.py` documents the accepted roles as `user, assistant, system, tool`). Variant tool-result spellings (`toolResult`, `tool_result`) collapse to `tool`; blank/`None` input becomes `unknown`. There is no separate `message_user` / `message_toolresult` / `message_error` taxonomy in the code.

Example:

```
user: "Check the weather"           → role=user      +1
assistant: "Let me check..."        → role=assistant +1
tool: {weather data}                → role=tool      +1
assistant: "It's sunny today..."    → role=assistant +1
```

### 3. Session

A tool-level session — the process-level unit identified by `agent_session_id`. Sessions belong to a tool: `claude`, `qwen`, `codex`, `zcode`, and `openclaw` each have their own fetcher (`scripts/fetch_*.py`) and session layout. One session contains multiple conversations and may span multiple local calendar days (which is why daily grouping follows turn/message timestamps, not session creation time).

### 4. Conversation

One round of dialogue: from a user sending a message to the AI completing the final response, including any tool calls in between. Stored as `conversation_id` ("one round of conversation" per the `save_message` docstring in `app/repositories/message_repo.py`).

### Concept relationship

```
Session (tool process: claude / qwen / codex / zcode / openclaw)
├── Conversation 1 (one round)
│   ├── user message          (role=user)
│   ├── assistant response    (role=assistant)
│   ├── tool result           (role=tool)
│   └── final assistant reply (role=assistant)
├── Conversation 2
│   ├── user message          (role=user)
│   └── assistant response    (role=assistant)
└── Conversation N
```

### Statistics example

```
Conversation 1:
  user: "Check the weather"
  │
  ├── Request 1 ──> assistant: "Let me check..."   (role=assistant)
  │
  ├── Request 2 ──> tool: {weather data}            (role=tool)
  │
  └── Request 3 ──> assistant: "It's sunny..."      (role=assistant)

Conversation 2:
  user: "What about tomorrow?"
  │
  └── Request 4 ──> assistant: "Tomorrow..."        (role=assistant)
```

Results:

- **Requests**: 4 model invocations (how they map to `request_count` depends on the tool — see the Request concept above)
- **Messages**: 6 rows (2 user + 3 assistant + 1 tool)
- **Conversations**: 2 rounds

### Database fields

| Field | Description |
|-------|-------------|
| `agent_session_id` | Tool session identifier (process level) |
| `conversation_id` | Conversation identifier (one round of dialogue) |
| `feishu_conversation_id` | Feishu conversation identifier |

## 1. End-to-end flow

```text
local JSONL / SQLite
  -> scripts/fetch_*.py
  -> scripts/shared/db.py
     -> daily_usage
     -> daily_messages
     -> agent_sessions
     -> session_messages
     -> daily_stats / hourly_stats
     -> user_daily_stats
  -> app/repositories/* / app/services/*
  -> Work / Manage pages, quota, reporting, analytics
```

At a high level:

- `daily_usage` is the day/tool/host aggregate fact table
- `daily_messages` is the message-level analytics fact table
- `agent_sessions` / `session_messages` power workspace/session views
- `daily_stats`, `hourly_stats`, and `user_daily_stats` are derived aggregates

## 2. Shared semantics

### 2.1 Core fields

| Field | Meaning | Main tables |
|------|---------|-------------|
| `tokens_used` | Open ACE's total-token value for that record | `daily_usage`, `daily_messages`, `agent_sessions`, `session_messages` |
| `input_tokens` | non-cached input tokens for that record | `daily_usage`, `daily_messages`, `agent_sessions`, `session_messages` |
| `output_tokens` | output tokens | `daily_usage`, `daily_messages`, `agent_sessions`, `session_messages` |
| `cache_tokens` | total cache tokens | only stored separately in `daily_usage` |
| `request_count` | Open ACE request-count semantic | `daily_usage`, `agent_sessions`, `user_daily_stats` |

Important caveats:

1. `daily_messages` does not have its own `cache_tokens` column.
2. Cache is preserved separately only in `daily_usage.cache_tokens`.
3. If a consumer already reads `tokens_used`, it usually must not add `cache_tokens` again.

### 2.2 Intended invariant

For these four tools, the current intent is to keep the stored totals as close as possible to:

```text
tokens_used == non-cached input_tokens + output_tokens + cache_tokens
```

But provider-native semantics differ:

- Claude exposes cache separately, so Open ACE explicitly adds cache into `tokens_used`
- Codex provider `total_tokens` already includes cached input
- Qwen provider `promptTokenCount` includes cache, while `totalTokenCount` is preserved as the provider total
- Zcode uses `turn_usage` as the source of truth

## 3. Tool-by-tool collection and computation

### 3.1 Claude

**Source**

- path: `~/.claude/projects/**.jsonl`
- script: `scripts/fetch_claude.py`

**How usage is extracted**

- Claude local logs are JSONL
- usage is read from `entry["usage"]` or `entry["message"]["usage"]`
- key functions:
  - `extract_tokens_from_entry()`
  - `process_jsonl_file()`
  - `_merge_messages_by_id()`

**Field mapping**

- `input_tokens` comes from `input_tokens`
- `output_tokens` comes from `output_tokens`
- `cache_read_tokens` comes from `cache_read_input_tokens`
- `cache_creation_tokens` comes from `cache_creation_input_tokens`
- `tokens_used` is computed as `input + output + cache_read + cache_creation`

**Why message-id merge exists**

- one logical Claude message can span multiple JSONL lines
- line-by-line inserts would duplicate token accounting and lose structured content
- Open ACE merges the logical message first, then writes `daily_messages` / `session_messages`

**request_count**

- counted per logical assistant message
- deduplicated by stable message id when available
- a zero-token assistant message can still count as a request

### 3.2 Codex

**Source**

- path: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- script: `scripts/fetch_codex.py`

**Key event types**

- `task_started`
- `turn_context`
- `response_item`
- `token_count`
- `task_complete`

**How usage is reconstructed**

- Codex does not store billing usage as one field per final message
- Open ACE rebuilds turns from the event stream
- `task_started` opens a turn
- `response_item` collects user / assistant messages
- `token_count` accumulates turn usage
- `task_complete` closes the turn

**Why attribution is per turn, not per session**

- a Codex session can span many hours or multiple days
- putting all tokens on the first assistant row distorts daily and hourly stats
- current logic reconstructs turns and attributes each turn to the initiating user message

**Field mapping**

- `tokens_used` comes from accumulated `last_token_usage.total_tokens`
- `cache_tokens` comes from `cached_input_tokens`
- `input_tokens` is computed as `input_tokens - cached_input_tokens`
- `output_tokens` comes from `output_tokens`
- `thoughts_tokens` only participates in daily aggregation and is not stored separately in `daily_messages`

**Important semantic note**

- for current local Codex logs, `token_count.last_token_usage` behaves as event-level increments and should be summed
- `cached_input_tokens` is already part of provider total, not an extra add-on

**request_count**

- counted per `task_started`
- not per assistant message row

**Why re-import deletes old rows first**

- parser fixes can move token attribution from assistant rows to user rows
- `delete_messages_for_agent_sessions()` clears old rows by `tool_name + host_name + agent_session_id`
- then the authoritative snapshot is re-written

### 3.3 Zcode

**Source**

- path: `~/.zcode/cli/db/db.sqlite`
- script: `scripts/fetch_zcode.py`
- source tables: `session`, `message`, `part`, `turn_usage`

**Why it differs from the other three**

- Zcode stores source data relationally in SQLite, not JSONL
- `message` / `part` are useful for transcript reconstruction
- `turn_usage` is the authoritative source for token accounting

**How usage is computed**

- `ZcodeSession` from `remote-agent/session_sync.py` is reused for transcript/project parsing
- token attribution is then done by querying `turn_usage`
- key functions:
  - `_get_turn_usage_rows()`
  - `_get_turn_usage_by_date()`
  - `process_zcode_session()`

**Field mapping**

- `tokens_used` comes from `computed_total_tokens`
- `input_tokens` comes from `turn_usage.input_tokens`
- `output_tokens` comes from `turn_usage.output_tokens`
- `cache_tokens` is computed as `cache_creation_input_tokens + cache_read_input_tokens`

**Why date grouping uses `turn_usage.started_at`**

- one session may span multiple local calendar days
- the authoritative billing timestamp for Zcode is the turn start
- so `daily_usage` is split by turn date, not by session creation time or dominant message day

**Message-level attribution**

- each turn is attached to the user message referenced by `turn_usage.user_message_id`
- this keeps `daily_messages` / `hourly_stats` aligned with real turn timing

**What happens on mismatch**

- partial match: warning is printed
  - `daily_usage` remains authoritative
  - `daily_messages` / `agent_sessions` may be incomplete for unmatched turns
- full mismatch: fallback warning is printed and the whole session total is injected into the first assistant message

### 3.4 Qwen

**Source**

- path: `~/.qwen/projects/**/chats/*.jsonl`
- some older layouts also use direct `*.jsonl`
- script: `scripts/fetch_qwen.py`

**How usage is extracted**

- usage comes from `usageMetadata`
- key functions:
  - `extract_tokens_from_entry()`
  - `process_jsonl_file()`

**Field mapping**

- `prompt_tokens` comes from `promptTokenCount`
- `candidates_tokens` comes from `candidatesTokenCount`
- `thoughts_tokens` comes from `thoughtsTokenCount`
- `cached_tokens` comes from `cachedContentTokenCount`
- `tokens_used` comes from `totalTokenCount`

**Important semantic note**

- `promptTokenCount` includes cached context
- Open ACE computes `actual_input_tokens = promptTokenCount - cachedContentTokenCount`
- `daily_messages.input_tokens` stores `actual_input_tokens`
- `tokens_used` still preserves provider `totalTokenCount`

**Why thoughts are not added again**

- `thoughtsTokenCount` is treated as an extra observation dimension, not something that should automatically be added on top of provider total
- otherwise Open ACE would inflate totals beyond provider semantics

**request_count**

- counted per assistant message
- deduplicated by message id when available

## 4. How data is stored

### 4.1 `daily_usage`

**Role**

- day/tool/host aggregate fact table
- suitable for trend charts, totals, quota, ROI, and cost estimation

**Write path**

- `save_usage()` in `scripts/shared/db.py`

**Main columns**

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

**Role**

- message-level analytics fact table
- suitable for hourly analysis, timelines, sender attribution, and conversation/project dimensions

**Write path**

- `save_messages_batch()` in `scripts/shared/db.py`

**Important constraint**

- there is no separate `cache_tokens` column here
- this is an analytics fact table, not the workspace runtime transcript authority
- direct `SUM(tokens_used)` requires understanding the tool-specific attribution model

### 4.3 `agent_sessions`

**Role**

- session summary table
- used for workspace session lists and session detail headers

**Update path**

- each fetcher has an `update_agent_sessions_stats()` implementation

**Typical fields**

- `message_count`
- `total_tokens`
- `request_count`
- `model`
- `project_path`
- `updated_at`

### 4.4 `session_messages`

**Role**

- transcript mirror for session detail pages
- allows fetchers to populate session replay data directly

**Update path**

- usually inserted from the fetcher's `update_agent_sessions_stats()`

### 4.5 `daily_stats` / `hourly_stats`

**Role**

- derived aggregate tables built from `daily_messages`

**Refresh behavior**

- after `save_messages_batch()` succeeds, `_refresh_daily_stats_for_messages(messages)` rebuilds:
  - `daily_stats`
  - `hourly_stats`

### 4.6 `user_daily_stats`

**Role**

- per-user daily aggregate table
- used for quota checks, trends, and fast user-level reads

**Refresh behavior**

- after `save_messages_batch()` finishes, `scripts/shared/user_stats_helper.py`
- calls `app/services/user_stats_aggregator.py`
- which aggregates `daily_messages` and `agent_sessions` into `user_daily_stats`

## 5. How downstream code consumes these tables

| Module | Primary source | Why |
|--------|----------------|-----|
| `app/repositories/usage_repo.py` | `daily_usage` first | totals, per-tool summaries, CSV, request counts; avoids `daily_messages` JOIN multiplication |
| `app/repositories/message_repo.py` | `daily_messages` | hourly usage, message timeline, sender/project/conversation analysis |
| `app/services/analysis_service.py` | `message_repo` + `hourly_stats` | trend and hourly views combine raw message aggregations with derived tables |
| `app/services/user_stats_aggregator.py` | `daily_messages` + `agent_sessions` | builds per-user daily aggregates |
| Work session detail | `agent_sessions` + `session_messages` | does not rely on `daily_messages` as the normal runtime source |
| Manage usage / analysis | `daily_usage`, `daily_messages`, `daily_stats`, `hourly_stats` | depends on whether the page needs totals, trends, or detail |

Practical rules:

1. For "how many tokens were used that day", prefer `daily_usage`
2. For "what happened in this hour", use `daily_messages` or `hourly_stats`
3. For "show me this session transcript", use `session_messages`
4. For "how much has this user used today", prefer `user_daily_stats`

## 6. Common pitfalls

### 6.1 Why can Open ACE differ from provider dashboards?

Common reasons:

- provider dashboards and local logs refresh at different times
- provider-side buckets may exist even when local logs do not expose them
- attribution may be session-based, turn-based, or message-based
- provider-specific total/cache/thought semantics differ

Differences do not automatically mean a bug. First identify which layer differs:

- missing source logs
- fetcher attribution
- comparing `daily_usage` to `daily_messages` even though they serve different purposes

### 6.2 Is cache included in total?

For these four tools, Open ACE currently treats cache as part of the total. The difference is only whether:

- the provider already includes cache in its native total, or
- Open ACE has to add cache into `tokens_used` itself

### 6.3 Why can `daily_usage` differ from `SUM(daily_messages.tokens_used)`?

Because:

- `daily_usage` is the tool-level aggregate authority
- `daily_messages` is the message-level attribution layer
- some tools attribute turns to user rows, not assistant rows
- fallback or partial-match cases can keep `daily_usage` complete while message/session attribution is only approximate

### 6.4 Why can a re-fetch change historical numbers?

Re-fetch does more than re-insert rows. It may include:

- message-id merge fixes
- turn-attribution fixes
- stale assistant-token cleanup
- deleting and rebuilding old session rows

If parser logic changes, historical attribution can be corrected too.

### 6.5 Why should some pages avoid summing `daily_messages.tokens_used` directly?

Because `daily_messages` is an analytics fact table, not the universal billing fact.

It is ideal for:

- timelines
- hourly patterns
- sender / project / conversation analysis

But for:

- daily totals
- tool-to-tool comparisons
- quota deduction

consumers should prefer `daily_usage` or `user_daily_stats`.

## 7. Maintenance guidance

When adding a new tool or changing a fetcher, check these questions in order:

1. What is the provider/source semantic for usage fields?
2. Does `tokens_used` already include cache or thoughts?
3. Should attribution be per session, per turn, or per message?
4. Are `daily_usage` and `daily_messages` being kept in their intended roles?
5. Does a parser change require stale session-row cleanup?
6. Are there fetcher unit tests or targeted regression tests covering the semantics?

Useful code entry points:

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

---

## 中文

# Claude / Codex / ZCode / Qwen Token 统计链路说明

本文档说明 Open ACE 如何为 `claude`、`codex`、`zcode`、`qwen` 这 4 个本地工具抓取 token、计算每日/消息级统计、写入数据库，并被 WebUI、配额、分析与报表模块消费。

目标读者：

- 用户：理解为什么 Open ACE 的 token 数与官方控制台可能不同
- 项目维护者：排查统计异常、修 fetcher、接入新工具
- 二次开发者：明确该改哪一层，避免重复累计或字段误用

## 核心概念

本开篇章节取代原 `CONCEPTS.md`，并按代码中的真实模型重新表述其四个核心概念。

### 1. Request（API 调用）

一次独立的模型调用，归属到某个计费单元。Open ACE 的 `request_count` 按工具定义：

- Claude / Qwen：按逻辑 **assistant 消息** 计数，有稳定 message id 时去重；token 为 0 的 assistant 消息也可能计为一次请求
- Codex：按 **`task_started`** 事件计数（每个重建的 turn 一次），而不是按 assistant 行
- ZCode：按 **`turn_usage`** 行归因（权威计费记录）

各工具的统计链路见第 3 节。

### 2. Message（消息）

`daily_messages` / `session_messages` 中的每一行都带一个归一化的 `role`，取值来自规范集合：

| Role | 含义 |
|------|------|
| `user` | 用户发送的消息 |
| `assistant` | AI 生成的回复 |
| `system` | 系统/上下文消息 |
| `tool` | 工具执行结果 |

规范值定义在 `app/utils/roles.py`（`normalize_message_role`），所有写入 `daily_messages` 的路径都在写边界经过它（`app/repositories/message_repo.py` 的文档注明接受的角色为 `user, assistant, system, tool`）。工具结果的变体拼写（`toolResult`、`tool_result`）会折叠为 `tool`；空值/`None` 变为 `unknown`。代码中不存在单独的 `message_user` / `message_toolresult` / `message_error` 分类。

示例：

```
user: "查一下天气"                  → role=user      +1
assistant: "我去查一下..."          → role=assistant +1
tool: {天气数据}                    → role=tool      +1
assistant: "今天晴天..."            → role=assistant +1
```

### 3. Session（会话）

工具级会话 —— 以 `agent_session_id` 标识的进程级单元。会话归属于某个工具：`claude`、`qwen`、`codex`、`zcode`、`openclaw` 各有自己的 fetcher（`scripts/fetch_*.py`）和会话布局。一个会话包含多轮对话，并且可能跨多个本地日历日（因此按日分组依据的是 turn/消息时间戳，而不是会话创建时间）。

### 4. Conversation（对话轮次）

一轮对话：从用户发送消息到 AI 完成最终回复（包含其间的工具调用）。以 `conversation_id` 存储（`app/repositories/message_repo.py` 的 `save_message` 文档注明"一轮对话"）。

### 概念关系

```
Session（工具进程：claude / qwen / codex / zcode / openclaw）
├── Conversation 1（一轮）
│   ├── user 消息             (role=user)
│   ├── assistant 回复        (role=assistant)
│   ├── tool 结果             (role=tool)
│   └── 最终 assistant 回复   (role=assistant)
├── Conversation 2
│   ├── user 消息             (role=user)
│   └── assistant 回复        (role=assistant)
└── Conversation N
```

### 统计示例

```
Conversation 1:
  user: "查一下天气"
  │
  ├── Request 1 ──> assistant: "我去查一下..."     (role=assistant)
  │
  ├── Request 2 ──> tool: {天气数据}                (role=tool)
  │
  └── Request 3 ──> assistant: "今天晴天..."        (role=assistant)

Conversation 2:
  user: "明天呢？"
  │
  └── Request 4 ──> assistant: "明天..."            (role=assistant)
```

结果：

- **Requests**：4 次模型调用（如何映射到 `request_count` 取决于工具 —— 见上方 Request 概念）
- **Messages**：6 行（2 user + 3 assistant + 1 tool）
- **Conversations**：2 轮

### 数据库字段

| 字段 | 说明 |
|------|------|
| `agent_session_id` | 工具会话标识（进程级） |
| `conversation_id` | 对话标识（一轮对话） |
| `feishu_conversation_id` | 飞书会话标识 |

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

- `input_tokens` 来自 `input_tokens`
- `output_tokens` 来自 `output_tokens`
- `cache_read_tokens` 来自 `cache_read_input_tokens`
- `cache_creation_tokens` 来自 `cache_creation_input_tokens`
- `tokens_used` 按 `input + output + cache_read + cache_creation` 计算

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

- `tokens_used` 来自 `last_token_usage.total_tokens` 的逐事件累加值
- `cache_tokens` 来自 `cached_input_tokens`
- `input_tokens` 按 `input_tokens - cached_input_tokens` 计算
- `output_tokens` 来自 `output_tokens`
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

- `tokens_used` 来自 `computed_total_tokens`
- `input_tokens` 来自 `turn_usage.input_tokens`
- `output_tokens` 来自 `turn_usage.output_tokens`
- `cache_tokens` 按 `cache_creation_input_tokens + cache_read_input_tokens` 计算

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

- `prompt_tokens` 来自 `promptTokenCount`
- `candidates_tokens` 来自 `candidatesTokenCount`
- `thoughts_tokens` 来自 `thoughtsTokenCount`
- `cached_tokens` 来自 `cachedContentTokenCount`
- `tokens_used` 来自 `totalTokenCount`

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

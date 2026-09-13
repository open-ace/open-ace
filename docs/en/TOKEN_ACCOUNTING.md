# Token Accounting for Claude / Codex / ZCode / Qwen

This document explains how Open ACE collects token usage for the four local tools `claude`, `codex`, `zcode`, and `qwen`, how it computes daily and message-level metrics, how those metrics are stored, and which downstream services/pages consume which table.

Target readers:

- users who want to understand why Open ACE numbers can differ from provider dashboards
- maintainers who need to debug or change fetchers
- contributors adding a new local tool or adjusting usage semantics

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
- ZCode uses `turn_usage` as the source of truth

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

### 3.3 ZCode

**Source**

- path: `~/.zcode/cli/db/db.sqlite`
- script: `scripts/fetch_zcode.py`
- source tables: `session`, `message`, `part`, `turn_usage`

**Why it differs from the other three**

- ZCode stores source data relationally in SQLite, not JSONL
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
- the authoritative billing timestamp for ZCode is the turn start
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

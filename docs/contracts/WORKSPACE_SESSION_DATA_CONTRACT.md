# Workspace Session Data Boundary and Usage Rules — Workspace Session 数据边界与使用规范

[English](#english) | [中文](#中文)

---

## English

This document defines the boundary of the three runtime session tables behind
Workspace, and the single product semantics of `request_count`.

## request_count

- Workspace `request_count` is defined as: `number of distinct assistant responses`
- When a stable message ID exists, dedupe by assistant `message_id` /
  `external_message_id`
- Only fall back to row-level event counting when no stable message ID exists
- The value is NOT the completion count on the provider's bill

## agent_sessions

`agent_sessions` is the authoritative session summary table for Workspace.

Allowed responsibilities:

- One row of summary per session
- Session status, ownership, resume, and routing-control metadata
- Header statistics for the Workspace session list / detail views

Forbidden responsibilities:

- Summary fields must not be back-filled from `session_messages` or
  `daily_messages` on read paths
- Transcript import paths must not implicitly accumulate summaries

Write rules:

- Updated only by an explicit summary owner
- Maintained uniformly through `increment_session_usage()` /
  `update_session_fields()`

## session_messages

`session_messages` is the authoritative transcript table for Workspace.

Allowed responsibilities:

- Session detail message list
- Autonomous milestone transcript
- Structured content replay

Forbidden responsibilities:

- Not the authoritative source of the Workspace summary
- Inserting messages must not implicitly update `agent_sessions`

Write rules:

- Prefer `append_transcript_message()`
- Writes should carry:
  - `source`
  - `external_message_id`
  - `source_timestamp`
  - `content_blocks`
- Idempotency is decided by `(session_id, role, external_message_id)` first

## daily_messages

`daily_messages` is an analytical fact table, not a Workspace runtime table.

Allowed responsibilities:

- Unified analytical facts across tools, hosts, and sources
- usage / reporting / governance / compliance / derived stats

Forbidden responsibilities:

- Not part of the normal Workspace session-summary read path
- Not a routine fallback source for the Workspace transcript

Write rules:

- Written through `DailyMessagesSink` (Issue #3027)
- Data sources:
  - `llm_proxy`: Workspace page AI conversations (`DailyMessagesSink`)
  - `remote_sync`: CLI session sync (`remote.py`)
- Idempotency strategy: unique constraint on
  `(date, tool_name, message_id, host_name)`
- Message ID generation: `{session_id}-{timestamp_ms}-{sequence}`
- Field alignment: keep consistent with `session_sync` in `remote.py`

## Runtime contract

- The transcript writer writes only `session_messages` by default
- The summary owner explicitly updates `agent_sessions`
- Fetchers / importers / remote history sync may backfill the transcript, but
  must not grow the summary as a side effect

---

## 中文

本文档定义 Workspace 运行时会话相关三张表的边界，以及 `request_count` 的唯一产品语义。

## request_count

- Workspace `request_count` 的定义是：`独立 assistant 响应次数`
- 有稳定消息 ID 时，按 assistant `message_id` / `external_message_id` 去重
- 没有稳定消息 ID 时，才回退到按行级事件计数
- 该值不是 provider 账单上的 completion 次数

## agent_sessions

`agent_sessions` 是 Workspace 会话摘要权威表。

允许职责：

- 一条 session 一行摘要
- 会话状态、归属、恢复、路由控制元数据
- Workspace session list / detail 头部统计

禁止职责：

- 不应由 `session_messages` 或 `daily_messages` 在读路径中反向覆盖摘要字段
- 不应由 transcript 导入路径隐式累计摘要

写入规范：

- 只能由显式 summary owner 更新
- 统一通过 `increment_session_usage()` / `update_session_fields()` 维护

## session_messages

`session_messages` 是 Workspace transcript 权威表。

允许职责：

- session detail 消息列表
- autonomous milestone transcript
- 结构化内容回放

禁止职责：

- 不承担 Workspace summary 权威来源
- 不应通过插入消息隐式更新 `agent_sessions`

写入规范：

- 优先使用 `append_transcript_message()`
- 需要携带：
  - `source`
  - `external_message_id`
  - `source_timestamp`
  - `content_blocks`
- 幂等优先按 `(session_id, role, external_message_id)` 判定

## daily_messages

`daily_messages` 是分析事实表，不是 Workspace 运行时表。

允许职责：

- 跨工具、跨主机、跨来源的统一分析事实
- usage / reporting / governance / compliance / derived stats

禁止职责：

- 不参与 Workspace session summary 正常读取链路
- 不作为 Workspace transcript 的常态兜底源

写入规范：

- 通过 `DailyMessagesSink` 写入（Issue #3027）
- 数据来源：
  - `llm_proxy`：Workspace 页面 AI 对话（`DailyMessagesSink`）
  - `remote_sync`：CLI 会话同步（`remote.py`）
- 幂等策略：按 `(date, tool_name, message_id, host_name)` 唯一约束
- 消息 ID 生成：`{session_id}-{timestamp_ms}-{sequence}`
- 字段对齐：与 `remote.py` 的 `session_sync` 保持一致

## 运行时 contract

- transcript writer 默认只写 `session_messages`
- summary owner 显式更新 `agent_sessions`
- fetcher / importer / remote history sync 允许补 transcript，但不得顺带增长 summary

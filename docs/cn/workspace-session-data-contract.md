# Workspace Session 数据边界与使用规范

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

## 运行时 contract

- transcript writer 默认只写 `session_messages`
- summary owner 显式更新 `agent_sessions`
- fetcher / importer / remote history sync 允许补 transcript，但不得顺带增长 summary

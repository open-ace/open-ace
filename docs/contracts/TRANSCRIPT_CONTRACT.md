# Remote Session Transcript Contract (#2047, Phase A) — 远程会话记录契约（#2047，Phase A）

[English](#english) | [中文](#中文)

---

## English

This document pins the transcript / `content_blocks` / message-count / replay
contract for remote sessions and records the Phase A product decision. The
Phase B follow-ups that depend on #2046 (command/test evidence separation)
and #2022 (normalized provider lifecycle events) are tracked in
[docs/dev-notes/2046-transcript-phase-b-followups.md](../dev-notes/2046-transcript-phase-b-followups.md).

## Scope

`RemoteSessionManager` serves two kinds of remote sessions over one shared
stdout ingestion path (`process_session_output` → `_accumulate_assistant_text`
→ `_flush_assistant_buffer`):

- **Ordinary interactive remote sessions** — remote workspace / remote terminal
  driven by a human through the web UI (`session_type` `chat` / `agent` /
  `terminal`).
- **Autonomous workflow sessions** — driven by the autonomous runner
  (`session_type == 'workflow'` with a `context.workflow_id`, detected by
  `is_autonomous_workflow_session`).

## Phase A decision: unified schema + autonomous-additive evidence policy

We adopt a **unified `session_messages` schema** (no separate transcript
profile, no new `is_autonomous` column). The autonomous structured-evidence
policy is **additive**: it is gated on the existing derived
`is_autonomous_workflow_session` flag, so the shared path's interactive
behaviour is unchanged.

Rationale (per the #2047 scope note, 2026-07-26):

- This phase must not change ordinary remote-session semantics.
- `session_messages` already carries `content_blocks`, so a second profile is
  not needed to express the difference.
- Whether a second profile is warranted is deferred to Phase B, after #2046
  separates authoritative command/test evidence and #2022 normalizes provider
  events.

### The #1939 regression (fixed in this phase)

PR #1939 widened the shared path so that tool/thinking-only turns wrote an
empty `content=""` assistant row and folded Claude `user` `tool_result` blocks
into the assistant turn. The motivation was autonomous (preserve real test
execution evidence), but the implementation was unguarded and applied to
ordinary interactive sessions too — producing empty assistant bubbles and
inflating `message_count`.

#2047 scopes that evidence policy to autonomous sessions:

- `_accumulate_assistant_text` accumulates structured blocks for ordinary
  sessions only when the turn also produced visible text; autonomous sessions
  accumulate block-only turns too.
- `_flush_assistant_buffer` writes a row for ordinary sessions only when there
  is visible text; autonomous sessions also persist block-only turns as
  `content_blocks` evidence.
- The `user` `tool_result` folding branch runs only for autonomous sessions.

## Turn contract

A *turn* is the assistant output accumulated between two flush triggers
(`type == "result"` or process `is_complete`). Persisted rows live in
`session_messages`.

| Session kind | Visible text | tool/thinking-only | user `tool_result` |
| --- | --- | --- | --- |
| Ordinary interactive | one `role=assistant` row, `content=text`, `source=remote_live`; accompanying `tool_use`/`thinking` blocks kept in `metadata.content_blocks` | **no row written, no count bump** | ignored (not folded) |
| Autonomous workflow | same as interactive | one `role=assistant` row, `content=""`, blocks in `metadata.content_blocks` | folded into the current turn's `metadata.content_blocks` |

`role` is `assistant`; `source` is `remote_live`; structured blocks ride in
`metadata.content_blocks` (and are also decoded into the `content_blocks`
column). System stream completions write a `role=system` row.

## Count contract

- `agent_sessions.message_count` is incremented by 1 per **newly inserted**
  assistant/system turn (`increment_session_usage(message_delta=1)`, gated on
  the stored row's `_was_inserted` flag). Tool-only turns that produce no row
  (ordinary sessions) do **not** increment it.
- `request_count`, `total_tokens`, `total_input_tokens`, `total_output_tokens`
  are driven independently by `process_usage_report`, not by transcript writes.
- There is no separate `conversation_turn_count` or `visible_message_count`
  column today; both are Phase B candidates once #2046/#2022 land.

## Idempotency

- `_flush_assistant_buffer` only counts a turn when
  `getattr(stored, "_was_inserted", False)`; metadata-merge updates of an
  existing row do not bump `message_count`.
- `result` flushes the turn; a subsequent process `is_complete` with empty
  data flushes an empty buffer and writes nothing extra (see
  `test_result_then_process_complete_does_not_double_flush`).
- Repeated identical turns produce distinct rows only when they are distinct
  turns; replaying the same stream does not duplicate rows.

## Replay contract (reconnect)

- Transcript replay: `GET /api/remote/sessions/<id>` →
  `RemoteSessionManager.get_session_status()["messages"]` →
  `SessionManager.get_messages`, ordered `timestamp ASC` (write order).
- Live event replay: the SSE `stream_session_output` route replays
  `remote_runtime_outputs` ordered by `event_index ASC`, cursor-tracked via
  `set_last_delivered` so a mid-stream disconnect does not duplicate events.
- These two sources are independent: `remote_runtime_outputs` is the live
  stdout ring buffer; `session_messages` is the durable transcript.

## Target: normalized turn identity (TranscriptTurn)

Issue #2047 proposes a normalized turn shape so persistence/presentation policy
can be decided uniformly. The **target** definition (implementation deferred to
#2022, which owns provider event normalization):

```python
@dataclass
class TranscriptTurn:
    turn_id: str
    role: str
    visible_text: str
    content_blocks: list[dict]
    tool_call_ids: list[str]
    evidence_ids: list[str]
    started_at: datetime | None
    completed_at: datetime | None
    terminal_reason: str | None
```

This phase does **not** introduce `TranscriptTurn`; it only documents the
target so #2022 can produce it and persistence/presentation layers can consume
it without re-parsing each CLI protocol.

## Tests that lock this contract

- `tests/integration/test_remote_session_transcript_e2e.py` — real SQLite DB
  rows for text / text+tool_use / tool-only / thinking-only / user tool_result
  (interactive vs autonomous), double-flush idempotency, replay order,
  OpenAI message shape, system stream.
- `tests/integration/test_remote_session_api_e2e.py` — `get_session_status`
  payload (`messages[]`, `message_count`) matches persisted rows; no empty
  bubble after a tool-only turn.
- `tests/unit/test_remote_assistant_message.py` — accumulation call contract
  including `test_empty_text_not_stored` (ordinary) and the autonomous-only
  evidence tests.
- `frontend/src/components/common/MessageContent.test.tsx` — block rendering
  (text / thinking / tool_use / tool_result / reasoning) and plain-content
  fallback.

---

## 中文

本文档固定远程会话的会话记录 / `content_blocks` / 消息计数 / 回放契约，并记录
Phase A 的产品决策。依赖 #2046（命令/测试证据分离）与 #2022（规范化 provider
生命周期事件）的 Phase B 跟进事项，见
[docs/dev-notes/2046-transcript-phase-b-followups.md](../dev-notes/2046-transcript-phase-b-followups.md)。

## 范围

`RemoteSessionManager` 通过一条共享的 stdout 摄取路径
（`process_session_output` → `_accumulate_assistant_text` →
`_flush_assistant_buffer`）服务两类远程会话：

- **普通交互式远程会话** —— 由人通过 web UI 驱动的远程工作区 / 远程终端
  （`session_type` 为 `chat` / `agent` / `terminal`）。
- **自主工作流会话** —— 由自主 runner 驱动（`session_type == 'workflow'` 且带
  `context.workflow_id`，由 `is_autonomous_workflow_session` 判定）。

## Phase A 决策：统一 schema + 自主会话增量式证据策略

我们采用**统一的 `session_messages` schema**（不设单独的会话记录 profile，
也不新增 `is_autonomous` 列）。自主会话的结构化证据策略是**增量式**的：它门控在
已有的派生标志 `is_autonomous_workflow_session` 上，因此共享路径的交互行为保持
不变。

理由（见 #2047 的范围说明，2026-07-26）：

- 本阶段不得改变普通远程会话的语义。
- `session_messages` 已经携带 `content_blocks`，不需要第二个 profile 来表达
  差异。
- 是否需要第二个 profile 推迟到 Phase B 再评估——待 #2046 分离权威的命令/测试
  证据、#2022 规范化 provider 事件之后。

### #1939 回归（本阶段修复）

PR #1939 拓宽了共享路径，使得仅含 tool/thinking 的轮次也写入一条空的
`content=""` assistant 行，并把 Claude `user` `tool_result` 块折叠进 assistant
轮次。动机是自主会话（保留真实的测试执行证据），但实现没有加守卫，同样作用于
普通交互式会话——产生空的 assistant 气泡并虚增 `message_count`。

#2047 把该证据策略限定在自主会话：

- `_accumulate_assistant_text` 只有在轮次同时产生可见文本时，才为普通会话累积
  结构化块；自主会话还会累积仅含块的轮次。
- `_flush_assistant_buffer` 只有在存在可见文本时，才为普通会话写行；自主会话
  还会把仅含块的轮次作为 `content_blocks` 证据持久化。
- `user` `tool_result` 折叠分支只对自主会话运行。

## 轮次契约

一个*轮次*（turn）是两次 flush 触发（`type == "result"` 或进程
`is_complete`）之间累积的 assistant 输出。持久化的行存放在
`session_messages`。

| 会话类型 | 有可见文本 | 仅 tool/thinking | `user` `tool_result` |
| --- | --- | --- | --- |
| 普通交互式 | 写入一行 `role=assistant`，`content=text`，`source=remote_live`；随附的 `tool_use`/`thinking` 块保存在 `metadata.content_blocks` | **不写行、不计数** | 忽略（不折叠） |
| 自主工作流 | 与交互式相同 | 写入一行 `role=assistant`，`content=""`，块保存在 `metadata.content_blocks` | 折叠进当前轮次的 `metadata.content_blocks` |

`role` 为 `assistant`；`source` 为 `remote_live`；结构化块随行保存在
`metadata.content_blocks`（并同时解码进 `content_blocks` 列）。系统流结束时
写入一条 `role=system` 行。

## 计数契约

- `agent_sessions.message_count` 每**新插入**一条 assistant/system 轮次加 1
  （`increment_session_usage(message_delta=1)`，以所存行的 `_was_inserted`
  标志为门控）。不产生行的纯 tool 轮次（普通会话）**不**计数。
- `request_count`、`total_tokens`、`total_input_tokens`、`total_output_tokens`
  由 `process_usage_report` 独立驱动，与会话记录写入无关。
- 目前没有单独的 `conversation_turn_count` 或 `visible_message_count` 列；
  两者都是 #2046/#2022 落地后的 Phase B 候选项。

## 幂等性

- `_flush_assistant_buffer` 只在 `getattr(stored, "_was_inserted", False)`
  时才为轮次计数；对已有行的元数据合并更新不会增加 `message_count`。
- `result` 会 flush 当前轮次；随后数据为空的进程 `is_complete` 只会 flush 一个
  空缓冲，不会额外写入（见
  `test_result_then_process_complete_does_not_double_flush`）。
- 重复的相同轮次只有在确实是不同轮次时才产生不同的行；重放同一路流不会复制
  行。

## 回放契约（重连）

- 会话记录回放：`GET /api/remote/sessions/<id>` →
  `RemoteSessionManager.get_session_status()["messages"]` →
  `SessionManager.get_messages`，按 `timestamp ASC`（写入顺序）排序。
- 实时事件回放：SSE `stream_session_output` 路由按 `event_index ASC` 重放
  `remote_runtime_outputs`，通过 `set_last_delivered` 做游标跟踪，中途断开
  不会重复事件。
- 这两个来源相互独立：`remote_runtime_outputs` 是实时 stdout 环形缓冲；
  `session_messages` 是持久化的会话记录。

## 目标形态：规范化的轮次标识（TranscriptTurn）

Issue #2047 提出一种规范化的轮次形状，让持久化/展示策略可以被统一决策。
**目标**定义如下（实现推迟到 #2022，由它负责 provider 事件规范化）：

```python
@dataclass
class TranscriptTurn:
    turn_id: str
    role: str
    visible_text: str
    content_blocks: list[dict]
    tool_call_ids: list[str]
    evidence_ids: list[str]
    started_at: datetime | None
    completed_at: datetime | None
    terminal_reason: str | None
```

本阶段**不**引入 `TranscriptTurn`；只是把目标记录在案，让 #2022 可以产出它，
持久化/展示层可以直接消费，而无需重新解析每种 CLI 协议。

## 锁定该契约的测试

- `tests/integration/test_remote_session_transcript_e2e.py` —— 真实 SQLite
  数据库行：文本 / 文本+tool_use / 纯 tool / 纯 thinking / user tool_result
  （交互式 vs 自主）、双 flush 幂等、回放顺序、OpenAI 消息形状、系统流。
- `tests/integration/test_remote_session_api_e2e.py` ——
  `get_session_status` 载荷（`messages[]`、`message_count`）与持久化行一致；
  纯 tool 轮次之后没有空气泡。
- `tests/unit/test_remote_assistant_message.py` —— 累积调用契约，包括
  `test_empty_text_not_stored`（普通会话）与仅自主会话的证据测试。
- `frontend/src/components/common/MessageContent.test.tsx` —— 块渲染
  （text / thinking / tool_use / tool_result / reasoning）与纯内容回退。

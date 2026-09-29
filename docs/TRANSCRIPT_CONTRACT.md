# Remote Session Transcript Contract (#2047, Phase A)

This document pins the transcript / `content_blocks` / message-count / replay
contract for remote sessions, records the Phase A product decision, and lists
the Phase B follow-ups that depend on #2046 (command/test evidence separation)
and #2022 (normalized provider lifecycle events).

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

## Phase B follow-ups

- #2046: separate authoritative command/test evidence (command ID, exit code,
  stdout/stderr, test verdict) from the transcript; the transcript should
  reference evidence IDs rather than be the evidence store.
- #2022: normalized provider lifecycle event / `TranscriptTurn`; this issue
  will then consume that event instead of per-protocol parsing.
- Re-evaluate whether an autonomous-specific retention/presentation policy
  still needs a separate profile once evidence is separated; if ordinary and
  autonomous policies converge, no profile is added.
- Legacy sessions created before this contract need a documented
  schema/version fallback (tracked separately).
- Daily stats / quota must not double-count once evidence is separated
  (`daily_messages` mirror + `quota_usage` reconciliation).

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

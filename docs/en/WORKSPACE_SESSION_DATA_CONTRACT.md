# Workspace Session Data Boundary and Usage Rules

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

# Phase B follow-ups — Remote Session Transcript Contract (#2047, Phase A)

Moved from docs/TRANSCRIPT_CONTRACT.md during the 2026-09-29 docs governance restructure. Issue-tracking content does not belong in the long-lived contract.（2026-09-29 文档治理时从契约文档迁出：issue 状态跟踪不属于长期契约。）

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

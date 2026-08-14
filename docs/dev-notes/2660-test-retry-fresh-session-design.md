# Design: test-evidence retry must force a fresh-session re-execution (#2590 follow-up)

Date: 2026-08-14. Issue: tracked under #2590's workflow failure (new issue to be filed as #2660).

## Root cause (verified against session JSONL + milestones)

`_run_test_phase` dispatches the test agent with `session_line="test"`, which
**resumes the same CLI session across retries**. The dev agent already ran
tests during development, so on a retry it answers by citing prior-round
results ("所有必测项已在前面回合完成验证" — sometimes with an empty response)
instead of executing anything new. The evidence gate only recognizes output
produced in the current attempt → verdict `inconclusive` on every retry →
MAX_TEST_RETRIES(2) exhausted → workflow failed. Deployed fixes don't cover
this: Fix A carries forward a prior *verdict* (here the prior attempt is also
inconclusive), Fix B's fresh-retry only fires on success+0-token+empty.

## Decision (user-approved): fresh session + explicit retry instructions

On a test-phase retry (`test_retries > 0` when `_run_test_phase` dispatches):

1. **Dispatch `session_line="fresh"`** instead of `"test"`. An unregistered
   line resolves to `(new uuid, None, resume=False)` — a brand-new session.
   The test prompt is self-contained (final plan ≤4000 chars, changed files,
   verification scopes, repo conventions), so nothing critical is lost, and
   structurally there is no prior round in context to cite.
2. **Append a retry block to the prompt**: 上一轮验证未通过证据门（未捕获
   可识别的新测试输出）。本轮必须重新执行验证矩阵的命令并在回复中包含
   每条命令的原始输出；不得引用之前回合的结果作为本轮验证证据。

First run (test_retries==0) is unchanged (resume is desirable there: the dev
session has the freshest mental context of what changed).

## Files

- `app/modules/workspace/autonomous/orchestrator.py` — `_run_test_phase` only.
- `tests/unit/test_test_retry_fresh_dispatch.py` — new regression tests.

## Testing

Unit tests (mock `_run_agent`, call `_run_test_phase`, assert the passed
`session_line` and prompt content):
- first run (test_retries=0): `session_line="test"`, no retry block;
- retry (test_retries>=1): `session_line="fresh"`, prompt contains the
  retry-instruction block.

No schema changes. E2E not required (no frontend change).

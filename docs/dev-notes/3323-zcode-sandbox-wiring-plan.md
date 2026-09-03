# #3323 — run `zcode` on the sandbox provider

Plan, for review before implementation. Revised after independent review found
several "already handled" claims false against source. Claims below verified on
2026-09-02 (see "Verification").

## Problem

`zcode` cannot run under a tenant with a production-isolation policy.
`_resolve_tenant_for_isolation` refuses it up front because its execution path
bypasses the sandbox provider — correct as written, since a silent local
fallback would be the unsandboxed execution #2023 acceptance criterion 12
forbids, but it leaves ZCode unusable for those tenants. The reason is a wiring
gap, not a protocol incompatibility.

## Why the protocol is not the blocker

`PtyWebSocketTransport`
(`app/modules/workspace/autonomous/sandbox/opensandbox/transport.py:159`)
already carries claude/qwen's stream-json turn. The provider handshake this plan
reuses is live at `agent_runner.py:3153-3163`:

```python
exec_policy = provider.agent_turn_policy(prompt=prompt, model=model, env=env)
exec_handle = provider.exec(sandbox_handle, command=cmd, env=env, exec_policy=exec_policy)
transport   = provider.get_transport(exec_handle)
```

`ZCodeAppServerSession` uses exactly this surface on its `process`
(`remote-agent/zcode_app_server.py`), mapped to the transport:

| session uses | transport offers |
| --- | --- |
| `stdin.write(str@:952 \| bytes@:969)` + `flush` | `write_stdin(bytes)` |
| `for raw in stdout` (`:759`) | `readline_stdout() -> bytes` |
| `for raw in stderr` — `_read_zcode_stderr_local` (`agent_runner.py:4123`) | `readline_stderr() -> bytes` |
| `pid` (`:128`; pause/resume guards `:1012`/`:1020`) | `pid -> None` |
| `returncode` (`:128`,`:132`,`:1001`) | `returncode` |
| `terminate()` (`:1002`), `kill()` (`:1006`) | `shutdown(grace)` |
| `wait(timeout)` (`:1004`,`:1028`) | `wait(timeout)` |

The stdin write is reached with **both** `str` (`:952`) and `bytes` (`:969`);
the local Popen is opened in binary mode (`agent_runner.py:3598`, no `text=`),
so the str write is already latently fragile locally, and the adapter must
normalise both to `write_stdin(bytes)`.

### The transport is a blocking line-iterator (an earlier draft got this wrong)

An earlier draft claimed `readline_stdout()` returns `b""` on a read timeout and
would spin at 100% CPU. **False.** `_LineStream.readline` (`transport.py:128`;
the class is `_LineStream`, not the "FramedStream" the earlier draft invented)
`continue`s on `queue.Empty` and returns `b""` **only** on real EOF. EOF is
delivered on exit: an `exit` frame (`_handle_text`, `:399`) or any stream break
(`_break_stream`, `:413`) closes the streams via the sentinel, and a reader
parked in `readline` unblocks when `shutdown()` closes the socket. So the
adapter's `.stdout`/`.stderr` are trivial generators — `while True: line =
readline(); if not line: return; yield line` — no `poll()` disambiguation, no
spin, no teardown hang.

## What the first draft got wrong (all verified against source)

These are the corrections that make this plan implementable:

1. **The #3237 refusal does NOT fire for a sandboxed ZCode.**
   `_plan_agent_state` runs at `agent_runner.py:2970`, but ZCode `return`s from
   `_run_local` at the `_APPSERVER_TOOLS` dispatch (`:2891`) — *before*
   `_select_sandbox_provider` (`:2953`) and `_plan_agent_state`. The comment at
   `:2711-2714` states this. So a resumed sandboxed ZCode would `--resume` into
   an empty ephemeral HOME and **silently cold-start**. The interim safety net
   must be **added inside `_run_zcode_appserver`**, not "kept": refuse (or no-op
   the resume) when the provider carries agent-state ephemerally and no carry
   exists yet. This is a required change, not a documentation note.

2. **`_on_pid_registered` with a null pid persists a NULL row, not nothing.**
   `orchestrator.py:7060` unconditionally stores `{"agent_pid": pid, …}`. The
   stream-json path guards this (`agent_runner.py:3233`, `transport.pid is not
   None`); the ZCode path (`:3652`) is unguarded. The wiring must add the same
   `transport.pid is not None` guard on the ZCode path.

3. **The refusal is narrowed by a carve-out, not by editing `_APPSERVER_TOOLS`.**
   `_APPSERVER_TOOLS = frozenset({"zcode", "zcode-code"})` (`:161`) also drives
   routing (`:2891`) and session-creation timing (`:2378`); editing it breaks
   those. The narrowing is a targeted carve-out at the refusal site (`:4341`)
   only, and must cover **both** aliases. (codex/openclaw are refused by the
   *other* branch — `not supports_stdin_input()`, `:4343` — and are unaffected.)

4. **`_run_zcode_appserver` has no provider/tenant plumbing yet.** Its signature
   (`:3517`) takes `user_id`/`workspace_type` but no `tenant_id` and no
   provider; tenant resolution and `_select_sandbox_provider` live in
   `_run_local` *after* the dispatch. So narrowing the refusal without threading
   the tenant into `_run_zcode_appserver` and selecting the provider there would
   reintroduce the unsandboxed execution criterion 12 forbids. The plumbing
   (pass `tenant_id`; call `_select_sandbox_provider`; branch local vs sandbox)
   is part of this change.

5. **The tracker's `process` must be `None` on the sandbox path.** The
   stream-json sandbox path sets `tracker.process = None` (`:3166`/`:3206`); the
   orchestrator's non-provider fallbacks dereference `session.process.pid`
   (`stop_session:5356`, `pause_session:5439`, `resume_session:5469`) and would
   hit `os.getpgid(None)` (the `:3272` comment warns of exactly this). The
   Popen-shaped adapter goes only into the local `process` variable (fed to
   `ZCodeAppServerSession` and `_read_zcode_stderr_local`); the **tracker** gets
   `process=None` plus `transport`/`sandbox_provider`/`exec_handle`, so all
   orchestrator control routes through the provider-first branches
   (`stop_session:5324`, `pause_session:5429`, `resume_session:5459`), which
   return before any `getpgid`.

6. **`terminate()`→`shutdown()` is a single SIGINT + PTY teardown**, not a
   SIGTERM, and `stop()`'s `kill()` escalation branch is dead code because the
   adapter's `wait()` returns `None` on timeout and never raises
   `TimeoutExpired`. This is safe (`shutdown` is idempotent via `_shutdown_done`)
   but is a behavioural change the plan states rather than hides.

## Design fork (the part most worth reviewing)

**Chosen: a `Popen`-shaped adapter over the transport, not retyping the
session.** `ZCodeAppServerSession` is ~1000 lines shared with the remote-agent
executor; an adapter confines the change to the sandbox path and leaves the
local path byte-identical. The adapter exposes
`.stdin`(`.write(str|bytes)`/`.flush`), `.stdout`, `.stderr`, `.pid`(None),
`.returncode`, `.terminate()`, `.kill()`, `.wait()`.

Alternatives: retype the session against a transport protocol (cleaner, but
widens blast radius to the executor's file); a second session class (duplicates
the protocol handling, the costliest part to get wrong twice). Both rejected.

## Interaction with #3237 (carry deferred, refusal added)

ZCode's own agent-state carry is out of scope: it needs the same minted-id
capture + `_resolve_session_line` extension #3319/#3321 establish, and bundling
it here would make both harder to review. Because the refusal does **not**
currently fire on this path (finding #1), the deferral is only safe once the
explicit in-function refusal is added. Reviewers: if you think the carry must
land here instead, say so — that is the main scoping judgement.

## Changes

1. New adapter over `PtyWebSocketTransport` exposing the surface above.
2. `_run_zcode_appserver` — thread `tenant_id` in; when the tenant selects a
   sandbox provider, run the `agent_turn_policy → exec → get_transport`
   handshake, wrap the transport as the local `process`, set the tracker
   `process=None` + `transport`/`sandbox_provider`/`exec_handle`, guard
   `_on_pid_registered` on `transport.pid is not None`, and add the
   ephemeral-state refusal. Keep the local `Popen` path unchanged otherwise.
   The pid guard is a **sandbox-vs-local branch**, not one blanket condition:
   the local path has no `transport` in scope, so it must keep registering the
   real `process.pid`; only the sandbox path skips registration when
   `transport.pid is None` (AC4 scopes this to the sandbox path).
3. Refusal carve-out at `agent_runner.py:4341` for both `zcode`/`zcode-code`,
   leaving the single-shot refusal intact.
4. Lifecycle parity: `upload_workspace` before, `collect_changes`/
   `apply_changes` after, `destroy` last.

## Acceptance

1. `zcode` runs to completion under a `production_required_tenants` tenant,
   inside the sandbox.
2. Wiring regression through the real `_run_zcode_appserver` against the fake
   OpenSandbox API, asserting the lifecycle order (create → upload_workspace →
   exec → collect → apply → destroy); must fail if the transport wiring is
   removed.
3. `stop_session` on a sandboxed ZCode routes through
   `provider.stop(exec_handle)` and never calls `os.getpgid`; must fail if the
   tracker is left with `process=<adapter>` or missing `sandbox_provider`/
   `exec_handle`.
4. `_on_pid_registered` is not called with a null pid on the sandbox path.
5. stdout/stderr adapter generators terminate on the transport's EOF (exit frame
   and stream-break), driven by the fake API.
6. A resumed sandboxed ZCode hits the in-function refusal (finding #1), never a
   silent cold start; must fail if that refusal is removed.
7. The local ZCode path and its existing tests are unchanged; single-shot tools
   still refused under production isolation.

## Verification (2026-09-02)

* Transport: `pid` hardcoded `None` (`transport.py:219`); `_LineStream.readline`
  returns `b""` only on EOF (`:128`); `shutdown` SIGINT + delete pty (`:281`).
* Session `self.process` surface complete per grep of
  `remote-agent/zcode_app_server.py`: stdin `:952`/`:969`, stdout `:759`,
  pid/returncode `:128`/`:132`, terminate/wait/kill `:1002`/`:1004`/`:1006`,
  pause/resume `os.kill(...pid...)` `:1014`/`:1022`.
* Dispatch order `:2891` (ZCode return) < `:2953` (`_select_sandbox_provider`) <
  `:2970` (`_plan_agent_state`); `_on_pid_registered` guard present at `:3233`
  (stream-json), absent at `:3652` (ZCode); `_read_zcode_stderr_local` iterates
  `process.stderr` at `:4123`.
* Provider-first control branches `stop_session:5324`, `pause_session:5429`,
  `resume_session:5459` return before `os.getpgid` (`:5356`/`:5439`/`:5469`);
  liveness guards key off `session.transport` (`:5421`/`:5453`); stream-json
  tracker sets `process=None` at `:3166`/`:3206`.
* ZCode engine present at `/Applications/ZCode.app/Contents/Resources/glm/
  zcode.cjs`; no protocol change proposed, so verified against source not a live
  turn.

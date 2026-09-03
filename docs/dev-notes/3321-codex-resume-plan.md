# #3321 — resume `codex` in autonomous single-shot runs

Plan, for review before implementation. Revised after independent review found
the first draft's carry channel did not exist for codex. Claims verified
against source and the live CLI on 2026-09-02 (see "Verification").

## The gating question is settled: `codex exec` can resume

The issue says: *"Can `codex exec` resume a session? Determine this first. Do
not implement against an assumed flag."* It can.

`codex exec resume [OPTIONS] <SESSION_ID> [PROMPT]` is a real non-interactive
subcommand (codex-cli 0.152.1). Verified live, with a three-way identity match
that closes any doubt about which id to use:

* `codex exec --json` emits `{"type":"thread.started","thread_id":"<uuid>"}` as
  its **first** event.
* That same `<uuid>` names the rollout file:
  `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` (`$CODEX_HOME`
  defaults to `~/.codex`), matching `scripts/fetch_codex.py:8`.
* `codex exec resume --json <uuid> "<prompt>"` resolves that rollout — an
  **unknown** id fails distinctly with *"no rollout found for thread id
  <uuid>"*, so a wrong id is a loud error, not a silent cold start.

So this is the **plumbing** case, implemented — not the design-change case, not
closed with a finding.

Two live caveats, carried forward as risks rather than assumptions:

* Resuming an id whose writer is still open fails with *"thread … already has
  an active writer"*. In production, milestone *N*'s codex process exits before
  milestone *N+1* starts, releasing the writer, so sequential milestones should
  not hit this — **but a test/first real run must confirm it**, because if it
  does bite it converts resume into a hard failure.
* `codex exec resume` **rejects `--sandbox` and `--cd`** (confirmed by running
  them). Sandbox policy is preserved with `-c sandbox_mode="read-only"` — a
  config override the subcommand accepts and `--strict-config` recognises as a
  real key (a bogus key is rejected, so this is not silently ignored). Working
  directory comes from the subprocess `cwd` the runner already sets.

## The blocking defect the first draft missed: the resume id is claude-only

Carrying nothing on disk is fine — the rollout persists for free (see below).
The hard part is the **id**, and the first draft's "surface it as the result's
session id" does not work, because the resume id is resolved claude-only in
three places, none reached by codex:

1. **Capture → persist.** The claude `cli_session_id` writers
   (`_capture_cli_session_id` at `:1285`, `_sync_sidebar_session_totals` at
   `:2239`) run **only from the streaming `_read_stdout` loop**
   (`:5071`/`:5109`/`:5143`/`:5176`). `_run_single_shot` (`:3952-4116`) — the
   codex path — never calls them and never touches `session_manager`, so
   codex's id is never written to the column. This is **not** fixed by
   flipping `_uses_sidebar_session_source` (that predicate gates 14
   claude-sidebar-specific sites, e.g. the `AGENT_STATE_CARRIED` refusal at
   `:2715`, and would misfire for codex); it needs a new explicit write.
2. **Resolve.** `_resolve_session_line` maps a line's tracking id to the real
   `cli_session_id` **only when `cli_tool == "claude-code"`**
   (`orchestrator.py:7228`); for every other tool it returns
   `(existing, existing, True)` — resume target == tracking id
   (`orchestrator.py:7265`).

Net: without a change, codex is launched with `codex exec resume <tracking-id>`
while the rollout is named `<thread_id>` → *"no rollout found"* every time. The
fix must (a) capture `thread_id` into `cli_session_id`, (b) extend the resolve
branch to codex, and (c) keep `tracking_session_id == task_id` so the per-task
HOME does not rotate.

## Why the rollout persists across milestones without a carry

`task_id == session_id` throughout the runner (`:2935`, `:3218`), and the
per-line `session_id` is the stable `wf["main_session_id"]` reused by
`_resolve_session_line`, so the per-task HOME
(`/run/openace-agent-tasks/<session_id>/home`, `_resolve_home_dir:1197`) is the
**same directory** across a line's milestones; `ensure_task_runtime_dirs` uses
`mkdir(exist_ok=True)` and never wipes it (`task_isolation.py`). `~/.codex`
lives under that HOME, so the rollout written on milestone 1 is still there on
milestone 2. This is the same mechanism claude-code already resumes on locally.
Only the **id** needs carrying, and that is what §"blocking defect" above adds.
(Codex also never gets a sandbox — it is refused under production isolation and
otherwise runs local — so #3237 does not apply.)

## openclaw: deferred (confirmed with the user)

`openclaw` reaches the same `_run_single_shot` path, but its resume is **not**
in scope for this issue. Correcting the first draft: openclaw *does* have a
documented on-disk layout — `scripts/fetch_openclaw.py:67-96` documents
`~/.openclaw/agents/main/sessions/<uuid>.jsonl`. What it lacks is a *verified*
non-interactive resume: its adapter captures no id and emits no resume flag
(`build_single_shot_args`, `openclaw.py:86`), and no live test was run against
the openclaw CLI. Wiring it would be implementing against an assumed flag, which
the issue forbids. Decision: **fix codex; defer openclaw**, recorded in code so
it is not a silent second case, and tracked for a separate issue if wanted.

## Changes

1. `build_single_shot_args` — add `resume: bool = False` and
   `resume_session_id: str = ""` to **base *and every override***: `base.py:162`,
   `codex_cli.py:138`, **and `openclaw.py:86`**. The overrides do not inherit a
   widened base signature, and only codex/openclaw reach `_run_single_shot`, so
   leaving openclaw's 3-arg override in place would still `TypeError` on the
   shared call at `agent_runner.py:3993`. openclaw accepts-and-ignores; codex
   uses them.
2. `codex_cli.build_single_shot_args` — when resuming, emit
   `codex exec resume --json -c sandbox_mode="read-only" <id> <prompt>`;
   otherwise byte-identical to today
   (`codex exec --json --sandbox read-only <prompt>`).
3. `_run_single_shot` — add `resume`/`resume_session_id` params and pass them to
   `build_single_shot_args`; the dispatch (`agent_runner.py:2910`) passes them
   through, mirroring the ZCode branch.
4. Capture — parse the first `--json` `thread.started.thread_id` in the
   single-shot stdout path and surface it as the result's captured session id.
   (`_extract_stream_session_id` only knows `session_id`/`sessionId`/`uuid`, so
   `thread_id` is codex-specific and must be added, not assumed generic.)
5. Persist + resolve — the load-bearing change; items 1–4 are inert without it:
   * **Persist** the captured `thread_id` with a **new explicit
     `self.session_manager.update_session_fields(session_id, {"cli_session_id":
     thread_id, …}, require_tenant=False)` inside `_run_single_shot`'s result
     path.** The codex wrapper `agent_sessions` row already exists before
     dispatch (`:2390-2403`, since codex is not in `_APPSERVER_TOOLS`), so the
     target row is present. Do **not** modify `_uses_sidebar_session_source` or
     route codex through the claude sidebar/host-mtime functions.
   * **Resolve** by extending the `_resolve_session_line` mapping branch
     (`orchestrator.py:7228`) from claude-only to also cover codex, keeping
     `tracking_session_id == task_id` (safe: the "mapping lost" arm returns a
     clean fresh start and nothing else assumes the branch is claude-only).
6. openclaw — a code comment at the adapter and the single-shot dispatch
   recording the deferral and why, plus the accept-and-ignore signature widening
   from Change #1 (so the shared call never raises for it).

## Acceptance

1. A two-turn regression driven through `_run_agent` → `_resolve_session_line`
   (not `_run_single_shot` in isolation): turn 1's `thread_id` is persisted to
   `cli_session_id`; turn 2 resolves that id and its argv is
   `codex exec resume … <thread_id>`. It must fail if **either** the capture/
   persist **or** the `_resolve_session_line` extension is removed — a test that
   hand-feeds `resume_session_id` into `_run_single_shot` would pass with the
   real carry deleted, which is exactly how the issue warns this gap hides.
2. The first-milestone (non-resume) codex argv is unchanged:
   `codex exec --json --sandbox read-only <prompt>`.
3. A resumed codex turn still runs read-only: its argv carries
   `-c sandbox_mode="read-only"` (since `--sandbox` is rejected on resume), so
   turn 2 is not less sandboxed than turn 1.
4. openclaw's argv and behaviour are unchanged, and passing `resume=…` to any
   adapter's `build_single_shot_args` does not raise.
5. An absent `thread_id` (capture failed) degrades to a cold, non-resume next
   turn — never `codex exec resume` with an empty id.

## Forward note (check at implementation, not a resume-correctness issue)

Populating `agent_sessions.cli_session_id` for codex means any UI/timeline/
transcript code that assumes a non-empty `cli_session_id` implies a Claude
`~/.claude/projects/<id>.jsonl` file would look in the wrong place for codex
(whose rollout lives under `~/.codex/sessions/...`). Grep the readers of
`cli_session_id` during implementation and guard on `cli_tool` where one assumes
the Claude layout. This is display-only; resume correctness does not depend on
it. The same applies to #3319 (qwen).

## Verification (2026-09-02)

* `codex exec resume --help` (0.152.1): `resume [OPTIONS] [SESSION_ID]
  [PROMPT]`; `--sandbox`/`--cd` rejected on the subcommand.
* `codex exec --json` first event `thread.started.thread_id`; rollout at
  `$CODEX_HOME/sessions/2026/09/02/rollout-…-<thread_id>.jsonl`; the event id ==
  the rollout filename id.
* resume `<known-id>` → "already has an active writer"; `<unknown-id>` → "no
  rollout found for thread id …"; `-c sandbox_mode="read-only"` accepted, and
  `--strict-config` accepts `sandbox_mode` while rejecting `not_a_real_key`.
* Source: `_run_single_shot` lacks resume params (`agent_runner.py:3952`) and
  never touches `session_manager`/`cli_session_id` (`:3952-4116`); dispatch
  (`:2910`), shared `build_single_shot_args` call (`:3993`), overrides at
  `codex_cli.py:138`/`openclaw.py:86`; `task_id==session_id` (`:2935`,`:3218`),
  per-task HOME (`:1197`); claude `cli_session_id` writers run only from
  `_read_stdout` (`:5071`/`:5109`/`:5143`/`:5176`); codex wrapper row created
  early (`:2390-2403`); resolve branch (`orchestrator.py:7228`,`:7265`);
  `_uses_sidebar_session_source` (`:824-826`) gates 14 sites incl. `:2715`;
  `codex_cli.py:127,161`; `scripts/fetch_openclaw.py:67`.

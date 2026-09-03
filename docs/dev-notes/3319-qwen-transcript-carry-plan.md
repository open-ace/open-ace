# #3319 — carry the CLI transcript for `qwen-code-cli`

Plan, for review before implementation. Revised after independent review found
the first draft's carry channel (store the transcript's on-disk path) both
insufficient (the resume id never reaches the CLI) and unsafe (a stored path
bypasses the traversal guard). Claims verified 2026-09-02 (see "Verification").

## Problem

`qwen-code-cli` runs on the OpenSandbox backend and shares claude-code's
stream-json path through `_run_local`, so it reaches the #3237 agent-state seam.
Before #3263 a sandboxed qwen resume was a silent no-op; #3263 made it an
explicit refusal (`agent_state_unavailable`). This issue replaces the refusal
with a working carry.

`qwen-code-cli` is the only tool that needs this here. ZCode returns to
`_run_zcode_appserver` and codex/openclaw to `_run_single_shot`, both before
`_select_sandbox_provider` (`agent_runner.py:2711-2714`). See #3321 and #3323.

## The blocking defect the first draft missed: resume id, not just the file

Carrying the transcript bytes to the right path is necessary but **not
sufficient**. Qwen mints its **own** session id and the runner never tells the
CLI to use ours:

* Turn 1 runs fresh (no `--resume`; qwen has no `--session-id` flag —
  `qwen_code.py:56-113`) and mints uuid **Q1**, writing
  `~/.qwen/projects/<encoded>/chats/Q1.jsonl`.
* The resume id is resolved **claude-only**. `_resolve_session_line` maps a
  line's tracking id to the real `cli_session_id` only when `cli_tool ==
  "claude-code"` (`orchestrator.py:7228`); otherwise it returns
  `(existing, existing, True)` — resume target == tracking id
  (`orchestrator.py:7265`). And the write that populates `cli_session_id` is
  itself gated claude-only (`_ensure_sidebar_session`, `agent_runner.py:2137`→
  `:2198`, behind `_uses_sidebar_session_source`).

So even with the bytes restored perfectly, turn 2's argv is `--resume
<tracking-id>` while the file is `chats/Q1.jsonl` → qwen finds nothing → silent
cold start. The first draft's "capture `stream_session_id` for carry only, do
not touch the resolve path" cannot work.

## Verified finding that simplifies the design (2026-09-03)

The reviews' one open question was whether qwen's session id is on the **stdout
stream** or must be recovered from disk. Tested against the installed qwen
(0.21.5): it **is** on the stream. Every stream-json event carries
`"session_id"`, starting with the very first `{"type":"system","subtype":"init",
"session_id":"<uuid>", …}` — *before* any model call — and that id equals the
transcript filename basename (`chats/<uuid>.jsonl`, confirmed on disk).

This is exactly claude's shape, so the carry is the **claude pattern**: capture
the id from the stream *during* the turn, then export/import a fixed
`chats/<id>.jsonl` path. It also removes the need for disk discovery — which was
never feasible anyway: the OpenSandbox API exposes only `download_file` /
`upload_file` / `run_command`, no file listing, so "list `chats/*.jsonl` and pick
the newest" would have required an extra exec during teardown. Stream capture
needs neither, and it is what this plan now implements. (The sidecar
`<line>.meta.json` the first draft proposed is still dropped, for the same
traversal/leak reasons the review raised.)

## Design: the minted id is the single source of truth

One id flows through capture → persist → resolve → import, so the file the CLI
looks for and the file we restore are named by the *same* id.

**Capture from the stream.** qwen reaches the generic stream-json path in
`_run_local`, which already calls `_capture_cli_session_id` on its stream events
(including the terminal `result` event, which qwen emits carrying `session_id`).
Widen that helper's internal gate to admit qwen so it sets
`session.cli_session_id` from the stream — the change is confined to that one
helper; `_uses_sidebar_session_source` and its twelve sidebar-linkage call sites
are left exactly as they are, so qwen's sidebar behaviour does not change, and
claude's capture is byte-for-byte unchanged.

**Persist** the captured id with a **dedicated, explicit write at export time** —
not by touching the claude sidebar machinery. The claude path writes
`cli_session_id` inside `_ensure_sidebar_session` (`agent_runner.py:2137`→`:2198`),
which is gated claude-only and falls back to `_find_latest_claude_session_id` — a
**host-side** `.claude` mtime glob that would look on the control plane, not
inside the pod. None of that fits qwen. Instead the export block
(`agent_runner.py:~3330`, inside `_run_local`, where `cli_tool`, `session` and the
captured id are all in scope) calls
`session_manager.update_session_fields(session.session_id, {"cli_session_id":
<id>}, require_tenant=False)` directly for qwen, in its own `try/except` and only
when a blob was actually carried. `tracking_session_id` stays `task_id`; nothing
rotates. (This mirrors the codex persist landed in #3321.)

**Resolve** by adding `qwen-code-cli` to `_RESUME_ID_MAPPED_TOOLS`
(`orchestrator.py`, the shared frozenset #3321 introduced), so turn 2 reads back
the persisted `cli_session_id` as its resume target. Confirmed safe: the branch's
"no mapping yet" arm returns `(existing, None, False)` — a clean fresh start for
turn 1 — and nothing else assumes it is claude-only.

**Import rebuilds the path from the validated id** — never from a stored string.
Turn 2 resolves resume target = Q1, argv `--resume Q1`; import writes the blob to
`.qwen/projects/-workspace/chats/<Q1>.jsonl`, where `<Q1>` is validated by
`_SAFE_SESSION_ID` before the path is built (identical to today's guard) and
`-workspace` is the encoded sandbox project dir (see Encoding). No path is
carried, so the traversal guard is intact.

### On-disk layout and encoding (verified)

* **Layout.** qwen 0.21.5 writes `chats/<sessionId>.jsonl` exclusively (1127
  transcripts on this machine, zero flat). The flat form (`<encoded>/<id>.jsonl`)
  is a legacy layout `fetch_qwen.py` still tolerates. The sandbox image pins the
  qwen version, so the provider addresses a fixed
  `.qwen/projects/-workspace/chats/<id>.jsonl` (analogous to claude's fixed
  `.claude/projects/-workspace/<id>.jsonl`), pinned by a test; if the image's
  qwen ever changed layout, this constant changes with it.
* **Encoding.** The two repo encoders differ in general —
  `encode_project_path_legacy` = `re.sub(r"[/\\:._]","-")`
  (`app/routes/workspace.py`) vs the runner's `_encode_project_path` =
  `re.sub(r"[^A-Za-z0-9]","-")` (`agent_runner.py:1746`) — but both map
  `/workspace` → `-workspace` (only the leading slash is touched), the sandbox's
  fixed cwd. Verified locally that qwen encodes cwd with the same `/`→`-` scheme.
  `-workspace` is pinned by a test and confirmed against a real sandbox run
  during implementation, the same way claude's constant already is.

## Changes

1. `provider.py` — `_agent_state_path(cli_session_id, cli_tool)` becomes
   tool-aware: claude keeps `.claude/projects/-workspace/<id>.jsonl`, qwen gets
   `.qwen/projects/-workspace/chats/<id>.jsonl`. `export_agent_state` /
   `import_agent_state` gain `cli_tool="claude-code"` and pass it through.
   `_SAFE_SESSION_ID` still validates the id before any path is built, so the
   traversal guard is unchanged and no path string is ever stored.
2. `agent_runner.py` —
   * **Capture**: widen `_capture_cli_session_id`'s internal gate to admit qwen
     (that one helper only — not `_uses_sidebar_session_source`), so its existing
     stream-event calls set `session.cli_session_id` for qwen too.
   * **Export/import**: thread `cli_tool` into both provider calls.
   * **Persist**: in the export block (`~:3330`, inside `_run_local`, where
     `cli_tool`/`session`/the captured id are in scope), an explicit
     `session_manager.update_session_fields(session.session_id, {"cli_session_id":
     <id>}, require_tenant=False)` for qwen — **not** via `_ensure_sidebar_session`
     (`:2137`→`:2198`) or the other claude-only sidebar functions. Own
     `try/except` (a DB hiccup degrades to a cold start, never loses the
     milestone) and only when `blob is not None`.
   * **Refusal**: `_plan_agent_state` (`:2715`) no longer refuses qwen; the
     refusal fires only for a tool that is neither claude-code nor qwen-code-cli.
3. `orchestrator.py` — add `qwen-code-cli` to `_RESUME_ID_MAPPED_TOOLS` so
   `_resolve_session_line` maps qwen's tracking id to its captured
   `cli_session_id` (the shared frozenset #3321 introduced).
4. `docs/sandbox-backends.md` — drop the "claude-code only" note; describe the
   capture-persist-resolve-import flow.

Deliberately **not** changed: `_uses_sidebar_session_source` (its 12 call sites
drive sidebar linkage — a separate concern; only `_capture_cli_session_id`'s own
gate widens); and no sidecar file is added.

## Acceptance

1. A two-turn qwen regression covering capture → persist → resolve → import:
   turn 1 captures `sessionId` from the stream into `cli_session_id` and the
   provider writes the blob to `.qwen/projects/-workspace/chats/<id>.jsonl`;
   turn 2 resolves that id and imports the blob to that same path. It must fail
   if **either** the persist/resolve extension **or** the import is removed.
2. Capture: a qwen `system/init` (or any) event's `session_id` lands in
   `session.cli_session_id`; claude's capture timing is unchanged.
3. Claude-code carry is unchanged; the tool-aware path leaves claude's fixed
   `.claude` path and its existing tests untouched.
4. Only the transcript round-trips — no `.qwen` credential/settings file — as an
   invariant asserted at the provider's written-path set (mirroring
   `test_no_credential_file_is_ever_carried`). Noted as an invariant, not as
   proof the feature works.
5. A hostile qwen id is refused by `_SAFE_SESSION_ID` before any import path is
   built.
6. The `_plan_agent_state` refusal no longer fires for qwen-code-cli; a
   synthetic uncarryable tool still triggers it (qwen was the only real tool
   reaching that seam, so the negative case needs a synthetic tool — stated, not
   pretended).

## Verification (2026-09-02)

* qwen 0.21.5: `-r/--resume <ID>` real (live `qwen --help`); adapter emits
  `--resume <session_id>` (`qwen_code.py:87`); on-disk `chats/<sessionId>.jsonl`
  only, `sessionId` both the basename and an in-file field (`fetch_qwen.py:645`
  reads the field, not the basename).
* Claude-only resume id path: persist gate `agent_runner.py:2242`; resolve
  branch `orchestrator.py:7228`/`:7265`; `_extract_stream_session_id` generic
  but Claude-shaped and only called on Claude SDK event types (`:1156`).
* Encoders `app/routes/workspace.py` (legacy) vs `agent_runner.py:1746`
  (runner); both `/workspace`→`-workspace`. `_SAFE_SESSION_ID` at
  `provider.py:187`; hostile-id test `tests/unit/test_opensandbox_agent_state.py`.

## Out of scope

Deployment topology is unchanged: the shared state-root contract from #3237
(`docs/sandbox-backends.md`) applies as-is — this changes what is carried and
how the id is threaded, not where state rests.

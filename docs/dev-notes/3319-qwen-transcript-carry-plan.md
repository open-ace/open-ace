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

## Design: the minted id is the single source of truth

One id flows through capture → persist → resolve → import, so the file the CLI
looks for and the file we restore are named by the *same* id. This replaces the
first draft's sidecar `<line>.meta.json` (which the review showed leaks past
`reap`/`discard`, is written non-atomically with the blob, and — by storing an
absolute path the import trusts — removes the `_SAFE_SESSION_ID` traversal guard
the current code depends on).

**Export discovers the id from the sandbox** (it cannot be known before the turn
— qwen mints it): list `.qwen/projects/*/chats/*.jsonl` in the sandbox, pick the
newest by mtime (the file this turn wrote or appended), read its `sessionId`
field, and return `(blob, discovered_id)`. Reading `sessionId` from the file
content — not the basename — matches how `scripts/fetch_qwen.py:645` identifies
sessions and is robust to any rename. Picking the newest *after* the turn means
a resumed turn carries this turn's transcript, not the stale one imported at its
start (the failure the first draft used to reject an alternative while sharing
it).

**Persist** the discovered id with a **dedicated, explicit write at export
time** — not by touching the claude sidebar machinery. The claude path writes
`cli_session_id` inside `_ensure_sidebar_session` (`agent_runner.py:2137`→`:2198`),
which is gated claude-only, fires *during* streaming (before qwen's id is even
discovered), and falls back to `_find_latest_claude_session_id` — a **host-side**
`.claude` mtime glob that would look on the control plane, not inside the pod.
None of that fits qwen. Instead, the export block (`agent_runner.py:~3328`),
which is where `discovered_id` first exists, calls
`session_manager.update_session_fields(session.session_id, {"cli_session_id":
discovered_id, …}, require_tenant=False)` directly for qwen. `tracking_session_id`
stays `task_id`; nothing rotates.

**Resolve** by extending the `_resolve_session_line` mapping branch
(`orchestrator.py:7228`) from claude-only to also cover qwen-code-cli, so turn 2
reads back the persisted `cli_session_id` as its resume target. (Confirmed safe:
the branch's "mapping lost" arm returns `(existing, None, False)` — a clean fresh
start — and nothing else assumes it is claude-only.)

This is why `_uses_sidebar_session_source` is **not** touched (below): the
sidebar-linkage functions it gates stay claude-only, and qwen's carry-id persist
is a separate, explicit code path that never enters them.

**Import rebuilds the path from the validated id** — never from a stored string.
Turn 2 resolves resume target = Q1, argv `--resume Q1`; import writes the blob to
`.qwen/projects/-workspace/chats/<Q1>.jsonl`, where `<Q1>` is validated by
`_SAFE_SESSION_ID` before the path is built (identical to today's guard) and
`-workspace` is the encoded sandbox project dir (see Encoding). No path is
carried, so the traversal guard is intact.

### On-disk layout and encoding (verified, and why no path is carried)

* **Layout.** qwen 0.21.5 on this machine writes `chats/<sessionId>.jsonl`
  exclusively (1127 transcripts, zero flat). The flat form
  (`<encoded>/<id>.jsonl`) is a legacy layout `fetch_qwen.py` still tolerates
  (probes `<encoded>/chats/*.jsonl` at `:787` and `<encoded>/*.jsonl` at `:782`).
  The sandbox image pins the qwen version, so import targets `chats/` and a test
  pins it; if the image's qwen ever changed layout, this constant changes with
  it.
* **Encoding.** The two repo encoders differ in general —
  `encode_project_path_legacy` = `re.sub(r"[/\\:._]","-")`
  (`app/routes/workspace.py`) vs the runner's `_encode_project_path` =
  `re.sub(r"[^A-Za-z0-9]","-")` (`agent_runner.py:1746`) — but both map
  `/workspace` → `-workspace` (only the leading slash is touched). Export
  **globs** `.qwen/projects/*/chats/` so discovery does not depend on the exact
  encoding; import uses `-workspace`, pinned by a test, and confirmed against a
  real sandbox run during implementation.

## Changes

1. `provider.py` — `export_agent_state` for qwen discovers the newest
   `chats/*.jsonl`, reads its `sessionId`, returns `(blob, discovered_id)`;
   `import_agent_state` writes to `chats/<validated_id>.jsonl` under the encoded
   project dir. `_SAFE_SESSION_ID` still validates the id before any path is
   built. claude-code keeps its fixed `-workspace/.claude` path unchanged.
2. `agent_runner.py` — thread `cli_tool` into the provider export/import; in the
   export block (`~:3328`), where `discovered_id` first exists, persist it to
   `agent_sessions.cli_session_id` via a direct
   `session_manager.update_session_fields(..., require_tenant=False)` for qwen —
   **not** by extending `_ensure_sidebar_session` (`:2137`→`:2198`) or the other
   claude-only sidebar functions (they fire during streaming, before the id
   exists, and fall back to a host-side `.claude` mtime glob); remove the
   `_plan_agent_state` refusal for qwen-code-cli. The persist write goes in its
   own `try/except` (a DB hiccup degrades to a cold start, never loses the
   completed milestone — matching the log-only philosophy at `:3293`) and fires
   only when `blob is not None`, so a no-transcript turn records no id.
3. `orchestrator.py` — extend the `_resolve_session_line` mapping branch
   (`:7228`) to qwen-code-cli so its resume target is the minted
   `cli_session_id`, not the tracking id.
4. `docs/sandbox-backends.md` — drop the "claude-code only" note; describe the
   discover-persist-resolve-import flow.

Deliberately **not** changed: `_uses_sidebar_session_source` (its 12 call sites
drive sidebar linkage — a separate concern — and qwen's carry-id persist is a
distinct explicit write that never enters them); and no sidecar file is added.

## Acceptance

1. A two-turn qwen regression through `_run_agent` → `_resolve_session_line`
   (not the provider in isolation): turn 1's discovered `sessionId` is persisted
   to `cli_session_id` and the blob uploaded to `chats/<id>.jsonl`; turn 2
   resolves that id, its argv carries `--resume <same id>`, **and** the blob is
   imported to that path. It must fail if **either** the persist/resolve
   extension **or** the import is removed — asserting only that `--resume` is in
   argv would pass with the carry deleted (the argv is built independently of
   the import block at `agent_runner.py:2996`), the exact round-1 defect.
2. Export picks the transcript written by *this* turn (newest by mtime), not a
   stale one imported at turn start.
3. Claude-code carry is unchanged; its stored state needs no discovery step and
   its existing tests pass untouched.
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

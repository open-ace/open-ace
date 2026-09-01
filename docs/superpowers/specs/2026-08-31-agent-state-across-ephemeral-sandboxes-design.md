# Carrying CLI session history across ephemeral sandboxes

Follow-up to #2023. The OpenSandbox backend gives every turn its own sandbox, so
the CLI's conversation transcript dies with the pod and `--resume` cannot work.
This spec covers making it work, and failing closed where it cannot.

## 1. The problem

The autonomous workflow keeps three long-lived session lines
(`orchestrator.py` `SESSION_LINE_FIELDS`), each spanning several milestones via
`--resume`:

```
main:         plan_created → plan_refined → plan_finalized → dev_started →
              pr_updated → pr_review_summary
review:       plan_reviewed → pr_reviewed
test:         tests_run (reused across every dev round)
verification: the #2335 acceptance verifier
```

Eight of the thirteen `_run_agent` call sites use a resuming line; five are
`"fresh"`.

Under `OpenSandboxProvider`, `HOME=/home/agent` is an `emptyDir` that dies with
the pod, and the CLI writes its transcript to
`$HOME/.claude/projects/<encoded>/<id>.jsonl`. The resulting chain:

1. **Turn 1** — the CLI mints session `X`. `_capture_cli_session_id`
   (`agent_runner.py`) reads it from the *stream*, not the filesystem, so it is
   persisted correctly. The sandbox is destroyed and `X.jsonl` goes with it.
2. **Turn 2** — `_resolve_session_line` sees `cli_session_id=X` and returns
   `resume=True`. `_run_local` emits `--resume X` into a fresh, empty `HOME`.
3. The CLI answers *"No conversation found with session ID: X"* — on stderr and
   in the stdout `result` event's `errors` array — which
   `_extract_cli_result_error` maps to `resume_session_not_found`.
4. The #2035 recovery in `_run_agent` clears `cli_session_id` and retries once
   with `resume=False`.
5. The fresh run captures session `Y` and persists it. **Turn 3 repeats the
   cycle with `Y`.**

The workflow does not fail. It degrades silently on every turn after the first
on every named line: one wasted agent invocation each, and no conversation
continuity anywhere.

Two secondary breakages share this root cause. `_replay_usage_from_jsonl` and
`_recover_response_text_from_jsonl` both read the *host's*
`~/.claude/projects` (`_claude_projects_root` resolves a host path), so under
the sandbox they silently find nothing — removing the recovery net for
large-context turns whose assistant stream events were dropped. The mtime-based
`cli_session_id` discovery fallback is dead for the same reason, leaving the
stream capture with no backstop.

**Status: reproduced against the real CLI (2.1.170).** Section 8 records the run
and the four things it established.

## 2. Measured sizes

Autonomous transcripts on the development machine: **n=31, median 0.1 MB,
p90 1.1 MB, max 3.2 MB**. Append-only. (Interactive sessions reach 75 MB, but
that is not this workload.) A per-turn transfer is cheap at this size, and the
cap in §5 is set from these numbers rather than guessed.

## 3. Goals and non-goals

**Goals**

- A resuming session line keeps its conversation history under any provider
  whose HOME does not persist between turns.
- A provider that *cannot* carry that history refuses a resuming turn rather
  than degrading silently.
- No credential ever round-trips through the control plane.

**Non-goals**

- Changing the workflow's session topology or moving context into prompts.
- Persisting anything beyond the CLI transcript.
- The two secondary breakages in §1. Same root cause, separate commit; they are
  listed in the plan but must not be bundled into the core change.

## 4. Rejected alternatives

**Upstream snapshots.** `CreateSandboxRequest.snapshotId` looks like the natural
primitive. It is not: `snapshot_restore.py` resolves a snapshot to a *container
image* (`restore_config.image`), so it commits the whole rootfs to a registry
and forces `DEFAULT_SNAPSHOT_RESTORE_ENTRYPOINT = ["tail", "-f", "/dev/null"]`.
The resulting image would be neither digest-pinned nor in `image_allowlist` —
both refused by our own config.

**Keep one sandbox alive per session line.** Workflows idle for hours waiting on
CI and PR review, against a sandbox TTL, holding a pod the whole time. The
workspace must be re-uploaded each turn regardless, because the host tree moves
on between milestones.

**Carry the transcript inside the workspace upload.** It would land under
`/workspace`, where the repo synthesis's `git add -A` would commit it — the
exact reason `HOME` was placed outside `/workspace` in the first place.

**A persistent volume as HOME.** `validate_spec_for_endpoint` refuses
host-backed volumes and the config has no per-line volume lifecycle. Revisit
only if transcript sizes ever outgrow a per-turn transfer.

## 5. Design

### 5.1 State persistence is a declared property, not an inferred one

The question is not "does this provider implement an export method" but "does
agent state survive between turns". Three states:

| State | Providers | Behaviour |
| --- | --- | --- |
| `persists` | Legacy (non-isolated), Remote | Nothing to do; HOME is already durable. |
| `carried` | OpenSandbox | Export before destroy, import after create. |
| `ephemeral` | anything else | A resuming turn is **refused up front**. |

A provider that declares nothing is treated as `ephemeral`. This mirrors #2023's
rule that an absent attestation removes a capability rather than granting one,
and it is why the check cannot be `hasattr(provider, "export_agent_state")` —
`RemoteMachineProvider` has a durable HOME and needs no seam.

`SandboxCapability` is the frozen #2022 enum and must not be extended, so this
is a provider attribute read defensively by the runner, in the same duck-typed
style as `agent_turn_policy` and `apply_changes`.

### 5.2 The seam

Two methods, implemented only by providers declaring `carried`:

```python
def export_agent_state(self, handle, *, cli_session_id: str) -> bytes | None
def import_agent_state(self, handle, *, cli_session_id: str, blob: bytes) -> None
```

`OpenSandboxProvider` moves exactly one file:
`/home/agent/.claude/projects/-workspace/<cli_session_id>.jsonl`.

The encoded directory is the constant `-workspace`: the CLI always runs with
`cwd=/workspace` inside the sandbox (`_exec_command` passes `cwd=_WORKSPACE`),
and `_encode_project_path`'s rule — `re.sub(r"[^A-Za-z0-9]", "-", realpath)` —
maps `/workspace` to `-workspace`. No host-path coupling, and the value is
asserted in a test rather than assumed.

**Only the transcript moves.** Not `.claude.json`, not `.credentials.json`, not
settings. Credentials must never round-trip through the control plane, and
#2023's rule is that the sandbox environment is constructed, never inherited —
`build_env` already mints the proxy token fresh each turn.

A blob larger than `_MAX_AGENT_STATE_BYTES` (16 MB — an order of magnitude over
the measured max, matching the shape of `ChangesetLimits`) is refused rather
than transferred.

### 5.3 The store

One class, one job: hold one transcript per session line.

```
<state_root>/<workflow_id>/<tracking_session_id>.jsonl   # 0700, control-plane owned
```

`state_root` follows the existing per-task convention —
`task_isolation.DEFAULT_TASK_ROOT` is `/run/openace-agent-tasks`, and
`.claude-preserve` already lives there as a sibling of each task tree — with an
environment override for deployments that want it elsewhere. Note that `/run` is
tmpfs on Linux: transcripts do not survive a reboot. That is the same bargain
`.claude-preserve` already makes, and §5.5 handles the consequence.

Operations: `put`, `get`, `discard`, `purge(workflow_id)`.

Keyed by the line's **tracking** session id, never by `cli_session_id`. The two
are different things and the distinction is the whole point: `cli_session_id` is
the provider's transcript id and changes on every force-fresh, while the tracking
id is the stable per-line identity `SESSION_LINE_FIELDS` stores on the workflow
row and `_resolve_session_line` returns. It survives force-fresh, and
`run_agent_task` already receives it as `session_id` — so this keying needs no
signature change. A `"fresh"` line gets a new uuid per call and therefore
correctly carries nothing.

Retention: purged when the workflow reaches a terminal state, plus an age-based
reaper bounding orphans, mirroring the `.claude-preserve` sibling reaper that
`openace-run-as.sh` already runs for exactly this failure mode.

### 5.4 Ordering in `_run_local`

`_run_local` currently builds `cmd` *before* `provider.create`, so `--resume`
would be baked into argv before we could know whether the transcript landed. The
order becomes:

```
create → upload_workspace → import_agent_state → build cmd → exec
```

`adapter.build_start_args` moves below the create block, and `resume` is passed
as whatever the import actually achieved. This is a real edit to a hot path and
belongs in its own commit.

Export runs after the turn completes and before `destroy`, keyed by the
`cli_session_id` captured from the stream during the run.

### 5.5 Failure semantics — mirroring `openace-run-as.sh` point for point

The launcher does not apply one policy. It fails closed exactly where failing is
free and continuing would corrupt, and logs everywhere the work is already done:

| Point | Legacy | This design |
| --- | --- | --- |
| Ensure a clean capture target, before the agent runs (`:538`, `exit 70`) | **Fail closed.** A failed `rm` would let `mv` nest `.claude` inside a survivor — "a mis-shaped tree, silently breaking `--resume`". Nothing has run, so aborting is free. | **Fail closed.** Before `create`, two cases refuse the turn with `agent_state_unavailable`: a resuming line on an `ephemeral` provider, and a store slot that is *present but unreadable or malformed*. No sandbox is created and no tokens are spent. |
| Capture after the agent ran (`:435`, `\|\| log_audit`) | **Log only** — "an exit here would rewrite the status." | **Log only.** An export failure emits an audit event and clears `cli_session_id` so the next turn starts fresh cleanly. A completed milestone's output is never discarded because its transcript could not be saved. |
| Restore (`:548`, `\|\| true`) | **Best effort.** | **Best effort.** An import failure drops `--resume` for this turn and records why. The fallback is correct, merely worse. |

**A store slot that is simply ABSENT is not a failure.** It is the state after a
reboot cleared tmpfs, or on a line's very first turn. Legacy's analogue is the
`if [ -d "$preserve_claude_dir" ]` guard around its restore: no preserve dir
means the restore is skipped and the CLI starts fresh. So absent maps to row
three — drop `--resume`, record why, continue. Only a slot we can see but cannot
trust is fail-closed, because there we cannot distinguish "no history" from
"broken history", which is exactly the mis-shaped-tree hazard `exit 70` exists
for.

The one genuinely new refusal is the first row's `ephemeral`-provider clause.
That is what converts today's guaranteed wasted invocation into an up-front
refusal, and it is free by construction.

`agent_state_unavailable` joins the reason-code table in
`docs/sandbox-backends.md` §6.

## 6. Testing

- **Store**: round-trip, cap refusal, `purge` on terminal state, reaper bounds.
- **Provider**: export/import against `FakeOpenSandboxApi` at the wire level —
  the fake already records uploads in `self.uploaded`, so the assertion is on the
  real path and filename, not on a shortcut attribute.
- **Encoded path**: assert `-workspace` is what `_encode_project_path` produces
  for the sandbox's cwd, so the two cannot drift.
- **Ordering**: `_run_local` performs create → upload → import → build cmd →
  exec, and `--resume` appears in argv **iff** the import succeeded. Mutation-test
  both directions; a test that passes with the import removed is not a test.
- **Failure semantics**: one test per row of the §5.5 table, each asserting the
  *distinct* behaviour (refuse / log-and-continue / drop-resume). These three
  differing on purpose is the whole design.
- **Regression**: the §1 chain end to end — turn 1 captures an id, turn 2 resumes
  successfully with the carried transcript. This is the test that fails today.
- **Credential exclusion**: an export never carries `.claude.json` or
  `.credentials.json`, asserted on the file list rather than on intent.

## 7. Scope boundary

The two secondary breakages in §1 would read the exported blob instead of the
host path. Same plan, separate commit, explicitly not bundled.

## 8. Observed behaviour

The chain in §1 was reproduced against the real CLI (2.1.170) before any code
was written, using an isolated `HOME` in place of the sandbox's ephemeral one —
an empty `HOME` is the entire mechanism, so the substitution is faithful for
this question. Four things it established:

**1. The transcript path and the encoding rule are exactly as assumed.** Turn 1
wrote `$HOME/.claude/projects/<encoded>/<session_id>.jsonl`, and
`_encode_project_path` predicted `<encoded>` byte-for-byte, including the
doubled `-` that `/.claude` produces. `/workspace` encodes to `-workspace`,
confirming §5.2's constant.

**2. The failure is real and its message is exact:**

```
No conversation found with session ID: c53d4b8d-872a-495d-8f1f-e72ab563999a
```

Note the colon after `ID`, absent from the substring `_extract_cli_result_error`
matches on. The match is a substring test, so it still fires — but any future
tightening of that predicate would break silently.

**3. It degrades rather than hard-failing, and only because of where the message
travels.** The text appears both on stderr *and* in the stdout `result` event's
`errors` array, and the classifier reads `errors` **before** `stderr_hint`. Feeding
the captured payload through the real `_extract_cli_result_error` returns
`resume_session_not_found`, so the #2035 recovery does fire under the sandbox
transport. Had the message been stderr-only, the sandbox path would have
classified it `unknown_cli_error`, the recovery would never have run, and every
resuming turn would have *failed the workflow* rather than degrading it. Worth
stating because it is load-bearing and undocumented.

**4. Carrying one file is sufficient — the remedy is validated, not just the
defect.** Restoring only `<encoded>/<id>.jsonl` into an otherwise brand-new
`HOME` — no `.claude.json`, no credentials, no settings — let `--resume` resolve:
the stream went `system/init → assistant → result` and **kept the original
session id** instead of minting a new one. This is the evidence behind §5.2's
"only the transcript moves" rule.

A trivial single-turn transcript was 8,748 bytes, consistent with §2's measured
distribution.

**Still unobserved:** the same two turns executed inside a real sandbox pod,
which additionally exercises the download/upload path through execd. The
implementation plan keeps that as its final verification step, on the same
reasoning that made this section necessary — every real run on #2023 found
something reading had missed.

## 9. Open risks

- **Transcript growth on the `main` line.** It spans six milestones and is
  append-only. The measured p90 is 1.1 MB, but a long CI-repair loop is untested
  at this size. The cap in §5.2 turns growth into a refusal rather than a hang,
  and the export path should record the size so the real distribution becomes
  observable.
- **Concurrency.** Two turns on the same line never overlap today: a workflow
  advances one milestone at a time, and `advance()` is what dispatches each
  agent run. So the store needs no locking. If a workflow ever runs two lines
  concurrently they are still different keys — but if the same line is ever
  parallelised, the store is where that invariant belongs.
- **Reboot loses history mid-workflow.** `/run` is tmpfs, so a control-plane
  restart empties the store. Per §5.5 that is an *absent* slot, not a corrupt
  one: the next turn drops `--resume` and continues on a fresh session rather
  than failing the workflow. History is lost, the run is not. A deployment that
  wants transcripts to survive reboots should point the override at persistent
  storage.
- **Control-plane locality.** The store is on one host. The scheduler is a single
  systemd unit, so this holds today; a multi-instance control plane would need
  §4's rejected DB option revisited.

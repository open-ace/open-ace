# Acceptance Verification — PR1 Vertical Slice (#2335)

> **For agentic workers:** This spec is implemented via `superpowers:writing-plans` → `superpowers:executing-plans` / `subagent-driven-development`. It covers **PR1 only** of a multi-PR program (see "Program decomposition").

**Goal:** Stop autonomous workflows from auto-closing issues on PR merge, and require an independent verifier to confirm `@open-ace-bot` against the merged code before the issue may close.

**Architecture:** Add an `acceptance_verification` phase after `merge`. Capture an acceptance snapshot (scope + checklist) from the issue body. Spawn an independent read-only verifier agent on the merged main SHA that runs a deterministic **scope gate** plus a holistic read, emitting structured per-item verdicts. Aggregate to an issue verdict; `confirmed`→explicit close + report, `rejected`→new dev round, `indeterminate`→pause.

**Tech stack:** Python (Flask), PostgreSQL/SQLite (`adapt_sql`), Alembic migration, the existing `PhaseResult`/`PHASE_HANDLERS` phase machine, the `evidence.py` `Verdict` enum, the session-line agent runner pattern.

---

## Background

#2335 (P0) was filed after the #2179–#2190 review: issues explicitly marked "禁止阶段性关闭" were nonetheless auto-closed because autonomous PRs append `Closes #N` and GitHub closes the issue on merge. Today the workflow **never calls `close_issue`** — it relies entirely on GitHub's `Closes #N` keyword (`phases/pr_review.py:296`). There is no verification that the issue's scope/acceptance was actually met on the merged code.

The full #2335 is large. It is decomposed into six complete, individually-shippable sub-projects:

| # | Sub-project | Status |
|---|---|---|
| **S1** | Close-keyword enforcement + explicit confirmed-close + `verification_status` + reopen guard | **PR1 (this spec)** |
| **S2** | Issue acceptance snapshot: parse scope/checklist/non-scope/closure-constraints (convention → LLM fallback) | **PR1 (this spec)** |
| **S3** | Independent verifier agent on merged main SHA; structured per-item verdicts + aggregation | **PR1 (this spec)** |
| **S4** | The other 5 mechanical gates (negative-test / legacy-pattern / call-chain / deployment / regression) | later PR |
| **S5** | Persistence polish: full restart recovery, human-override UI/audit, LLM-extraction maturity | later PR |
| **S6** | Full state-machine polish + E2E widening | later PR |

**PR1 includes exactly one mechanical gate (scope).** The verifier also does a holistic read for items the gate cannot cover, so non-scope checklist items still get a verdict (often `indeterminate`, which is safe — it pauses rather than closes).

## Non-goals (PR1)

- The 5 other gates (negative-test, legacy-pattern, call-chain, deployment, regression).
- Full restart-recovery polish and the human-override UI/audit (a manual DB override path is out of scope; `indeterminate` pauses for a human who acts out of band).
- PR *creation* identity (`gh pr create` still attributes to the owner — tracked in #2340).
- Multi-tenant verifier isolation hardening beyond reusing the existing `openace-run-as --isolated` path.

## Confirmed decisions (from design review)

- **Parsing:** convention parse first (`## Scope` / `## 验收标准`|`## Acceptance Criteria` / `## 不在 Scope` / closure-constraint phrases); if required sections absent, the verifier LLM-extracts them as its first step, persisted with `source=llm, confidence=low`.
- **Close behavior:** `confirmed` → workflow auto-closes via explicit `close_issue()` + posts acceptance report (per spec). Verifier is conservative (defaults to `indeterminate` when not clearly confirmed).
- **§D verifier tools:** read-only set (`REVIEW_ALLOWED_TOOLS`) **+ `Bash`** for the scope gate's `git diff`/`git log` on merged main. **No Write/Edit on the verification target.** Runs via `openace-run-as --isolated` (credentialless).
- **§E scope gate base:** diff `<base_commit_sha>..<verification_merge_sha>` on main.
- **§H `rejected`:** start a new development round (carrying the rejection report as feedback); transition to `failed` only if the dev-round cap is hit.

---

## A. State machine

New phase `acceptance_verification` is appended to `PHASE_ORDER` (`orchestrator.py:577`) after `merge`:

```
development → pr_review → report → merge
                                   ↓ (PR merged; next_phase = acceptance_verification)
                          acceptance_verification   (status = verification_pending)
                                   ├─ confirmed      → close issue + report → completed (terminal)
                                   ├─ rejected       → development (dev_round + 1) | failed (cap hit)
                                   └─ indeterminate  → paused (human acts out of band)
```

- `merge`'s `PhaseResult.completed(next_phase="completed")` (`phases/merge.py:411`) becomes `next_phase="acceptance_verification"`.
- `_COMPLETED_TERMINAL_PHASES` (`orchestrator.py:586`) is updated so only `acceptance_verification` (on `confirmed`) and `completed` are terminal.
- `PHASE_STATUS_MAP` (`orchestrator.py:589`) gains `acceptance_verification → verification_pending`.
- A new handler is registered in `PHASE_HANDLERS` (`phases/__init__.py`) following the migrated-phase contract (`PhaseResult`).

**Backward compatibility:** existing `completed` workflows are untouched. `verification_status` is nullable; `NULL`/absent means "not yet verified" (pre-feature). Only workflows that reach `merge` after this change enter `acceptance_verification`.

## B. Close-keyword enforcement (S1)

1. **Stop appending `Closes #N`** to autonomous PR bodies (`phases/pr_review.py:296`). Replace with `Implements #N` (non-closing reference). This single change eliminates the premature-close pattern.
2. **Add `GitHubOps.close_issue(number)`** (`gh issue close <num>`, repo-scoped, `api_only=True` so it runs as the service user with `GH_TOKEN` — posts/acts as `@open-ace-bot`, consistent with #2341). Called **only** on `confirmed`, after the acceptance report is posted.
3. **Reopen guard:** at `acceptance_verification` entry, if the issue is closed but `verification_status != confirmed` (e.g. an external/manual PR slipped a `Closes #N` through), reopen it (`gh issue reopen`) and emit an audit `workflow_events` row. The workflow then proceeds to verify.

## C. Issue acceptance snapshot (S2) — hybrid parse

New module `app/modules/workspace/autonomous/acceptance_snapshot.py`:

- `parse_acceptance_snapshot(issue_body: str) -> AcceptanceSnapshot` — deterministically extracts from markdown sections:
  - `required_paths`: path globs from `## Scope` (lines that look like file paths / `- \`path\`` / backticked globs).
  - `checklist`: `- [ ]` / `- [x]` items from `## 验收标准` / `## Acceptance Criteria`.
  - `non_scope`: from `## 不在 Scope` / `## Non-Scope`.
  - `closure_constraints`: booleans for phrases like "禁止阶段性关闭" / "do not close until".
  - `source`: `"convention"` if the required sections were present, else `"missing"`.
  - `confidence`: `"high"` for convention, unset for missing.
- When `source == "missing"`, the verifier agent (§D) performs LLM extraction as its first step and the snapshot is re-persisted with `source="llm", confidence="low"` (the canonicalization + hash reflect the final extracted form).
- **Hash:** `issue_acceptance_hash = sha256(canonical_json(snapshot))`. Captured at **preparation** time (so a mid-implementation issue edit changes the hash and forces re-verification).
- Persisted to `autonomous_workflows.issue_acceptance_snapshot` (jsonb) + `issue_acceptance_hash`.

## D. Independent verifier agent (S3)

- New session line `verification`: `SESSION_LINE_FIELDS` (`orchestrator.py:801`) += `"verification" → "verification_session_id"`; the column is added to `ALLOWED_WORKFLOW_FIELDS` (`autonomous_repo.py:28`).
- Spawned by the `acceptance_verification` handler via the existing agent runner (`AutonomousAgentRunner.run_agent_task`, `agent_runner.py:1935`) with:
  - `session_line="verification"`, `allowed_tools = REVIEW_ALLOWED_TOOLS ∪ {"Bash"}` (constants.py:38), **no Write/Edit**.
  - A checkout of **main at `verification_merge_sha`** in an isolated worktree (fresh, not the dev worktree), via `openace-run-as --isolated` (credentialless).
  - The acceptance snapshot + the issue body as input; instructed to be conservative (default `indeterminate`).
- Output: structured per-item verdicts (see §F). If the snapshot was `source=missing`, the agent first extracts scope/checklist and the orchestrator persists the completed snapshot before continuing.

## E. Scope gate (the one mechanical gate in PR1)

Deterministic, in-process (orchestrator-side, not LLM):

- `changed_paths = gh.get_changed_files(base=base_commit_sha, head=verification_merge_sha)` (reuse `GitHubOps.get_changed_files`).
- For each `required_path` glob in the snapshot: `confirmed` if some changed path matches, else `rejected` with the missing path as evidence.
- The gate's per-path verdicts feed the issue-level aggregation (§F). A `rejected` scope item → issue `rejected`.

## F. Verdict model + aggregation

Reuse `evidence.py`'s `Verdict` enum (`CONFIRMED` / `REJECTED` / `UNKNOWN`). The verifier emits, per acceptance item:

```json
{"item": "<checklist text or required path>", "verdict": "CONFIRMED|REJECTED|UNKNOWN",
 "evidence": [{"ref": "file:line|git-diff|test", "note": "..."}], "rationale": "..."}
```

**Issue-level rule** (`aggregate_verdicts`):
- any required item `REJECTED` → **rejected**;
- else any required item `UNKNOWN`/indeterminate → **indeterminate**;
- else all `CONFIRMED` → **confirmed**.

The scope gate (§E) produces per-path verdicts that are merged into the item list before aggregation.

## G. Persistence + minimal idempotency

New `autonomous_workflows` columns (one Alembic migration, idempotent per prod conventions — `if column not in existing_columns` guard):

| column | type | notes |
|---|---|---|
| `verification_status` | text | `pending`/`confirmed`/`rejected`/`indeterminate`; nullable |
| `verification_merge_sha` | text | the main SHA the verifier ran against |
| `verification_started_at` / `verification_completed_at` | timestamp | |
| `verification_attempt` | int | |
| `verification_report` | jsonb | per-item verdicts + issue verdict + rationale |
| `issue_acceptance_snapshot` | jsonb | parsed/extracted snapshot |
| `issue_acceptance_hash` | text | sha256(canonical) |
| `verified_by` | text | agent identity/version |
| `verification_session_id` | text | session-line binding |
| `issue_closed_by_workflow_at` | timestamp | set when the workflow closes the issue |

- **Idempotency key:** `(verification_merge_sha, issue_acceptance_hash)`. Re-entering `acceptance_verification` with the same pair is a no-op (no re-verify, no re-close, no re-comment). A new merge SHA or an edited issue (new hash) re-verifies.
- A `workflow_milestones` row records each verification attempt (reuse `create_milestone_idempotent`, `orchestrator.py:3331`) with `phase="acceptance_verification"` and the report in `metadata`.

## H. Transitions on verdict

- **confirmed** → post acceptance report comment (merge SHA, scope evidence, per-item verdicts, verifier identity) → `gh.close_issue(issue_number)` → set `issue_closed_by_workflow_at` → `status=completed` (terminal).
- **rejected** → if `dev_round < cap`, set phase back to `development`, `dev_round + 1`, attach the rejection report as round feedback; else `status=failed`. Issue remains open.
- **indeterminate** → `status=paused` (issue open; human provides missing evidence out of band).

## I. Testing

- **Unit** (`tests/issues/2335/`):
  - Snapshot parser: convention present → `source=convention`; absent → `source=missing`; hash stability (canonical form); closure-constraint phrase detection.
  - Scope gate: required path present/missing → per-path verdicts; glob matching.
  - `aggregate_verdicts`: all combinations (any REJECTED→rejected; any UNKNOWN→indeterminate; all CONFIRMED→confirmed).
  - Close-keyword: PR body builder emits `Implements #N`, never `Closes #N`.
  - `close_issue` called only on `confirmed` (not rejected/indeterminate); runs via `api_only` (service-user/bot identity).
  - Reopen guard: closed issue + non-confirmed → reopened + audit event.
  - Idempotency: re-entry with same `(merge_sha, hash)` → no-op.
  - Phase handler: `acceptance_verification` returns the right `PhaseResult` for each verdict.
- **Phase machine invariant** (extend `tests/autonomous/test_phase_b_acceptance.py` style): `merge` now targets `acceptance_verification`; only confirmed reaches `completed`.
- **E2E** (headless, `tests/e2e/` per the frontend-E2E rule — this is a backend flow but the E2E harness seeds workflows): seed workflow → merge → verifier rejects (snapshot required path missing from diff) → issue stays open + new dev round → fix (add the path) → reverify confirmed → issue closed by `@open-ace-bot` with report.

## J. File map

**Create:**
- `app/modules/workspace/autonomous/acceptance_snapshot.py` — parser + `AcceptanceSnapshot` + canonicalization/hash.
- `app/modules/workspace/autonomous/phases/acceptance_verification.py` — the phase handler (spawn verifier, run scope gate, aggregate, transition).
- `migrations/versions/2026080X_XXX_acceptance_verification_columns.py` — the column migration (idempotent).
- `tests/issues/2335/test_acceptance_snapshot.py`, `test_scope_gate.py`, `test_verdict_aggregation.py`, `test_close_keyword_enforcement.py`, `test_acceptance_phase.py`.

**Modify:**
- `app/modules/workspace/autonomous/orchestrator.py` — `PHASE_ORDER`, `PHASE_STATUS_MAP`, `_COMPLETED_TERMINAL_PHASES`, `SESSION_LINE_FIELDS`; verdict-transition wiring.
- `app/modules/workspace/autonomous/phases/__init__.py` — register the handler.
- `app/modules/workspace/autonomous/phases/merge.py` — `next_phase="acceptance_verification"`.
- `app/modules/workspace/autonomous/phases/pr_review.py:296` — `Closes #N` → `Implements #N`.
- `app/modules/workspace/autonomous/github_ops.py` — add `close_issue(number)` (api_only).
- `app/modules/workspace/autonomous/constants.py` — `VERIFICATION_ALLOWED_TOOLS` (= `REVIEW_ALLOWED_TOOLS ∪ {"Bash"}`).
- `app/repositories/autonomous_repo.py` — new columns in `ALLOWED_WORKFLOW_FIELDS`; SELECT/INSERT/UPDATE handling (repo already uses SELECT *).
- `schema/schema-postgres.sql` + `schema/schema-sqlite.sql` — regenerated via `scripts/rebuild_schema_snapshots.py` (PG16 client).
- `tests/autonomous/test_phase_b_acceptance.py` (or sibling) — phase-order/terminal invariant update.

## Acceptance criteria (PR1)

- [ ] Autonomous PR bodies no longer contain `Closes #N` (use `Implements #N`); merge does not auto-close the issue.
- [ ] After merge, the workflow enters `acceptance_verification`; the issue stays open until `confirmed`.
- [ ] An issue closed externally (e.g. stray `Closes #N`) with `verification_status != confirmed` is reopened by the workflow.
- [ ] The verifier runs on the merged main SHA (`verification_merge_sha`), with read-only + `Bash` tools, no Write/Edit.
- [ ] The scope gate rejects when a snapshot `required_path` is absent from `base..merge` diff, with that path as evidence.
- [ ] `aggregate_verdicts` maps any-rejected→rejected, any-unknown→indeterminate, all-confirmed→confirmed.
- [ ] `confirmed` → `close_issue()` posts/acts as `@open-ace-bot`, posts the acceptance report, sets `issue_closed_by_workflow_at`, reaches `completed`.
- [ ] `rejected` → new dev round (or `failed` at cap); issue stays open.
- [ ] `indeterminate` → `paused`; issue stays open.
- [ ] Re-entering the phase with the same `(merge_sha, issue_acceptance_hash)` is a no-op.
- [ ] A mid-flight issue edit changes `issue_acceptance_hash` and triggers re-verification.
- [ ] `issue_acceptance_snapshot` is captured at preparation; convention-parse first, LLM-extract fallback flagged `source=llm, confidence=low`.
- [ ] Migration is idempotent and schema snapshots regenerate byte-exact.

## Risks / mitigations

- **Wrong `confirmed` auto-closes a not-really-done issue.** Mitigation: conservative verifier (default `indeterminate`); the scope gate is a hard mechanical check; only all-confirmed closes. The issue can be reopened manually.
- **Stale manual root `scheduler_worker`** (see `prod-scheduler-port-9090-mihomo-conflict` memory) could run old code. Mitigation: deploy step restarts `openace-scheduler.service`; the deploy runbook (updated memory) covers this.
- **`Bash` in the verifier toolset** could mutate the isolated worktree. Mitigation: the worktree is a throwaway checkout of merged main; the verifier must not write the acceptance target and has no creds to push. Acceptance is read from git state, not the working tree.
- **LLM-extracted snapshots are non-deterministic.** Mitigation: convention parse preferred; LLM snapshots flagged low-confidence and tend to yield `indeterminate` (pause), not false `confirmed`.

Refs #2335. Follow-ups: #2340 (PR-create identity), and S4–S6 (remaining gates, persistence polish, E2E widening).

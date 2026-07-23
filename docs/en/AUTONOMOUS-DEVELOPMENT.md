# AI Autonomous Development

This document is for users, operators, and maintainers of Open ACE AI Autonomous Development. It describes the current feature boundary, workflow lifecycle, three-session topology, CI repair, isolated execution, usage accounting, and frontend observability.

> This document describes the implementation in this repository. Changes to autonomous development must update this document, its Chinese counterpart, and the relevant regression tests.

## 1. Overview

AI Autonomous Development turns a requirement or GitHub Issue into an auditable software delivery workflow:

1. Prepare an isolated branch and worktree.
2. Generate a plan and have it reviewed independently.
3. Refine the plan into an approved final plan.
4. Implement the change and run targeted tests.
5. Create or update a pull request.
6. Review the code independently, apply feedback, and re-review it.
7. Produce a final report.
8. Wait for GitHub CI and repair failures when possible.
9. Synchronize the base branch or resolve conflicts, merge the PR, and clean up the branch and worktree.

The workflow is not one opaque Agent script. Important decisions, AI sessions, code changes, tests, reviews, retries, and failure reasons are stored as recoverable milestones.

### 1.1 Intended use

Good fits include:

- GitHub Issues with clear boundaries and testable acceptance criteria;
- Small and medium changes that need plan review, code review, and a CI feedback loop;
- Serial batches of multiple issues;
- Controlled automation where token usage, request counts, sessions, and diffs must remain visible.

It is not intended for:

- Work that requires production credentials or arbitrary administrator access;
- Tasks whose completion cannot be assessed by repository tests, CI, or explicit acceptance criteria;
- Letting an Agent mutate protected Git metadata, bypass branch protection, or merge unchecked code.

## 2. User operations

### 2.1 Creating a workflow

In the AI Autonomous Development page, select a project, CLI tool, model, and requirement source. The requirement can be text or a GitHub Issue URL/number.

Creation stores a `definition_snapshot` containing the requirement, tool, model, branch strategy, and batch metadata. Later configuration changes must not silently rewrite the definition of an existing run.

Branch strategies include an isolated worktree, a new branch, and the current branch. Batch creation forces isolated worktrees and locks one `origin/main` base SHA for the batch, so later items do not accidentally inherit an earlier item's unmerged intermediate state.

### 2.2 Pause, resume, and stop

- **Pause** freezes the running Agent process and retains workflow state. A manual pause is never auto-resumed.
- **Resume** continues the frozen process or returns the persisted phase to scheduling.
- **Stop** terminates the Agent and marks the workflow `cancelled`. Later queued items in the same batch are cancelled as well.
- **Retry after failure** is available only for `failed` or `planning_timeout` and resumes from the persisted current phase.

Pause and stop are different controls. New states and error paths must not make either action disappear while a workflow is active.

### 2.3 Milestone operations

- **View definition/plan/review/report** opens persisted content.
- **View code changes** shows a milestone diff or the complete PR diff and statistics.
- **View session** opens the milestone's stable session line.
- **Cancel round** cancels later milestones and enters `wait` for feedback.
- **Fork here** copies history through the selected milestone into a separate workflow and worktree.
- **Continue with feedback** records feedback as a milestone and returns to the appropriate phase.

## 3. Domain model

### 3.1 Workflow

`autonomous_workflows` stores workflow-level state:

- Current phase, status, development round, and error;
- Project, branch, worktree, PR, and batch metadata;
- `main_session_id`, `review_session_id`, and `test_session_id`;
- Aggregated token, input/output token, and request usage;
- CI repair attempts, failure fingerprint, and diagnostics state;
- Pause, timeout, feedback, and recovery context.

### 3.2 Milestone

`workflow_milestones` is the timeline's audit unit. Each row describes one explicit event such as plan generation, plan review, implementation, testing, PR review, CI diagnostics, or conflict resolution.

A milestone stores its own `phase_*` usage delta plus its session, content, commits, diff statistics, and error. It is not a copy of the workflow's cumulative usage.

### 3.3 Agent session

`agent_sessions` stores Open ACE's stable session identity and the actual provider/CLI session ID. Recovery may replace the underlying provider transcript, but it must not replace the workflow's stable session-line identity.

## 4. Lifecycle and state machine

The normal phase order is:

```text
preparation → planning → development → pr_review → report → merge
```

`wait` is a user-feedback phase outside the linear `PHASE_ORDER`. Cancelling a round or supplying feedback can enter it before returning to the relevant business phase.

Primary statuses:

| Status | Meaning |
|--------|---------|
| `queued` | Waiting behind an earlier batch item |
| `pending` | Ready for the scheduler |
| `preparing` | Preparing repository, branch, and worktree |
| `planning` | Generating, reviewing, and finalizing the plan |
| `developing` | Implementing and testing |
| `pr_review` | Reviewing, fixing, and re-reviewing the PR |
| `reporting` | Producing the final report |
| `waiting` | Waiting for user feedback |
| `merging` | Checking CI, repairing, synchronizing, and merging |
| `paused` | Manual, application-quota, or hard upstream-quota pause |
| `planning_timeout` | Planning timed out and awaits extension or retry |
| `completed` | PR merged and cleanup completed |
| `failed` | Automatic recovery was exhausted |
| `cancelled` | User stop or batch cancellation |

Persisted state is the recovery authority. A server restart must not depend solely on an in-memory Agent, lock, or SSE connection to decide the next step.

## 5. Strict three-session topology

Each workflow owns exactly three stable session lines:

| Line | Persisted field | Milestones |
|------|-----------------|------------|
| `main` | `main_session_id` | Plan generation/refinement, development, PR fixes, final summary, and CI repair |
| `review` | `review_session_id` | Plan review and PR code review |
| `test` | `test_session_id` | Testing and verification across development rounds |

The lines resume across milestones so that:

- The implementation Agent retains continuity across requirements, plan, and code changes.
- The review Agent remains independent from the implementer.
- The test Agent designs a separate verification matrix instead of accepting the implementer's claims.
- UI, usage accounting, and troubleshooting have stable identities.

### 5.1 Context overflow

When a provider transcript exceeds the model context limit, Open ACE:

1. Detects an input/context overflow;
2. Clears the old provider-transcript binding for that stable line;
3. Retries with a self-contained minimal prompt;
4. Rebinds the new provider session to the same Open ACE session row;
5. Preserves usage from failed attempts.

Recovery therefore does not create a fourth session line. `main / review / test` must remain the only stable workflow topology.

## 6. Scheduling, concurrency, and batches

`AutonomousScheduler` scans active workflows and advances up to three workflows concurrently. This is a fixed module-level limit (`MAX_CONCURRENT_WORKFLOWS = 3` in `app/services/autonomous_scheduler.py`), not an operator-tunable setting; changing it requires a code change.

It enforces three levels of exclusion:

- A **database lock** prevents multiple service instances from advancing one workflow.
- A **workspace key** prevents concurrent mutation of the same checkout.
- A **branch key** prevents one branch from being attached to multiple worktrees.

`waiting` counts toward a user's active workflow limit but is not an Agent-running batch status. A batch advances one workflow at a time. A prior `paused` or `cancelled` item blocks the queue; completed, failed, or otherwise eligible terminal/waiting states allow the next item to be considered.

On shutdown, the scheduler asks active orchestrators to drain. On startup, orphaned Agent processes are cleaned up and uncertain persisted state is made inspectable instead of being advanced twice.

## 7. Git, PRs, and change scope

### 7.1 Worktree-first isolation

An isolated worktree is the preferred strategy. `preferred_worktree_path` remains stable. If conflict handling temporarily removes the original worktree, CI repair must restore the same PR branch worktree before consuming a repair attempt.

### 7.2 Agents do not own protected Git metadata

The Agent edits worktree files. Controlled `GitHubOps` code creates branches, commits, pushes, PRs, base synchronization, conflict commits, and merges. Prompt and command filters are defense in depth; operating-system permissions are the final boundary.

### 7.3 Effective change scope

Validation cannot compare only the local `HEAD` before and after an Agent call. A worktree may already contain:

- An unpushed commit from an earlier transient push failure;
- A temporary conflict-resolution commit;
- Files left by an interrupted round;
- A base-branch synchronization commit.

The implementation uses the remote PR head and effective pre-merge PR diff as its baseline:

- Existing valid PR changes are preserved.
- Only new changes within the requested scope are accepted.
- Scope expansion and excessive changed-file counts are rejected.
- Synchronizing `main` does not consume an AI repair attempt.
- Running commands without producing a necessary change is not a successful repair.
- A valid local commit that still needs pushing is validated and pushed rather than mislabeled as “no code changes.”

## 8. Development, testing, and independent review

The `main` line implements the final plan. The `test` line then designs a targeted verification matrix using both the plan and actual diff and runs available repository checks.

The `review` line performs PR review. Its verdict is structured rather than inferred only from response length. Substantive findings return to `main` for fixes and then to `review` for another round. The final summary must describe what was actually implemented and any residual risk.

The target repository's declared runtime takes precedence over the Open ACE service runtime. A Python 3.9 service process, for example, does not authorize rewriting a Python 3.11 repository for Python 3.9 compatibility.

## 9. Merge phase and automatic CI repair

### 9.1 Processing order

The merge phase:

1. Reads PR and check status.
2. Synchronizes the PR branch with `main` first when it is behind, then waits for fresh CI.
3. Collects complete actionable logs for failed checks.
4. Builds local reproduction instructions and a repository runtime contract.
5. Uses the `main` line to repair the failure.
6. Runs corresponding commands and isolated pre-commit convergence.
7. Validates effective change scope, commits, and pushes.
8. Waits for the next CI run.
9. Verifies that the failure fingerprint actually changed.
10. Merges and cleans up after checks pass.

Base synchronization, worktree restoration, and waiting for CI logs do not consume AI repair attempts. An attempt is counted only when actionable evidence exists and an Agent repair actually starts.

### 9.2 Diagnostic and retry boundaries

- Missing CI logs are polled up to six times; the Agent is not asked to guess.
- Automatic CI repair is capped at three attempts.
- Pre-commit convergence is capped at three passes.
- An unchanged meaningful failure fingerprint after a code change stops early.
- A degraded fingerprint without logs cannot trigger a false “unchanged failure.”
- Cancelled checks are not actionable code failures.
- Runner failure, empty output, context overflow, and “no new code” remain distinct outcomes.

The repair prompt requires inspecting `.github/workflows/`, `package.json`, `Makefile`, `tox.ini`, `pytest.ini`, and `scripts/`, then reproducing the exact CI command instead of running only a small test subset the Agent assumes is relevant.

### 9.3 Merge conflicts

Conflicts are resolved in a temporary isolated worktree bound to the current PR branch. The result still passes scope validation so unrelated changes from `main` or another checkout cannot leak into the PR.

## 10. Error classification and recovery

Classification uses structured runner errors and zero-token error envelopes. It must not scan arbitrary successful plan text, because a document that mentions “rate limit” is not a provider failure.

| Type | Example | Behavior |
|------|---------|----------|
| Transient network/Git | TLS, connection reset, DNS, temporary push failure | Keep phase and retry; fail after the bounded retry budget |
| Transient API | 429, 5xx, overloaded | Back off and retry for up to roughly 30 minutes |
| Bailian allocation throttle | `usage allocated quota exceeded` | Treat as transient and retry; **never** convert it to manual pause |
| Hard upstream quota | Explicit `platform quota exceeded` or equivalent | `paused`, manually recoverable after provider allocation returns |
| Open ACE application quota | User token/request/cost quota exceeded or quota check unavailable | Fail-closed pause; scheduler auto-resumes after recovery |
| Context overflow | Maximum context/input length | Replace provider transcript on the same stable session line |
| Insufficient CI evidence | Actions logs unavailable or unauthorized | Wait for diagnostics, then fail with an actionable permission error |
| Repository integrity violation | `.git` content, inode, owner, or ACL tampering | Fail closed with exit code 68 |

Manual, application-quota, and hard upstream-quota pauses all use `paused`, but their reasons differ. Only an application-quota pause is auto-resumable. Manual and hard upstream pauses require a user decision.

## 11. Cross-user isolation and security

### 11.1 Dedicated Agent account

The current implementation runs code Agents as a dedicated credentialless account, `openace-agent` (configurable through `OPENACE_AUTONOMOUS_AGENT_ACCOUNT` or `autonomous.agent_system_account`). It does not run them as the project owner or Open ACE service account.

The account must:

- Be non-root;
- Not belong to administrative groups such as `root`, `wheel`, `sudo`, or `admin`;
- Differ from the project owner and service account;
- Be launchable only through the narrow `openace-run-as --isolated` sudoers rule.

Controlled command wrappers in `/usr/local/libexec/openace-agent-bin` constrain the Agent's use of critical commands such as `git`, `gh`, Python, and pytest, so the restricted sudo entry cannot bypass orchestration through arbitrary Git or runtime operations.

### 11.2 Filesystem permissions

The launcher:

- Starts from an empty environment and injects only required identity, locale, temporary-directory, Git `safe.directory`, and explicit proxy variables.
- Grants Agent write ACLs only on worktree files.
- Grants read/traverse ACLs on Git metadata for normal clones and linked worktrees.
- Preserves project-owner access to newly created files.
- Serializes launches for the dedicated account.
- Revokes temporary ACLs and kills orphaned Agent processes after normal exit, signals, or recovery on the next invocation.

### 11.3 Git integrity registry

Before execution, the root launcher atomically stores the `.git` entry type, device/inode, mode, owner/group, content digest, and exact ACL snapshot under `/run`. After execution it:

1. Verifies structure, content, ownership, and owner/other permissions.
2. Allows only the POSIX ACL `mask::` representation change the launcher itself may cause.
3. Requires all base and named ACL entries to remain identical.
4. Restores the original ACL and compares the raw signature and ACL exactly.

Content, inode, type, owner, other-permission, or non-mask ACL changes fail closed. The legacy two-line registry has a one-time compatibility path only when a real ACL mask explains the difference; successful recovery immediately upgrades it to the exact format.

Do not delete `/run/openace-agent-*` just to make a run continue. For `OPENACE_REPO_INTEGRITY_VIOLATION`, inspect worktree registration, remote PR head, the `.git` pointer/directory, and ACLs first. Only then decide whether to archive obsolete state and rebuild the worktree.

## 12. Usage accounting and AI Activity

### 12.1 Usage

Workflow totals are recomputed from each milestone's `phase_total_tokens`, `phase_input_tokens`, `phase_output_tokens`, and `phase_request_count`. Repeatedly adding a resumed session's cumulative total would double count usage.

The runner maintains a baseline for provider cumulative counters and stores only call deltas. Real usage from API retries and context recovery is carried into the milestone as well.

### 12.2 AI Activity

AI Activity streams tool use, assistant text, usage, retries, and system events through SSE. It is live observability, not the durable audit log:

- `thinking_tokens` is a high-frequency cumulative estimate, not a discrete action or authoritative usage, so the backend does not send it to the UI.
- Empty assistant text and a lone `-` are not rendered.
- SSE responds with `connected` immediately and sends a visible keepalive every 30 seconds.
- Agent phases retain one stable activity host across short scheduler gaps so the panel does not flash in and out.
- Before the first token, the UI shows a friendly wait/heartbeat rather than a fabricated `--:--:--`.
- Only genuinely long silence becomes stale; normal model first-token latency is not labeled as failure too early.
- The panel shows recent events; the complete transcript is available through View session.

Activity attaches only to Agent-running planning, development, and PR-review phases, plus explicit merge repair/conflict milestones. Queue, preparation, report, and user-wait phases must not impersonate an active AI call.

## 13. Timeline UX invariants

The timeline is both a control plane and the main diagnostic surface. Frontend changes must preserve:

- Stable ascending database creation-time and ID ordering, so a system precursor appears before the repair it triggered.
- Visible Pause and Stop controls for active workflows.
- A compact, wrapping header that does not overlap metrics or final-plan/review/change buttons on narrow screens.
- Wrapping milestone action buttons.
- Per-milestone tokens, requests, and session identity for finalized plans and other AI milestones; “no new AI usage” when a reused session made no new call.
- No misleading zero-token badge on system-only milestones.
- Scrollable content and diff modal bodies in fullscreen, without an extra title-row gap from the fullscreen control.
- Auto-expansion for the latest activity milestone that respects manual collapse and viewing older milestones.
- Auto-scroll only while the user remains near the bottom.

## 14. Deployment requirements

The standard installer configures:

- `/usr/local/bin/openace-run-as`;
- `/usr/local/libexec/openace-agent-bin`;
- The `openace-agent` system account;
- A sudoers rule that allows only isolated launch;
- `setfacl`, `getfacl`, `flock`, `pkill`, Git, GitHub CLI, and the selected Agent CLI.

Important settings:

| Setting | Default | Purpose |
|---------|---------|---------|
| `AUTONOMOUS_TASK_TIMEOUT` | `3600` seconds | Timeout for one Agent task |
| `AUTONOMOUS_MAX_CHANGED_FILES` | `60` | Maximum automatic changed-file count |
| `OPENACE_AUTONOMOUS_AGENT_ACCOUNT` | `openace-agent` | Isolated Agent account |
| `OPENACE_RUN_AS` | `/usr/local/bin/openace-run-as` | Isolated launcher |
| `OPENACE_AGENT_GUARD_BIN` | `/usr/local/libexec/openace-agent-bin` | Controlled command directory for isolated execution |

Internal limits referenced elsewhere in this document are also module-level constants (in `app/services/autonomous_scheduler.py` and `app/modules/workspace/autonomous/orchestrator.py`) and are not operator-tunable:

| Constant | Default | Purpose |
|----------|---------|---------|
| `MAX_CONCURRENT_WORKFLOWS` | `3` | Concurrent workflows advanced by the scheduler (see also §6) |
| `MAX_CI_REPAIR_ATTEMPTS` | `3` | Automatic merge-phase CI repair attempts (§9.2) |
| `MAX_CI_DIAGNOSTICS_ATTEMPTS` | `6` | Bounded scheduler polls when failed-job logs stay unavailable (§9.2) |
| `MAX_PRE_COMMIT_CONVERGENCE_PASSES` | `3` | Isolated `pre-commit` convergence rounds (§9.2) |
| `API_RETRY_TOTAL_TIMEOUT` | `1800` seconds | Maximum total backoff window for transient API errors, roughly 30 minutes (§10) |
| `PLANNING_TIMEOUT` | `1800` seconds | Planning phase timeout |

When upgrading an older installation, run the installer validation/upgrade path and confirm that legacy broad `openace-run-as` sudoers files are disabled.

## 15. API overview

All endpoints require authentication and enforce workflow ownership or administrator access.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/autonomous/workflows` | Create one workflow or a batch |
| `GET /api/autonomous/workflows` | List workflows |
| `GET /api/autonomous/workflows/:id` | Read workflow details |
| `POST /api/autonomous/workflows/:id/pause` | Pause |
| `POST /api/autonomous/workflows/:id/resume` | Resume |
| `POST /api/autonomous/workflows/:id/stop` | Stop |
| `POST /api/autonomous/workflows/:id/retry` | Retry a failed/timed-out workflow |
| `GET /api/autonomous/workflows/:id/timeline` | List milestones |
| `POST /api/autonomous/workflows/:id/milestones/:mid/cancel` | Cancel a round and wait for feedback |
| `POST /api/autonomous/workflows/:id/milestones/:mid/fork` | Fork from a milestone |
| `GET /api/autonomous/workflows/:id/events/stream` | SSE activity stream |
| `GET /api/autonomous/workflows/:id/pr-diff` | PR diff |
| `GET /api/autonomous/workflows/:id/pr-stats` | PR change statistics |

See the [API reference](API.md) and `app/routes/autonomous.py` for complete fields and responses.

## 16. Code map

| File | Responsibility |
|------|----------------|
| `app/routes/autonomous.py` | API, authorization, pause/resume/stop, SSE, milestone operations |
| `app/services/autonomous_scheduler.py` | Scheduling, quota gate, concurrency, batches, distributed lock |
| `app/modules/workspace/autonomous/orchestrator.py` | State machine, prompts, three sessions, CI repair, conflicts, merge |
| `app/modules/workspace/autonomous/agent_runner.py` | CLI adapters, resume, activity, and usage collection |
| `app/modules/workspace/autonomous/github_ops.py` | Controlled Git and GitHub operations |
| `app/repositories/autonomous_repo.py` | Workflow, milestone, lock, and usage persistence |
| `scripts/openace-run-as.sh` | Cross-user low-privilege launch, ACLs, Git integrity |
| `app/modules/workspace/autonomous/agent_bin/` | Git, GitHub, and runtime command guards for isolated execution |
| `frontend/src/components/work/WorkflowTimeline.tsx` | Timeline, activity panel, controls, and modals |
| `frontend/src/components/work/WorkflowTimeline.utils.ts` | Activity host/filter and diff utilities |

## 17. Regression matrix for design changes

Do not test only the helper that exposed the current failure. Cover the complete lifecycle.

### 17.1 Sessions and usage

- Resume all three stable lines across multiple milestones.
- Preserve the stable line after context overflow.
- Keep API retry/context-recovery usage without loss or duplication.
- Show session and usage for finalized plan, test, review, and CI repair milestones.
- Keep `thinking_tokens` and empty activity out of the UI.

### 17.2 CI repair

- PR branch behind `main`.
- Remote head different from local head.
- Valid unpushed local commit.
- Worktree temporarily removed by conflict resolution.
- Runner non-zero exit, exit-zero error envelope, and empty output.
- Logs unavailable, cancelled check, changed/unchanged failure fingerprint.
- No Agent change, real change, and out-of-scope change.
- Pre-commit file edits, multi-pass convergence, and cache permissions.
- Minimal-context retry after overflow.

### 17.3 Isolated execution

- Normal clone and linked worktree.
- No extended ACL, existing named ACL, and inherited ACL.
- Repeated successful runs.
- Recovery after TERM and SIGKILL.
- Worktree deletion/recreation.
- `.git` content, inode, mode, owner, and non-mask ACL tampering.
- Upgrade from the legacy two-line registry.

### 17.4 Quota and recovery

- Open ACE application quota exhaustion and recovery.
- Fail-closed behavior when quota checks fail.
- Manual pause does not auto-resume.
- Manual recovery from hard upstream quota.
- Bailian `allocated quota exceeded` keeps retrying.
- Normal prose about quota/rate limits does not trigger classification.

### 17.5 Frontend

- Wide and narrow headers.
- Activity before first event, between milestones, and during a long wait.
- Stable ordering when timestamps match.
- Wrapping action buttons.
- Fullscreen scrolling for content and diff modals.
- Manual collapse, viewing old milestones, and auto-scroll do not fight each other.

Suggested focused commands:

```bash
pytest -q tests/issues/716 tests/unit/test_autonomous_ci_guardrails.py
pytest -q tests/unit/test_autonomous_timeline_session_identity.py
pytest -q tests/unit/test_upstream_quota_pause.py
pytest -q tests/issues/1395
cd frontend && npm test -- --run WorkflowTimeline
```

Then run `tests/autonomous/`, relevant issue regressions, and the repository's full CI as required by the change.

## 18. Troubleshooting

| Symptom | First checks |
|---------|--------------|
| Workflow does not advance | Status, `error_message`, scheduler logs, DB lock, and branch/worktree conflicts |
| AI Activity is absent | Whether this is an Agent phase, activity-host milestone, SSE/keepalive, stable session ID |
| Activity contains a dash-only row | Whether empty assistant filtering was bypassed |
| Token count grows unexpectedly | Whether cumulative resumed-session usage was added to multiple milestones |
| Bailian throttle stops automation | Whether `allocated quota exceeded` was misclassified as hard quota |
| CI repair attempts disappear immediately | Whether attempt count increased before logs, worktree restoration, or base sync |
| Repair says no changes | Whether the baseline incorrectly used local HEAD instead of remote PR head |
| `.git` integrity failure | Inspect registry, ACL, worktree pointer, inode, and interruption logs; do not delete registry blindly |
| Manual pause auto-resumes | Check whether its reason incorrectly uses the application-quota prefix |
| Fullscreen modal cannot scroll | Check `min-height: 0` and inner `overflow: auto` on modal body/content |

## 19. Known boundaries

- Live AI Activity depends on in-process SSE and is not a complete cross-restart log. Milestones and sessions are the durable audit record.
- In-memory workspace/branch sets provide fast scheduler exclusion; multi-instance correctness also relies on database locks and Git constraints.
- Automatic CI repair has a strict attempt cap and never loops indefinitely.
- Provider error wording can change. New adapters must constrain matching to zero-token envelopes and structured runner results.
- Isolated execution depends on Linux ACLs and controlled sudoers. It must not degrade to running as the project owner when those requirements are unavailable.

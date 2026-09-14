# Multi-user Isolation Acceptance Handbook (#3379 / #3374)

This handbook describes how to execute the #3374 nine-item acceptance checklist
against a **real multi-user Linux deployment**, how to read the acceptance
record produced by `scripts/multiuser_acceptance.py`, and which
boundaries are covered by **declared exemptions** rather than automated
assertions. The approved plan of record is
`docs/dev-notes/3379-multiuser-acceptance-plan.md` (v2.1).

## 1. Environment prerequisites

**Product prerequisites (same tier as PR-A/#3384)**:
- **#3387/#3110 (merged)**: the app's config resolution honors
  `OPENACE_CONFIG_DIR` (it used to read `~/.open-ace` and never see the
  config volume).
- **#3389 shared-namespace provisioning**: nothing in the product created
  `<base>/shared` — item (d) used to record the fresh-deployment 403 as a
  declared known gap; the entrypoint now provisions it (openace-shared
  group, **3775** with the sticky bit).
- **#3390 (UID drift)**: on container recreation the entrypoint re-useradds
  active users without uid pinning — a deactivated user's directories are
  numerically inherited by an active account. Item (f) asserts this by
  owner name and is expected to FAIL until the fix lands (recorded
  honestly).
- **#3394 (fixed, PR #3395)**: the frontend integrity check expected a
  `main.*.js` that vite never produced — production images crash-looped.
- **#3396 (OS-layer shared isolation)**: shared dirs are group
  openace-shared (GLOBAL — every tenant's account joins) 2775/664 —
  cross-tenant and post-revocation OS-level access persists. Item (d) has
  OS-layer probes, expected to FAIL, recorded honestly.
- **#3397 (declared deviation)**: a fresh multi-user production deployment
  following DEPLOYMENT.md cannot start (empty DB refused; a bare migration
  leaves no default admin). The script works around it with a one-shot
  `alembic upgrade head && init_db.py` container — a DECLARED deviation
  recorded in every run's notes; revert to the documented path once fixed.

- A real Linux host (`linux` + `docker` CLI + compose v2; enforced at startup).
- Enough memory for three qwen-code-webui instances (a few hundred MB each).
- **A fresh stack**: the script requires the default admin to be in the
  must_change_password state. After any previous run, first clean the
  DEDICATED project (not the default one — that may be the production
  deployment in the same checkout):
  `docker compose -p acceptance-multi -f docker-compose.yml -f docker-compose.multi-user.yml down -v --remove-orphans`.
- **Host exclusivity**: the multi-user stack inherits the base compose's
  daemon-global container names (`open-ace`, …) and holds host ports 19888
  and 3100–3200 — with a product stack already running, the acceptance
  aborts cleanly at `up` (name/port conflict). Coexistence would need a
  rename+offset override like the single-user tail's (declared, not built).
- On CI, a dedicated `multiuser-acceptance` job (main-push, observation
  period) builds its own image and drives compose with
  `IMAGE_NAME=open-ace:$GITHUB_SHA` — the tested code and the image
  fingerprint stay strictly coupled; the public `openace/open-ace:latest` is
  never pulled by accident.

## 2. How to run

```bash
# Locally (repo root; optionally export IMAGE_NAME=open-ace:<tag> first)
python3 scripts/multiuser_acceptance.py

# Keep the MULTI-USER stack up for manual inspection (the single-user tail
# always builds and cleans its own isolated project)
ACCEPTANCE_KEEP_STACK=1 python3 scripts/multiuser_acceptance.py

# Custom service URL / record directory
ACCEPTANCE_BASE_URL=http://host:19888 \
ACCEPTANCE_RECORD_DIR=./my-records \
python3 scripts/multiuser_acceptance.py
```

Flow: **refuse if the dedicated project has state** → bootstrap env (the
three mandatory secrets) → **two-phase config** (first `up` lets the
entrypoint generate the full multi-user config → stop → a same-image helper
container merges ONLY `max_instances=3` → second `up`) → default-admin first
login with password change → two-tenant, five-user scenario (tenant-1:
alice/bob/dave/erin, tenant-2: carol; `system_account` triggers the real
useradd wrapper) → nine-item assertions → single-user regression tail →
`down -v` (unless KEEP_STACK).

Both stacks run in DEDICATED compose projects (`acceptance-multi` /
`acceptance-single`) — an existing deployment beside this checkout is never
touched; if the dedicated project already holds containers/volumes the script
refuses to run (exit `2`, printing the cleanup command).

Exit codes: `0` all passed; `1` failures or an aborted run (compose logs are
dumped into the record directory on BOTH paths — including a completed run
with failures); `2` environment unsuitable or non-empty dedicated project.

First REAL run on a branch (before merge): the workflow's `pull_request`
trigger uses the workflow file FROM THE PR BRANCH, so any PR touching
`scripts/multiuser_acceptance.py`, the workflow, or the handbooks runs the
acceptance for real in CI. (`workflow_dispatch` needs the workflow
registered on the default branch first — that is why pre-merge dispatch
404s.)

## 3. Nine-item checklist: attack / expectation matrix

| # | Checklist item | Automated attack | Expected (PASS criteria) |
|---|---|---|---|
| a | Concurrent private dirs/history/model config | alice/bob concurrent user-url; in-container /proc scan | Distinct ports, distinct UIDs (sudo -u effective), 0700 homes each; webui env carries only distinct `webui:<uid>` proxy tokens, `OPENAI_API_KEY` == proxy token, no real/sensitive keys |
| b | ID tampering / traversal / symlink / cross-user restore | `required_isolation=none`; A's session token against B's session routes; DB-seeded machines/agent_sessions/machine_assignments then B stops/attaches A's terminal; fs browse of B's workspace home (`/workspace/<B>`), `../`, symlink into B's home | Floor not lowered (stays os_user); the 403/404 matrix hits each cell; all three fs cases 400 — the `/workspace/<B>` path passes the base-dir gate and is then refused by realpath resolution + the #3376 home-lock (`/home/*` would only hit the base-dir prefix gate, never reaching the home-lock, hence the workspace-side targets) |
| c | No other user's credentials in env; A's tools stay out of B's area | `docker exec -u alice` reading B's webui `/proc/<pid>/environ`, `ls /home/bob` | EPERM/EACCES (real-UID semantics); model-config separation shares item a's evidence channel |
| d | Shared-project grant and revocation | alice creates `<base>/shared/acc-team-proj`; bob (same tenant) / carol (other tenant) browse; browse again after revocation; **OS-layer probes** (carol/bob shell ls/touch — the same channel item c argues terminal equivalence with) | bob 200, carol 400; after revocation bob 400; OS probes expected to FAIL (global openace-shared group, #3396, recorded honestly) |
| e | Resource ceilings / cancellation / crash isolation | Pre-seeded `max_instances=3`; a 4th instance; admin stops alice's instance; `kill -9` bob's webui | 4th instance 503 (body unstructured — recorded verbatim, itself an acceptance finding); others' sessions and /readyz undisturbed; the freed slot is reusable |
| f | Deactivation / token revocation / restart orphans | After deactivating bob: session, URL-token, process, proxy token (read from the webui env's `OPENAI_API_KEY` — the sudo-launch path inlines only that known key set); after `up -d --force-recreate` (container recreated, volumes kept — `restart` keeps the writable layer and cannot distinguish a secret on the volume from one left in the layer): in-container process and port checks; alice's old token re-verified | All 401 / instance destroyed / proxy token 401; no leftover webui processes after recreation, nothing listening on 3100–3200 in-container; token_secret volume persistence keeps the old token valid (#3377's actual claim). **Depends on PR-A (#3384)** |
| g | Explicit refusal when a backend/level is unavailable | Contract endpoint; user-url requesting `sandboxed`; unmapped user (erin) requesting `os_user` | Contract `isolation_level=os_user` with reasons containing **no** SANDBOX_PROBE_REASON_CODES; 400 `isolation_level_unsupported`; 400 `identity_mapping_missing` |
| h | No regression in single-user mode | Base compose in its OWN project (port 19889, fresh volumes), leaving the multi-user stack untouched | Contract `none`, single instance on 3100 (recorded as a declared exemption if the app-side single-user launch limitation fires — §5.8), admin login, /readyz 200 |
| i | Publishable sample / permission conditions / capability matrix / real results | Recorder | Record carries git SHA, image digest, docker/compose versions, kernel, policy_revision; capability matrix cross-references `WORKSPACE_ISOLATION_CAPABILITIES` |

## 4. Records and template

- Location: `ACCEPTANCE_RECORD_DIR` (default
  `test-results/acceptance-records/`; uploaded as a CI artifact with 90-day
  retention — **not a long-term archive**; the final record should be posted
  into #3374 or committed to the repo).
- `multiuser-acceptance-<utc-ts>.json`: per-item PASS/FAIL plus **truncated
  request/response pairs even for passing items** (`items[].evidence`,
  truncated at 400 chars) + environment fingerprint + run notes.
- A same-named `.md`: the human-readable matrix (the executed version of the
  table above).
- Blank template for operators:

```markdown
# Multi-user isolation acceptance record (manual run)
- Executed at (UTC):
- Operator:
- git SHA / image digest:
- docker/compose versions / kernel:
- policy_revision:
- Summary: __ passed / __ failed
- Per-item results and evidence: (fill per the §3 matrix)
- Manual review items (dual-browser screenshots, default-UI walkthrough):
- Deviations and notes:
```

## 5. Declared exemptions and boundaries (deliberate, not oversights)

1. **sandboxed level**: the compose shape mounts no `sandbox-backends.json`;
   the contract deterministically reports `isolation_level=os_user` with no
   sandbox probe codes in reasons (backend-unconfigured is deliberately
   silenced by the contract; multi_process is unreachable without a backend).
   When a real cluster is available, the **extension checklist** (six external
   assumptions, one record row each): 3100 endpoint reachability, entrypoint
   upstream constraints, renew format, gateway-credential self-capture
   boundary, single-web-process assumption, boot-probe upgrade and memo TTL.
2. **VSCode ownership gate**: its store is in-process memory and cannot be
   seeded externally — covered by unit tests
   (`tests/unit/test_vscode_ownership_3376.py`) plus this declared exemption
   (compose has no remote agent).
3. **Remote path validation**: `is_valid_remote_path` is a purely structural
   check and does not reject "someone else's home-shaped" paths — the #3376
   semantic boundary, recorded as-is, not a failure of this acceptance.
4. **Terminal-equivalence argument**: the script asserts EACCES via
   `docker exec -u alice ls /home/bob`. Commands inside a terminal run under
   alice's real UID (the terminal process itself runs as that UID); there is
   no OS-level difference — `docker exec -u` and "user A's terminal" are
   equivalent under the same UID semantics.
5. **"Ephemeral materials cleaned per policy"**: under compose this is
   container/volume lifecycle semantics (`down -v`). The
   "control plane restarts while the container survives" shape does not exist
   under compose (restart restarts the whole container); that shape is left
   for a Kubernetes-deployment acceptance.
6. **Task cancellation**: approximated by instance stop + session
   termination (bob being undisturbed after alice's stop is the expected
   behavior), with this boundary declared.
7. **Unstructured max_instances 503 body**: recorded verbatim — it is itself
   an acceptance finding, not an assertion failure.
8. **Default-admin 3100 launch (capability-separated exemption)**:
   single-user containers run as uid 1000 and never provision an OS account
   for the default admin, so the sudo path cannot launch the 3100 instance
   FOR THE ADMIN — a known app-side mapping limitation. The script first
   makes a REAL capability assertion: a user with
   `system_account=open-ace` (the container's own account) takes the
   sudo-free direct-launch branch and its user-url must return 200/:3100 —
   if that fails, the admin's 502/503 FAILs too (no regression can hide).
   Only with the capability proven does the admin's 502/503 record as
   EXEMPT (the declared limitation). The exemption collapses once the app
   side maps an account for the default admin. Note: the single-user tail runs in its own
   compose project (web port 19889, workspace port range offset to
   13100-13200 to avoid colliding with the multi-user stack's 3100-3200)
   — in that shape the single-user webui is not reachable at its
   advertised host URL; the item-h assertions are all API-level and never
   connect to it.

## 6. Deployment note (carried from #3384)

Since #3384, `update_user(is_active=false)` writes the `tokens_valid_after`
column in the same UPDATE. **On development databases, run
`alembic upgrade head` after pulling that change before triggering any
deactivation**, otherwise deactivations fail with a 500 (org-sync paths fail
silently instead). Production PostgreSQL checks migration head at startup
(REQUIRE_HEAD) and refuses to boot without it — unaffected.

## 7. Manual review points (beyond automation)

- Item a: dual-browser screenshots (alice/bob identities side by side),
  visually confirming the private directories and histories.
- Item h: manual walkthrough of the single-user default UI (workspace open,
  file browsing).
- Attach screenshots and walkthrough conclusions under the "Manual review
  items" section of the record template.

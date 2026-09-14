# Multi-user Isolation Acceptance Handbook (#3379 / #3374)

This handbook describes how to execute the #3374 nine-item acceptance checklist
against a **real multi-user Linux deployment**, how to read the acceptance
record produced by `scripts/multiuser_acceptance_3379.py`, and which
boundaries are covered by **declared exemptions** rather than automated
assertions. The approved plan of record is
`docs/superpowers/plans/2026-09-13-issue-3379-multiuser-acceptance.md` (v2.1).

## 1. Environment prerequisites

- A real Linux host (`linux` + `docker` CLI + compose v2; enforced at startup).
- Enough memory for three qwen-code-webui instances (a few hundred MB each).
- **A fresh stack**: the script requires the default admin to be in the
  must_change_password state. After any previous run, first
  `docker compose -f docker-compose.yml -f docker-compose.multi-user.yml down -v`.
- On CI, a dedicated `multiuser-acceptance` job (main-push, observation
  period) builds its own image and drives compose with
  `IMAGE_NAME=open-ace:$GITHUB_SHA` — the tested code and the image
  fingerprint stay strictly coupled; the public `openace/open-ace:latest` is
  never pulled by accident.

## 2. How to run

```bash
# Locally (repo root; optionally export IMAGE_NAME=open-ace:<tag> first)
python3 scripts/multiuser_acceptance_3379.py

# Keep the stack up for manual inspection (skip the final down -v)
ACCEPTANCE_KEEP_STACK=1 python3 scripts/multiuser_acceptance_3379.py

# Custom service URL / record directory
ACCEPTANCE_BASE_URL=http://host:19888 \
ACCEPTANCE_RECORD_DIR=./my-records \
python3 scripts/multiuser_acceptance_3379.py
```

Flow: bootstrap env (generates the three mandatory secrets) → **pre-seed
config.json into the config volume** (`workspace.multi_user_mode=true`,
`max_instances=3`; the volume is created with compose labels when absent so
the first `up` adopts it) → `up -d --wait` → default-admin first login with
password change → two-tenant, five-user scenario (tenant-1:
alice/bob/dave/erin, tenant-2: carol; `system_account` triggers the real
useradd wrapper) → nine-item assertions → single-user regression tail →
`down -v` (unless KEEP_STACK).

Exit codes: `0` all passed; `1` failures or an aborted run (compose logs are
dumped into the record directory); `2` environment unsuitable.

## 3. Nine-item checklist: attack / expectation matrix

| # | Checklist item | Automated attack | Expected (PASS criteria) |
|---|---|---|---|
| a | Concurrent private dirs/history/model config | alice/bob concurrent user-url; in-container /proc scan | Distinct ports, distinct UIDs (sudo -u effective), 0700 homes each; webui env carries only distinct `webui:<uid>` proxy tokens, `OPENAI_API_KEY` == proxy token, no real/sensitive keys |
| b | ID tampering / traversal / symlink / cross-user restore | `required_isolation=none`; A's session token against B's session routes; DB-seeded machines/agent_sessions/machine_assignments then B stops/attaches A's terminal; fs browse of B's home, `../`, symlink into B's home | Floor not lowered (stays os_user); the 403/404 matrix hits each cell; all three fs cases 400 (realpath first) |
| c | No other user's credentials in env; A's tools stay out of B's area | `docker exec -u alice` reading B's webui `/proc/<pid>/environ`, `ls /home/bob` | EPERM/EACCES (real-UID semantics); model-config separation shares item a's evidence channel |
| d | Shared-project grant and revocation | alice creates `<base>/shared/acc-team-proj`; bob (same tenant) / carol (other tenant) browse; browse again after revocation | bob 200, carol 400; after revocation bob 400 |
| e | Resource ceilings / cancellation / crash isolation | Pre-seeded `max_instances=3`; a 4th instance; admin stops alice's instance; `kill -9` bob's webui | 4th instance 503 (body unstructured — recorded verbatim, itself an acceptance finding); others' sessions and /readyz undisturbed; the freed slot is reusable |
| f | Deactivation / token revocation / restart orphans | After deactivating bob: session, URL-token, process, proxy token; after `compose restart`: in-container process and port checks; alice's old token re-verified | All 401 / instance destroyed / proxy token 401; no leftover webui processes after restart, nothing listening on 3100–3200 in-container; token_secret persistence keeps the old token valid (#3377). **Depends on PR-A (#3384)** |
| g | Explicit refusal when a backend/level is unavailable | Contract endpoint; user-url requesting `sandboxed`; unmapped user (erin) requesting `os_user` | Contract `isolation_level=os_user` with reasons containing **no** SANDBOX_PROBE_REASON_CODES; 400 `isolation_level_unsupported`; 400 `identity_mapping_missing` |
| h | No regression in single-user mode | `down -v`, then base compose only | Contract `none`, single instance on 3100, admin login, /readyz 200 |
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

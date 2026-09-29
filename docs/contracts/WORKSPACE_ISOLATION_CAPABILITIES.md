# Workspace Isolation Capabilities — 工作区隔离能力

[English](#english) | [中文](#中文)

---

## English

Open ACE provides a **versioned multi-user isolation capability contract** for
the local interactive workspace (chat WebUI, filesystem interface, session
history). Administrators and trusted integrators should query the
actually-effective capabilities through the API rather than relying on README
claims or capability booleans passed in by clients. This document explains the
contract semantics, deployment requirements, the reason code cross-reference,
and known gaps. Related issues: #3374 (os_user), #3378 (sandboxed).

## 1. Capability Contract Fields

Query endpoint: `GET /api/workspace/isolation-capabilities` (requires a
logged-in session)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "qwen-code-webui-per-user | qwen-code-webui-shared | opensandbox:<tier>",
  "isolation_level": "none | os_user | sandboxed",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress", "kernel"],
  "reasons": [{"code": "...", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract"
  },
  "policy_revision": "2026-09-12.2"
}
```

- `local_workspace_multi_user`: whether multi-user isolation of the local
  interactive workspace is supported.
- `backend`: the actual runner — `qwen-code-webui-per-user` (a dedicated WebUI
  process per user), `qwen-code-webui-shared` (one shared instance), or
  `opensandbox:<tier>` (#3378: the sandboxed level, with the WebUI running in
  an OpenSandbox pod of that tier).
- `isolation_level` / `enforced` / `unsupported`: see the next section.
- `reasons`: machine-readable reasons when unsupported (may be empty).
- `entry_points`: per-entry isolation coverage status (§4). **Emitted only in
  supported snapshots** — the entry matrix describes the coverage of
  multi-user isolation; unsupported (single-user/unverified/degraded)
  deployments do not carry it, avoiding "no isolation" and "webui: enforced"
  contradicting each other in the same frame. The matrix is a static audit
  conclusion, versioned with `policy_revision`; binding each entry's
  conformance to code is follow-up work (§7).
- `policy_revision`: the contract semantics version; bumped whenever the
  derivation logic or the entry matrix changes.

## 2. Isolation Level Semantics

Level ordering: `none < os_user < sandboxed` (both the `required_isolation`
request parameter and the `workspace.required_isolation_level` floor are
compared under this ordering and can only be raised, never lowered).

| Level | Semantics |
|---|---|
| `none` | All local interactive sessions run as one shared service account, with no inter-user file/environment/process isolation (single-user lightweight mode, by design) |
| `os_user` | Per-user system account with dedicated HOME/TMP/XDG; the WebUI process is started as that user's UID via `sudo -u`; the subprocess environment is an explicit allowlist (real model API keys never enter; a proxy token is used instead); the filesystem interface applies an application-layer home-subtree lock + OS permissions |
| `sandboxed` | Each user's WebUI process runs in an OpenSandbox pod (#3378): one dedicated pod per instance, a dedicated webui image that is digest-pinned and in the image_allowlist, deny-default egress, always-present resource bounds; identity is a per-instance token secret (not an OS account; destroyed with the instance); the browser reaches it through a control-plane local-port proxy. Deployment requirements and honest boundaries: §6 |

**Boundary statement**: `os_user` shares the host kernel, has no namespace
isolation, and has no network egress policy — "separate directories / a
different HOME" does not constitute strong runtime isolation. The
`resources`, `network_egress`, and `kernel` dimensions are always listed
under `unsupported` for `os_user` (the interactive WebUI has no per-task
cgroup, only an instance-count cap and idle cleanup; no egress policy; shared
host kernel). For the dimension semantics and verification boundaries of the
`sandboxed` level see §6.1. Resource and sandbox policy for autonomous tasks
follows the separate #2022 sandbox contract (`sandbox_effective_policy`); see
`../dev/SANDBOX_BACKENDS.md`.

**Platform boundary**: only Linux deployments can declare `os_user`. macOS
skips system user creation, so Open ACE cannot establish/verify the identity
mapping; Windows forces a single instance. Both report the contract as
`unsupported/platform_unsupported`.

## 3. Reason Code Cross-Reference

Three code families, with distinct responsibilities:

### 3.1 Contract `reasons[]` (deployment-level: why unsupported overall)

| code | Meaning |
|---|---|
| `webui_disabled` | The WebUI manager is not enabled; no interactive workspace runtime |
| `platform_unsupported` | Non-Linux platform (Windows/macOS/other) |
| `multi_user_mode_disabled` | Multi-user mode not enabled (single-user lightweight mode; an expected state, not a defect) |
| `launch_path_degraded` | The WebUI launch path cannot carry per-user instances (the message parenthesizes the specific reason from §3.3, such as dev-directory mode / missing wrapper); the contract and the /user-url gate look at the same path and cannot contradict each other |
| `launch_path_unverified` | On a cold worker the manager is not yet initialized and the probe has not run: the level is **provisional** (the deployment shape qualifies) and clears automatically once the manager initializes; consumers that cache or roll out by revision should also check this code |

### 3.2 Gate `error_code` (request-level: rejections of `user-url?required_isolation=...`)

| code | Meaning |
|---|---|
| `invalid_isolation_level` | Invalid `required_isolation` value (legal values: none/os_user/sandboxed) |
| `isolation_level_unsupported` | The requested level exceeds what the deployment actually supports; refuses to start silently with weaker isolation |
| `identity_mapping_missing` | The user has no `system_account` mapping in the database; when isolation is explicitly requested there is no silent username fallback (**os_user chain only**; sandboxed identity is a per-instance token and does not consult OS accounts) |
| `per_user_launch_unavailable` | The launch path cannot run as that user's UID (the message embeds the §3.3 reason code; **os_user chain only**) |

The gate for `sandboxed` requests is the capability probe itself: when the
level is not met, the request is rejected with a §3.4 probe code (preferred)
or `isolation_level_unsupported`, and never produces
`identity_mapping_missing`/`per_user_launch_unavailable`.

Easy-to-confuse pair: `launch_path_unverified` (deployment-level, "cold
worker not yet probed, level provisional") vs `identity_mapping_missing`
(user-level, the gate rejecting "this user has no identity mapping").

### 3.3 Probe reason codes (embedded in the message of `per_user_launch_unavailable`, or parenthesized in the deployment-level `launch_path_degraded`)

| code | Meaning |
|---|---|
| `platform_unsupported` | Non-Linux/macOS platform |
| `webui_executable_missing` | The qwen-code-webui executable cannot be found |
| `current_user_unresolved` | The service process's own UID cannot be resolved |
| `dev_directory_mode_shared_account` | Dev-directory mode runs node as the service user, without switching UID |
| `launch_wrapper_missing` | The `openace-webui-launch` wrapper is not installed or not executable (the real precondition of the sudo path) |
| `sudo_unavailable` | No sudo binary |
| `privileged_system_account` | The target system account has uid 0 (e.g. mapped to root) |
| `reserved_system_account` | The target system account has uid < 1000 (the system-reserved range) |

### 3.4 sandboxed probe and runtime reason codes (#3378)

Probing the `sandboxed` level is **zero-pod and fail-closed on the
configuration plane** (decided without creating any pod); of the codes below,
the first 8 are probe-level reasons (a hit means rejection), the next 4 are
snapshot-level (they appear in the contract `reasons[]` and do not block
declaration), and the last 2 are runtime error codes (thrown by the launcher,
not probe codes).

| code | Level | Meaning | Remediation |
|---|---|---|---|
| `sandbox_backend_unconfigured` | probe | sandbox-backends.json is missing, unparseable, or unconfigured | Provide/fix the backend configuration (see `../dev/SANDBOX_BACKENDS.md` §3) |
| `sandbox_tier_missing` | probe | `workspace.sandbox_tier` (or the backend `default_tier`) has no matching entry in `endpoints` | Fix `sandbox_tier` or add the tier to `endpoints` |
| `webui_image_missing` | probe | `webui_image` not configured for the tier | Configure an image containing qwen-code-webui (reference build: `scripts/docker/webui-sandbox.Dockerfile`) |
| `webui_image_not_pinned` | probe | `webui_image` is not digest-pinned (`name@sha256:<64 hex>`) | Switch to a digest reference — tags can be retargeted and would hollow out the allowlist |
| `webui_image_not_allowed` | probe | `webui_image` is not in the `image_allowlist` | Add the image to the `image_allowlist`, or switch to one already listed |
| `sandbox_api_key_missing` | probe | The environment variable named by the tier's `api_key_env` is empty in this process — the very first API call to create a pod would fail; the contract and the launch path must not reach contradictory conclusions about the same host (#3375 principle) | Set that API key in the environment running the web process (the `api_key_env` field of sandbox-backends.json) |
| `sandbox_multi_process_unsupported` | probe | There is **another** web process holding a fresh heartbeat (an unreadable/undecidable heartbeat root is likewise treated as present — sole-survivor status cannot be proven): the per-instance token secret and instance management are in-process memory state, and across replicas about 2/3 of token validations would 401 at random; **the shipped k8s manifest (3 replicas) has this shape by default, and sandboxed automatically falls back to the os_user chain under multiple replicas** | Only a single-web-process deployment (e.g. docker-compose with one replica) may declare sandboxed; multi-replica shapes use os_user, or wait for multi-replica instance-management support (follow-up) |
| `sandbox_proxy_unreachable` | probe | `workspace.webui_callback_url` is unset; or the URL is unreachable under that tier's egress policy (loopback; sidecar tier not in `egress_allow_hosts`; CNI tier is a private/in-cluster address) | Set `webui_callback_url`; for the sidecar tier add the control-plane hostname to `egress_allow_hosts`; for the CNI tier ensure public reachability |
| `sandbox_runtime_unverified` | snapshot | Static view: only configuration-plane validation has passed; kernel/network_egress await confirmation by the first pod boot probe (the memo falls back to this state on control-plane restart, a failed same-tier probe, or an entry older than 1h) | No fix needed; upgrades automatically after the first successful pod start |
| `sandbox_launch_unverified` | snapshot | Cold worker (the manager is not yet initialized and the sandbox launch chain has not been exercised in this process): the level is **provisional** and clears automatically once the manager initializes (aligned with the os_user `launch_path_unverified` precedent) | No fix needed; disappears after the first workspace activity |
| `sandbox_runtime_kata_negative_only` | snapshot | The first pod probe passed, but kernel is only negatively verified (Kata can only rule out gVisor and cannot be distinguished from unisolated runc): kernel stays unsupported | No fix needed; switching to a gVisor tier yields positive kernel verification |
| `sandbox_runtime_egress_negative_only` | snapshot | The first pod probe passed, but egress is only negatively verified (the cluster-level deny-default control of gVisor/CNI tiers — proving the reject path exists, not that the allow path works): network_egress stays unsupported; only an actual read of the sidecar tier's `/policy` upgrades it (T-M) | No fix needed; use a sidecar attestation tier when you need to declare network_egress |
| `sandbox_create_failed` | runtime | The create request was rejected, create failed, or the boot probe failed (a probe failure destroys the pod immediately) | Inspect the reason embedded in the message (including pass-through provider probe codes) |
| `sandbox_endpoint_unresolved` | runtime | Resolving the pod's port-3100 endpoint via `GET /sandboxes/{id}/endpoints/3100` failed — the gateway cannot answer for undeclared ports (external assumption; cluster-side verification belongs to #3379) | Check the gateway's behavior for undeclared ports; this path is not verified against a real cluster |

## 4. Entry Coverage Matrix

| Entry | Status | Notes |
|---|---|---|
| webui (chat tool) | enforced | Per-user instance/UID/port/token; stop and token revocation are isolated by user_id |
| filesystem_api | partial | Upload/download/delete carry the home-subtree lock (#1813); browse/check-path rely on OS permission scoping |
| session_history | enforced | Application-layer ownership gate + tenant fail-closed |
| terminal | partial | Terminal tokens are authorized per machine, not bound to the terminal session owner (known gap) |
| vscode | partial | owner is recorded as machine.created_by; weak project_path validation (known gap) |
| autonomous | separate_contract | Follows the #2022 sandbox contract; out of scope for this contract |

**Matrix values under sandboxed deployments (#3378, from revision
2026-09-12.2)**: the matrix is emitted per level — `webui` enters the pod
with the `sandboxed` level (`enforced`); `terminal`/`vscode`/`filesystem_api`
emit **`sandboxed_entry_not_wired`** (their executors still live on the
control-plane host, not wired into the user's sandbox instance), and the
`/fs` host tree is likewise unavailable to sandboxed users — their files are
inside the pod, served by the webui's built-in in-pod file browsing;
`session_history` stays `enforced` (per-pod snapshot storage); `autonomous`
stays `separate_contract`. Matrix values of os_user/none snapshots are
unchanged. The pre-existing gaps of these entries (§8) are unaffected by the
isolation level.

## 5. Multi-User Mode Deployment Requirements (policy revision 2026-09-11.2)

Whether the contract reports `supported/os_user` is decided by the
**launch-path readiness probe** (§3.3: WebUI resolution, non-dev-directory
mode, the `openace-webui-launch` wrapper, sudo); a Docker layout is no longer
a precondition — a package-install shape
(scripts/install-central/package-method, with `sudo -u` and the wrapper in
place) can equally verify and enforce os_user.

Two reference deployments:

1. **Docker multi-user**: the `docker-compose.multi-user.yml` overlay (root +
   `OPENACE_ALLOW_ROOT_MULTI_USER=1` + `WORKSPACE_BASE_DIR=/workspace` +
   `WORKSPACE_MULTI_USER_MODE=true`); the image ships useradd and the
   wrapper, and system accounts are provisioned automatically;
2. **Package-install multi-user**: the installer's `_WS_MULTI_USER` path
   (non-root run + `/home` layout); with a healthy probe it reports os_user;
   each user's system account must already exist (resolvable via getpwnam,
   uid ≥ 1000).

Shared requirements: a restricted `openace-webui-launch` wrapper in
sudo/sudoers; no special kernel requirements (the os_user level is exactly
the OS account boundary); production baseline secrets are documented in the
compose comments.

When the probe is degraded (dev-directory mode / missing wrapper, etc.) the
contract honestly returns `launch_path_degraded` unsupported — it **never**
silently degrades to the shared account and then claims support; in that
shape the default launch path keeps its existing behavior, and an explicit
`required_isolation=os_user` gets a structured rejection.

## 6. sandboxed Level Deployment Requirements and Honest Declarations (policy revision 2026-09-12.2)

`sandboxed` = the WebUI process runs in an OpenSandbox pod: one dedicated pod
per user per instance, a dedicated webui image that is digest-pinned and in
the image_allowlist, deny-default egress; the browser reaches it through a
control-plane local-port proxy (port-based, no path-rewriting assumption);
identity is a per-instance token secret (invalid when the instance is
destroyed; no OS account/sudo chain). The probe **does not check platform or
multi_user_mode** — the pods live in a remote cluster, and single-user +
sandboxed is a legitimate hardening shape.

### 6.1 Dimension Table

| Dimension | sandboxed static (after the config-plane probe passes) | sandboxed after the first pod probe | os_user (for comparison) |
|---|---|---|---|
| identity | enforced | enforced | enforced |
| filesystem | enforced | enforced | enforced |
| environment | enforced | enforced | enforced |
| process | enforced | enforced | enforced |
| resources | enforced (the create body always carries resourceLimits; the default 4Gi/2CPU is also a bound) | enforced | unsupported |
| kernel | unsupported (`sandbox_runtime_unverified`) | gVisor: enforced (positive identification via `/proc/version`); Kata: stays unsupported (`sandbox_runtime_kata_negative_only`, negative only) | unsupported (shared host kernel) |
| network_egress | unsupported (same reason as kernel) | **sidecar attestation tier only**: enforced (an actual read of the egress sidecar's `/policy`); gVisor/CNI tiers: stay unsupported (`sandbox_runtime_egress_negative_only` — the CNI default-deny is only a negative control, proving the reject path exists, not that the allow path actually works) | unsupported |

The five statically enforced dimensions rest on **configuration facts**
(dedicated pods, image allowlist, always-present resource bounds), not on
per-pod verification; only a per-pod boot probe can prove kernel/
network_egress. Probe results are an in-process memo — **they fall back to
unverified after a control-plane restart, a failed same-tier probe, or an
entry older than 1h**; this is an intentionally honest contract (T-L: failure
revokes, entries carry a 1h TTL — latest evidence wins, and a long-lived
process does not rely on probe conclusions from hours ago).

### 6.2 Deployment Requirements

1. `endpoints.<tier>.webui_image`: digest-pinned and present in the
   `image_allowlist`. Validation happens in the capability probe, not at
   config parse — a bad value only degrades the sandboxed level and does not
   affect the backend configuration shared with autonomous tasks. Reference
   build: `scripts/docker/webui-sandbox.Dockerfile` (node + qwen-code-webui +
   non-root uid 1000, listening on 0.0.0.0:3100); production images must be
   digest-pinned and allowlisted on their own.
2. `workspace.webui_callback_url` **must be set**: the probe uses it as the
   LLM proxy callback URL (the per-request host is only knowable at launch,
   so a static probe needs a stable URL). The sidecar tier must add the
   control-plane hostname to that tier's `egress_allow_hosts`; the CNI tier
   must be publicly reachable (loopback/private/in-cluster addresses are
   rejected).
3. Proxy token TTL has **no deployment precondition** (T-F): the launcher
   clamps the pod lifetime — at create,
   `min(WebUI token TTL, effective proxy token TTL)`; each renew re-clamps to
   `min(now + WebUI token TTL, token expiry)` (UTC; if the token is
   undecodable/empty it clamps to now, i.e. it terminates together with the
   expired credential). A short TTL only shortens the pod's life; it never
   lets a pod outlive its LLM credential; there is no need to raise
   `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES` in order to declare sandboxed
   (raising it would incidentally lengthen the local webui credential
   lifetime).
4. HTTPS: keep the external reverse proxy's port-range mapping
   (`/webui/<port>`, see `../guide/NGINX.md`) — in the sandboxed shape the
   browser port is the local proxy port, drawn from the same port_range
   (default 3100-3200); single-user + sandboxed replaces the hardcoded 3100
   with proxy-port allocation.
5. `workspace.sandbox_tier` is optional: it selects which endpoint tier
   interactive pods land on, defaulting to the backend `default_tier`.
   **The pin is a floor, not a target** (consistent with #3375's floor
   definition): effective requirement = max(pin floor, request parameter),
   and the launch shape = the **strongest verified shape** satisfying that
   requirement — so once `webui_image` is configured (probe passing,
   capability sandboxed), a bare `/user-url` without parameters also takes
   the sandbox shape even if the pin (e.g. the `os_user` docker-entrypoint
   writes by default) is lower; the os_user chain (identity mapping, sudo
   launch) then does not apply, and the gate admits via the sandboxed branch.
   Deployments without `webui_image` configured behave exactly as before
   (os_user chain; an explicit `os_user` request still requires a
   system_account mapping).
6. Reachability of the webui entry and the pod's port-3100 endpoint is
   **not verified end-to-end against a real cluster** (#3379; runtime
   failures surface structurally as `sandbox_endpoint_unresolved` and never
   hang silently).

### 6.3 TTL Chain

| Stage | Value | Source |
|---|---|---|
| WebUI token TTL | 24h (default) | `OPENACE_WEBUI_TOKEN_TTL_SECONDS` (86400) |
| pod create timeout | min(WebUI token TTL, effective proxy token TTL) | Computed by the launcher at launch |
| pod renew target | min(now(UTC) + WebUI token TTL, proxy token expiry) — the pod lives at most until its LLM credential expires; undecodable/empty token → clamped to now (fail-closed; no longer nudged forward by a fallback TTL each round); past the clamp point, idle reclamation tears it down normally and the next `/user-url` rebuilds + restores from snapshot | Launcher renew clamping |
| Idle reclamation | 30min (default) | `workspace.idle_timeout_minutes` |
| Periodic snapshot export + renew | Default 5min, effectively following the cleanup interval: the maintenance check rides on the cleanup loop; when `workspace.cleanup_interval_minutes` (default 5) > 5min the actual export interval = the cleanup interval | `SANDBOX_MAINTENANCE_INTERVAL_SECONDS` = 300 (floor) + cleanup loop sleep |
| Process heartbeat refresh | Constant 300s — an independent timer greenlet (the worker startup hook is raised by the same env gating as reconcile), **does not stretch with the cleanup interval**; freshness window = 2×300+60 = 660s | `webui_sandbox.HEARTBEAT_REFRESH_SECONDS`; window `HEARTBEAT_FRESH_WINDOW_SECONDS` |
| Default proxy token TTL | 240min (no need to raise it for sandboxed; the pod TTL is clamped below it) | `DEFAULT_PROXY_TOKEN_TTL_MINUTES`, env `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES` |

The token is re-minted with the per-instance secret on every `/user-url`
hit; health checks probe with the current token through the local proxy (a
pass proves the whole chain proxy → gateway → pod → webui → token
validation).

### 6.4 Honest Declarations

- **Config-plane probing ≠ per-pod verification**: the five statically
  enforced dimensions rest on configuration facts; kernel/network_egress
  await the first pod boot probe; **after a control-plane restart it falls
  back to unverified**; a failed probe revokes that tier's memo, and entries
  expire after 1h (T-L).
- **Kata's kernel verification is negative-only** (it can only rule out
  gVisor, not distinguish from unisolated runc): kernel stays unsupported +
  `sandbox_runtime_kata_negative_only`; only positive identification of
  gVisor upgrades it to enforced.
- **Egress verification for gVisor/CNI tiers is negative-only** (the
  cluster-level deny-default control: it proves the reject path exists, not
  that the allow path actually works; the config layer already forbids
  declaring sidecar on a gVisor tier): network_egress stays unsupported +
  `sandbox_runtime_egress_negative_only`; only an actual read of a sidecar
  attestation tier's `/policy` upgrades it to enforced (T-M).
- **Session history**: whole-directory tar snapshots (stored under a
  dedicated root, keyed `webui-<user_id>.tar`); a control-plane crash loses
  at most one export interval of increments (default 5min; export rides the
  cleanup loop, so a longer `cleanup_interval_minutes` lengthens the loss
  window); **degraded starts (restore gate timing out at 60s, unconfirmed
  restore, or an unreadable snapshot) are never exported** — better to keep
  the old snapshot than to let an empty tree overwrite an intact one; a
  degraded start from an unreadable snapshot additionally **writes no
  control-plane confirmation record** (so neither reconcile nor the export
  guard will recognize its empty tree); the record is cleared only after a
  deletion is **confirmed successful**, and a failed deletion is retained
  for the next retry round; the snapshot cap defaults to 16MiB (tunable via
  `OPENACE_WEBUI_STATE_MAX_BYTES`), and exceeding it skips export — that
  user's history is **frozen at the last intact snapshot** until manual
  cleanup; **no automatic GC**.
- **token 401 semantics (N8)**: after a sandbox instance stops, its issued
  tokens 401 on the CP URL-token path (the per-instance secret dies with the
  instance).
- **Entry wiring scope**: terminal/vscode/fs are not wired into the sandbox
  shape (`sandboxed_entry_not_wired`); the `/fs` host tree is unavailable to
  sandboxed users (files live in the pod; webui ships its own in-pod file
  browsing).
- **External assumptions**: the gateway's credential-injection headers are
  held end-to-end by the proxy self-captured inside the pod — "the proxy is
  a dumb pipe and the token is validated by the webui inside the pod"
  embeds an assumption about webui behavior; reachability of the port-3100
  endpoint and the entrypoint's upstream constraints are external
  assumptions, with real-cluster verification belonging to #3379.
- **Multi-replica and orphan reclamation (T-D, round 2 revision)**: orphan
  reclamation is decided by process generation and checks the control-plane
  heartbeat file before destroying
  (`<CONFIG_DIR>/webui-agent-state/webui-heartbeat-<process-generation>.json`,
  one per web process; both the filename and self-exclusion use a
  **per-process uuid4 generation** — same-node containers share the host
  boot_id and pids collide often, so a `(boot_id, pid)` identity would make
  single-node k3s/kind replicas mistake each other for "themselves");
  refresh is carried by an **independent 300s timer** (constant period,
  decoupled from the cleanup interval; replicas without a manager instance
  also refresh); a process deletes its own heartbeat file on exit (gunicorn
  `worker_exit` hook + atexit, idempotent and fail-soft) — **the restart
  window of a single-process deployment therefore approaches zero**, but the
  SIGKILL/crash path cannot clean up: a leftover heartbeat naturally expires
  after at most one freshness window (≤ 660s), during which declaring
  sandboxed is refused and the deployment falls back to the os_user chain.
  Sweeping is skipped when other fresh heartbeats exist. **reconcile
  fratricide is eliminated** (under 3 replicas/rolling overlap, a new
  replica no longer sweeps other replicas' online pods). **Heartbeat reads
  are fail-closed**: an unreadable heartbeat root or file = "cannot prove
  sole survivor", treated as "a peer exists" (the probe refuses sandboxed,
  sweeping is skipped), while the write side stays fail-soft. **But pod
  ownership is still single-process in-memory state** — token validation and
  instance management (reclamation, snapshot export) under multiple replicas
  remain unsupported and need a single-replica or replica-aware refactor
  (§8).
- pause/resume/warm pool do not apply to webui pods; in the gunicorn shape,
  normal shutdown cleans up the heartbeat file via the `worker_exit` hook
  (dev/non-gunicorn shapes fall back to atexit; SIGKILL cannot be covered —
  a leftover heartbeat expires after at most one freshness window), the
  instance side still exports on best effort at normal shutdown, and
  abnormal exits are caught by the orphan reconcile at the next startup.

## 7. Consumer Usage

Query capabilities:

```bash
curl -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities
```

- The endpoint is a read-only construction: it does not create the WebUI
  manager (so it mints no token secret and starts no cleanup greenlet); when
  no manager exists yet, the snapshot is derived from the on-disk
  configuration and cannot verify the launch path (this limitation shows up
  as `launch_path_degraded` being possible only while a manager is active).
- Authentication: any authenticated user (session cookie or Bearer). The
  contract carries no secrets; WebUI-token iframe callers are outside this
  endpoint's service scope (iframe flows use their own per-resource tokens).

Start a workspace with an isolation requirement (insufficient capability
yields a structured 400 instead of a silent weak start):

```bash
curl -H "Authorization: Bearer <token>" \
  "https://<open-ace>/api/workspace/user-url?required_isolation=os_user"
```

**The isolation requirement is server-side** (config.json
`workspace.required_isolation_level`): multi-user install shapes
**explicitly write `os_user`** — generated by docker-entrypoint on first
start (overridable via the `WORKSPACE_REQUIRED_ISOLATION_LEVEL` environment
variable); the package installer pins only **after the `openace-webui-launch`
wrapper is actually installed successfully** (reusing the sudoers rule's
executability predicate `[ -x /usr/local/bin/openace-webui-launch ]`), and
**does not pin** when the wrapper is missing, emitting a clear installation
warning — avoiding deployments that "pinned `os_user` yet have no wrapper"
and 400 across the board. When not explicitly configured, it is **derived**
from the level actually verified in the contract snapshot. **Honest
declaration**: the derived value is a mirror, not a floor — when the launch
path degrades through operational actions (rerunning the installer clobbers
the wrapper, `webui_path` is repointed to a dev checkout, sudo is removed),
the derived value follows it down to `none`, and the default path keeps the
old behavior (shared-account launch) instead of erroring. Both directions
log a WARNING and are visible in the response's `isolation.reasons`:

- **Derived-path** degradation: keeps serving with weaker isolation, with a
  WARNING noting that the default launch is no longer per-user isolated;
- **Pinned deployment** degradation: the floor exceeds what the host can
  verify, every launch is rejected by `isolation_level_unsupported`, and a
  WARNING notes the all-lines REJECT until the launch path is fixed — the
  rejection never happens silently.

For a true hard floor, keep/set an explicit `required_isolation_level`. The
`required_isolation` request parameter can only **raise** the requirement,
never lower it; empty/blank values are treated as absent. The background
prestart at login and workspace directory provisioning go through the same
evaluation — launches/provisioning the gate would reject simply do not
happen.

- Success: the response carries `url`/`token`/`system_account` and
  `isolation` (a snapshot of the deployment's verified posture; the
  server-side floor guarantees the default path also passes the gate, so the
  echo matches the actual launch).
- Failure: `success:false` + `error_code` (§3.2) + `reasons` (the
  deployment-level and request-level namespaces coexist) + `isolation`.
- Under single-user mode (floor none), calls without parameters keep the
  existing behavior.
- **Behavior change**: in multi-user mode, a user with an empty
  `system_account` gets `identity_mapping_missing` 400 even on the default
  path (no parameters) — login no longer auto-fills the username convention;
  an administrator must set the mapping explicitly; a cached instance whose
  launch account no longer matches the current mapping restarts
  automatically onto the new account.

Probe trade-off note: `supports_per_user_launch`/
`per_user_launch_readiness` resolve the WebUI executable **probe-only**
(never triggering an npm build); readiness results (including the degraded
state) are memoized in-process in both directions with a 30-second TTL, and
a successful resolution is additionally cached permanently. The real
precondition of the sudo path is that the `openace-webui-launch` wrapper is
installed and executable (the sudoers rule itself cannot be verified
cheaply); target system accounts reject uid 0 and the reserved range
(`uid<1000`).

## 8. Known Gaps and Road Ahead

The gaps below were confirmed during the audit and, per issue #3374's
direction, split into separate follow-up work; this contract's `entry_points`
matrix reflects them honestly:

1. Bind terminal/VSCode tokens to users: `terminal/<id>/status` must not
   return the browser token to other authorized users on the same machine;
   attach validates the session owner; the VSCode owner becomes the
   requester.
2. Server-side validation of the terminal `work_dir` (aligned with fs.py
   base_dirs).
3. Add the home-subtree lock to `/fs/browse` and `/fs/check-path`.
4. Harden against reissued tokens being invalidated on restart when the
   WebUI `token_secret` is not persisted.
5. The interactive workspace `sandboxed` level: delivered by #3378 (§6);
   remaining follow-ups: real-cluster end-to-end acceptance (#3379,
   including port-3100 endpoint reachability verification), sandbox wiring
   of the `terminal`/`vscode`/`fs` entries, webui history quota/GC, and
   token validation/instance management under multiple web replicas
   (reconcile is already heartbeat-mutual-exclusive, but pod ownership is
   still single-process in-memory state).
6. Real Linux end-to-end isolation acceptance for multi-user mode
   (concurrent users + a cross-privilege attempt matrix).

## Appendix: Policy revision history

Changes in `POLICY_REVISION` 2026-09-12.1: the `sandboxed` level and zero-pod
probing entered the vocabulary; the `kernel` dimension was added (`os_user`
honestly declares unsupported — shared host kernel); the user-url gate gained
a sandboxed branch (rejecting by probe reason instead of going through the
OS account chain).

Changes in `POLICY_REVISION` 2026-09-12.2 (all changes):

- **T-B gate semantics**: the `required_isolation` floor is a floor, not a
  target — effective requirement = max(pin floor, request parameter), launch
  shape = the **strongest verified shape** meeting that requirement
  (`_resolve_form` derives from the same capability snapshot). Hence a
  deployment with `webui_image` configured serves a bare `/user-url` in the
  sandbox shape by default even if the pin is only `os_user`; deployments
  without `webui_image` behave exactly as before (os_user chain; an explicit
  `os_user` request still requires a system_account mapping); an explicit
  `sandboxed` request keeps the fail-closed shape (probe rejection +
  launcher double-check, never silently degrading to local).
- **Probe reason vocabulary changes**: added
  `sandbox_multi_process_unsupported` (when another web process with a fresh
  heartbeat exists, or the heartbeat root is unreadable so sole-survivor
  status cannot be proven, declaring sandboxed is refused — the
  per-instance secret and instance management are in-process memory state);
  removed `sandbox_proxy_token_ttl_too_short` (a short TTL is not a
  deployment defect: pod lifetime is clamped to before LLM credential expiry
  at create and at every renew, and raising the TTL would incidentally
  lengthen the local webui credential lifetime).
- **T-M sidecar-only egress upgrade**: network_egress is upgraded to
  enforced only on an actual read of a sidecar attestation tier's `/policy`;
  the cluster-level deny-default control of gVisor/CNI tiers is negative
  verification (proving the reject path exists, not that the allow path
  works), staying unsupported +
  `sandbox_runtime_egress_negative_only`.
- **T-L memo failure revocation + 1h TTL**: the boot-probe memo is no longer
  write-only — a failed same-tier pod probe revokes that tier's upgrade
  (latest evidence wins); memo entries carry timestamps and expire back to
  unverified after 1h. The fallback conditions are therefore "control-plane
  restart, failed same-tier probe, or an entry older than 1h" — one of the
  three, no longer restart alone.

---

## 中文

Open ACE 为本地交互工作区(聊天 WebUI、文件接口、会话历史)提供**版本化的多用户
隔离能力契约**。管理员与可信接入方应通过 API 查询实际生效的能力,而不是依赖
README 声明或客户端传入的 capability 布尔值。本文档说明契约语义、部署要求、
reason code 对照与已知缺口。关联 issue:#3374(os_user)、#3378(sandboxed)。

## 1. 能力契约字段

查询端点:`GET /api/workspace/isolation-capabilities`(需要登录会话)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "qwen-code-webui-per-user | qwen-code-webui-shared | opensandbox:<tier>",
  "isolation_level": "none | os_user | sandboxed",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress", "kernel"],
  "reasons": [{"code": "...", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract"
  },
  "policy_revision": "2026-09-12.2"
}
```

- `local_workspace_multi_user`:本地交互工作区多用户隔离是否受支持。
- `backend`:实际运行器——`qwen-code-webui-per-user`(每用户独立 WebUI 进程)、
  `qwen-code-webui-shared`(共享单实例)或 `opensandbox:<tier>`(#3378:sandboxed
  等级,WebUI 运行于该 tier 的 OpenSandbox pod)。
- `isolation_level` / `enforced` / `unsupported`:见下节。
- `reasons`:unsupported 时的机器可读原因(可能为空)。
- `entry_points`:各入口的隔离覆盖状态(§4)。**仅在 supported 快照中输出**——
  入口矩阵描述的是多用户隔离的覆盖面,unsupported(单用户/未验证/降级)部署
  不携带该矩阵,避免"无隔离"与"webui: enforced"同帧自相矛盾。该矩阵为静态
  审计结论,随 `policy_revision` 版本化;各入口与代码的 conformance 绑定为
  后续工作(§7)。
- `policy_revision`:契约语义版本;推导逻辑或入口矩阵变化时递增。

## 2. 隔离等级语义

等级序:`none < os_user < sandboxed`(`required_isolation` 请求参数与
`workspace.required_isolation_level` 下限均按此序比较,只能抬高不能降低)。

| 等级 | 语义 |
|---|---|
| `none` | 所有本地交互会话共享同一服务账户运行,无用户间文件/环境/进程隔离(单用户轻量模式,by design) |
| `os_user` | 每用户独立系统账户与 HOME/TMP/XDG;WebUI 进程以该用户 UID 经 `sudo -u` 启动;子进程环境为显式白名单(真实模型 API key 永不进入,以代理 token 替代);文件接口应用层 home 子树锁 + OS 权限 |
| `sandboxed` | 每用户 WebUI 进程运行于 OpenSandbox pod(#3378):每实例独立 pod、digest-pinned 且在 image_allowlist 的专属 webui 镜像、deny-default 出口、恒有资源边界;身份为 per-instance token secret(非 OS 账户,实例销毁即失效);浏览器经控制面本地端口代理访问。部署要求与诚实边界见 §6 |

**边界声明**:`os_user` 共享宿主内核,没有命名空间隔离、没有网络出口策略——
"分目录/更换 HOME"不构成强运行时隔离。`resources`、`network_egress` 与
`kernel` 三个维度对 `os_user` 始终列在 `unsupported`(交互 WebUI 无 per-task
cgroup,仅实例数上限与空闲清理;无出口策略;共享宿主内核)。`sandboxed`
等级的维度语义与验证边界见 §6.1。autonomous 任务的资源与沙箱策略沿用独立的
#2022 sandbox 契约(`sandbox_effective_policy`),见 `../dev/SANDBOX_BACKENDS.md`。

**平台边界**:仅 Linux 部署可申报 `os_user`。macOS 跳过系统用户创建,Open ACE
无法建立/验证身份映射;Windows 强制单实例。两者契约均为
`unsupported/platform_unsupported`。

## 3. Reason Code 对照

三套代码,职责不同:

### 3.1 契约 `reasons[]`(部署级:为什么整体 unsupported)

| code | 含义 |
|---|---|
| `webui_disabled` | WebUI 管理器未启用,无交互工作区运行时 |
| `platform_unsupported` | 非 Linux 平台(Windows/macOS/其他) |
| `multi_user_mode_disabled` | 多用户模式未启用(单用户轻量模式,预期状态而非缺陷) |
| `launch_path_degraded` | WebUI 启动路径无法承载按用户实例(message 括注 §3.3 的具体原因,如 dev 目录模式/包装器缺失);契约与 /user-url 门闸看同一条路径,不会互相矛盾 |
| `launch_path_unverified` | 冷 worker 上 manager 尚未初始化、探针未运行:等级为 **provisional**(部署形态达标),manager 初始化后自动消除;按 revision 缓存/灰度的接入方应同时检查该码 |

### 3.2 门闸 `error_code`(请求级:`user-url?required_isolation=...` 的拒绝)

| code | 含义 |
|---|---|
| `invalid_isolation_level` | `required_isolation` 取值非法(合法值:none/os_user/sandboxed) |
| `isolation_level_unsupported` | 请求等级超过部署实际支持等级,拒绝以弱隔离静默启动 |
| `identity_mapping_missing` | 用户在数据库中无 `system_account` 映射;显式要求隔离时不做 username 静默回退(**仅 os_user 链**;sandboxed 的身份是 per-instance token,不查 OS 账户) |
| `per_user_launch_unavailable` | 启动路径无法以该用户 UID 运行(message 内嵌 §3.3 的原因码;**仅 os_user 链**) |

`sandboxed` 请求的门闸是能力探测本身:等级不满足时按 §3.4 的探测码
(优先)或 `isolation_level_unsupported` 拒绝,不产生
`identity_mapping_missing`/`per_user_launch_unavailable`。

易混对照:`launch_path_unverified`(部署级,"冷 worker 尚未探针,等级 provisional")
vs `identity_mapping_missing`(用户级,门闸拒绝"这个用户没有身份映射")。

### 3.3 探针原因码(嵌在 `per_user_launch_unavailable` 的 message 中,或作为部署级 `launch_path_degraded` 的括注)

| code | 含义 |
|---|---|
| `platform_unsupported` | 非 Linux/macOS 平台 |
| `webui_executable_missing` | 找不到 qwen-code-webui 可执行文件 |
| `current_user_unresolved` | 服务进程自身 UID 无法解析 |
| `dev_directory_mode_shared_account` | dev 目录模式以服务用户运行 node,不切换 UID |
| `launch_wrapper_missing` | `openace-webui-launch` 包装器未安装或不可执行(sudo 路径的真实前置) |
| `sudo_unavailable` | 无 sudo 二进制 |
| `privileged_system_account` | 目标系统账户 uid 为 0(如映射到 root) |
| `reserved_system_account` | 目标系统账户 uid < 1000(系统保留段) |

### 3.4 sandboxed 探测与运行期原因码(#3378)

`sandboxed` 等级的探测是**零 pod、配置面 fail-closed** 的(不创建 pod 即可
判定);以下前 8 个为探测级 reason(命中即拒绝),后 4 个为快照级(出现在契约
`reasons[]` 中、不阻止申报),最后 2 个为运行期错误码(启动器抛出,非探测码)。

| code | 级别 | 含义 | 修复动作 |
|---|---|---|---|
| `sandbox_backend_unconfigured` | 探测 | sandbox-backends.json 缺失、不可解析或未配置 | 提供/修复后端配置(见 `../dev/SANDBOX_BACKENDS.md` §3) |
| `sandbox_tier_missing` | 探测 | `workspace.sandbox_tier`(或后端 `default_tier`)在 `endpoints` 中无对应条目 | 修正 `sandbox_tier` 或在 `endpoints` 补齐该 tier |
| `webui_image_missing` | 探测 | 该 tier 未配置 `webui_image` | 配置含 qwen-code-webui 的镜像(参考构建:`scripts/docker/webui-sandbox.Dockerfile`) |
| `webui_image_not_pinned` | 探测 | `webui_image` 非 digest-pinned(`name@sha256:<64 hex>`) | 改用 digest 引用——tag 可被重指向,会架空白名单 |
| `webui_image_not_allowed` | 探测 | `webui_image` 不在 `image_allowlist` | 将镜像加入 `image_allowlist`,或换用已在列的镜像 |
| `sandbox_api_key_missing` | 探测 | 该 tier 的 `api_key_env` 指定的环境变量在本进程为空——创建 pod 的第一个 API 调用就会失败;契约与启动路径不得对同一台主机给出矛盾结论(#3375 原则) | 在运行 web 进程的环境中设置该 API key(sandbox-backends.json 的 `api_key_env` 字段) |
| `sandbox_multi_process_unsupported` | 探测 | 存在**另一个**持有新鲜心跳的 web 进程(心跳根不可读/不可判定时同样视为存在——无法证明本进程是独苗):per-instance token secret 与实例管理是进程内存态,跨副本时约 2/3 的 token 校验会随机 401;**出货的 k8s manifest(3 副本)默认即此形态,sandboxed 在多副本下自动回落到 os_user 链** | 单 web 进程部署(如 docker-compose 单副本)才可申报 sandboxed;多副本形态使用 os_user,或等待多副本实例管理支持(follow-up) |
| `sandbox_proxy_unreachable` | 探测 | `workspace.webui_callback_url` 未设置;或该 URL 在该 tier 出口策略下不可达(loopback;sidecar tier 不在 `egress_allow_hosts`;CNI tier 为私网/集群内地址) | 设置 `webui_callback_url`;sidecar tier 将控制面主机名加入 `egress_allow_hosts`;CNI tier 保证公网可达 |
| `sandbox_runtime_unverified` | 快照 | 静态视图:仅配置面验证通过;kernel/network_egress 待首个 pod boot probe 确认(memo 在控制面重启、同 tier probe 失败或条目超过 1h 后回退到该状态) | 无需修复;首次成功启动 pod 后自动升级 |
| `sandbox_launch_unverified` | 快照 | 冷 worker(manager 尚未初始化、沙箱启动链路未在本进程演练过):等级为 **provisional**,manager 初始化后自动消除(对齐 os_user 的 `launch_path_unverified` 先例) | 无需修复;首次工作区活动后消失 |
| `sandbox_runtime_kata_negative_only` | 快照 | 首 pod probe 通过,但 kernel 仅负向验证(Kata 只能排除 gVisor,无法与未隔离 runc 区分):kernel 保持 unsupported | 无需修复;换 gVisor tier 可获得 kernel 正向验证 |
| `sandbox_runtime_egress_negative_only` | 快照 | 首 pod probe 通过,但出口仅负向验证(gVisor/CNI tier 的集群级 deny-default 对照——证明拒绝路径存在,不能证明放行生效):network_egress 保持 unsupported;仅 sidecar tier 的 `/policy` 实读才升级(T-M) | 无需修复;需要申报 network_egress 时使用 sidecar attestation tier |
| `sandbox_create_failed` | 运行期 | create 请求被拒、create 失败或 boot probe 失败(probe 失败会立即销毁 pod) | 查看 message 内嵌原因(含 provider probe 码透传) |
| `sandbox_endpoint_unresolved` | 运行期 | pod 的 3100 端点经 `GET /sandboxes/{id}/endpoints/3100` 解析失败——网关无法为未声明端口应答(外部假设,集群端验证归 #3379) | 检查网关对未声明端口的应答行为;该通路未经真实集群验证 |

## 4. 入口覆盖矩阵

| 入口 | 状态 | 说明 |
|---|---|---|
| webui(聊天工具) | enforced | 每用户独立实例/UID/端口/token;停止与 token 撤销按 user_id 隔离 |
| filesystem_api | partial | 上传/下载/删除有 home 子树锁(#1813);browse/check-path 依赖 OS 权限作用域 |
| session_history | enforced | 应用层所有权闸门 + 租户 fail-closed |
| terminal | partial | 终端令牌按机器授权,不绑定终端会话所有者(已知缺口) |
| vscode | partial | owner 记录为 machine.created_by;project_path 校验弱(已知缺口) |
| autonomous | separate_contract | 沿用 #2022 sandbox 契约,不在本契约范围 |

**sandboxed 部署下的矩阵取值(#3378,revision 2026-09-12.2 起)**:矩阵按等级
输出——`webui` 随 `sandboxed` 等级进入 pod(`enforced`);`terminal`/`vscode`/
`filesystem_api` 输出 **`sandboxed_entry_not_wired`**(执行体仍在控制面宿主上,
未接线到用户的沙箱实例),对 sandboxed 用户的 `/fs` host 树亦不可用——其文件在
pod 内,由 webui 自带的 in-pod 文件浏览承载;`session_history` 仍 `enforced`
(per-pod 快照存储);`autonomous` 仍 `separate_contract`。os_user/none 快照的
矩阵取值不变。这些入口的既有缺口(§8)不受隔离等级影响。

## 5. 多用户模式部署要求(policy revision 2026-09-11.2)

契约是否报告 `supported/os_user` 由**启动路径就绪探针**判定(§3.3:WebUI 解析、
非 dev 目录模式、`openace-webui-launch` 包装器、sudo),不再以 Docker 布局为
先决条件——包安装形态(scripts/install-central/package-method,`sudo -u` 与
wrapper 齐备)同样可以验证并强制 os_user。

两种参考部署:

1. **Docker 多用户**:`docker-compose.multi-user.yml` 叠加(root + `OPENACE_ALLOW_ROOT_MULTI_USER=1` + `WORKSPACE_BASE_DIR=/workspace` + `WORKSPACE_MULTI_USER_MODE=true`),镜像内置 useradd 与 wrapper,系统账户自动供给;
2. **包安装多用户**:installer 的 `_WS_MULTI_USER` 路径(非 root 运行 + `/home` 布局),探针健康即报告 os_user;每用户系统账户需已存在(getpwnam 可解析,uid ≥ 1000)。

共同要求:sudo/sudoers 中受限的 `openace-webui-launch` 包装器;无内核特殊要求
(os_user 等级即 OS 账户边界);生产基线密钥见 compose 注释。

探针降级(dev 目录模式/缺 wrapper 等)时契约如实返回 `launch_path_degraded`
unsupported——**不会**静默降级到共享账户后宣称支持;该形态下默认启动路径保持
既有行为,显式 `required_isolation=os_user` 得到结构化拒绝。

## 6. sandboxed 等级部署要求与诚实声明(policy revision 2026-09-12.2)

`sandboxed` = WebUI 进程运行于 OpenSandbox pod:每用户每实例一个独立 pod、
digest-pinned 且在 image_allowlist 的专属 webui 镜像、deny-default 出口;浏览器
经控制面本地端口代理访问(端口形态,无路径重写假设);身份为 per-instance
token secret(实例销毁即失效,无 OS 账户/sudo 链)。探测**不检查平台与
multi_user_mode**——pod 在远端集群,单用户 + sandboxed 是合法的加固形态。

### 6.1 维度表

| 维度 | sandboxed 静态(配置面探测通过后) | sandboxed 首 pod probe 后 | os_user(对照) |
|---|---|---|---|
| identity | enforced | enforced | enforced |
| filesystem | enforced | enforced | enforced |
| environment | enforced | enforced | enforced |
| process | enforced | enforced | enforced |
| resources | enforced(create body 恒携带 resourceLimits,默认 4Gi/2CPU 亦为边界) | enforced | unsupported |
| kernel | unsupported(`sandbox_runtime_unverified`) | gVisor:enforced(`/proc/version` 正向识别);Kata:保持 unsupported(`sandbox_runtime_kata_negative_only`,仅负向) | unsupported(共享宿主内核) |
| network_egress | unsupported(同 kernel reason) | **仅 sidecar attestation tier**:enforced(egress sidecar `/policy` 实读);gVisor/CNI tier:保持 unsupported(`sandbox_runtime_egress_negative_only`——CNI 默认拒绝只是负向对照,证明拒绝路径存在,不能证明放行真的生效) | unsupported |

静态 enforced 五维的依据是**配置事实**(独立 pod、镜像白名单、恒有资源边界),
不是 per-pod 验证;kernel/network_egress 只有 per-pod boot probe 能证。probe
结果为进程内 memo——**控制面重启、同 tier probe 失败、或条目超过 1h 后回退
unverified**,这是有意的诚实契约(T-L:失败即撤销、条目带 1h 时效——最新
证据优先,长驻进程不依赖数小时前的探测结论)。

### 6.2 部署要求

1. `endpoints.<tier>.webui_image`:digest-pinned 且出现在 `image_allowlist`。
   校验发生在能力探测而非配置 parse——坏值只降级 sandboxed 等级,不影响
   autonomous 任务共用的后端配置。参考构建:`scripts/docker/webui-sandbox.Dockerfile`
   (node + qwen-code-webui + 非 root uid 1000,监听 0.0.0.0:3100);生产镜像
   必须自行 digest-pin 并入白名单。
2. `workspace.webui_callback_url` **必须设置**:探测以它为 LLM proxy 回连 URL
   (每请求 host 只在启动时可知,静态探测需要稳定 URL)。sidecar tier 须将
   控制面主机名加入该 tier 的 `egress_allow_hosts`;CNI tier 须公网可达
   (loopback/私网/集群内地址会被拒)。
3. proxy token TTL **无部署前置**(T-F):pod 寿命由启动器钳制——create 时
   `min(WebUI token TTL, 生效 proxy token TTL)`,每次 renew 再钳到
   `min(now + WebUI token TTL, token 过期时刻)`(UTC;token 不可解码/为空时
   钳到 now,即随失效凭证一起终止)。短 TTL 只缩短 pod 寿命,不会让 pod 活过
   其 LLM 凭证;不需要为申报 sandboxed 抬高
   `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES`(抬高会连带拉长本地 webui 凭据
   寿命)。
4. HTTPS:沿用外部反代的端口段映射(`/webui/<port>`,见 `../guide/NGINX.md`)
   ——sandboxed 形态的浏览器端口是本地代理端口,取自同一 port_range
   (默认 3100-3200);单用户 + sandboxed 以代理端口分配替代硬编码 3100。
5. `workspace.sandbox_tier` 可选:指定交互 pod 落在哪个 endpoint tier,缺省用
   后端 `default_tier`。**pin 是下限,不是目标**(与 #3375 的 floor 定义一致):
   生效要求 = max(pin 下限, 请求参数),启动形态 = 满足该要求的**最强已验证
   形态**——因此 `webui_image` 配置(探测通过、能力为 sandboxed)后,不带参数的
   `/user-url` 也走沙箱形态,即使 pin(如 docker-entrypoint 默认写入的
   `os_user`)更低;此时 os_user 链(身份映射、sudo 启动)不适用,门闸按
   sandboxed 分支放行。未配置 `webui_image` 的部署行为完全不变(os_user 链,
   显式 `os_user` 请求照旧要求 system_account 映射)。
6. webui 入口与 pod 3100 端点可达性**未经真实集群端到端验证**(#3379;运行期
   失败以 `sandbox_endpoint_unresolved` 结构化浮出,不会静默挂死)。

### 6.3 TTL 链

| 环节 | 数值 | 来源 |
|---|---|---|
| WebUI token TTL | 24h(默认) | `OPENACE_WEBUI_TOKEN_TTL_SECONDS`(86400) |
| pod create timeout | min(WebUI token TTL, 生效 proxy token TTL) | 启动器 launch 时计算 |
| pod renew 目标 | min(now(UTC) + WebUI token TTL, proxy token 过期时刻)——pod 至多活到其 LLM 凭证失效;token 不可解码/为空 → 钳到 now(fail-closed,不再每轮前移一个 fallback TTL);钳制点之后由空闲回收正常拆除,下次 `/user-url` 重建 + 快照恢复 | 启动器 renew 钳制 |
| 空闲回收 | 30min(默认) | `workspace.idle_timeout_minutes` |
| 周期快照导出 + renew | 默认 5min,实际随 cleanup 间隔:维护检查搭载在 cleanup 循环里,`workspace.cleanup_interval_minutes`(默认 5)> 5min 时实际导出间隔 = cleanup 间隔 | `SANDBOX_MAINTENANCE_INTERVAL_SECONDS` = 300(下限)+ cleanup 循环 sleep |
| 进程心跳刷新 | 恒定 300s——独立定时器 greenlet(worker 启动钩子随 reconcile 同一 env 门控拉起),**不随 cleanup 间隔伸缩**;新鲜窗口 = 2×300+60 = 660s | `webui_sandbox.HEARTBEAT_REFRESH_SECONDS`;窗口 `HEARTBEAT_FRESH_WINDOW_SECONDS` |
| 默认 proxy token TTL | 240min(无需为 sandboxed 抬高;pod TTL 被钳到它之下) | `DEFAULT_PROXY_TOKEN_TTL_MINUTES`,env `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES` |

token 随每次 `/user-url` 命中以 per-instance secret 重铸;健康检查用现行 token
经本地代理探测(通过即证明 代理→网关→pod→webui→token 校验 全链)。

### 6.4 诚实声明

- **配置面探测 ≠ per-pod 验证**:静态 enforced 五维依据配置事实;kernel/
  network_egress 待首个 pod boot probe;**控制面重启后回退 unverified**;
  probe 失败即撤销该 tier memo,条目 1h 过期(T-L)。
- **Kata 的 kernel 验证仅负向**(只能排除 gVisor,不能与未隔离 runc 区分):
  kernel 保持 unsupported + `sandbox_runtime_kata_negative_only`;gVisor 正向
  识别才升级 enforced。
- **gVisor/CNI tier 的出口验证仅负向**(集群级 deny-default 对照:证明拒绝
  路径存在,不能证明放行真的生效;config 层已禁止 gVisor tier 申报 sidecar):
  network_egress 保持 unsupported + `sandbox_runtime_egress_negative_only`;
  仅 sidecar attestation tier 的 `/policy` 实读升级 enforced(T-M)。
- **会话历史**:整目录 tar 快照(存于独立根,键 `webui-<user_id>.tar`);
  控制面 crash 丢失 ≤ 一轮导出间隔的增量(默认 5min;导出搭载 cleanup 循环,
  `cleanup_interval_minutes` 拉长时丢失窗口随之拉长);**降级启动(restore 门
  60s 超时、恢复未确认、或快照不可读)的实例不导出**——宁可保住旧快照,也
  不让空树覆盖完好快照;不可读快照的降级启动进一步**不写控制面确认记录**
  (reconcile 与导出守卫因此都不会认它的空树);记录仅在删除**确认成功**后
  清除,删除失败保留至下轮重试;快照上界默认
  16MiB(`OPENACE_WEBUI_STATE_MAX_BYTES` 可调),越限跳过导出——该用户历史
  **冻结在最后一份完好快照**,直到手工清理;**无自动 GC**。
- **token 401 语义(N8)**:沙箱实例停止后,其已发 token 在 CP URL-token 路径
  401(per-instance secret 随实例销毁)。
- **入口接线范围**:terminal/vscode/fs 未接线到沙箱形态
  (`sandboxed_entry_not_wired`);`/fs` host 树对 sandboxed 用户不可用(文件
  在 pod 内,webui 自带 in-pod 文件浏览)。
- **外部假设**:网关凭据注入头由 pod 内自捕获的代理端到端持有——"代理为哑
  管道、token 在 pod 内 webui 校验"包含对 webui 行为的假设;3100 端点可达性
  与 entrypoint 上游约束为外部假设,真实集群验证归 #3379。
- **多副本与孤儿回收(T-D,round 2 修订)**:孤儿回收按进程 generation 判别,
  且销毁前检查控制面心跳文件
  (`<CONFIG_DIR>/webui-agent-state/webui-heartbeat-<process-generation>.json`,
  每 web 进程一个;文件名与自我排除均按**每进程 uuid4 generation**——同节点
  容器共享宿主 boot_id 且 pid 常碰撞,`(boot_id, pid)` 身份会让单节点
  k3s/kind 副本互认为"自己");刷新由**独立 300s 定时器**承担(恒定周期,
  与 cleanup 间隔解耦,无 manager 实例的副本也会刷新);进程退出时删除自己的
  心跳文件(gunicorn `worker_exit` 钩子 + atexit,幂等 fail-soft)——**单进程
  部署的重启窗口因此趋零**,但 SIGKILL/崩溃路径无法清理,残留心跳至多一个
  新鲜窗口(≤ 660s)后自然过期,期间 sandboxed 申报被拒并回落 os_user 链。
  存在其它新鲜心跳时跳过清扫。**reconcile 互杀已消除**(3 副本/滚动重叠下
  新副本不再清扫其它副本的在线 pod)。**心跳读取 fail-closed**:心跳根或
  心跳文件不可读 = "无法证明独苗",按"存在 peer"处理(探测拒绝 sandboxed、
  清扫跳过),写入侧仍 fail-soft。**但 pod 归属仍是单进程内存态**——多副本下
  的 token 校验与实例管理(回收、快照导出)仍不支持,需要单副本或多副本感知
  的重构(§8)。
- pause/resume/warm pool 不适用于 webui pod;gunicorn 形态正常停机经
  `worker_exit` 钩子清理心跳文件(dev/非 gunicorn 形态由 atexit 兜底;
  SIGKILL 无法覆盖——残留心跳至多一个新鲜窗口后过期),实例侧正常停机仍尽力
  导出,异常退出由下次启动的孤儿 reconcile 兜底。

## 7. 接入方用法

查询能力:

```bash
curl -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities
```

- 端点为只读构造:不创建 WebUI manager(即不铸造 token secret、不启动清理
  greenlet);manager 尚未存在时快照按磁盘配置推导,无法验证启动路径(该限制
  体现在 `launch_path_degraded` 只在 manager 活跃时可能出现)。
- 鉴权:任意已认证用户(session cookie 或 Bearer)。契约不含机密;WebUI-token
  iframe 调用方不在此端点服务范围内(iframe 流程使用各自的 per-resource token)。

带隔离要求启动工作区(能力不足时得到结构化 400,而非静默弱启动):

```bash
curl -H "Authorization: Bearer <token>" \
  "https://<open-ace>/api/workspace/user-url?required_isolation=os_user"
```

**隔离要求是服务端的**(config.json `workspace.required_isolation_level`):
多用户安装形态会**显式写入 `os_user`**——docker-entrypoint 首启生成
(`WORKSPACE_REQUIRED_ISOLATION_LEVEL` 环境变量可覆盖);package installer
则在 `openace-webui-launch` wrapper **实际安装成功之后**才 pin(复用 sudoers
规则的可执行判据 `[ -x /usr/local/bin/openace-webui-launch ]`),wrapper 缺失时
**不 pin**并给出明确安装 warning——避免装出「pin 了 `os_user` 却没有 wrapper」
的全线 400 部署。未显式配置时从契约快照实际验证到的等级**派生**。
**诚实声明**:派生值是一面镜子而非下限——启动路径因运维动作退化(重跑
installer 冲掉 wrapper、`webui_path` 改指 dev checkout、sudo 被移除)时,
派生值会跟着降到 `none`,默认路径保持旧行为(共享账户启动)而非报错。
两个方向都会打 WARNING 日志,并在响应的 `isolation.reasons` 中可见:

- **派生路径**降级:继续以弱隔离服务,WARNING 提示默认启动不再按用户隔离;
- **pin 过的部署**降级:下限高于宿主可验证的能力,所有启动被
  `isolation_level_unsupported` 拒绝,WARNING 提示修复启动路径前全线 REJECT
  ——拒绝不会静默发生。

需要真正的硬下限,请保留/设置显式
`required_isolation_level`。`required_isolation` 请求参数**只能抬高**要求,不能
降低;空值/空白参数视为缺省。登录时的后台预启动(prestart)与工作区目录供给
走同一评估——门闸会拒绝的启动/供给不会发生。

- 成功:响应含 `url`/`token`/`system_account` 与 `isolation`(部署已验证姿态
  快照;服务端下限保证默认路径同样经过门闸,回显与实际启动一致)。
- 失败:`success:false` + `error_code`(§3.2)+ `reasons`(部署级与请求级两个
  命名空间并存)+ `isolation`。
- 单用户模式(下限 none)下不带参数的调用保持既有行为。
- **行为变化**:多用户模式下 `system_account` 为空的用户,默认路径(无参数)
  也会得到 `identity_mapping_missing` 400——登录不再自动回填 username 约定,
  需管理员显式设置映射;缓存实例的启动账户与当前映射不符时会自动重启到新账户。

探针取舍说明:`supports_per_user_launch`/`per_user_launch_readiness` 以
**probe-only** 方式解析 WebUI 可执行文件(绝不触发 npm build);就绪结果
(含降级态)在进程内按 30 秒 TTL 双向记忆化,成功解析额外永久缓存。sudo
路径的真实前置是 `openace-webui-launch` 包装器已安装且可执行(sudoers 规则
本身无法廉价验证);目标系统账户拒绝 uid 0 与保留段(`uid<1000`)。

## 8. 已知缺口与后续路线

以下缺口已在审计中确认,按 issue #3374 的指示拆分为独立后续工作,本契约的
`entry_points` 矩阵如实反映:

1. 终端/VSCode 令牌与用户绑定:`terminal/<id>/status` 不向同机器其他授权用户返回
   browser token;attach 校验会话所有者;VSCode owner 改为请求者。
2. 终端 `work_dir` 服务端校验(对齐 fs.py base_dirs)。
3. `/fs/browse`、`/fs/check-path` 补 home 子树锁。
4. WebUI `token_secret` 未持久化时重启导致已发 token 失效的加固。
5. 交互工作区 `sandboxed` 等级:#3378 已交付(§6);遗留 follow-up:真实集群
   端到端验收(#3379,含 3100 端点可达性验证)、`terminal`/`vscode`/`fs` 入口
   沙箱接线、webui 历史 quota/GC、多 web 副本下的 token 校验/实例管理
   (reconcile 已心跳互斥,但 pod 归属仍是单进程内存态)。
6. 多用户模式真实 Linux 端到端隔离验收(并发用户 + 越权尝试矩阵)。

## 附录：策略版本历史

`POLICY_REVISION` 2026-09-12.1 的变更:`sandboxed` 等级与零 pod 探测入词表;
新增 `kernel` 维度(`os_user` 如实申报 unsupported——共享宿主内核);user-url
门闸增加 sandboxed 分支(按探测 reason 拒绝,不再走 OS 账户链)。

`POLICY_REVISION` 2026-09-12.2 的变更(全部变化):

- **T-B 闸门语义**:`required_isolation` 下限是 floor 不是目标——生效要求 =
  max(pin 下限, 请求参数),启动形态 = 满足该要求的**最强已验证形态**
  (`_resolve_form` 从同一能力快照推导)。因此配置了 `webui_image` 的部署,
  不带参数的 `/user-url` 默认也走沙箱形态,即使 pin 仅为 `os_user`;未配置
  `webui_image` 的部署行为完全不变(os_user 链,显式 `os_user` 请求照旧要求
  system_account 映射);显式 `sandboxed` 请求保持 fail-closed 形状(探测
  拒绝 + 启动器二次校验,不会静默降级本地)。
- **探测 reason 词表变化**:新增 `sandbox_multi_process_unsupported`(存在
  另一个持有新鲜心跳的 web 进程,或心跳根不可读无法证明独苗时,拒绝申报
  sandboxed——per-instance secret 与实例管理是进程内存态);移除
  `sandbox_proxy_token_ttl_too_short`(短 TTL 不是部署缺陷:pod 寿命在
  create 与每次 renew 时被钳制到 LLM 凭证失效之前,抬高 TTL 反而连带拉长
  本地 webui 凭据寿命)。
- **T-M 仅 sidecar 升级 egress**:network_egress 只在 sidecar attestation
  tier 的 `/policy` 实读时升级 enforced;gVisor/CNI tier 的集群级
  deny-default 对照是负向验证(证明拒绝路径存在,不能证明放行生效),保持
  unsupported + `sandbox_runtime_egress_negative_only`。
- **T-L memo 失败撤销 + 1h TTL**:boot-probe memo 不再只写不读——同 tier
  的 pod probe 失败即撤销该 tier 的升级(最新证据优先);memo 条目带
  时间戳,超过 1h 视为过期回退 unverified。回退条件因此是"控制面重启、
  同 tier probe 失败、或条目超过 1h"三者之一,不再仅重启。

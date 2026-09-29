# Workspace Isolation Capabilities — 工作区隔离能力

[English](#english) | [中文](#中文)

---

## English

Open ACE publishes a **versioned multi-user isolation capability contract** for local interactive
workspaces (chat WebUI, file API, session history). Admins and trusted integrators should query the
capability that is actually in force through the API, instead of relying on README claims or on
capability booleans supplied by a client.

This document covers the contract's semantics, deployment requirements, the reason codes and the
known gaps. Related issues: #3374 (os_user), #3378 (sandboxed), #3431 (confinement and local gVisor
container), #3438 (local Kata container).

This is the **reference**. To choose and configure an isolation mode, start with the admin guide
[WORKSPACE_ISOLATION](../guide/WORKSPACE_ISOLATION.md).

## 1. Contract fields

Endpoint: `GET /api/workspace/isolation-capabilities` (requires a signed-in session)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "shared | plain | bwrap | local-gvisor | local-kata | opensandbox:<tier>",
  "isolation_level": "none | os_user | sandboxed",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress", "kernel"],
  "reasons": [{"code": "...", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "enforced",
    "session_history": "enforced",
    "terminal": "remote_machine_scope",
    "vscode": "remote_machine_scope",
    "autonomous": "separate_contract"
  },
  "entry_point_details": {
    "filesystem_api": {
      "status": "enforced",
      "scope": "local_workspace",
      "covered_by_isolation_level": true,
      "operations": [
        {"name": "upload", "roots": ["home"],
         "symlink_policy": "never_followed_with_elevated_privilege"},
        {"name": "browse", "roots": ["home", "shared_projects"],
         "symlink_policy": "resolved_then_rejected_if_outside"}
      ],
      "access_control": ["home_subtree_lock", "shared_project_acl", "os_account_dac",
                         "nofollow_fd_descent"],
      "boundary": "...",
      "limitations": [],
      "residuals": [{"code": "shared_project_roots_are_cross_user_by_design", "message": "..."}]
    }
  },
  "policy_revision": "2026-09-26.3",
  "install_method": "package | docker | dev | unknown",
  "available_backends": {"local-kata": {"available": false, "reason": "install_method_docker"}, "...": {"available": true}}
}
```

- **`local_workspace_multi_user`:** whether multi-user isolation of local interactive workspaces is
  supported.
- **`backend`:** the runner that is actually in use.

  | value | meaning |
  |---|---|
  | `plain` | one WebUI process per user, as that user's OS account |
  | `bwrap` | #3431: as above, and each process runs in a systemd scope + bubblewrap (§5.1) |
  | `shared` | a single shared instance |
  | `local-gvisor` | #3431: sandboxed level, WebUI in a local gVisor container (§5.2) |
  | `local-kata` | #3438: sandboxed level, WebUI in a local Kata container (§5.3) |
  | `opensandbox:<tier>` | #3378: sandboxed level, WebUI in an OpenSandbox pod of that tier (§6) |

  These are the names of `workspace.isolation.backend` (#3446): the snapshot reports the backend the
  admin configured. A configured backend that is not ready is reported with `supported: false` and its
  reason, never replaced by another backend. The level reported is the one verified on this host.
- **`install_method` / `available_backends`:** how this deployment was installed
  (`OPENACE_INSTALL_METHOD`) and, for every backend, whether it could be used here at all
  (`install_method_docker` / `platform_unsupported` when not). Readiness of the configured backend is
  in `reasons`.
- **`isolation_level` / `enforced` / `unsupported`:** see §2.
- **`reasons`:** machine-readable causes when the deployment is unsupported (may be empty).
- **`entry_point_details`:** added in #3410, and present exactly when `entry_points` is. These are
  machine-readable details for each entry point. They let an integrator tell apart "meets the declared
  level", "governed by another mechanism" and "a real gap" without parsing prose. Fields:
  - `status`: identical to the same key in `entry_points` (pinned by a unit test);
  - `scope`: `local_workspace` / `remote_machine` / `separate_contract`;
  - `covered_by_isolation_level`: whether the snapshot's declared `isolation_level` covers this entry
    point;
  - `operations[]`: `{name, roots, symlink_policy}`, the reachable roots and symlink policy declared
    **per operation** (they differ within one entry point, see §4);
  - `access_control[]`: codes of the mechanisms that actually enforce the entry point;
  - `limitations[]`: **real gaps or uncovered scope; they take part in admission decisions**
    (always empty for `enforced` entry points);
  - `residuals[]`: known and accepted properties (e.g. shared project roots are cross-user by design);
    **they do not take part in admission decisions**.
- **Fail closed:** an unknown `status` or a `policy_revision` you have not reviewed **must be treated
  as a rejection**. The contract adds status words over time, and integrators must never treat an
  unknown value as a pass.
- **`entry_points`:** the isolation coverage of each entry point (§4).
  - It is **emitted only in supported snapshots**. The matrix describes multi-user coverage, so an
    unsupported deployment (single-user, unverified or degraded) omits it. That avoids "no isolation"
    and "webui: enforced" contradicting each other in the same response.
  - It is a static audit conclusion, versioned by `policy_revision`. Binding each entry point to its
    code (conformance) is follow-up work (§8).
- **`policy_revision`:** the contract's semantic version. It increments whenever the derivation logic
  or the entry-point matrix changes.

## 2. Isolation level semantics

The levels are ordered `none < os_user < sandboxed`. The `required_isolation` request parameter and
the floor, `workspace.isolation.level`, are both compared in this order; a request can only raise the
requirement, never lower it.

| level | meaning |
|---|---|
| `none` | All local interactive sessions run as one service account, with no file, environment or process isolation between users (the lightweight single-user mode, by design) |
| `os_user` | See the list below |
| `sandboxed` | The per-user WebUI runs in a sandbox with its own kernel boundary. It is either an OpenSandbox pod (#3378, §6) or a local container (#3431/#3438, §5.2 and §5.3). Deployment requirements and honest limits are in §5 and §6 |

An `os_user` deployment provides:
- a separate system account and separate HOME/TMP/XDG directories for each user;
- a WebUI process started as that user's UID through `sudo -u`;
- a child-process environment that is an explicit allowlist: the real model API key never enters it
  and is replaced by a proxy token;
- a file API protected by an application-level home-subtree lock plus OS permissions.

**Boundary statement:** `os_user` shares the host kernel and has no namespace isolation and no
network egress policy. "A separate directory and a different HOME" is not strong runtime isolation.
For plain `os_user`, the `resources`, `network_egress` and `kernel` dimensions are always
`unsupported`:
- `resources`: interactive WebUIs have no per-task cgroup, only an instance cap and idle cleanup;
- `network_egress`: there is no egress policy;
- `kernel`: the host kernel is shared.

The confinement mode (§5.1) adds `resources` and `network_egress`. For the dimension semantics and
verification limits of `sandboxed`, see §5.2, §5.3 and §6.1. Resource and sandbox policy for
autonomous tasks follows the separate #2022 sandbox contract (`sandbox_effective_policy`); see
[SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md).

**Platform boundary:** only Linux deployments can declare `os_user`. On macOS, Open ACE skips
system-user creation and cannot establish or verify the identity mapping. Windows is forced to a
single instance. Both report `unsupported/platform_unsupported`.

## 3. Reason codes

There are four sets of codes, each with a different job.

### 3.1 Contract `reasons[]` (deployment level: why the deployment as a whole is unsupported)

| code | meaning |
|---|---|
| `webui_disabled` | The WebUI manager is not enabled: there is no interactive workspace runtime |
| `platform_unsupported` | Not a Linux platform (Windows, macOS, other) |
| `isolation_backend_shared` | `workspace.isolation.backend` is `shared` (the lightweight single-user mode: an expected state, not a defect) |
| `launch_path_degraded` | The WebUI launch path cannot host per-user instances. The message names the specific §3.3 cause, e.g. dev-directory mode or a missing wrapper. The contract and the `/user-url` gate look at the same path, so they never contradict each other |
| `launch_path_unverified` | On a cold worker the manager is not initialized yet and the probe has not run. The level is **provisional** (the deployment shape qualifies) and the code clears once the manager initializes. Integrators that cache or roll out by revision should check this code too |

### 3.2 Gate `error_code` (request level: rejections of `user-url?required_isolation=...`)

| code | meaning |
|---|---|
| `invalid_isolation_level` | `required_isolation` has an invalid value (valid: none/os_user/sandboxed) |
| `isolation_level_unsupported` | The requested level exceeds what the deployment actually supports. The launch is refused rather than silently started with weaker isolation |
| `identity_mapping_missing` | The user has no `system_account` mapping in the database. When isolation is required explicitly, there is no silent fallback to the username (**os_user chain only**; the pod form's identity is a per-instance token and does not look up an OS account) |
| `per_user_launch_unavailable` | The launch path cannot run as this user's UID. The message embeds the §3.3 cause (**os_user chain only**) |

For a `sandboxed` request on the OpenSandbox pod form, the gate is the capability probe itself. When
the level is not met, the request is rejected with a §3.4 probe code (preferred) or with
`isolation_level_unsupported`. It never produces `identity_mapping_missing` or
`per_user_launch_unavailable`. The local container forms (§5.2, §5.3) keep the os_user identity chain.

Easily confused:
- `launch_path_unverified` is deployment-level: "a cold worker has not probed yet; the level is
  provisional".
- `identity_mapping_missing` is user-level: the gate refuses because "this user has no identity
  mapping".

### 3.3 Probe codes (embedded in the `per_user_launch_unavailable` message, or given in parentheses on a deployment-level `launch_path_degraded`)

| code | meaning |
|---|---|
| `platform_unsupported` | Not Linux or macOS |
| `webui_executable_missing` | The qwen-code-webui executable cannot be found |
| `current_user_unresolved` | The service process's own UID cannot be resolved |
| `dev_directory_mode_shared_account` | Dev-directory mode runs node as the service user without switching UID |
| `launch_wrapper_missing` | The `openace-webui-launch` wrapper is not installed or not executable (the real precondition of the sudo path) |
| `sudo_unavailable` | No sudo binary |
| `privileged_system_account` | The target system account has uid 0 (e.g. mapped to root) |
| `reserved_system_account` | The target system account has uid < 1000 (the reserved system range) |

The confined and local-container backends add their own `confinement_*` codes; see §5.1, §5.2 and §5.3.

### 3.4 sandboxed probe and runtime codes (#3378, opensandbox backend)

The `sandboxed` probe is **zero-pod and fail-closed on configuration**: it decides without creating a
pod. The codes fall into three groups:
- the first 8 are probe-level: a hit refuses the level;
- the next 4 are snapshot-level: they appear in the contract's `reasons[]` without blocking the
  declaration;
- the last 2 are runtime error codes, raised by the launcher rather than the probe.

| code | level | meaning | fix |
|---|---|---|---|
| `sandbox_backend_unconfigured` | probe | sandbox-backends.json is missing, unparsable or not configured | Provide or fix the backend config (see [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md) §3) |
| `sandbox_tier_missing` | probe | `workspace.isolation.tier` (or the backend's `default_tier`) has no entry in `endpoints` | Fix `isolation.tier`, or add that tier to `endpoints` |
| `webui_image_missing` | probe | The tier has no `webui_image` | Configure an image that contains qwen-code-webui (reference build: `scripts/docker/webui-sandbox.Dockerfile`) |
| `webui_image_not_pinned` | probe | `webui_image` is not digest-pinned (`name@sha256:<64 hex>`) | Use a digest reference: a tag can be re-pointed, which defeats the allowlist |
| `webui_image_not_allowed` | probe | `webui_image` is not in `image_allowlist` | Add the image to `image_allowlist`, or use an image already on it |
| `sandbox_api_key_missing` | probe | The environment variable named by the tier's `api_key_env` is empty in this process, so the first pod-creating API call would fail. The contract and the launch path must not disagree about the same host (the #3375 principle) | Set that API key in the environment of the web process (`api_key_env` in sandbox-backends.json) |
| `sandbox_multi_process_unsupported` | probe | **Another** web process holds a fresh heartbeat (see below) | Only a single-web-process deployment (e.g. docker-compose with one replica) can declare sandboxed. Use os_user for multi-replica deployments, or wait for multi-replica instance management (follow-up) |
| `sandbox_proxy_unreachable` | probe | `workspace.webui_callback_url` is not set, or that URL is unreachable under the tier's egress policy: a loopback address, a sidecar tier where it is not in `egress_allow_hosts`, or a CNI tier where it is a private or in-cluster address | Set `webui_callback_url`. On a sidecar tier, add the control plane's host name to `egress_allow_hosts`. On a CNI tier, make sure it is publicly reachable |
| `sandbox_runtime_unverified` | snapshot | Static view: only the configuration has been verified; kernel/network_egress await the first pod boot probe. The memo falls back to this state after a control-plane restart, a failed probe on the same tier, or when an entry is older than 1 h | Nothing to fix; it upgrades automatically after the first pod starts successfully |
| `sandbox_launch_unverified` | snapshot | Cold worker: the manager is not initialized and the sandbox launch chain has not run in this process. The level is **provisional** and the code clears once the manager initializes (like `launch_path_unverified` for os_user) | Nothing to fix; it disappears after the first workspace activity |
| `sandbox_runtime_kata_negative_only` | snapshot | The first pod probe passed, but the kernel was only verified negatively: in a pod, Kata can only be told apart from gVisor, not from unisolated runc. kernel stays unsupported | Nothing to fix; a gVisor tier gives positive kernel verification |
| `sandbox_runtime_egress_negative_only` | snapshot | The first pod probe passed, but egress was only verified negatively (see below). network_egress stays unsupported | Nothing to fix; use a sidecar attestation tier when network_egress must be declared |
| `sandbox_create_failed` | runtime | The create request was refused, creation failed, or the boot probe failed (a failed probe destroys the pod at once) | See the cause in the message (including passed-through provider probe codes) |
| `sandbox_endpoint_unresolved` | runtime | The pod's port-3100 endpoint could not be resolved through `GET /sandboxes/{id}/endpoints/3100`: the gateway cannot answer for an undeclared port. This is an external assumption; cluster-side verification belongs to #3379 | Check how the gateway answers for undeclared ports; this path has not been verified on a real cluster |

More detail on two of the codes:
- **`sandbox_multi_process_unsupported`:**
  - A heartbeat root that cannot be read or decided counts as another process: there is no proof that
    this process is the only one.
  - The per-instance token secret and instance management are in-process memory. Across replicas,
    about two thirds of token checks would randomly fail with 401.
  - **The shipped k8s manifest (3 replicas) has exactly this shape, so the `opensandbox` backend is
    reported unsupported there and launches are refused.**
- **`sandbox_runtime_egress_negative_only`:** gVisor and CNI tiers are checked against the
  cluster-level deny-default. That proves a refusal path exists, but not that allowed traffic really
  flows. Only a real read of `/policy` on a sidecar tier upgrades network_egress (T-M).

## 4. Entry-point coverage matrix

| entry point | status | scope | coverage and evidence |
|---|---|---|---|
| webui (chat tool) | enforced | local_workspace | A separate instance, UID, port and token per user. Stopping and token revocation are isolated by user_id |
| filesystem_api | enforced | local_workspace | See below |
| session_history | enforced | local_workspace | An application-level ownership gate, plus tenant fail-closed |
| terminal | remote_machine_scope | remote_machine | **A remote-machine capability.** This entry point allocates no local path, account or token. It is governed by the machine-assignment ACL, the session owner and the tenant (#3376). The local isolation level does not cover user isolation on the remote machine itself |
| vscode | remote_machine_scope | remote_machine | As above (code-server). owner = the requester (#3376 `VSCodeOwnerStore`); the proxy and WebSocket use the same gate |
| autonomous | separate_contract | separate_contract | Follows the #2022 sandbox contract; outside this contract |

**How `filesystem_api` is enforced:**
- **Declared roots per operation:** each of the eight `/api/fs` operations declares its roots and
  symlink policy (see the table below and `entry_point_details`).
- **realpath first:** every request path is resolved with realpath first, and anything outside the
  declared roots is refused.
- **Descent without following symlinks:** under a root process, upload, download, delete-file and
  search descend from the home root one component at a time, opening each directory with
  `O_NOFOLLOW` and working from the directory fd. They then write (`renameat`), read, delete
  (`unlinkat`) or walk (`fwalk`) relative to that fd. In the non-root package install they delegate
  to wrappers that probe, write and delete as the target account (#3410).
- **Other operations:** `create-directory` uses the same creatable set as check-path, and the
  single-file path lock follows the same rule as browse (per base's home root).
- **Home permissions:** `<base>/<account>` is 0700.

**`filesystem_api` boundaries per operation.** These are `entry_point_details.filesystem_api.operations`.
They differ within one entry point, which is exactly why a single prose sentence cannot describe them
faithfully.

| operation | roots | symlink policy |
|---|---|---|
| `browse` | home, shared_projects | resolved_then_rejected_if_outside |
| `check-path` | home, shared_projects, workspace_root, workspace_root_first_level_non_home | resolved_then_rejected_if_outside |
| `create-directory` | same as `check-path` | resolved_then_rejected_if_outside |
| `home` | home | not_applicable |
| `upload` | home | **never_followed_with_elevated_privilege** |
| `download` | home | **never_followed_with_elevated_privilege** |
| `delete-file` | home | **never_followed_with_elevated_privilege** |
| `search` | home | **never_followed_with_elevated_privilege** |

What the two policy words mean:

- **`resolved_then_rejected_if_outside`:** the request path is resolved with realpath first, and
  anything that lands outside the row's roots is refused. For users with a `system_account`, browse,
  check-path and create-directory run as the target account (`sudo -u`), so the access that follows is
  limited by that account's own permissions.
- **`never_followed_with_elevated_privilege`:** in addition to the above, **no symlink below the home
  root is ever followed by a process with more privilege than the target account**:
  - A root process first asserts with `fstat` that the home root belongs to the target account.
  - It then opens directory fds from the home root one component at a time with `O_NOFOLLOW`, and
    writes, reads, deletes and walks relative to that fd. The walk skips symlinks themselves.
  - A single-user process already is that account.
  - The non-root package install delegates to `openace-write-as` / `openace-rm`. After validating the
    user and the path prefix, every filesystem probe, write and delete they do runs as the target
    account through `runuser`.

`workspace_root_first_level_non_home` means a **first-level** subdirectory of the base dir that is
**not any user's home root** (rule 3 of `_check_path_rejection_reason`). The write entry points do not
include shared_projects: browse and check-path can read shared projects, but single-file writes and
downloads stay limited to the user's own home. Relaxing that would be a new feature.

**Deployment preconditions (what `filesystem_api: enforced` rests on):**

1. **The workspace base dir must be root-owned and not user-writable.** `docker-entrypoint.sh` creates
   it with a root `mkdir -p` as `root:root 0755`, and `/home` gets `chmod 755`.
   - **The anchor:** the trust anchor of single-file operations is `realpath(<base>/<account>)`.
     Below it, no symlink is ever followed with more privilege than the target account, and the root
     path also asserts with `fstat` that the anchor belongs to the target account.
   - **No escape to another volume:** the account directory itself is resolved, but **that cannot be
     used to move a home onto an arbitrary volume**. The request path is resolved with realpath first,
     then compared against the base prefix **exactly as configured**, and a home that resolves outside
     every configured base is refused.
   - **To use a large volume:** point `<base>/<account>` at a location inside a configured base, or
     add the volume path to `WORKSPACE_BASE_DIR`.
2. **The non-root multi-user package install must reinstall `openace-write-as` and `openace-rm`** (at
   least this version; rerunning `scripts/install-central/package-method/install.sh` is enough).
   - **Why:** in that shape the web process cannot cross the 0700 homes, so uploads and deletes are
     done by these two wrappers as the target account. Their symlink and directory refusals (exit 5 /
     exit 6) are translated into 400 by the route.
   - **Too-old wrappers:** if an installed wrapper lacks its capability marker
     (`openace-write-as-capability: symlink-refusal=1` or `openace-rm-capability: account-scoped=1`),
     the route **fails closed and refuses that upload or delete** with a 500 whose message says how to
     reinstall.
   - The `enforced` declaration therefore holds for every deployment: either the wrapper acts as the
     target account, or the operation does not happen.
3. **`<base>/<account>` is 0700.**
   - **Docker install:** new directories are created 0700. Existing volumes converge to 0700 at
     container start (entrypoint) and at login provisioning (a root process, through descriptors that
     do not follow symlinks), and only directories owned by that account are changed.
   - **Package install:** the permissions of `/home/<account>` come from `useradd` (`HOME_MODE`).
     With a custom base, `openace-mkdir` creates the directories as the target account with the
     default umask, and the service account cannot change them. Set existing account directories to
     0700 yourself.
4. **Three residuals are machine-readable** in `entry_point_details.filesystem_api.residuals` and
   **take no part in admission decisions**:
   - shared project roots are cross-user by design;
   - the package install's wrapper precondition;
   - users without a mapped `system_account` work as the web process in `<base>/<username>`.

   Two edge cases have **no home root at all**, so every `/api/fs` operation is refused for them:
   - an unmapped user whose username happens to be another user's `system_account`, under a root
     process (both would point at the same OS account and directory);
   - an account named `shared`, whether it comes from `system_account` or from a username (which
     SSO or organization sync can also produce), because `<base>/shared` is the root of the shared
     project namespace, not a home.
5. **Plain `os_user` shares the host kernel:** `resources` / `network_egress` / `kernel` stay
   `unsupported` (see the §2 boundary statement).

**Matrix values in an OpenSandbox pod deployment (#3378 onward).** The matrix is emitted per level:
- **`webui`:** moves into the pod with the `sandboxed` level (`enforced`).
- **`terminal` / `vscode` / `filesystem_api`:** emit **`sandboxed_entry_not_wired`**. Their executors
  still run on the control-plane host and are not wired to the user's sandbox instance.
  - The host `/fs` tree is unavailable to sandboxed users: their files are in the pod, served by the
    WebUI's own in-pod file browser.
  - In sandboxed snapshots these three keep their existing `limitations` (the remote-scope notes of
    terminal/vscode) and add `sandboxed_entry_not_wired`.
  - `filesystem_api`'s `boundary` changes to explain that it is not wired, and it no longer carries
    the residuals that describe only the host tree.
- **`session_history`:** stays `enforced` (per-pod snapshot storage).
- **`autonomous`:** stays `separate_contract`.

For os_user and none snapshots, see the table above (recalibrated in #3410). That `filesystem_api`
differs between sandboxed and os_user is **deliberate**: local enforcement is not the same as being
wired into the pod.

The local container forms (§5.2, §5.3) keep the os_user matrix: their home is the host directory.

## 5. Multi-user deployment requirements

Whether the contract reports `supported/os_user` is decided by the **launch-path readiness probe**
(§3.3: WebUI resolution, not dev-directory mode, the `openace-webui-launch` wrapper, sudo). The Docker
layout is no longer a precondition: the package install (`scripts/install-central/package-method`,
with `sudo -u` and the wrappers in place) can verify and enforce os_user too.

Two reference deployments:

1. **Docker multi-user:** the `docker-compose.multi-user.yml` overlay (root +
   `OPENACE_ALLOW_ROOT_MULTI_USER=1` + `WORKSPACE_BASE_DIR=/workspace` +
   `WORKSPACE_ISOLATION_BACKEND=plain`, which the entrypoint writes into the generated config as
   `workspace.isolation {"level": "os_user", "backend": "plain"}`). The image ships useradd and the
   wrappers, and system accounts are provisioned automatically.
2. **Package multi-user:** the installer's multi-user path (runs as non-root, `/home` layout), which
   writes the same `isolation` block.
   A healthy probe reports os_user. Each user's system account must already exist (resolvable by
   getpwnam, uid ≥ 1000).

Common requirements: the restricted `openace-webui-launch` wrapper in sudo/sudoers. There are no
special kernel requirements, because the os_user level is the OS account boundary. For baseline
production secrets, see the compose comments.

When the probe degrades (dev-directory mode, a missing wrapper, ...), the contract honestly returns
`launch_path_degraded` unsupported. It **never** silently falls back to the shared account while
claiming support, and because `workspace.isolation.level` is the floor, every launch is then refused
with a structured error until the launch path is repaired.

**Install method matters for the backends below.** The Docker install supports only `shared`,
`plain` and `opensandbox`; `bwrap` and the local containers (§5.1–§5.3) need the package install on a
Linux host, and startup refuses them elsewhere (#3446). See the install-method table in
[WORKSPACE_ISOLATION](../guide/WORKSPACE_ISOLATION.md#2-what-your-install-method-allows).

### 5.1 Backend `bwrap`: confined OS account (#3431)

`workspace.isolation {"level": "os_user", "backend": "bwrap"}` confines each OS-account WebUI at
launch. It stays at the `os_user` level (the host kernel is shared), but the `resources` and
`network_egress` dimensions become `enforced`, and `backend` reports `bwrap`:

| layer | mechanism | effect |
|---|---|---|
| resources | `systemd-run --scope` (`MemoryMax`/`MemorySwapMax=0`/`CPUQuota`/`TasksMax`) | Hard cgroup v2 limits, including a fork-bomb ceiling |
| identity | `setpriv --reuid/--regid --init-groups --no-new-privs`, with the capability and bounding sets cleared | Carries only the account's own supplementary groups. `systemd-run --scope --uid` would keep the caller's (root's) group 0, which is why it is not used |
| filesystem | bubblewrap: read-only host root; empty tmpfs over `/tmp`, `/var/tmp`, `/run` and the workspace base | Only the user's own home and `<base>/shared` are bound back; other users' homes are invisible (not merely access-denied) |
| network | bubblewrap `--unshare-net` (loopback only) + a host-side egress proxy | The proxy is the only way out. See below |
| ingress | reverse tunnel | See below |

**The egress proxy:**
- **What it allows:** only allowlisted `host:port` pairs, which are the host:port of
  `webui_callback_url` plus `isolation.egress_allow`. They come **only from server-side
  configuration, never from a request's Host header**. Everything else gets 403.
- **Where decisions are logged:** `/var/log/openace-webui/<uid>.egress.log`, owned by root, mode
  0600.
  - Root opens the file and hands the descriptor to the host-side process, so processes inside the
    sandbox cannot open, replace or truncate it.
  - Other processes of the same account on the host could in theory attach to the host-side process
    that holds the descriptor. That is outside what this protects against.
- **The log is bounded and may be summarized:**
  - **ALLOW/FAIL decisions are never dropped.** Beyond 50 lines per second, or past their own byte
    ceiling, they are written as counts summarized per **matched allowlist entry**, a finite set.
  - Hosts are normalized before matching and logging. Hosts containing whitespace or control
    characters, and methods that are not short upper-case words, are refused as BAD.
  - DENY/BAD have their own limit of 50 lines per second and 8 MiB per launch, so a flood of refusals
    cannot hide the allow records.
  - Every client-supplied field is cut to 256 characters, and an older log over 8 MiB is rotated to
    `.1` at launch.

**The reverse tunnel:**
- **Outbound only:** processes in the sandbox only **connect out** to a host-side socket. They keep a
  few idle tunnels open, and a browser connection is paired with one when it arrives.
- **Read-only socket directory:** the socket directory is bound into the sandbox **read-only**, so
  the host side never follows a path the sandbox can write.
- **Bounded connections:**
  - Concurrent browser connections are capped at `TasksMax / 8` (at most 256).
  - A single source address can take at most half of them.
  - A connection silent in both directions for 300 seconds is closed.
  - An anonymous client therefore cannot exhaust the scope's task budget.

Settings (`workspace.isolation` in `config.json`; they apply to `bwrap`, `local-gvisor` and
`local-kata`):

| key | default | meaning |
|---|---|---|
| `limits.memory` | `4G` | systemd `MemoryMax` (container backends: `--memory`) |
| `limits.cpu_percent` | `200` | percent, `CPUQuota` (container backends: `--cpus`) |
| `limits.tasks` | `512` | `TasksMax` (container backends: `--pids-limit` / `--ulimit nproc`) |
| `egress_allow` | `[]` | extra allowed `host:port` pairs (`*.domain:port` and `[ipv6]:port` are supported) |

**`webui_callback_url` is required.** Without it, the API address would come from the request's Host
header, which the user controls, so the user could rewrite the allowlist. The probe therefore reports
`confinement_callback_url_missing`.

**Account restrictions:**
- **uid:** the target account must have uid ≥ 1000.
- **Privileged groups:** the account must not belong to any of `sudo`/`wheel`/`admin`/`adm`/`shadow`/
  `disk`/`docker`/`lxd`/`incus`/`incus-admin`/`libvirt`/`kvm`/`systemd-journal`/`staff`/`lpadmin` or
  gid 0. This is a name list, so add site-specific privileged groups with `denied_groups`. File access
  inside the sandbox is still decided by the real supplementary groups, which are carried into the
  sandbox.
- **Other policy restrictions:** the policy file can extend the list with `denied_groups`, and limit
  the allowed workspace bases with `bases` (e.g. `["/home"]`).
- **Root-controlled executables:** the WebUI executable (after resolving symlinks), its whole npm
  `node_modules` tree (which includes the `qwen` CLI it starts) and every directory in the policy's
  `path` must be owned by root and not group/other-writable, or the launch is refused.
  `#!/usr/bin/env node` finds `node` through that PATH, so the npm global prefix must belong to root.

**Deployment requirements.** These need the package install; the Docker install has no systemd, and
enabling it there fails closed with the codes below.

- Linux + systemd (cgroup v2), `bubblewrap` ≥ 0.8 (for `--disable-userns`), `setpriv` (util-linux).
- Unprivileged user namespaces must be available. Ubuntu 24.04+ restricts them through AppArmor by
  default: load the distribution's `bwrap-userns-restrict` profile (the same requirement as Codex and
  Claude Code).
- The installer installs:
  - `/usr/local/bin/openace-webui-confine`;
  - the root-owned policy file `/etc/openace/webui-confine.json`, which lists the WebUI executables
    that may be started as a user and the in-sandbox `PATH`;
  - the sudoers rule `<service> ALL=(root) NOPASSWD: /usr/local/bin/openace-webui-confine launch *`.
- The WebUI runtime must honour `HTTP(S)_PROXY`. Node 22.21+ applies it to `fetch` with
  `NODE_USE_ENV_PROXY=1`, which the wrapper sets. Requests that ignore the proxy get no network
  (fail closed).

**Fail closed:** when confinement is configured but the host cannot provide it, the launch-path probe
reports `launch_path_degraded` with one of these codes in parentheses. It never silently launches
unconfined:
- `confinement_platform_unsupported`, `confinement_wrapper_missing`;
- `confinement_bwrap_missing`, `confinement_setpriv_missing`, `confinement_systemd_unavailable`;
- `confinement_userns_unavailable`, `confinement_bwrap_too_old`, `confinement_policy_invalid`;
- `confinement_callback_url_missing`, `confinement_check_failed`.

Two launch shapes that cannot be confined are refused at launch: dev-directory mode, and "the service
account is the target account". A cold worker (manager not initialized, probe not run yet) reports
only the plain os_user dimensions.

**Honest statement:**

- **Threat model:** confinement protects "other local users from the WebUI/agent in the sandbox". It
  does **not** defend against a compromised service account, which still holds its other sudoers
  rights (running git and similar as any user). Rerunning the installer after enabling confinement
  removes the `openace-webui-launch` rule that could start an **unconfined** WebUI (defence in depth).
- `kernel` stays `unsupported`: the kernel is shared with the host. Use the sandboxed level for kernel
  isolation.
- The WebUI's `--token-secret` is still on its command line (qwen-code-webui accepts it only as a
  flag; existing behaviour).
- The egress proxy decides on the host name and port the client gives and **does not inspect TLS**
  (the same known limit as Claude Code's sandbox proxy). Allowing a broad domain leaves an
  exfiltration channel. Addresses that an allowlisted host name resolves to are not checked a second
  time.
- The host-side ingress forwarder still listens on `0.0.0.0:<port>` (the same exposure as without
  confinement) and relies on the WebUI token.
- Any process inside the sandbox can fill the reverse-tunnel pool and make the user's own WebUI
  unreachable. The effect is limited to that user's own workspace (the sandbox could replace its own
  WebUI anyway): self-denial of service.
- Acceptance: `scripts/webui_confine_acceptance.py` (needs a disposable Linux host,
  `CONFINE_ACCEPTANCE_DISPOSABLE=1`).

### 5.2 Backend `local-gvisor`: local gVisor container (#3431 Option 2)

The **sandboxed** level without Kubernetes: each user's WebUI runs in a local Docker container on
the gVisor (runsc) runtime. It shares the root entry point, the host-side process and the egress proxy
with §5.1; only the sandbox changes from bubblewrap to a gVisor container. The identity is still the
user's OS account, and the home is still `<base>/<account>` on the host.

| dimension | mechanism |
|---|---|
| kernel | gVisor's user-space kernel. The readiness probe reads `/proc/version` inside a container and **positively** confirms gVisor |
| resources | `docker run --memory/--memory-swap/--cpus` + `--ulimit nproc=<tasks_max>` (a per-uid process cap the gVisor kernel enforces). Under gVisor, `--pids-limit` bounds the sentry's host threads, so it is `max(tasks_max, 256)` |
| filesystem | Read-only image root + `--tmpfs /tmp`. Only the user's home, `<base>/shared` and the log directory are mounted. `--user <uid>:<gid>` + the account's supplementary groups (`--group-add`) |
| process / privileges | `--cap-drop ALL`, `--security-opt no-new-privileges`, `--init` |
| network | `--network none`. Ingress and egress work as in §5.1: a reverse tunnel, plus an egress proxy that only allows allowlisted `host:port` pairs, reached through UNIX sockets mounted read-only. This needs runsc `--host-uds=open` |
| environment | The WebUI environment reaches the container on stdin. It never appears on a command line, and `docker inspect` cannot see it |

Snapshot: `isolation_level = sandboxed`, `backend = local-gvisor`, all seven dimensions
`enforced`. The differences from the OpenSandbox pod form (§6) are **deliberate**:

- **Entry-point matrix:** the os_user one. The home is a host directory, so `/api/fs` and the other
  entry points stay `enforced`.
- **`/user-url` gate:** it keeps the OS account chain (`system_account` mapping, account checks).
- **Launch form:** it stays the per-user local process form and **never** routes to the OpenSandbox
  pod launcher. The pod form is used only for backend `opensandbox`.

Deployment requirements (a Linux package install; a single Docker host is enough):

1. Docker + gVisor, with a dedicated runtime registered in `/etc/docker/daemon.json`:
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
   - `--host-uds=open` only allows **connecting** to host sockets (not creating them), and applies
     only to containers that use this runtime.
   - The container can only connect to sockets that already exist, with permissions allowing it, in
     the mounted directories (home, shared, the log directory, the read-only socket directory). That
     matches the bubblewrap form of §5.1.
2. The WebUI image: build it with `scripts/docker/webui-sandbox.Dockerfile` (node, python3 and a
   pinned qwen-code-webui), and **pin it by content** in the policy file:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
    "local-gvisor": {"image": "sha256:<64 hex>", "runtime": "runsc-openace"}}
   ```
   - The section is named after the backend (#3446); `docker` is shared by the container backends.
     The pre-#3446 `container` section is refused with the conversion in the message.
   - `image` must be `name@sha256:<64 hex>` or a local image ID `sha256:<64 hex>`. A tag can be
     re-pointed, so it is refused.
   - `webui` lists paths **inside the image**.
   - Rerunning the installer keeps these sections.
3. `workspace.isolation {"level": "sandboxed", "backend": "local-gvisor"}`.
   `workspace.webui_callback_url` is required (as in §5.1). `isolation.container_webui` changes the
   WebUI path inside the image (default `/usr/bin/qwen-code-webui`).

`fs.protected_symlinks=1` is required (the default on Debian/Ubuntu/RHEL). docker (as root) mounts
the log directory by path, and this setting stops the account from steering the mount source with a
symlink in the sticky `/tmp`. Without it the probe reports `confinement_symlinks_unprotected`.

**Readiness:** the manager checks with `sudo -n openace-webui-confine launch --probe --backend local-gvisor` (root):
- the docker CLI is root-controlled;
- the runtime is registered;
- the image is present locally (nothing is pulled at launch);
- the kernel inside a container is gVisor;
- a container can connect to a read-only-mounted host UNIX socket.

A success is cached for an hour and a failure for 30 seconds. Reason codes:
- `confinement_docker_unavailable`, `confinement_runtime_missing`, `confinement_image_missing`;
- `confinement_kernel_unverified`, `confinement_runtime_host_uds_disabled`;
- `confinement_symlinks_unprotected`, `confinement_policy_invalid`, `confinement_check_failed`;
- and, shared with §5.1, `confinement_platform_unsupported`, `confinement_callback_url_missing`,
  `confinement_wrapper_missing`.

**Lifecycle:** the root launcher stays the container's parent for its whole life.
- **Normal stop:** the manager sends SIGTERM to sudo, which is forwarded to the docker CLI and then
  to the container. The WebUI exits and `--rm` removes the container.
- **Escalation:** when the manager escalates to SIGKILL (which sudo does not forward), the root
  launcher notices it was reparented and runs `docker rm -f` on the container, by the id recorded in
  a root-owned cid file.
- **Supervisor gone:** when the host-side process exits, the launcher also removes the container.
- **Cleanup:** a container left over on the same port is removed before a new launch starts, and the
  run directory is cleaned up at the end.
- **Watchdog:** if the host-side process disappears unexpectedly, a watchdog inside the container
  stops the WebUI after about 30 seconds, and `--rm` removes the container.

**Honest statement:**

- This section covers gVisor (runsc) only; for Kata see §5.3.
- As in §5.1: the egress proxy does not inspect TLS, there is no defence against a compromised
  service account, and the host-side ingress still listens on `0.0.0.0:<port>`.
- The image content (node version, toolchain) is the set of tools the agent can use: a product
  decision.

### 5.3 Backend `local-kata`: local Kata container (#3438)

The same local-container form as §5.2 on a **Kata Containers** runtime: each WebUI runs in a
lightweight virtual machine with its own guest kernel. Snapshot: `isolation_level = sandboxed`,
`backend = local-kata`, all seven dimensions `enforced`. The entry-point matrix, the
identity gate and the local launch form are the same as in §5.2.

Differences from §5.2:

| item | Kata form |
|---|---|
| kernel isolation | Hardware virtualization (KVM); the guest kernel differs from the host's |
| ingress/egress channel | Framed streams over the container's stdin/stdout instead of host sockets (see below) |
| watchdog | The host sends a heartbeat every 5 seconds. The container side stops the WebUI after 30 seconds without any frame, or when stdin ends |
| task limit | `--pids-limit` and `--ulimit nproc` both apply inside the guest and are set to `tasks_max` (no gVisor 256 floor) |

**The ingress/egress channel:**
- **Why host sockets can't be used:** a Kata guest sees host UNIX sockets through virtio-fs but
  **cannot connect to them** (`ECONNREFUSED`, measured). No socket directory is mounted.
- **How it works instead:** every ingress and egress connection is multiplexed as **frames** over
  the container's stdin/stdout. Each stream has its own credit window, so a slow stream does not stall
  the channel.
- **No network:** the container is still `--network none`. No bridge is created and no firewall rules
  are written.
- **Stream rules:**
  - The host opens only INGRESS streams and the container only EGRESS streams.
  - The streams the container can have open at once, including egress connections still running, are
    capped.
- **Fail closed without the container's cooperation:** any framing violation drops the whole
  channel. The host-side process then exits, and the root launcher removes the container.

Deployment requirements:

1. A Linux host with `/dev/kvm`: bare metal, or a virtual machine with nested virtualization enabled.
2. **Kata ≥ 3.32.0.** Docker 29 puts a `time` namespace into every OCI spec, and older Kata fails with
   `invalid namespace type` (fixed by kata-containers#13082, first shipped in 3.32.0).
3. **Runtime:**
   - Installing `containerd-shim-kata-v2` root-owned in a standard system `bin` directory (e.g.
     `/usr/local/bin`, which is also on dockerd's PATH) is enough to use `io.containerd.kata.v2`
     directly, **without** changing `daemon.json` or restarting Docker. Registering a name in
     `daemon.json` also works.
   - The policy file has a `local-kata` section:
     ```json
     {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
      "local-kata": {"image": "sha256:<64 hex>", "runtime": "io.containerd.kata.v2"}}
     ```
   - When a shim name is used, the probe looks for the shim only in the standard system `bin`
     directories and requires it to be root-controlled.
4. Under nested virtualization the guest boots slowly (about 70 seconds measured with three levels of
   nesting). Kata's default `dial_timeout = 45` is too short there: raise it (e.g. to 180) in
   `/etc/kata-containers/configuration.toml`. On bare metal the default is fine.
5. `workspace.isolation {"level": "sandboxed", "backend": "local-kata"}`; everything else as in
   §5.2.

**Readiness:** `sudo -n openace-webui-confine launch --probe --backend local-kata` (root). In addition to
the §5.2 checks, it verifies:
- `/dev/kvm` exists;
- a probe container started on the Kata runtime passes a **positive check from the host**. Docker's
  `State.Pid` for it must be a hypervisor process (`qemu-system-*` / `cloud-hypervisor` /
  `firecracker` / `stratovirt`) whose parent is the `containerd-shim-kata-v2` started for **this**
  container id. Under runc, `State.Pid` is the container's own init process instead;
- the guest's `uname -r` differs from the host's;
- the stdio channel round-trips.

This closes the one-directional check of the pod form described in §6.4, where Kata can only be
confirmed to be not gVisor. The probe waits at most 180 seconds for the guest to boot; the whole probe
takes about 7 minutes in the worst case, and the manager allows 8. New reason codes:
`confinement_kvm_unavailable`, `confinement_channel_failed`; the rest as in §5.2.

**Honest statement:**

- **Throughput:** the stdio channel is slower than bridge TCP. With three levels of nesting, stdio
  measured about 1.7 MB/s in each direction, against about 11.4 MB/s for bridge TCP in the same
  environment. That is enough for the WebUI and LLM streaming, but large downloads through the egress
  proxy are noticeably slower. It is the price of giving the guest no network interface and managing
  no firewall rules on the host.
- **Memory:** `--memory` sizes the guest VM, and Kata adds its own overhead (default guest memory and
  so on), so the usage the host sees is higher than that value.
- Otherwise as in §5.2.

## 6. Backend `opensandbox`: requirements and honest statement

`workspace.isolation {"level": "sandboxed", "backend": "opensandbox"}` means the WebUI process runs in an OpenSandbox pod:
- one pod per user and instance;
- a dedicated WebUI image that is digest-pinned and on the image_allowlist;
- deny-default egress;
- the browser reaches it through a local port proxy on the control plane (port form, no path-rewrite
  assumption);
- the identity is a per-instance token secret, which is invalidated when the instance is destroyed.
  There is no OS account or sudo chain.

The probe **does not check the platform**: the pod runs on a remote cluster. The pod form is used
only for this backend (#3446): it is never inferred from a configured `webui_image`, and when its
probe fails the snapshot is unsupported with the probe's reason; there is no fallback to the
OS-account form.

Changes in `POLICY_REVISION` 2026-09-12.1:
- the `sandboxed` level and the zero-pod probe joined the vocabulary;
- a new `kernel` dimension, which `os_user` honestly declares unsupported (shared host kernel);
- a sandboxed branch in the user-url gate, which refuses by probe reason instead of walking the OS
  account chain.

Changes in `POLICY_REVISION` 2026-09-12.2 (the complete list):

- **T-B gate semantics** (superseded by #3446, which makes the backend explicit): the launch form was
  the strongest verified form meeting max(floor, request), so a configured `webui_image` switched
  default launches to pods. Since #3446 the form follows `workspace.isolation.backend` alone.
- **Probe reason vocabulary:**
  - Added `sandbox_multi_process_unsupported`. Sandboxed is refused while another web process holds
    a fresh heartbeat, or while an unreadable heartbeat root makes it impossible to prove this process
    is the only one. The per-instance secret and instance management are in-process memory.
  - Removed `sandbox_proxy_token_ttl_too_short`. A short TTL is not a deployment defect: a pod's
    lifetime is clamped, at create and at every renew, to end before its LLM credential expires, and
    raising the TTL would also lengthen the local WebUI credentials' lifetime.
- **T-M, only a sidecar upgrades egress:** network_egress is upgraded to enforced only by a real read
  of `/policy` on a sidecar attestation tier. The cluster-level deny-default check on gVisor/CNI tiers
  is negative verification (it proves a refusal path exists, not that allowed traffic flows), so it
  stays unsupported + `sandbox_runtime_egress_negative_only`.
- **T-L, memo revoked on failure + 1 h TTL:** the boot-probe memo is no longer write-only.
  - A failed pod probe on a tier revokes that tier's upgrade (the newest evidence wins).
  - Memo entries are time-stamped and fall back to unverified after 1 h.
  - The fallback conditions are therefore "control-plane restart, a failed probe on the same tier, or
    an entry older than 1 h", no longer only a restart.

### 6.1 Dimensions

| dimension | sandboxed static (configuration probe passed) | sandboxed after the first pod probe | os_user (for comparison) |
|---|---|---|---|
| identity | enforced | enforced | enforced |
| filesystem | enforced | enforced | enforced |
| environment | enforced | enforced | enforced |
| process | enforced | enforced | enforced |
| resources | enforced (the create body always carries resourceLimits; the default 4Gi/2CPU is also a bound) | enforced | unsupported |
| kernel | unsupported (`sandbox_runtime_unverified`) | gVisor: enforced (positively identified from `/proc/version`); Kata: stays unsupported (`sandbox_runtime_kata_negative_only`, negative only) | unsupported (shared host kernel) |
| network_egress | unsupported (same reason as kernel) | **sidecar attestation tier only**: enforced (a real read of the egress sidecar's `/policy`). gVisor/CNI tiers: stays unsupported (`sandbox_runtime_egress_negative_only`: the CNI default deny is only a negative check; it proves a refusal path exists, not that allowed traffic really flows) | unsupported |

The five statically enforced dimensions rest on **configuration facts** (a separate pod, the image
allowlist, an always-present resource bound), not on per-pod verification. kernel and network_egress
can only be proven by a per-pod boot probe.

Probe results are an in-process memo that **falls back to unverified after a control-plane restart,
a failed probe on the same tier, or when the entry is older than 1 h**. That is a deliberately honest
contract (T-L): revoke on failure and expire entries after 1 h, because the newest evidence wins and a
long-running process should not rely on a probe from hours ago.

### 6.2 Deployment requirements

1. **`endpoints.<tier>.webui_image`:** digest-pinned and listed in `image_allowlist`.
   - Validation happens in the capability probe, not when the configuration is parsed, so a bad value
     only degrades the sandboxed level and does not affect the backend configuration that autonomous
     tasks share.
   - Reference build: `scripts/docker/webui-sandbox.Dockerfile` (node + qwen-code-webui + non-root
     uid 1000, listening on 0.0.0.0:3100). Production images must be digest-pinned and allowlisted by
     you.
2. **`workspace.webui_callback_url` must be set.** The probe uses it as the LLM proxy callback URL:
   the per-request host is only known at launch, and a static probe needs a stable URL.
   - On a sidecar tier, add the control plane's host name to that tier's `egress_allow_hosts`.
   - On a CNI tier, it must be publicly reachable; loopback, private and in-cluster addresses are
     refused.
3. **The proxy token TTL has no deployment precondition** (T-F). The launcher clamps the pod lifetime:
   - at create, to `min(WebUI token TTL, effective proxy token TTL)`;
   - at every renew, to `min(now + WebUI token TTL, token expiry)` (UTC). An undecodable or empty token
     is clamped to now, so the pod ends together with its invalid credential.

   A short TTL only shortens pod lifetimes and never lets a pod outlive its LLM credential. There is
   no need to raise `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES` to declare sandboxed; raising it would
   also lengthen the local WebUI credentials' lifetime.
4. **HTTPS:** reuse the external reverse proxy's port-range mapping (`/webui/<port>`, see
   [NGINX](../guide/NGINX.md)). In the sandboxed form the browser port is the local proxy port, taken from the
   same port_range (default 3100-3200).
5. **`workspace.isolation.tier` is optional.** It chooses which endpoint tier interactive pods land
   on; the default is the backend's `default_tier`. The pod's identity is its per-instance token, so
   the gate admits a `sandboxed` requirement through the sandboxed branch, without the OS-account
   chain (identity mapping, sudo launch).
6. **Not verified end to end on a real cluster:** the reachability of the WebUI entry point and the
   pod's 3100 endpoint (#3379). A runtime failure surfaces as `sandbox_endpoint_unresolved` rather
   than hanging silently.

### 6.3 TTL chain

| step | value | source |
|---|---|---|
| WebUI token TTL | 24 h (default) | `OPENACE_WEBUI_TOKEN_TTL_SECONDS` (86400) |
| pod create timeout | min(WebUI token TTL, effective proxy token TTL) | computed by the launcher at launch |
| pod renew target | min(now(UTC) + WebUI token TTL, proxy token expiry). See below | the launcher's renew clamp |
| idle reclaim | 30 min (default) | `workspace.idle_timeout_minutes` |
| periodic snapshot export + renew | 5 min by default. See below | `SANDBOX_MAINTENANCE_INTERVAL_SECONDS` = 300 (floor) + the cleanup loop's sleep |
| process heartbeat refresh | a constant 300 s. See below | `webui_sandbox.HEARTBEAT_REFRESH_SECONDS`; window `HEARTBEAT_FRESH_WINDOW_SECONDS` |
| default proxy token TTL | 240 min (no need to raise it for sandboxed; the pod TTL is clamped below it) | `DEFAULT_PROXY_TOKEN_TTL_MINUTES`, env `OPENACE_PROXY_TOKEN_TTL_WEBUI_MINUTES` |

Notes on three of the rows:
- **Pod renew target:**
  - A pod lives at most until its LLM credential expires.
  - An undecodable or empty token clamps to now (fail closed; the target no longer moves forward by a
    fallback TTL every round).
  - After the clamp point, idle reclaim tears the pod down normally, and the next `/user-url`
    recreates it and restores the snapshot.
- **Snapshot export:** the maintenance check rides on the cleanup loop. When
  `workspace.cleanup_interval_minutes` (default 5) is longer than 5 min, the real export interval is
  the cleanup interval.
- **Heartbeat refresh:** a separate timer greenlet does it. The worker start hook starts it behind the
  same env gate as reconcile, and it **does not scale with the cleanup interval**. The freshness window
  is 2×300+60 = 660 s.

The token is re-minted with the per-instance secret on every `/user-url` hit. The health check probes
through the local proxy with the current token; passing proves the whole chain:
proxy → gateway → pod → webui → token validation.

### 6.4 Honest statement

- **A configuration probe is not per-pod verification.** The five statically enforced dimensions rest
  on configuration facts, and kernel/network_egress await the first pod boot probe. The level **falls
  back to unverified after a control-plane restart**. A failed probe revokes that tier's memo, and
  entries expire after 1 h (T-L).
- **Kata's kernel verification in a pod is one-directional:** it can only rule out gVisor and cannot
  tell Kata from unisolated runc. kernel stays unsupported + `sandbox_runtime_kata_negative_only`;
  only gVisor's positive identification upgrades it to enforced. (The local Kata container of §5.3
  verifies Kata positively from the host instead.)
- **Egress verification on gVisor/CNI tiers is negative only.** The cluster-level deny-default check
  proves a refusal path exists, not that allowed traffic really flows, and the configuration layer
  already forbids gVisor tiers from declaring a sidecar. network_egress stays unsupported +
  `sandbox_runtime_egress_negative_only`; only a real read of `/policy` on a sidecar attestation tier
  upgrades it (T-M).
- **Session history:** a whole-directory tar snapshot, stored under a separate root with the key
  `webui-<user_id>.tar`.
  - **Crash loss:** a control-plane crash loses at most one export interval of changes (5 min by
    default; export rides on the cleanup loop, so a longer `cleanup_interval_minutes` widens the
    window).
  - **Degraded starts are not exported.** A start is degraded when the restore gate timed out after
    60 s, the restore was not confirmed, or the snapshot was unreadable. Keeping the old snapshot beats
    overwriting a good one with an empty tree.
  - **Unreadable snapshots are not recorded either.** A degraded start from an unreadable snapshot
    also **writes no control-plane confirmation record**, so neither reconcile nor the export guard
    will trust its empty tree.
  - **Deletion records:** a record is cleared only after a delete is **confirmed**; a failed delete is
    kept for the next round.
  - **Size limit:** snapshots are capped at 16 MiB by default (`OPENACE_WEBUI_STATE_MAX_BYTES`), and
    export is skipped over the cap. The user's history is then **frozen at the last good snapshot**
    until cleaned up by hand. **There is no automatic GC.**
- **Token 401 semantics (N8):** after a sandbox instance stops, the tokens it issued get 401 on the CP
  URL-token path (the per-instance secret dies with the instance).
- **Entry-point wiring:** terminal/vscode/fs are not wired to the sandbox form
  (`sandboxed_entry_not_wired`). The host `/fs` tree is unavailable to sandboxed users: their files
  are in the pod, served by the WebUI's own in-pod file browser.
- **External assumptions:**
  - The pod-internal proxy the pod captures itself holds the gateway credential-injection header end
    to end.
  - "The proxy is a dumb pipe and the token is validated by the WebUI in the pod" includes
    assumptions about WebUI behaviour.
  - The reachability of the 3100 endpoint and the entrypoint's upstream constraints are external
    assumptions; verification on a real cluster belongs to #3379.
- **Multiple replicas and orphan reclaim (T-D, round-2 revision):**
  - **Orphan reclaim** tells processes apart by process generation, and before destroying anything it
    checks the control-plane heartbeat files
    (`<CONFIG_DIR>/webui-agent-state/webui-heartbeat-<process-generation>.json`, one per web process).
    File names and self-exclusion use a **per-process uuid4 generation**: containers on one node share
    the host boot_id and often collide on pid, so a `(boot_id, pid)` identity would make replicas on a
    single-node k3s/kind cluster mistake each other for themselves.
  - **Refresh:** a **separate 300 s timer** refreshes the heartbeat, at a constant period decoupled
    from the cleanup interval, so replicas without a manager instance refresh too.
  - **Exit:** a process deletes its own heartbeat file on exit (gunicorn `worker_exit` hook + atexit;
    idempotent, fail-soft). **The restart window of a single-process deployment is therefore close to
    zero.** SIGKILL and crashes cannot clean up; a leftover heartbeat expires after at most one
    freshness window (≤ 660 s), and until then the `opensandbox` backend is reported unsupported and
    launches are refused.
  - **Sweeps and reconcile:** sweeps are skipped while another fresh heartbeat exists. **Replicas no
    longer kill each other in reconcile:** with 3 replicas or a rolling overlap, a new replica no longer
    sweeps another replica's live pods.
  - **Heartbeat reads fail closed:** an unreadable heartbeat root or heartbeat file means "cannot
    prove this is the only process" and is treated as "a peer exists" (the probe refuses sandboxed,
    and sweeps are skipped). Writes are still fail-soft.
  - **What is still unsupported:** pod ownership is in-process memory, so token validation and
    instance management (reclaim, snapshot export) are not supported across replicas. That needs a
    single replica or a replica-aware refactor (§8).
- **Pause, resume, shutdown:**
  - pause/resume and the warm pool do not apply to WebUI pods.
  - A normal gunicorn shutdown removes the heartbeat file through the `worker_exit` hook; dev and
    non-gunicorn shapes fall back to atexit. SIGKILL is not covered, and a leftover heartbeat expires
    after at most one freshness window.
  - A normal instance shutdown still tries to export, and abnormal exits are handled by the orphan
    reconcile at the next start.

## 7. For integrators

Query the capabilities:

```bash
curl -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities
```

- **Read-only construction:** the endpoint does not create a WebUI manager, so it mints no token
  secret and starts no cleanup greenlet. While no manager exists yet, the snapshot is derived from the
  configuration on disk and cannot verify the launch path. That is why `launch_path_degraded` can only
  appear while a manager is active.
- **Authentication:** any authenticated user (session cookie or Bearer). The contract holds no
  secrets. WebUI-token iframe callers are not served by this endpoint; iframe flows use their own
  per-resource tokens.

**Admission example (#3410).** The check below is **item for item identical** to the unit test
`TestDocumentedAdmissionPredicate` (`tests/unit/test_workspace_isolation_contract_3374.py`), which
runs this very snippet from both language versions of this document. Change one and you must change
the other, or the document drifts from the version:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities > caps.json
python3 - caps.json <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
NEEDED = ("webui", "filesystem_api", "session_history")    # local workbench entry points
KNOWN  = {"enforced", "partial", "remote_machine_scope",
          "separate_contract", "sandboxed_entry_not_wired", "disabled"}
REVIEWED = {"2026-09-26.3"}                                # revisions you have reviewed
d = c.get("entry_point_details", {})
ok = (
    c.get("local_workspace_multi_user") == "supported"
    and c.get("isolation_level") == "os_user"
    and c.get("policy_revision") in REVIEWED
    and {"identity", "filesystem", "environment", "process"} <= set(c.get("enforced", []))
    and all(s in KNOWN for s in c.get("entry_points", {}).values())   # unknown status -> reject
    and all(
        d.get(e, {}).get("status") == "enforced"
        and d.get(e, {}).get("covered_by_isolation_level")
        and not d.get(e, {}).get("limitations")                      # residuals do not count
        for e in NEEDED
    )
)
print("ACCEPT" if ok else "REJECT")
PY
```

Key points:

- **Pin only the `policy_revision`s you have reviewed**; reject unknown revisions and unknown entry
  `status` values.
- **Decide on whether `limitations` is empty, not on `residuals`.** Residuals are known and accepted
  properties (such as shared project roots being cross-user by design); treating them as gaps would
  reject every healthy deployment.
- **`terminal` / `vscode` are `remote_machine_scope`.** They are governed by the machine ACL and are
  not covered by the local isolation level.
  - **For a "local workbench only, no remote machines" product shape, grant no machine assignment.**
    Do not require these two entry points to become `enforced`: the contract reports mechanisms, not
    your authorization decisions.
  - A server-side switch to disable entry points is item 7 of §8.

Launch a workspace with an isolation requirement (you get a structured 400 instead of a silently
weaker launch when the capability falls short):

```bash
curl -H "Authorization: Bearer <token>" \
  "https://<open-ace>/api/workspace/user-url?required_isolation=os_user"
```

**The isolation requirement is server-side:** `workspace.isolation.level` in config.json is the
floor for every launch (#3446). The installers and the Docker entrypoint write it together with the
backend. There is no derived floor: when operator actions degrade the launch path (rerunning the
installer wipes the wrapper, `webui_path` is pointed at a dev checkout, sudo is removed), launches are
**refused** with `isolation_level_unsupported` until it is repaired, and a WARNING says so; they are
never served on the shared account.

The `required_isolation` request parameter **can only raise** the requirement, never lower it; an
empty or blank parameter means the default. The background prestart at login and workspace directory
provisioning go through the same evaluation, so a launch or provisioning the gate would refuse never
happens.

- **Success:** the response contains `url`/`token`/`system_account` and `isolation` (a snapshot of the
  deployment's verified posture; the server-side floor makes the default path go through the gate as
  well, so the echo matches the real launch).
- **Failure:** `success:false` + `error_code` (§3.2) + `reasons` (deployment-level and request-level
  namespaces side by side) + `isolation`.
- **Backend `shared`** (floor none): calls without the parameter keep the existing behaviour.
- **Behaviour change:** in multi-user mode, a user with an empty `system_account` gets
  `identity_mapping_missing` 400 on the default path too (no parameter). Login no longer back-fills the
  username convention, so an admin must set the mapping explicitly. When a cached instance's launch
  account no longer matches the current mapping, it restarts automatically under the new account.

**Probe trade-offs:**
- **Probe-only resolution:** `supports_per_user_launch` / `per_user_launch_readiness` resolve the WebUI
  executable **probe-only**, so they never trigger an npm build.
- **Memoization:** readiness results (including degraded ones) are memoized in-process for 30 seconds
  in both directions, stamped when the check ends. A successful resolution is also cached permanently.
- **What the sudo path needs:** the real precondition is that the `openace-webui-launch` wrapper is
  installed and executable; the sudoers rule itself cannot be verified cheaply.
- **Target accounts:** uid 0 and the reserved range (uid < 1000) are refused.

## 8. Known gaps and road map

The following gaps were confirmed in the audit and, as issue #3374 asks, split into separate
follow-ups. This contract's `entry_points` matrix reflects them faithfully:

1. ~~Binding terminal/VSCode tokens to users~~, ~~server-side validation of the terminal
   `work_dir`~~, ~~a home-subtree lock for `/fs/browse` and `/fs/check-path`~~: **closed by #3376 /
   PR #3380.** After they merged on 2026-09-13, the matrix and this section were not updated for a
   while, which led integrators to treat fixed gaps as live risks (one of the reasons for #3410). The
   current boundary is §4 and `entry_point_details`.
2. Cross-user symlink writes through the file API: **closed by #3410** (see §4 and the CHANGELOG).
   Closed in the same batch:
   - the race window where download/delete-file/search under a root process "checked, then operated by
     path";
   - `openace-rm` deleting as root in the package install;
   - the missing home-subtree lock on `create-directory`;
   - the blanket 400 of `/api/fs` single-file paths and `/api/fs/home` in multi-base deployments;
   - `<base>/<account>` at 0755.
3. *(Number kept; the original item is closed.)*
4. Hardening against a restart invalidating issued tokens when the WebUI `token_secret` is not
   persisted.
5. The `sandboxed` level for interactive workspaces: delivered by #3378 (§6) and, without Kubernetes,
   by #3431/#3438 (§5.2, §5.3). Remaining follow-ups:
   - end-to-end acceptance on a real cluster (#3379, including 3100 endpoint reachability);
   - wiring the `terminal`/`vscode`/`fs` entry points into the sandbox;
   - WebUI history quota/GC;
   - token validation and instance management across multiple web replicas (reconcile already uses
     heartbeat exclusion, but pod ownership is still in-process memory).
6. Real end-to-end isolation acceptance for multi-user mode on Linux (concurrent users + an
   access-attempt matrix): the baseline was delivered by #3379. #3410 added probes for the upload
   symlink matrix, cross-user `create-directory`, `<base>/<account>` 0700, and "login provisioning does
   not follow a `.qwen` symlink" (`scripts/multiuser_acceptance.py` item b).
7. **No entry-point kill switch.** The contract reserves the `disabled` status, but **no deployment
   shape emits it yet**: the server has no setting to force the terminal/vscode/filesystem_api entry
   points off (pinned by the unit test `test_no_snapshot_emits_the_reserved_disabled_status`).
   Deployments that need "local workbench only" currently get it by granting no remote machine
   (machine assignment), and `entry_point_details[*].scope` lets integrators check that by machine.
8. **Conformance binding between the entry-point matrix and the code.**
   `entry_points`/`entry_point_details` are static audit conclusions versioned by `policy_revision`;
   no automatic check binds each operation's declaration to its implementation yet. Review and the
   matching cases in `tests/` keep them consistent for now.
9. ~~One configuration vocabulary~~: **done in #3446** (`workspace.isolation {level, backend}`, the
   same names in the snapshot and the root policy, startup validation per install method). Follow-up:
   an `openace isolation check` command and an installer `--isolation` option.

---

## 中文

Open ACE 为本地交互工作区(聊天 WebUI、文件接口、会话历史)提供**版本化的多用户
隔离能力契约**。管理员与可信接入方应通过 API 查询实际生效的能力,而不是依赖
README 声明或客户端传入的 capability 布尔值。本文档说明契约语义、部署要求、
reason code 对照与已知缺口。关联 issue:#3374(os_user)、#3378(sandboxed)、#3431(约束与本机
gVisor 容器)、#3438(本机 Kata 容器)。

本文是**参考文档**。要选择并配置一种隔离方式,请先看管理员指南
[WORKSPACE_ISOLATION](../guide/WORKSPACE_ISOLATION.md)。

## 1. 能力契约字段

查询端点:`GET /api/workspace/isolation-capabilities`(需要登录会话)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "shared | plain | bwrap | local-gvisor | local-kata | opensandbox:<tier>",
  "isolation_level": "none | os_user | sandboxed",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress", "kernel"],
  "reasons": [{"code": "...", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "enforced",
    "session_history": "enforced",
    "terminal": "remote_machine_scope",
    "vscode": "remote_machine_scope",
    "autonomous": "separate_contract"
  },
  "entry_point_details": {
    "filesystem_api": {
      "status": "enforced",
      "scope": "local_workspace",
      "covered_by_isolation_level": true,
      "operations": [
        {"name": "upload", "roots": ["home"],
         "symlink_policy": "never_followed_with_elevated_privilege"},
        {"name": "browse", "roots": ["home", "shared_projects"],
         "symlink_policy": "resolved_then_rejected_if_outside"}
      ],
      "access_control": ["home_subtree_lock", "shared_project_acl", "os_account_dac",
                         "nofollow_fd_descent"],
      "boundary": "...",
      "limitations": [],
      "residuals": [{"code": "shared_project_roots_are_cross_user_by_design", "message": "..."}]
    }
  },
  "policy_revision": "2026-09-26.3",
  "install_method": "package | docker | dev | unknown",
  "available_backends": {"local-kata": {"available": false, "reason": "install_method_docker"}, "...": {"available": true}}
}
```

- `local_workspace_multi_user`:本地交互工作区多用户隔离是否受支持。
- `backend`:即 `workspace.isolation.backend` 的名字(#3446),快照报告管理员配置的 backend——`plain`
  (每用户独立 WebUI 进程,以该用户的 OS 账户运行)、`bwrap`(#3431:同上,且每个进程运行在 systemd
  scope + bubblewrap 约束内,见 §5.1)、`shared`(共享单实例)、`local-gvisor`(#3431:sandboxed 等级,
  WebUI 运行于本机 gVisor 容器,见 §5.2)、`local-kata`(#3438:sandboxed 等级,WebUI 运行于本机 Kata
  容器,见 §5.3)或 `opensandbox:<tier>`(#3378:sandboxed 等级,WebUI 运行于该 tier 的 OpenSandbox pod,
  见 §6)。配置了但未就绪的 backend 报告为 `supported: false` 并给出原因,绝不会被替换成别的 backend;
  报告的等级是本机验证到的等级。
- `install_method` / `available_backends`:本部署的安装方式(`OPENACE_INSTALL_METHOD`),以及每个 backend
  在这里到底能否使用(不能时为 `install_method_docker` / `platform_unsupported`)。所配置 backend 的就绪
  状态见 `reasons`。
- `isolation_level` / `enforced` / `unsupported`:见下节。
- `reasons`:unsupported 时的机器可读原因(可能为空)。
- `entry_point_details`(#3410 新增,与 `entry_points` 同生共死):每个入口的机器
  可读细节,让接入方能区分「满足所声明等级」「由其它机制治理」「真实缺口」,
  而不必解析散文。字段:
  - `status`:与 `entry_points` 中同名键完全一致(单测钉住);
  - `scope`:`local_workspace` / `remote_machine` / `separate_contract`;
  - `covered_by_isolation_level`:该入口是否被本快照声明的 `isolation_level` 覆盖;
  - `operations[]`:`{name, roots, symlink_policy}`——**逐操作**声明可达根集合与
    符号链接策略(同一入口内并不统一,见 §4);
  - `access_control[]`:实际强制该入口的机制代号;
  - `limitations[]`:**真实缺口/未覆盖范围,参与准入判定**(`enforced` 入口恒为空);
  - `residuals[]`:已知且被接受的性质(如共享项目根按设计跨用户),**不参与准入判定**。
- **未知 `status` 或未评审过的 `policy_revision` 必须按拒绝处理**(fail closed):
  本契约会新增状态词,接入方不得把未知值当作通过。
- `entry_points`:各入口的隔离覆盖状态(§4)。**仅在 supported 快照中输出**——
  入口矩阵描述的是多用户隔离的覆盖面,unsupported(单用户/未验证/降级)部署
  不携带该矩阵,避免"无隔离"与"webui: enforced"同帧自相矛盾。该矩阵为静态
  审计结论,随 `policy_revision` 版本化;各入口与代码的 conformance 绑定为
  后续工作(§8)。
- `policy_revision`:契约语义版本;推导逻辑或入口矩阵变化时递增。

## 2. 隔离等级语义

等级序:`none < os_user < sandboxed`(`required_isolation` 请求参数与
下限 `workspace.isolation.level` 均按此序比较,只能抬高不能降低)。

| 等级 | 语义 |
|---|---|
| `none` | 所有本地交互会话共享同一服务账户运行,无用户间文件/环境/进程隔离(单用户轻量模式,by design) |
| `os_user` | 每用户独立系统账户与 HOME/TMP/XDG;WebUI 进程以该用户 UID 经 `sudo -u` 启动;子进程环境为显式白名单(真实模型 API key 永不进入,以代理 token 替代);文件接口应用层 home 子树锁 + OS 权限 |
| `sandboxed` | 每用户 WebUI 运行在拥有独立内核边界的沙箱里:OpenSandbox pod(#3378,§6)或本机容器(#3431/#3438,§5.2、§5.3)。以下为 pod 形态:每用户 WebUI 进程运行于 OpenSandbox pod(#3378):每实例独立 pod、digest-pinned 且在 image_allowlist 的专属 webui 镜像、deny-default 出口、恒有资源边界;身份为 per-instance token secret(非 OS 账户,实例销毁即失效);浏览器经控制面本地端口代理访问。部署要求与诚实边界见 §6 |

**边界声明**:`os_user` 共享宿主内核,没有命名空间隔离、没有网络出口策略——
"分目录/更换 HOME"不构成强运行时隔离。`resources`、`network_egress` 与
`kernel` 三个维度对**普通** `os_user` 始终列在 `unsupported`(交互 WebUI 无 per-task
cgroup,仅实例数上限与空闲清理;无出口策略;共享宿主内核)。约束模式(§5.1)会补上
`resources` 与 `network_egress`。`sandboxed` 等级的维度语义与验证边界见 §5.2、§5.3 与 §6.1。autonomous 任务的资源与沙箱策略沿用独立的
#2022 sandbox 契约(`sandbox_effective_policy`),见 [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md)。

**平台边界**:仅 Linux 部署可申报 `os_user`。macOS 跳过系统用户创建,Open ACE
无法建立/验证身份映射;Windows 强制单实例。两者契约均为
`unsupported/platform_unsupported`。

## 3. Reason Code 对照

四套代码,职责不同:

### 3.1 契约 `reasons[]`(部署级:为什么整体 unsupported)

| code | 含义 |
|---|---|
| `webui_disabled` | WebUI 管理器未启用,无交互工作区运行时 |
| `platform_unsupported` | 非 Linux 平台(Windows/macOS/其他) |
| `isolation_backend_shared` | `workspace.isolation.backend` 为 `shared`(单用户轻量模式,预期状态而非缺陷) |
| `launch_path_degraded` | WebUI 启动路径无法承载按用户实例(message 括注 §3.3 的具体原因,如 dev 目录模式/包装器缺失);契约与 /user-url 门闸看同一条路径,不会互相矛盾 |
| `launch_path_unverified` | 冷 worker 上 manager 尚未初始化、探针未运行:等级为 **provisional**(部署形态达标),manager 初始化后自动消除;按 revision 缓存/灰度的接入方应同时检查该码 |

### 3.2 门闸 `error_code`(请求级:`user-url?required_isolation=...` 的拒绝)

| code | 含义 |
|---|---|
| `invalid_isolation_level` | `required_isolation` 取值非法(合法值:none/os_user/sandboxed) |
| `isolation_level_unsupported` | 请求等级超过部署实际支持等级,拒绝以弱隔离静默启动 |
| `identity_mapping_missing` | 用户在数据库中无 `system_account` 映射;显式要求隔离时不做 username 静默回退(**仅 os_user 链**;sandboxed 的身份是 per-instance token,不查 OS 账户) |
| `per_user_launch_unavailable` | 启动路径无法以该用户 UID 运行(message 内嵌 §3.3 的原因码;**仅 os_user 链**) |

OpenSandbox pod 形态下,`sandboxed` 请求的门闸是能力探测本身(本机容器形态 §5.2/§5.3 仍走 os_user 身份链):等级不满足时按 §3.4 的探测码
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

约束模式与本机容器模式另有各自的 `confinement_*` 原因码,见 §5.1、§5.2、§5.3。

### 3.4 sandboxed 探测与运行期原因码(#3378,opensandbox backend)

`sandboxed` 等级的探测是**零 pod、配置面 fail-closed** 的(不创建 pod 即可
判定);以下前 8 个为探测级 reason(命中即拒绝),后 4 个为快照级(出现在契约
`reasons[]` 中、不阻止申报),最后 2 个为运行期错误码(启动器抛出,非探测码)。

| code | 级别 | 含义 | 修复动作 |
|---|---|---|---|
| `sandbox_backend_unconfigured` | 探测 | sandbox-backends.json 缺失、不可解析或未配置 | 提供/修复后端配置(见 [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md) §3) |
| `sandbox_tier_missing` | 探测 | `workspace.isolation.tier`(或后端 `default_tier`)在 `endpoints` 中无对应条目 | 修正 `isolation.tier` 或在 `endpoints` 补齐该 tier |
| `webui_image_missing` | 探测 | 该 tier 未配置 `webui_image` | 配置含 qwen-code-webui 的镜像(参考构建:`scripts/docker/webui-sandbox.Dockerfile`) |
| `webui_image_not_pinned` | 探测 | `webui_image` 非 digest-pinned(`name@sha256:<64 hex>`) | 改用 digest 引用——tag 可被重指向,会架空白名单 |
| `webui_image_not_allowed` | 探测 | `webui_image` 不在 `image_allowlist` | 将镜像加入 `image_allowlist`,或换用已在列的镜像 |
| `sandbox_api_key_missing` | 探测 | 该 tier 的 `api_key_env` 指定的环境变量在本进程为空——创建 pod 的第一个 API 调用就会失败;契约与启动路径不得对同一台主机给出矛盾结论(#3375 原则) | 在运行 web 进程的环境中设置该 API key(sandbox-backends.json 的 `api_key_env` 字段) |
| `sandbox_multi_process_unsupported` | 探测 | 存在**另一个**持有新鲜心跳的 web 进程(心跳根不可读/不可判定时同样视为存在——无法证明本进程是独苗):per-instance token secret 与实例管理是进程内存态,跨副本时约 2/3 的 token 校验会随机 401;**出货的 k8s manifest(3 副本)默认即此形态,`opensandbox` backend 在那里报告为 unsupported,启动一律被拒绝** | 单 web 进程部署(如 docker-compose 单副本)才可使用 `opensandbox`;多副本形态使用 OS 账户类 backend,或等待多副本实例管理支持(follow-up) |
| `sandbox_proxy_unreachable` | 探测 | `workspace.webui_callback_url` 未设置;或该 URL 在该 tier 出口策略下不可达(loopback;sidecar tier 不在 `egress_allow_hosts`;CNI tier 为私网/集群内地址) | 设置 `webui_callback_url`;sidecar tier 将控制面主机名加入 `egress_allow_hosts`;CNI tier 保证公网可达 |
| `sandbox_runtime_unverified` | 快照 | 静态视图:仅配置面验证通过;kernel/network_egress 待首个 pod boot probe 确认(memo 在控制面重启、同 tier probe 失败或条目超过 1h 后回退到该状态) | 无需修复;首次成功启动 pod 后自动升级 |
| `sandbox_launch_unverified` | 快照 | 冷 worker(manager 尚未初始化、沙箱启动链路未在本进程演练过):等级为 **provisional**,manager 初始化后自动消除(对齐 os_user 的 `launch_path_unverified` 先例) | 无需修复;首次工作区活动后消失 |
| `sandbox_runtime_kata_negative_only` | 快照 | 首 pod probe 通过,但 kernel 仅负向验证(Kata 只能排除 gVisor,无法与未隔离 runc 区分):kernel 保持 unsupported | 无需修复;换 gVisor tier 可获得 kernel 正向验证 |
| `sandbox_runtime_egress_negative_only` | 快照 | 首 pod probe 通过,但出口仅负向验证(gVisor/CNI tier 的集群级 deny-default 对照——证明拒绝路径存在,不能证明放行生效):network_egress 保持 unsupported;仅 sidecar tier 的 `/policy` 实读才升级(T-M) | 无需修复;需要申报 network_egress 时使用 sidecar attestation tier |
| `sandbox_create_failed` | 运行期 | create 请求被拒、create 失败或 boot probe 失败(probe 失败会立即销毁 pod) | 查看 message 内嵌原因(含 provider probe 码透传) |
| `sandbox_endpoint_unresolved` | 运行期 | pod 的 3100 端点经 `GET /sandboxes/{id}/endpoints/3100` 解析失败——网关无法为未声明端口应答(外部假设,集群端验证归 #3379) | 检查网关对未声明端口的应答行为;该通路未经真实集群验证 |

## 4. 入口覆盖矩阵

| 入口 | 状态 | scope | 覆盖范围与证据 |
|---|---|---|---|
| webui(聊天工具) | enforced | local_workspace | 每用户独立实例/UID/端口/token;停止与 token 撤销按 user_id 隔离 |
| filesystem_api | enforced | local_workspace | 八个 `/api/fs` 操作逐个声明 roots 与符号链接策略(见下表与 `entry_point_details`);所有请求路径先 realpath,落到声明根之外即拒;upload/download/delete-file/search 在 root 进程下自 home 根逐段 `O_NOFOLLOW` 以目录 fd 下降后再写入(`renameat`)、读取、删除(`unlinkat`)或遍历(`fwalk`),包安装非 root 形态则委托给以目标账户身份探测/写入/删除的 wrapper(#3410);`create-directory` 与 check-path 同可创建集;单文件路径锁与 browse 同口径(逐 base home 根);`<base>/<account>` 0700 |
| session_history | enforced | local_workspace | 应用层所有权闸门 + 租户 fail-closed |
| terminal | remote_machine_scope | remote_machine | **远端机器能力**:本入口不分配本地路径/账户/令牌;治理方为 machine assignment ACL + 会话所有者 + 租户(#3376)。本地隔离等级不覆盖远端机器自身的用户隔离 |
| vscode | remote_machine_scope | remote_machine | 同上(code-server);owner=请求者(#3376 `VSCodeOwnerStore`),proxy/WS 同闸门 |
| autonomous | separate_contract | separate_contract | 沿用 #2022 sandbox 契约,不在本契约范围 |

**`filesystem_api` 逐操作边界**(即 `entry_point_details.filesystem_api.operations`,
同一入口内并不统一,这正是单句散文无法如实描述的原因):

| 操作 | roots | 符号链接策略 |
|---|---|---|
| `browse` | home, shared_projects | resolved_then_rejected_if_outside |
| `check-path` | home, shared_projects, workspace_root, workspace_root_first_level_non_home | resolved_then_rejected_if_outside |
| `create-directory` | 同 `check-path` | resolved_then_rejected_if_outside |
| `home` | home | not_applicable |
| `upload` | home | **never_followed_with_elevated_privilege** |
| `download` | home | **never_followed_with_elevated_privilege** |
| `delete-file` | home | **never_followed_with_elevated_privilege** |
| `search` | home | **never_followed_with_elevated_privilege** |

两个策略词的含义:

- `resolved_then_rejected_if_outside`:请求路径先 realpath,落到该行 roots 之外即拒。
  browse/check-path/create-directory 对有 `system_account` 的用户以目标账户身份执行
  (`sudo -u`),因此其后的访问受该账户自身权限约束。
- `never_followed_with_elevated_privilege`:在上一条的基础上,**home 根之下的任何
  符号链接都不会被权限高于目标账户的进程跟随**。root 进程先以 `fstat` 断言 home 根
  属于目标账户,再自 home 根逐段 `O_NOFOLLOW` 打开目录 fd,写入/读取/删除/遍历都
  相对该 fd 完成(遍历时跳过符号链接本身);单用户进程本身就是该账户;包安装非 root
  形态委托给 `openace-write-as`/`openace-rm`,二者在校验用户与路径前缀之后的每一次
  文件系统探测、写入与删除都经 `runuser` 以目标账户身份执行。

`workspace_root_first_level_non_home` 指 base dir 的**一级**子目录且**不是任何用户的
home 根**(`_check_path_rejection_reason` 规则 3);写入口不包含 shared_projects——
browse/check-path 可读共享项目,但单文件写/下载仍限本人 home,放宽属于新功能。

**部署前提(`filesystem_api: enforced` 的证据边界)**:

1. workspace base dir 必须 root 所有且非用户可写(`docker-entrypoint.sh` 以 root
   `mkdir -p` 建为 `root:root 0755`,`/home` 为 `chmod 755`)。单文件操作的信任锚是
   `realpath(<base>/<account>)`,其**下**不以高于目标账户的权限跟随任何符号链接,
   root 分支另以 `fstat` 断言该锚属于目标账户。账户目录本身会被解析,但**不能借此
   把 home 挪到任意卷**:请求路径先 realpath,再与**按配置原样**的 base 前缀比较,
   解析后落在所有已配置 base 之外的 home 会被直接拒绝。要挂载大卷,请让
   `<base>/<account>` 指向某个已配置 base 之内的位置,或把卷路径加入
   `WORKSPACE_BASE_DIR`。
2. **包安装非 root 多用户形态必须重装 `openace-write-as` 与 `openace-rm`**(≥ 本次
   版本,重跑 `scripts/install-central/package-method/install.sh` 即可):该形态下
   web 进程无法穿越 0700 home,上传与删除由这两个 wrapper 以目标账户身份完成;
   符号链接/目录拒绝(exit 5 / exit 6)由路由翻译成 400。**若已安装的 wrapper 版本
   过旧**(缺少能力标记 `openace-write-as-capability: symlink-refusal=1` 或
   `openace-rm-capability: account-scoped=1`),路由直接 **fail-closed 拒绝对应的
   上传或删除(500,错误信息给出重装指引)**——因此 `enforced` 声明对所有部署都
   成立:要么 wrapper 以目标账户执行,要么操作根本不发生。
3. `<base>/<account>` 为 0700:Docker 形态下新目录以 0700 创建,既有卷在容器启动
   (entrypoint)与登录供给(root 进程,经不跟随符号链接的描述符)时收敛到 0700,
   且只改属于该账户的目录。包安装形态的 `/home/<account>` 权限由 `useradd`
   (`HOME_MODE`)决定;自定义 base 时目录由 `openace-mkdir` 以目标账户身份按默认
   umask 创建,服务账户无权修改,请运维自行将既有账户目录设为 0700。
4. 三项已声明 residual(共享项目根按设计跨用户、包安装形态的 wrapper 前提、未映射
   `system_account` 的用户以 web 进程身份在 `<base>/<username>` 内工作)在
   `entry_point_details.filesystem_api.residuals` 中机器可读,**不参与准入判定**。
   未映射用户的用户名若恰是另一用户的 `system_account`,root 进程下该用户**没有
   任何 home 根**(所有 `/api/fs` 操作都会被拒)——两者会指向同一个 OS 账户与目录。
   名为 `shared` 的账户(无论来自 `system_account` 还是用户名,后者也可能由 SSO /
   组织同步产生)同样没有 home 根:`<base>/shared` 是共享项目命名空间根,不是 home。
5. `os_user` 共享宿主内核:`resources` / `network_egress` / `kernel` 三维度仍
   `unsupported`(§2 边界声明)。

**sandboxed 部署下的矩阵取值(#3378 起)**:矩阵按等级
输出——`webui` 随 `sandboxed` 等级进入 pod(`enforced`);`terminal`/`vscode`/
`filesystem_api` 输出 **`sandboxed_entry_not_wired`**(执行体仍在控制面宿主上,
未接线到用户的沙箱实例),对 sandboxed 用户的 `/fs` host 树亦不可用——其文件在
pod 内,由 webui 自带的 in-pod 文件浏览承载。这三个入口在 sandboxed 快照中保留各自
原有的 `limitations`(terminal/vscode 的远端范围说明)并追加
`sandboxed_entry_not_wired`;`filesystem_api` 的 `boundary` 改为说明未接线,且不再
携带只描述 host 树的 residuals。`session_history` 仍 `enforced`
(per-pod 快照存储);`autonomous` 仍 `separate_contract`。os_user/none 快照的
矩阵取值见上表(#3410 重标定);本机容器形态(§5.2、§5.3)沿用 os_user 矩阵,因为其 home 是宿主目录。
`filesystem_api` 在 sandboxed 下与 os_user 下的取值
不同是**刻意**的——本地强制不等于已接线到 pod。

## 5. 多用户模式部署要求

契约是否报告 `supported/os_user` 由**启动路径就绪探针**判定(§3.3:WebUI 解析、
非 dev 目录模式、`openace-webui-launch` 包装器、sudo),不再以 Docker 布局为
先决条件——包安装形态(scripts/install-central/package-method,`sudo -u` 与
wrapper 齐备)同样可以验证并强制 os_user。

两种参考部署:

1. **Docker 多用户**:`docker-compose.multi-user.yml` 叠加(root + `OPENACE_ALLOW_ROOT_MULTI_USER=1` + `WORKSPACE_BASE_DIR=/workspace` + `WORKSPACE_ISOLATION_BACKEND=plain`,entrypoint 会在生成的配置中写入 `workspace.isolation {"level": "os_user", "backend": "plain"}`),镜像内置 useradd 与 wrapper,系统账户自动供给;
2. **包安装多用户**:installer 的多用户路径(非 root 运行 + `/home` 布局),写入同样的 `isolation` 配置块,探针健康即报告 os_user;每用户系统账户需已存在(getpwnam 可解析,uid ≥ 1000)。

共同要求:sudo/sudoers 中受限的 `openace-webui-launch` 包装器;无内核特殊要求
(os_user 等级即 OS 账户边界);生产基线密钥见 compose 注释。

探针降级(dev 目录模式/缺 wrapper 等)时契约如实返回 `launch_path_degraded`
unsupported——**不会**静默降级到共享账户后宣称支持;由于 `workspace.isolation.level` 就是下限,此时每一次
启动都会被结构化拒绝,直到启动路径修复。

**安装方式决定下面的 backend 能否使用**:Docker 安装只支持 `shared`、`plain` 与 `opensandbox`;`bwrap`
与本机容器(§5.1–§5.3)需要 Linux 主机上的包安装,在其他安装方式下启动时即被拒绝(#3446)。见
[WORKSPACE_ISOLATION](../guide/WORKSPACE_ISOLATION.md#2-安装方式决定了哪些可用) 中的安装方式对照表。

### 5.1 Backend `bwrap`:受约束的 OS 账户(#3431)

`workspace.isolation {"level": "os_user", "backend": "bwrap"}` 让每个 OS 账户 WebUI 在启动时被约束,
仍属 `os_user` 等级(共享宿主内核),但 `resources` 与 `network_egress` 两个维度
变为 `enforced`,`backend` 报告 `bwrap`:

| 层 | 机制 | 效果 |
|---|---|---|
| 资源 | `systemd-run --scope`(`MemoryMax`/`MemorySwapMax=0`/`CPUQuota`/`TasksMax`) | cgroup v2 硬限制,含 fork bomb 上限 |
| 身份 | `setpriv --reuid/--regid --init-groups --no-new-privs`,能力集与 bounding set 清空 | 只带账户自己的附加组(`systemd-run --scope --uid` 会保留调用者即 root 的 group 0,故不用它) |
| 文件系统 | bubblewrap:宿主根只读、`/tmp` `/var/tmp` `/run` 与 workspace base 为空 tmpfs | 仅本人 home 与 `<base>/shared` 被绑回;其他用户 home 不可见(不只是拒绝访问) |
| 网络 | bubblewrap `--unshare-net`(仅 loopback) + 宿主侧出口代理 | 唯一出路是代理;代理只放行 `host:port` 白名单(`webui_callback_url` 的主机:端口 + `isolation.egress_allow`,**只取服务端配置,绝不取请求 Host 头**),其它一律 403;判定记入 `/var/log/openace-webui/<uid>.egress.log`(root 所有 0600,由 root 打开后把描述符交给宿主侧进程——沙箱内进程无法打开、替换或截断它;同一账户在宿主上的其它进程理论上可附着到持有描述符的宿主侧进程,不在防护范围内)。日志有界且可能被汇总:**ALLOW/FAIL 判定从不丢弃**(超出每秒 50 行或其单独的字节上限时,按**所匹配的白名单条目**汇总计数写出——条目集合有限;主机在匹配与记录前统一规范化,含空白/控制字符的主机及非短大写方法按 BAD 拒绝),DENY/BAD 另有每秒 50 行与每次启动 8 MiB 的上限(拒绝洪泛无法掩盖放行记录);每个客户端字段截断到 256 字符,超过 8 MiB 的旧日志在启动时轮转为 `.1` |
| 入口 | 反向隧道 | 沙箱内只向宿主侧 socket **主动外连**(保持少量空闲隧道,浏览器连接到来时配对);socket 目录以**只读**方式绑入沙箱,宿主侧从不跟随沙箱可写的路径;同时处理的浏览器连接数取 `TasksMax / 8`(上限 256),单个来源地址至多占一半;双向静默 300 秒的连接会被关闭——匿名客户端不能耗尽 scope 的任务配额 |

配置项(`config.json` 的 `workspace`):

| 键 | 默认 | 说明 |
|---|---|---|
| `limits.memory` | `4G` | systemd `MemoryMax`(容器类 backend:`--memory`) |
| `limits.cpu_percent` | `200` | 百分比,`CPUQuota`(容器类 backend:`--cpus`) |
| `limits.tasks` | `512` | `TasksMax`(容器类 backend:`--pids-limit` / `--ulimit nproc`) |
| `egress_allow` | `[]` | 额外放行的 `host:port`(支持 `*.domain:port`、`[ipv6]:port`) |

**`webui_callback_url` 为必填**:未设置时 API 地址会取自请求的 Host 头(用户可控),
白名单就会被用户改写,因此探针报告 `confinement_callback_url_missing`。

**账户限制**:目标账户须 uid ≥ 1000,且不得属于特权组(`sudo`/`wheel`/`admin`/`adm`/
`shadow`/`disk`/`docker`/`lxd`/`incus`/`incus-admin`/`libvirt`/`kvm`/`systemd-journal`/
`staff`/`lpadmin` 或 gid 0;名单式,站点特有的特权组请用 `denied_groups` 追加)——
沙箱内的文件访问仍按真实附加组判定,这些组会被带进沙箱。策略文件可用
`denied_groups` 追加组名、用 `bases` 限定允许的 workspace base(如 `["/home"]`)。
WebUI 可执行文件(解析符号链接后)、它所在的整个 npm `node_modules` 树(也包含它启动的
`qwen` CLI),以及策略 `path` 中的每个目录都须为 root 所有且非组/其他人可写,否则拒绝启动
(`#!/usr/bin/env node` 通过该 PATH 找 `node`)——即 npm 全局前缀必须归 root。

部署要求(包安装形态;Docker 形态无 systemd,启用后按下列原因码 fail closed):

- Linux + systemd(cgroup v2)、`bubblewrap` ≥ 0.8(需要 `--disable-userns`)、`setpriv`(util-linux);
- 非特权 user namespace 可用。Ubuntu 24.04+ 的 AppArmor 默认限制它:加载发行版自带的
  `bwrap-userns-restrict` profile(与 Codex/Claude Code 的要求相同);
- installer 安装 `/usr/local/bin/openace-webui-confine`、根属主策略文件
  `/etc/openace/webui-confine.json`(列出允许以用户身份启动的 WebUI 可执行文件与沙箱内
  `PATH`),以及 sudoers 规则 `<service> ALL=(root) NOPASSWD: /usr/local/bin/openace-webui-confine launch *`;
- WebUI 运行时须遵循 `HTTP(S)_PROXY`:Node 22.21+ 在 `NODE_USE_ENV_PROXY=1`(wrapper
  已设置)下对 `fetch` 生效;不遵循代理的请求没有网络(fail closed)。

**fail-closed**:配置了约束但宿主不满足时,启动路径探针报告 `launch_path_degraded`,
括注下列原因码之一,不会静默以未约束方式启动:
`confinement_platform_unsupported`、`confinement_wrapper_missing`、
`confinement_bwrap_missing`、`confinement_setpriv_missing`、
`confinement_systemd_unavailable`、`confinement_userns_unavailable`、
`confinement_bwrap_too_old`、`confinement_policy_invalid`、`confinement_callback_url_missing`、
`confinement_check_failed`。
dev 目录模式与"服务账户即目标账户"两种无法约束的启动形态在启动时直接拒绝。冷 worker(manager 未初始化、
探针未跑)只报告普通 os_user 维度。

**诚实声明**:

- **威胁模型**:约束保护的是"沙箱内的 WebUI/agent 与其它本地用户",**不**防御被攻破的
  服务账户——它仍持有 sudoers 中的其它权限(以任意用户运行 git 等)。启用约束后重跑
  installer,会去掉可启动**未约束** WebUI 的 `openace-webui-launch` 规则(纵深防御)。
- `kernel` 仍 `unsupported`:与宿主共享内核;需要内核隔离请用 sandboxed 等级。
- WebUI 的 `--token-secret` 仍在其命令行上(qwen-code-webui 只接受命令行参数,既有行为)。
- 出口代理按客户端给出的主机名与端口判定,**不检查 TLS**(与 Claude Code 沙箱代理的已知
  局限相同);放行宽泛域名即留下外带通道。白名单主机名解析到的地址不再二次校验。
- 宿主侧入口转发仍监听 `0.0.0.0:<port>`(与未约束时的暴露面相同),依赖 WebUI token。
- 沙箱内任意进程都能占满反向隧道池,让本用户自己的 WebUI 不可达——影响只限该用户自己的
  工作区(沙箱本来就能替换自己的 WebUI),属自我拒绝服务。
- 验收:`scripts/webui_confine_acceptance.py`(需一次性 Linux 主机,
  `CONFINE_ACCEPTANCE_DISPOSABLE=1`)。

### 5.2 Backend `local-gvisor`:本机 gVisor 容器(#3431 Option 2)

不需要 Kubernetes 的 **sandboxed** 等级:每个用户的 WebUI 运行在本机 Docker 容器里,运行时为
gVisor(runsc)。与 §5.1 共用同一个 root 入口、宿主侧进程与出口代理,只是沙箱从 bubblewrap
换成了 gVisor 容器;身份仍是该用户的 OS 账户,home 仍是宿主上的 `<base>/<account>`。

| 维度 | 机制 |
|---|---|
| kernel | gVisor 用户态内核;readiness 探针在容器内读取 `/proc/version`,**正向**确认是 gVisor |
| 资源 | `docker run --memory/--memory-swap/--cpus` + `--ulimit nproc=<tasks_max>`(gVisor 内核按 uid 强制的进程数上限);`--pids-limit` 在 gVisor 下限制的是 sentry 的宿主线程,取 `max(tasks_max, 256)` |
| 文件系统 | 只读镜像根 + `--tmpfs /tmp`;只挂入本人 home、`<base>/shared` 与日志目录;`--user <uid>:<gid>` + 账户附加组(`--group-add`) |
| 进程/权限 | `--cap-drop ALL`、`--security-opt no-new-privileges`、`--init` |
| 网络 | `--network none`;出入口与 §5.1 相同:反向隧道 + 只放行白名单 `host:port` 的出口代理(经只读挂入的 UNIX socket,需 runsc `--host-uds=open`) |
| 环境 | WebUI 环境经容器 stdin 传入,不出现在命令行,`docker inspect` 也看不到 |

快照:`isolation_level = sandboxed`、`backend = local-gvisor`、七个维度全部 `enforced`。
与 OpenSandbox pod 形态(§6)的区别是**有意的**:

- 入口矩阵沿用 os_user 的那一份——home 是宿主目录,`/api/fs` 等入口仍然 `enforced`;
- `/user-url` 门闸仍走 OS 账户链(`system_account` 映射、账户检查);
- 启动形态仍是"每用户本地进程"形态,**绝不**路由到 OpenSandbox pod 启动器(pod 形态按
  `opensandbox:*` backend 判定,而不是按等级)。

部署要求(Linux 包安装形态,单机 Docker 即可):

1. Docker + gVisor,并注册一个专用运行时(`/etc/docker/daemon.json`):
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
   `--host-uds=open` 只允许**连接**宿主 socket(不能创建),且只作用于使用该运行时的容器;容器能连接的
   是挂入目录(home、shared、日志目录、只读 socket 目录)中已存在且权限允许的 socket——与 §5.1 的
   bubblewrap 形态一致。
2. WebUI 镜像:用 `scripts/docker/webui-sandbox.Dockerfile` 构建(含 node、python3 与固定版本的
   qwen-code-webui),并在策略文件里**按内容固定**:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
    "local-gvisor": {"image": "sha256:<64 hex>", "runtime": "runsc-openace"}}
   ```
   分节以 backend 命名(#3446),`docker` 由各容器类 backend 共用;#3446 之前的 `container` 分节会被拒绝,
   错误信息给出转换写法。`image` 必须是 `name@sha256:<64 hex>` 或本地镜像 ID `sha256:<64 hex>`(tag
   可被改指,拒绝);`webui` 列出的是**镜像内**的路径;installer 重跑会保留这些分节。
3. `workspace.isolation {"level": "sandboxed", "backend": "local-gvisor"}`,`workspace.webui_callback_url`
   必填(同 §5.1);可用 `isolation.container_webui` 改镜像内 WebUI 路径(默认 `/usr/bin/qwen-code-webui`)。

要求 `fs.protected_symlinks=1`(Debian/Ubuntu/RHEL 默认):日志目录按路径由 docker(root)挂载,
该设置阻止账户在粘滞的 `/tmp` 中用符号链接引导挂载源;未满足时报 `confinement_symlinks_unprotected`。

readiness:manager 通过 `sudo -n openace-webui-confine launch --probe --backend local-gvisor`(root)检查——docker CLI 为
root 所控、运行时已注册、镜像已在本地(启动时不拉取)、容器内内核为 gVisor、容器能连上只读挂入的
宿主 UNIX socket。成功结果缓存 1 小时,失败 30 秒。原因码:`confinement_docker_unavailable`、
`confinement_runtime_missing`、`confinement_image_missing`、`confinement_kernel_unverified`、
`confinement_runtime_host_uds_disabled`、`confinement_symlinks_unprotected`、`confinement_policy_invalid`、
`confinement_check_failed`
(以及 §5.1 共有的 `confinement_platform_unsupported`、`confinement_callback_url_missing`、
`confinement_wrapper_missing`)。

生命周期:root 启动进程在容器存活期间一直作为父进程——manager 对 sudo 发 SIGTERM → 转发给 docker
CLI → 容器 → WebUI 退出、`--rm` 删除容器;manager 升级为 SIGKILL(sudo 不转发)时,root 启动进程
发现自己被重新挂接,按 root 所有的 cid 文件中记录的 id 对该容器执行 `docker rm -f`;宿主侧进程退出时,
root 启动进程同样删除容器。同一端口上遗留的旧容器在启动前被移除,运行目录在结束时清理。若宿主侧进程意外消失,容器内的看门狗约 30 秒后停止 WebUI,`--rm` 删除容器。

**诚实声明**:

- 本节只覆盖 gVisor(runsc);Kata 见 §5.3。
- 与 §5.1 相同:出口代理不检查 TLS;不防御被攻破的服务账户;宿主侧入口仍监听 `0.0.0.0:<port>`。
- 镜像内容(node 版本、工具链)即 agent 可用的工具集,属产品决定。

### 5.3 Backend `local-kata`:本机 Kata 容器(#3438)

与 §5.2 相同的本机容器形态,运行时换成 **Kata Containers**:每个 WebUI 跑在一台轻量虚拟机里,
拥有自己的 guest 内核。快照:`isolation_level = sandboxed`、`backend = local-kata`、
七个维度全部 `enforced`;入口矩阵、身份门闸、本地启动形态与 §5.2 一致。

与 §5.2 的差别:

| 项 | Kata 形态 |
|---|---|
| 内核隔离 | 硬件虚拟化(KVM);guest 内核与宿主不同 |
| 出入口通道 | Kata guest 经 virtio-fs 看到的宿主 UNIX socket **无法连接**(实测 `ECONNREFUSED`),因此不挂 socket 目录;入口与出口的所有连接以**帧**的形式复用在容器的 stdin/stdout 上(每条流独立的信用窗口,慢流不阻塞通道)。容器仍是 `--network none`,不建网桥、不写防火墙规则。宿主只打开 INGRESS 流、容器只打开 EGRESS 流,容器同时打开的流(含仍在运行的出口连接)有上限;任何违反帧协议的行为都会断开整个通道:宿主侧进程随即退出,root 启动进程据此删除容器(fail closed,不依赖容器配合) |
| 看门狗 | 宿主每 5 秒发一次心跳;容器侧 30 秒收不到任何帧、或 stdin 结束,即停止 WebUI |
| 任务上限 | `--pids-limit` 与 `--ulimit nproc` 都作用在 guest 内,取 `tasks_max`(无 gVisor 的 256 下限) |

部署要求:

1. 有 `/dev/kvm` 的 Linux 主机(物理机,或开启嵌套虚拟化的虚拟机)。
2. **Kata ≥ 3.32.0**:Docker 29 会在 OCI spec 里带上 `time` namespace,更早的 Kata 报
   `invalid namespace type`(kata-containers#13082 修复,首发于 3.32.0)。
3. 运行时:把 `containerd-shim-kata-v2` 以 root 所有安装到标准系统 `bin` 目录(如 `/usr/local/bin`,它也在
   dockerd 的 PATH 上)即可直接用 `io.containerd.kata.v2`,**无需**修改 `daemon.json` 或重启 Docker;也可以在
   `daemon.json` 注册一个名字。策略文件中有一个 `local-kata` 分节:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
    "local-kata": {"image": "sha256:<64 hex>", "runtime": "io.containerd.kata.v2"}}
   ```
   用 shim 名时,探针只在标准系统 `bin` 目录中查找 shim,并要求它为 root 所控。
4. 嵌套虚拟化下 guest 启动较慢(三层嵌套实测约 70 秒),Kata 默认的 `dial_timeout = 45` 不够,需要在
   `/etc/kata-containers/configuration.toml` 中调大(例如 180);物理机上默认值即可。
5. `workspace.isolation {"level": "sandboxed", "backend": "local-kata"}`,其余同 §5.2。

readiness:`sudo -n openace-webui-confine launch --probe --backend local-kata`(root)——除 §5.2 的检查外:
`/dev/kvm` 存在;用 Kata 运行时启动一个探针容器,并**在宿主侧正向确认**:docker 报告的
`State.Pid` 是 hypervisor 进程(`qemu-system-*` / `cloud-hypervisor` / `firecracker` / `stratovirt`),其父进程是
为**这个**容器 id 启动的 `containerd-shim-kata-v2`(runc 下 `State.Pid` 是容器自己的 init 进程);
guest 的 `uname -r` 与宿主不同;stdio 通道能往返。这弥补了 §6.4 所述 pod 形态下"Kata 只能确认不是
gVisor"的单向判定。guest 启动最多等待 180 秒,整个探针最坏约 7 分钟(manager 给 8 分钟)。新增原因码:`confinement_kvm_unavailable`、
`confinement_channel_failed`;其余同 §5.2。

**诚实声明**:

- 吞吐:stdio 通道比网桥 TCP 慢。三层嵌套实测 stdio 双向各约 1.7 MB/s,同环境网桥 TCP 约
  11.4 MB/s;对 WebUI 与 LLM 流式响应足够,经出口代理的大文件下载会明显变慢。这是为了不给 guest
  任何网络接口、不在宿主上管理防火墙规则而做的取舍。
- 内存:`--memory` 决定 guest 虚拟机的大小,Kata 自身还有额外开销(默认 guest 内存等),宿主上看到的
  占用高于该值。
- 其余同 §5.2。

## 6. Backend `opensandbox`:部署要求与诚实声明

`workspace.isolation {"level": "sandboxed", "backend": "opensandbox"}` = WebUI 进程运行于 OpenSandbox pod:每用户每实例一个独立 pod、
digest-pinned 且在 image_allowlist 的专属 webui 镜像、deny-default 出口;浏览器
经控制面本地端口代理访问(端口形态,无路径重写假设);身份为 per-instance
token secret(实例销毁即失效,无 OS 账户/sudo 链)。探测**不检查平台**——pod 在
远端集群。pod 形态只用于这个 backend(#3446):不会因配置了 `webui_image` 而被推断出来;探测失败时快照为
unsupported 并给出探测原因,不会回落到 OS 账户形态。

`POLICY_REVISION` 2026-09-12.1 的变更:`sandboxed` 等级与零 pod 探测入词表;
新增 `kernel` 维度(`os_user` 如实申报 unsupported——共享宿主内核);user-url
门闸增加 sandboxed 分支(按探测 reason 拒绝,不再走 OS 账户链)。

`POLICY_REVISION` 2026-09-12.2 的变更(全部变化):

- **T-B 闸门语义**(已被 #3446 取代,backend 改为显式配置):启动形态曾是满足 max(下限, 请求) 的最强已验证
  形态,因此配置了 `webui_image` 就会把默认启动切到 pod。自 #3446 起,启动形态只取决于
  `workspace.isolation.backend`。
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
4. HTTPS:沿用外部反代的端口段映射(`/webui/<port>`,见 [NGINX](../guide/NGINX.md))
   ——sandboxed 形态的浏览器端口是本地代理端口,取自同一 port_range
   (默认 3100-3200)。
5. `workspace.isolation.tier` 可选:指定交互 pod 落在哪个 endpoint tier,缺省用后端 `default_tier`。pod 的
   身份是其 per-instance token,因此门闸按 sandboxed 分支放行 `sandboxed` 要求,不走 OS 账户链(身份映射、
   sudo 启动)。
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
- **pod 内 Kata 的 kernel 验证仅负向**(单向判定:只能排除 gVisor,不能与未隔离 runc 区分):
  kernel 保持 unsupported + `sandbox_runtime_kata_negative_only`;gVisor 正向
  识别才升级 enforced。(§5.3 的本机 Kata 容器改为在宿主侧正向确认 Kata。)
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
  新鲜窗口(≤ 660s)后自然过期,期间 `opensandbox` backend 报告为 unsupported,启动一律被拒绝。
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

**安全准入示例(#3410)**。下面这段判定与单元测试
`TestDocumentedAdmissionPredicate`(`tests/unit/test_workspace_isolation_contract_3374.py`,
它直接执行本文中英两个版本里的这段代码)**逐项一致**——改一处必须改另一处,否则文档会随版本漂移:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities > caps.json
python3 - caps.json <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
NEEDED = ("webui", "filesystem_api", "session_history")    # 本地工作台入口
KNOWN  = {"enforced", "partial", "remote_machine_scope",
          "separate_contract", "sandboxed_entry_not_wired", "disabled"}
REVIEWED = {"2026-09-26.3"}                                # 你已评审过的 revision
d = c.get("entry_point_details", {})
ok = (
    c.get("local_workspace_multi_user") == "supported"
    and c.get("isolation_level") == "os_user"
    and c.get("policy_revision") in REVIEWED
    and {"identity", "filesystem", "environment", "process"} <= set(c.get("enforced", []))
    and all(s in KNOWN for s in c.get("entry_points", {}).values())   # 未知状态 -> 拒绝
    and all(
        d.get(e, {}).get("status") == "enforced"
        and d.get(e, {}).get("covered_by_isolation_level")
        and not d.get(e, {}).get("limitations")                      # residuals 不参与判定
        for e in NEEDED
    )
)
print("ACCEPT" if ok else "REJECT")
PY
```

要点:

- **只钉你已评审过的 `policy_revision`**;未知 revision 与未知入口 `status` 一律拒绝。
- 判定用 `limitations` 是否为空,**不要**用 `residuals`——后者是已知且被接受的性质
  (如共享项目根按设计跨用户),把它当缺口会把所有健康部署也拒掉。
- `terminal` / `vscode` 是 `remote_machine_scope`:它们由机器 ACL 治理,不在本地
  隔离等级的覆盖面内。**需要"仅本地工作台、禁远端机器"的产品形态,做法是不授予
  machine assignment**,而不是要求这两个入口变成 `enforced`——契约报告的是机制,
  不是你的授权决定(服务端入口开关见 §8 第 7 条)。

带隔离要求启动工作区(能力不足时得到结构化 400,而非静默弱启动):

```bash
curl -H "Authorization: Bearer <token>" \
  "https://<open-ace>/api/workspace/user-url?required_isolation=os_user"
```

**隔离要求是服务端的**:config.json 中的 `workspace.isolation.level` 就是每一次启动的下限(#3446)。安装脚本与
Docker entrypoint 会把它和 backend 一起写入。不存在派生下限:启动路径因运维动作退化(重跑 installer 冲掉
wrapper、`webui_path` 改指 dev checkout、sudo 被移除)时,启动会以 `isolation_level_unsupported` **被拒绝**,
直到修复为止,并打 WARNING 日志;绝不会改用共享账户服务。

`required_isolation` 请求参数**只能抬高**要求,不能降低;空值/空白参数视为缺省。登录时的后台预启动
(prestart)与工作区目录供给走同一评估——门闸会拒绝的启动/供给不会发生。

- 成功:响应含 `url`/`token`/`system_account` 与 `isolation`(部署已验证姿态
  快照;服务端下限保证默认路径同样经过门闸,回显与实际启动一致)。
- 失败:`success:false` + `error_code`(§3.2)+ `reasons`(部署级与请求级两个
  命名空间并存)+ `isolation`。
- backend `shared`(下限 none)下不带参数的调用保持既有行为。
- **行为变化**:多用户模式下 `system_account` 为空的用户,默认路径(无参数)
  也会得到 `identity_mapping_missing` 400——登录不再自动回填 username 约定,
  需管理员显式设置映射;缓存实例的启动账户与当前映射不符时会自动重启到新账户。

探针取舍说明:`supports_per_user_launch`/`per_user_launch_readiness` 以
**probe-only** 方式解析 WebUI 可执行文件(绝不触发 npm build);就绪结果
(含降级态)在进程内按 30 秒 TTL 双向记忆化(以检查**结束**时刻计时),成功解析额外永久缓存。sudo
路径的真实前置是 `openace-webui-launch` 包装器已安装且可执行(sudoers 规则
本身无法廉价验证);目标系统账户拒绝 uid 0 与保留段(`uid < 1000`)。

## 8. 已知缺口与后续路线

以下缺口已在审计中确认,按 issue #3374 的指示拆分为独立后续工作,本契约的
`entry_points` 矩阵如实反映:

1. ~~终端/VSCode 令牌与用户绑定~~、~~终端 `work_dir` 服务端校验~~、
   ~~`/fs/browse`、`/fs/check-path` 补 home 子树锁~~ — **已于 #3376 / PR #3380 关闭**。
   这三条在 2026-09-13 合入后,矩阵与本节文字一直未更新,导致接入方把已修好的缺口
   当作现存风险(#3410 的起因之一)。当前边界以 §4 与 `entry_point_details` 为准。
2. 文件接口跨用户符号链接写入 — **已于 #3410 关闭**(见 §4 与 CHANGELOG)。
   同批关闭的还有:download/delete-file/search 在 root 进程下"先校验、再按路径操作"
   的竞态窗口、包安装形态 `openace-rm` 以 root 执行的删除、`create-directory`
   缺失的 home 子树锁、多 base 部署下 `/api/fs` 单文件路径与 `/api/fs/home` 的
   全线 400、`<base>/<account>` 0755。
3. *(编号保留,原条目已关闭)*
4. WebUI `token_secret` 未持久化时重启导致已发 token 失效的加固。
5. 交互工作区 `sandboxed` 等级:#3378 已交付(§6),不依赖 Kubernetes 的形态由 #3431/#3438 交付
   (§5.2、§5.3);遗留 follow-up:真实集群
   端到端验收(#3379,含 3100 端点可达性验证)、`terminal`/`vscode`/`fs` 入口
   沙箱接线、webui 历史 quota/GC、多 web 副本下的 token 校验/实例管理
   (reconcile 已心跳互斥,但 pod 归属仍是单进程内存态)。
6. 多用户模式真实 Linux 端到端隔离验收(并发用户 + 越权尝试矩阵)——已由 #3379
   交付基线;#3410 追加了上传符号链接矩阵、跨用户 `create-directory`、
   `<base>/<account>` 0700 与"登录供给不跟随 `.qwen` 符号链接"探针
   (`scripts/multiuser_acceptance.py` item b)。
7. **入口禁用开关(kill switch)缺失**:契约保留 `disabled` 状态,但当前**没有任何
   部署形态会输出它**——服务端尚无"强制关闭 terminal/vscode/filesystem_api 入口"
   的配置项(单元测试 `test_no_snapshot_emits_the_reserved_disabled_status` 钉住
   这一点)。需要"仅本地工作台"形态的部署,目前通过不授予远端机器(machine
   assignment)实现,并由 `entry_point_details[*].scope` 让接入方机器可判。
8. **入口矩阵与代码的 conformance 绑定**:`entry_points`/`entry_point_details` 是
   随 `policy_revision` 版本化的静态审计结论,尚无自动检查把每个操作的声明与其实现
   绑定;声明与实现的一致性目前由评审与 `tests/` 中的对应用例保证。
9. ~~统一配置词汇~~:**已在 #3446 完成**(`workspace.isolation {level, backend}`,快照与 root 策略文件使用同一套
   名称,启动时按安装方式校验)。后续:`openace isolation check` 命令与安装脚本的 `--isolation` 选项。

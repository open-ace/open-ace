# Workspace Isolation Capabilities

[中文](../cn/WORKSPACE_ISOLATION_CAPABILITIES.md) · Admin guide: [WORKSPACE_ISOLATION](WORKSPACE_ISOLATION.md)

Open ACE publishes a **versioned multi-user isolation capability contract** for local interactive
workspaces (chat WebUI, file API, session history). Admins and trusted integrators should query the
capability that is actually in force through the API, instead of relying on README claims or on
capability booleans supplied by a client.

This document covers the contract's semantics, deployment requirements, the reason codes and the
known gaps. Related issues: #3374 (os_user), #3378 (sandboxed), #3431 (confinement and local gVisor
container), #3438 (local Kata container).

This is the **reference**. To choose and configure an isolation mode, start with the admin guide
[WORKSPACE_ISOLATION](WORKSPACE_ISOLATION.md).

## 1. Contract fields

Endpoint: `GET /api/workspace/isolation-capabilities` (requires a signed-in session)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "qwen-code-webui-per-user | qwen-code-webui-per-user-confined | qwen-code-webui-shared | local-container:runsc | local-container:kata | opensandbox:<tier>",
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
  "policy_revision": "2026-09-26.2"
}
```

- **`local_workspace_multi_user`:** whether multi-user isolation of local interactive workspaces is
  supported.
- **`backend`:** the runner that is actually in use.

  | value | meaning |
  |---|---|
  | `qwen-code-webui-per-user` | one WebUI process per user |
  | `qwen-code-webui-per-user-confined` | #3431: as above, and each process runs in a systemd scope + bubblewrap (§5.1) |
  | `qwen-code-webui-shared` | a single shared instance |
  | `local-container:runsc` | #3431: sandboxed level, WebUI in a local gVisor container (§5.2) |
  | `local-container:kata` | #3438: sandboxed level, WebUI in a local Kata container (§5.3) |
  | `opensandbox:<tier>` | #3378: sandboxed level, WebUI in an OpenSandbox pod of that tier (§6) |

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
the `workspace.required_isolation_level` floor are both compared in this order; a request can only
raise the requirement, never lower it.

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
[SANDBOX_BACKENDS](SANDBOX_BACKENDS.md).

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
| `multi_user_mode_disabled` | Multi-user mode is off (the lightweight single-user mode: an expected state, not a defect) |
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

The confinement and local-container modes add their own `confinement_*` codes; see §5.1, §5.2 and §5.3.

### 3.4 sandboxed probe and runtime codes (#3378, OpenSandbox pod form)

The `sandboxed` probe is **zero-pod and fail-closed on configuration**: it decides without creating a
pod. The codes fall into three groups:
- the first 8 are probe-level: a hit refuses the level;
- the next 4 are snapshot-level: they appear in the contract's `reasons[]` without blocking the
  declaration;
- the last 2 are runtime error codes, raised by the launcher rather than the probe.

| code | level | meaning | fix |
|---|---|---|---|
| `sandbox_backend_unconfigured` | probe | sandbox-backends.json is missing, unparsable or not configured | Provide or fix the backend config (see [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md) §3) |
| `sandbox_tier_missing` | probe | `workspace.sandbox_tier` (or the backend's `default_tier`) has no entry in `endpoints` | Fix `sandbox_tier`, or add that tier to `endpoints` |
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
  - **The shipped k8s manifest (3 replicas) has exactly this shape, so sandboxed automatically falls
    back to the os_user chain there.**
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

## 5. Multi-user deployment requirements (policy revision 2026-09-16.1)

Whether the contract reports `supported/os_user` is decided by the **launch-path readiness probe**
(§3.3: WebUI resolution, not dev-directory mode, the `openace-webui-launch` wrapper, sudo). The Docker
layout is no longer a precondition: the package install (`scripts/install-central/package-method`,
with `sudo -u` and the wrappers in place) can verify and enforce os_user too.

Two reference deployments:

1. **Docker multi-user:** the `docker-compose.multi-user.yml` overlay (root +
   `OPENACE_ALLOW_ROOT_MULTI_USER=1` + `WORKSPACE_BASE_DIR=/workspace` +
   `WORKSPACE_MULTI_USER_MODE=true`). The image ships useradd and the wrappers, and system accounts are
   provisioned automatically.
2. **Package multi-user:** the installer's `_WS_MULTI_USER` path (runs as non-root, `/home` layout).
   A healthy probe reports os_user. Each user's system account must already exist (resolvable by
   getpwnam, uid ≥ 1000).

Common requirements: the restricted `openace-webui-launch` wrapper in sudo/sudoers. There are no
special kernel requirements, because the os_user level is the OS account boundary. For baseline
production secrets, see the compose comments.

When the probe degrades (dev-directory mode, a missing wrapper, ...), the contract honestly returns
`launch_path_degraded` unsupported. It **never** silently falls back to the shared account while
claiming support. In that state the default launch path keeps its existing behaviour, and an explicit
`required_isolation=os_user` gets a structured refusal.

**Install method matters for the optional modes below.** The Docker install supports only `none`,
plain `os_user` and the OpenSandbox pod form; the confinement mode and the local containers (§5.1–§5.3)
need the package install on a Linux host. See the install-method table in
[WORKSPACE_ISOLATION](WORKSPACE_ISOLATION.md#2-what-your-install-method-allows).

### 5.1 Optional: os_user confinement (#3431, policy revision 2026-09-25.1)

`workspace.os_user_confinement = "bwrap"` confines each os_user WebUI at launch. It stays at the
`os_user` level (the host kernel is shared), but the `resources` and `network_egress` dimensions
become `enforced`, and `backend` reports `qwen-code-webui-per-user-confined`:

| layer | mechanism | effect |
|---|---|---|
| resources | `systemd-run --scope` (`MemoryMax`/`MemorySwapMax=0`/`CPUQuota`/`TasksMax`) | Hard cgroup v2 limits, including a fork-bomb ceiling |
| identity | `setpriv --reuid/--regid --init-groups --no-new-privs`, with the capability and bounding sets cleared | Carries only the account's own supplementary groups. `systemd-run --scope --uid` would keep the caller's (root's) group 0, which is why it is not used |
| filesystem | bubblewrap: read-only host root; empty tmpfs over `/tmp`, `/var/tmp`, `/run` and the workspace base | Only the user's own home and `<base>/shared` are bound back; other users' homes are invisible (not merely access-denied) |
| network | bubblewrap `--unshare-net` (loopback only) + a host-side egress proxy | The proxy is the only way out. See below |
| ingress | reverse tunnel | See below |

**The egress proxy:**
- **What it allows:** only allowlisted `host:port` pairs, which are the host:port of
  `webui_callback_url` plus `confinement_egress_allow`. They come **only from server-side
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

Settings (`workspace` in `config.json`):

| key | default | meaning |
|---|---|---|
| `os_user_confinement` | `""` | `"bwrap"` enables it; `""`/`"off"` disables it; any other value → `confinement_mode_invalid` (`"runsc"` and `"kata"`: §5.2, §5.3) |
| `confinement_memory_max` | `4G` | systemd `MemoryMax` |
| `confinement_cpu_quota` | `200` | percent, `CPUQuota` |
| `confinement_tasks_max` | `512` | `TasksMax` |
| `confinement_egress_allow` | `[]` | extra allowed `host:port` pairs (`*.domain:port` and `[ipv6]:port` are supported) |

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
- `confinement_mode_invalid`, `confinement_platform_unsupported`, `confinement_wrapper_missing`;
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

### 5.2 Optional: local container sandbox (`os_user_confinement = "runsc"`, #3431 Option 2, policy revision 2026-09-26.1)

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

Snapshot: `isolation_level = sandboxed`, `backend = local-container:runsc`, all seven dimensions
`enforced`. The differences from the OpenSandbox pod form (§6) are **deliberate**:

- **Entry-point matrix:** the os_user one. The home is a host directory, so `/api/fs` and the other
  entry points stay `enforced`.
- **`/user-url` gate:** it keeps the OS account chain (`system_account` mapping, account checks).
- **Launch form:** it stays the per-user local process form and **never** routes to the OpenSandbox
  pod launcher. The pod form is chosen by the `opensandbox:*` backend, not by the level.

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
   {"webui": ["/usr/bin/qwen-code-webui"],
    "container": {"image": "sha256:<64 hex>", "docker": "/usr/bin/docker",
                  "runtimes": {"runsc": "runsc-openace"}}}
   ```
   - `image` must be `name@sha256:<64 hex>` or a local image ID `sha256:<64 hex>`. A tag can be
     re-pointed, so it is refused.
   - `webui` lists paths **inside the image**.
   - The older single `runtime` key still means the runsc runtime.
   - Rerunning the installer keeps the `container` section.
3. `workspace.os_user_confinement = "runsc"`. `workspace.webui_callback_url` is required (as in
   §5.1). `confinement_container_webui` changes the WebUI path inside the image (default
   `/usr/bin/qwen-code-webui`).

`fs.protected_symlinks=1` is required (the default on Debian/Ubuntu/RHEL). docker (as root) mounts
the log directory by path, and this setting stops the account from steering the mount source with a
symlink in the sticky `/tmp`. Without it the probe reports `confinement_symlinks_unprotected`.

**Readiness:** the manager checks with `sudo -n openace-webui-confine launch --probe` (root):
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

### 5.3 Optional: local Kata container (`os_user_confinement = "kata"`, #3438, policy revision 2026-09-26.2)

The same local-container form as §5.2 on a **Kata Containers** runtime: each WebUI runs in a
lightweight virtual machine with its own guest kernel. Snapshot: `isolation_level = sandboxed`,
`backend = local-container:kata`, all seven dimensions `enforced`. The entry-point matrix, the
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
   - Putting `containerd-shim-kata-v2` on dockerd's PATH (e.g. `/usr/local/bin`) is enough to use
     `io.containerd.kata.v2` directly, **without** changing `daemon.json` or restarting Docker.
     Registering a name in `daemon.json` also works.
   - The policy file maps each mode to its runtime with `runtimes`:
     ```json
     {"webui": ["/usr/bin/qwen-code-webui"],
      "container": {"image": "sha256:<64 hex>", "docker": "/usr/bin/docker",
                    "runtimes": {"runsc": "runsc-openace", "kata": "io.containerd.kata.v2"}}}
     ```
   - When a shim name is used, the probe requires the shim binary to be root-controlled.
4. Under nested virtualization the guest boots slowly (about 70 seconds measured with three levels of
   nesting). Kata's default `dial_timeout = 45` is too short there: raise it (e.g. to 180) in
   `/etc/kata-containers/configuration.toml`. On bare metal the default is fine.
5. `workspace.os_user_confinement = "kata"`; everything else as in §5.2.

**Readiness:** `sudo -n openace-webui-confine launch --probe --backend kata` (root). In addition to
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

## 6. sandboxed level on OpenSandbox: requirements and honest statement (semantics last changed in 2026-09-12.2; every snapshot reports the current POLICY_REVISION)

`sandboxed` on OpenSandbox means the WebUI process runs in an OpenSandbox pod:
- one pod per user and instance;
- a dedicated WebUI image that is digest-pinned and on the image_allowlist;
- deny-default egress;
- the browser reaches it through a local port proxy on the control plane (port form, no path-rewrite
  assumption);
- the identity is a per-instance token secret, which is invalidated when the instance is destroyed.
  There is no OS account or sudo chain.

The probe **does not check the platform or multi_user_mode**: the pod runs on a remote cluster, so
single-user + sandboxed is a legitimate hardened shape.

Changes in `POLICY_REVISION` 2026-09-12.1:
- the `sandboxed` level and the zero-pod probe joined the vocabulary;
- a new `kernel` dimension, which `os_user` honestly declares unsupported (shared host kernel);
- a sandboxed branch in the user-url gate, which refuses by probe reason instead of walking the OS
  account chain.

Changes in `POLICY_REVISION` 2026-09-12.2 (the complete list):

- **T-B gate semantics:** the `required_isolation` floor is a floor, not a target.
  - The effective requirement is max(pinned floor, request parameter), and the launch form is the
    **strongest verified form** that meets it (`_resolve_form` derives it from the same capability
    snapshot).
  - So in a deployment with `webui_image` configured, a parameter-less `/user-url` takes the sandbox
    form by default, even when the pin is only `os_user`.
  - Deployments without `webui_image` behave exactly as before: the os_user chain, and an explicit
    `os_user` request still requires a system_account mapping.
  - An explicit `sandboxed` request keeps its fail-closed shape: the probe refuses, the launcher
    checks again, and there is no silent downgrade to local.
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
   [NGINX](NGINX.md)). In the sandboxed form the browser port is the local proxy port, taken from the
   same port_range (default 3100-3200). Single-user + sandboxed allocates a proxy port instead of the
   hard-coded 3100.
5. **`workspace.sandbox_tier` is optional.** It chooses which endpoint tier interactive pods land on;
   the default is the backend's `default_tier`.
   - **The pin is a floor, not a target** (the same floor definition as #3375). The effective
     requirement is max(pinned floor, request parameter), and the launch form is the **strongest
     verified form** that meets it.
   - So once `webui_image` is configured (probe passed, capability sandboxed), a parameter-less
     `/user-url` also takes the sandbox form, even when the pin (e.g. the `os_user` that
     docker-entrypoint writes by default) is lower. The os_user chain (identity mapping, sudo launch)
     then does not apply, and the gate admits through the sandboxed branch.
   - Deployments without `webui_image` behave exactly as before: the os_user chain, and an explicit
     `os_user` request still requires a system_account mapping.
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
    freshness window (≤ 660 s), and until then sandboxed is refused and falls back to the os_user
    chain.
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
REVIEWED = {"2026-09-26.2"}                                # revisions you have reviewed
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

**The isolation requirement is server-side** (`workspace.required_isolation_level` in config.json).

**How the multi-user installs pin it:**
- **Docker install:** writes **`os_user` explicitly**, generated by docker-entrypoint on first start
  (the `WORKSPACE_REQUIRED_ISOLATION_LEVEL` environment variable overrides it).
- **Package installer:** pins only **after the `openace-webui-launch` wrapper is actually installed**.
  It reuses the sudoers rule's executable test `[ -x /usr/local/bin/openace-webui-launch ]`. When the
  wrapper is missing it **does not pin** and prints a clear installation warning. That avoids
  producing a deployment that "pins `os_user` without a wrapper" and answers 400 to everything.
- **Neither:** without an explicit setting, the requirement is **derived** from the level the contract
  snapshot actually verified.

**Honest statement: the derived value is a mirror, not a floor.** When operator actions degrade the
launch path (rerunning the installer wipes the wrapper, `webui_path` is pointed at a dev checkout, sudo
is removed), the derived value drops to `none` with it, and the default path keeps the old behaviour
(launching as the shared account) instead of failing. Both directions log a WARNING and are visible in
the response's `isolation.reasons`:

- **A derived deployment** degrades: it keeps serving with weaker isolation, and a WARNING says default
  launches are no longer isolated per user.
- **A pinned deployment** degrades: the floor is above what the host can verify, so every launch is
  refused with `isolation_level_unsupported`, and a WARNING says everything is REJECTed until the
  launch path is fixed. The refusal never happens silently.

For a real hard floor, keep or set an explicit `required_isolation_level`. The `required_isolation`
request parameter **can only raise** the requirement, never lower it; an empty or blank parameter
means the default. The background prestart at login and workspace directory provisioning go through
the same evaluation, so a launch or provisioning the gate would refuse never happens.

- **Success:** the response contains `url`/`token`/`system_account` and `isolation` (a snapshot of the
  deployment's verified posture; the server-side floor makes the default path go through the gate as
  well, so the echo matches the real launch).
- **Failure:** `success:false` + `error_code` (§3.2) + `reasons` (deployment-level and request-level
  namespaces side by side) + `isolation`.
- **Single-user mode** (floor none): calls without the parameter keep the existing behaviour.
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
9. **The configuration vocabulary is being unified** (#3446): one `workspace.isolation` block with a
   `level` and a `backend`, the same names in the snapshot and the root policy, and startup validation
   that knows which backends each install method allows.

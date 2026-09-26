# Workspace Isolation: Admin Guide

[中文](../cn/WORKSPACE_ISOLATION.md)

How to choose, configure and verify isolation between users of the **interactive workspace** (the
chat WebUI, the file API and session history). The exact guarantees, every reason code and the
integrator contract are in the reference: [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md).
Autonomous agents are isolated separately; see [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md).

> The configuration keys below are today's. Issue #3446 will replace them with a single
> `workspace.isolation` block (`level` + `backend`); this guide will be updated when it lands.

## 1. The isolation modes

Open ACE reports two things about every deployment: an **isolation level** (`none` < `os_user` <
`sandboxed`) and the **backend** that provides it.

| mode | level | backend reported | what separates users | kernel |
|---|---|---|---|---|
| Single user | `none` | `qwen-code-webui-shared` | nothing (one shared process) | host |
| Per-user OS account | `os_user` | `qwen-code-webui-per-user` | a separate OS account, home and WebUI process for each user | host |
| Confined OS account | `os_user` | `qwen-code-webui-per-user-confined` | as above, plus bubblewrap (other homes invisible, no network except an allowlist proxy) and cgroup limits | host |
| Local gVisor container | `sandboxed` | `local-container:runsc` | as above, in a Docker container on gVisor's user-space kernel | gVisor |
| Local Kata container | `sandboxed` | `local-container:kata` | as above, in a Docker container that is a lightweight VM | own guest kernel |
| OpenSandbox pod | `sandboxed` | `opensandbox:<tier>` | a Kubernetes pod per user (gVisor or Kata), no host filesystem at all | gVisor / Kata |

Rules of thumb:
- **Trusted users on one machine:** `os_user` is enough.
- **Agents that run untrusted code, or users who must not affect each other's resources or network:**
  use the confined `os_user` mode at least.
- **You need a kernel boundary:** use one of the `sandboxed` modes. Without Kubernetes, that is a local
  gVisor or Kata container; with a Kubernetes cluster, an OpenSandbox pod.

## 2. What your install method allows

**Check this first.** Which modes can work depends on how Open ACE was installed, and the app only
tells you after you try (for example with `confinement_wrapper_missing` in the Docker install).

| mode | package install (Linux) | Docker install | package install (macOS) |
|---|---|---|---|
| Single user | ✅ | ✅ | ✅ |
| Per-user OS account | ✅ accounts on the host | ✅ accounts inside the Open ACE container | ❌ (see below) |
| Confined OS account | ✅ | ❌ | ❌ |
| Local gVisor container | ✅ | ❌ | ❌ |
| Local Kata container | ✅ (needs `/dev/kvm`) | ❌ | ❌ |
| OpenSandbox pod | ✅ | ✅ | ✅ |

**Why the Docker install cannot offer the three local sandboxes:**
- **Not shipped:** the image does not include the confine wrapper (`openace-webui-confine`).
- **bubblewrap cannot run in a container:** it needs systemd as PID 1 (for `systemd-run --scope`)
  and unprivileged user namespaces, which Docker's default seccomp profile blocks. Loosening either
  (`--privileged`, `seccomp=unconfined`) weakens the outer container more than bubblewrap would
  strengthen the inner one.
- **gVisor and Kata would need the host's Docker:** starting per-user containers from inside the
  Open ACE container means mounting `/var/run/docker.sock`, which is root on the host. On top of
  that, `/workspace/<user>` is a path inside the container (a named volume), not a host path, so the
  host's Docker would mount the wrong thing.

**What the Docker install gives you instead:**
- The Open ACE container itself separates all users from the host, though not from each other.
  Running that container on gVisor (`runtime: runsc` in compose) hardens it further, without changing
  the reported level.
- Per-user `sandboxed` isolation comes from the OpenSandbox pod mode.

**macOS:** Open ACE cannot create or verify per-user system accounts there, so it reports
`unsupported/platform_unsupported` for multi-user isolation. Use a Linux host, or OpenSandbox pods.

**If you need per-user sandboxing without Kubernetes, choose the package install on Linux.**

## 3. Configure a mode

All `config.json` keys below live under `workspace`. After changing them, restart Open ACE and verify
(§4).

### 3.1 Single user (`none`)

The default: `"multi_user_mode": false`. Nothing else to do.

### 3.2 Per-user OS account (`os_user`)

```json
"workspace": {"enabled": true, "multi_user_mode": true}
```

- **Docker install:** use the multi-user overlay (it runs the container as root so it can create
  accounts):
  ```bash
  ./scripts/bootstrap-compose-env.sh
  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
  ```
  See [DEPLOYMENT](DEPLOYMENT.md#multi-user-workspace-deployment).
- **Package install:** run the installer with the multi-user workspace enabled
  (`WORKSPACE_MULTI_USER_MODE=true`). It installs the `openace-webui-launch` wrapper and its sudoers
  rule, and pins `required_isolation_level` to `os_user` once the wrapper is in place. Each user needs
  an existing system account (uid ≥ 1000) set as their `system_account` in Open ACE.

Reference: [capabilities §5](WORKSPACE_ISOLATION_CAPABILITIES.md#5-multi-user-deployment-requirements-policy-revision-2026-09-161).

### 3.3 Confined OS account (`os_user`, confined)

Package install on Linux with systemd, `bubblewrap` ≥ 0.8 and `setpriv`. On Ubuntu 24.04+, load the
`bwrap-userns-restrict` AppArmor profile.

```json
"workspace": {
  "enabled": true,
  "multi_user_mode": true,
  "webui_callback_url": "http://10.0.0.5:19888",
  "os_user_confinement": "bwrap",
  "confinement_memory_max": "4G",
  "confinement_cpu_quota": 200,
  "confinement_tasks_max": 512,
  "confinement_egress_allow": ["pypi.org:443"]
}
```

- `webui_callback_url` is **required**: it defines the Open ACE API / LLM proxy address the sandbox
  may reach, taken from server configuration rather than from the request.
- `confinement_egress_allow` adds further `host:port` pairs. Everything else is refused, and every
  decision is logged to `/var/log/openace-webui/<uid>.egress.log`.
- Rerun the package installer after enabling it: it installs `/usr/local/bin/openace-webui-confine`,
  the root-owned policy `/etc/openace/webui-confine.json` and the sudoers rule, and removes the rule
  that could start an unconfined WebUI.

Reference: [capabilities §5.1](WORKSPACE_ISOLATION_CAPABILITIES.md#51-optional-os_user-confinement-3431-policy-revision-2026-09-251).

### 3.4 Local gVisor container (`sandboxed`)

Package install on Linux with Docker and gVisor. Three pieces:

1. **Docker runtime** (`/etc/docker/daemon.json`, then restart Docker):
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
2. **WebUI image**, built with `scripts/docker/webui-sandbox.Dockerfile`, and the **root policy**
   `/etc/openace/webui-confine.json` pinning it by content:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"],
    "container": {"image": "sha256:<64 hex>", "docker": "/usr/bin/docker",
                  "runtimes": {"runsc": "runsc-openace"}}}
   ```
3. **`config.json`:** the same block as §3.3 with `"os_user_confinement": "runsc"`, then rerun the
   package installer (it removes the sudoers rule that could start an unconfined WebUI).

The host must also have `fs.protected_symlinks=1` (the default on Debian/Ubuntu/RHEL).

Reference: [capabilities §5.2](WORKSPACE_ISOLATION_CAPABILITIES.md#52-optional-local-container-sandbox-os_user_confinement--runsc-3431-option-2-policy-revision-2026-09-261).

### 3.5 Local Kata container (`sandboxed`)

Package install on a Linux host with `/dev/kvm` (bare metal, or a VM with nested virtualization),
Docker, and **Kata ≥ 3.32.0** (older Kata fails on Docker 29 with `invalid namespace type`).

1. **Runtime:** install `containerd-shim-kata-v2` as a root-owned binary in `/usr/local/bin` (or
   another standard system `bin` directory; the readiness probe only looks in those, so a shim that
   is only on dockerd's PATH, e.g. in `/opt/kata/bin`, is reported as missing). No `daemon.json`
   change or Docker restart is needed. Under nested virtualization, raise `dial_timeout` (e.g. to
   180) in `/etc/kata-containers/configuration.toml`.
2. **Root policy:** add the Kata runtime to the same `container` section:
   ```json
   "runtimes": {"runsc": "runsc-openace", "kata": "io.containerd.kata.v2"}
   ```
3. **`config.json`:** the same block as §3.3 with `"os_user_confinement": "kata"`, then rerun the
   package installer. `fs.protected_symlinks=1` is required here too.

Traffic between the host and the Kata VM runs over the container's stdin/stdout, so the VM has no
network interface at all; large downloads through the egress proxy are slower than with gVisor.

Reference: [capabilities §5.3](WORKSPACE_ISOLATION_CAPABILITIES.md#53-optional-local-kata-container-os_user_confinement--kata-3438-policy-revision-2026-09-262).

### 3.6 OpenSandbox pod (`sandboxed`)

A Kubernetes cluster running OpenSandbox (see [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md) §2–§3). In
`/etc/openace/sandbox-backends.json`, give a tier a digest-pinned `webui_image` that is also in
`image_allowlist`; then in `config.json`:

```json
"workspace": {
  "enabled": true,
  "multi_user_mode": true,
  "webui_callback_url": "https://openace.example.com",
  "sandbox_tier": "kata",
  "required_isolation_level": "sandboxed"
}
```

- **`multi_user_mode: true` is what makes it one pod per user.** Without it, Open ACE runs a single
  shared pod for every user: the snapshot still says `sandboxed`, but users are not separated from
  each other. In the Docker install, multi-user mode needs the root overlay from §3.2.
- **Pin `required_isolation_level: "sandboxed"`.** If the OpenSandbox probe fails (a missing
  `webui_callback_url`, an image problem, several web processes), the deployment falls back to the
  `os_user` form. With the pin, launches are refused instead of silently running as plain `os_user`.
- `sandbox_tier` is optional (default: the backend's `default_tier`).
- Only a **single web process** can host pods today; a multi-replica deployment falls back to
  `os_user` with `sandbox_multi_process_unsupported`.
- Do not also set `os_user_confinement` to `runsc` or `kata`. When both are configured and the
  OpenSandbox probe passes, the pod form wins and the local container setting is silently ignored.

Reference: [capabilities §6](WORKSPACE_ISOLATION_CAPABILITIES.md#6-sandboxed-level-on-opensandbox-requirements-and-honest-statement-semantics-last-changed-in-2026-09-122-every-snapshot-reports-the-current-policy_revision) and [SANDBOX_BACKENDS §8](SANDBOX_BACKENDS.md#8-interactive-workspaces-the-sandboxed-isolation-level).

## 4. Verify what is in force

Never assume: ask the running deployment.

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities | python3 -m json.tool
```

Check three fields:

- `isolation_level` and `backend`: do they match the mode you configured (§1)?
- `local_workspace_multi_user`: `supported`, or `unsupported` with `reasons`.
- `reasons[]`: why the deployment is below what you configured. The two families behave differently:
  - **Confined and local-container modes** (`bwrap`, `runsc`, `kata`) fail closed. When the host
    cannot provide the mode, the deployment reports `launch_path_degraded` with the precise
    `confinement_*` cause in parentheses and never starts an unconfined WebUI.
  - **OpenSandbox pods** fall back. When the pod probe fails, the snapshot reports the `os_user`
    level with the `sandbox_*` reason as a separate entry, and users get plain `os_user` WebUIs unless
    you pinned `required_isolation_level: "sandboxed"` (§3.6, §5).

The codes you are most likely to meet:

| code | fix |
|---|---|
| `confinement_callback_url_missing` | set `workspace.webui_callback_url` |
| `confinement_wrapper_missing` | install the confine wrapper (package installer); in the Docker install, see §2 |
| `confinement_systemd_unavailable`, `confinement_userns_unavailable` | the host lacks systemd or unprivileged user namespaces (Ubuntu 24.04+: load `bwrap-userns-restrict`); in the Docker install, see §2 |
| `confinement_runtime_missing` | register the gVisor runtime / install the Kata shim root-owned in `/usr/local/bin` (§3.5) |
| `confinement_image_missing` | build the WebUI image on this host; nothing is pulled at launch |
| `confinement_kvm_unavailable` | Kata needs `/dev/kvm` |
| `confinement_kernel_unverified` | the runtime is not the one configured (e.g. runc named as Kata) |
| `webui_image_*`, `sandbox_proxy_unreachable`, `sandbox_multi_process_unsupported` | OpenSandbox pod configuration (a separate `reasons[]` entry, not a `launch_path_degraded` cause); see [capabilities §3.4](WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-probe-and-runtime-codes-3378-opensandbox-pod-form) |

The complete list is in [capabilities §3](WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-codes).

## 5. Enforce a minimum level

`workspace.required_isolation_level` (`none`, `os_user` or `sandboxed`) is a floor: every workspace
launch that the deployment cannot isolate at that level is refused with a structured error instead of
starting with weaker isolation. The multi-user installers pin `os_user`; for the sandboxed modes, pin
`sandboxed` yourself. Without a pin, the requirement mirrors whatever the deployment currently
verifies, which can silently drop if the launch path degrades. A request can raise the requirement (`/api/workspace/user-url?required_isolation=sandboxed`)
but never lower it.

Reference: [capabilities §7](WORKSPACE_ISOLATION_CAPABILITIES.md#7-for-integrators).

## 6. Common mistakes

- **Choosing the Docker install and then wanting a local sandbox.** See §2: plan the install method
  around the isolation you need.
- **Forgetting `webui_callback_url`.** The confined and local-container modes refuse to run without
  it; the OpenSandbox pod mode falls back to `os_user` (pin `sandboxed` to turn that into a refusal).
- **Pod mode without `multi_user_mode: true`.** Every user then shares one pod (§3.6).
- **A broad `confinement_egress_allow` entry.** The egress proxy decides on host names and does not
  inspect TLS; allowing `*.example.com:443` allows exfiltration to anything under it.
- **Adding a user to a privileged group** (`sudo`, `docker`, `kvm`, `adm`, ...). The confined and
  local-container modes refuse such accounts, because group membership travels into the sandbox.
- **Treating `os_user` as a kernel boundary.** It is not: users share the host kernel. Use a
  `sandboxed` mode when that matters.

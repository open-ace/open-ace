# Workspace Isolation: Admin Guide

[中文](../cn/WORKSPACE_ISOLATION.md)

How to choose, configure and verify isolation between users of the **interactive workspace** (the
chat WebUI, the file API and session history). The exact guarantees, every reason code and the
integrator contract are in the reference: [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md).
Autonomous agents are isolated separately; see [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md).

## 1. One setting: level and backend

Isolation is configured in one place, `workspace.isolation` in `config.json`:

```json
"workspace": {
  "enabled": true,
  "webui_callback_url": "http://10.0.0.5:19888",
  "isolation": {"level": "sandboxed", "backend": "local-kata"}
}
```

- **`level`** is what you require: `none` < `os_user` < `sandboxed`. It is also the floor: every
  workspace launch the deployment cannot isolate at that level is refused with a structured error,
  never started with weaker isolation.
- **`backend`** is how it is provided. Each backend belongs to exactly one level, and the same name
  is what the running deployment reports (§4), what the root policy file uses for its sections, and
  what the confine wrapper's `--backend` takes.

| level | backend | what separates users | kernel |
|---|---|---|---|
| `none` | `shared` | nothing: one WebUI for everyone (the default) | host |
| `os_user` | `plain` | a separate OS account, home and WebUI process for each user | host |
| `os_user` | `bwrap` | as `plain`, plus bubblewrap (other homes invisible, no network except an allowlist proxy) and cgroup limits | host |
| `sandboxed` | `local-gvisor` | as `bwrap`, in a Docker container on gVisor's user-space kernel | gVisor |
| `sandboxed` | `local-kata` | as `bwrap`, in a Docker container that is a lightweight VM | own guest kernel |
| `sandboxed` | `opensandbox` | a Kubernetes pod per user (gVisor or Kata), no host filesystem at all | gVisor / Kata |

A level/backend pair that does not belong together (e.g. `os_user` + `local-kata`) stops the server
at startup with the list of allowed backends. So does any of the keys this block replaced
(`multi_user_mode`, `required_isolation_level`, `os_user_confinement`, `sandbox_tier`,
`confinement_*`); the message names the replacement.

Upgrading converts them for you: the package installer (fresh install, local and remote upgrade) and
the Docker entrypoint (every start) run `scripts/convert_workspace_isolation.py` on `config.json`.
It keeps an OpenSandbox deployment on `opensandbox` (with its tier) and keeps a declared floor, and
prints the block it wrote. A config.json mounted read-only is not converted; convert it yourself with
`python3 scripts/convert_workspace_isolation.py /path/to/config.json`.

Rules of thumb:
- **Trusted users on one machine:** `plain`.
- **Agents that run untrusted code, or users who must not affect each other's resources or network:**
  `bwrap` at least.
- **You need a kernel boundary:** a `sandboxed` backend. Without Kubernetes, `local-gvisor` or
  `local-kata`; with a Kubernetes cluster, `opensandbox`.

## 2. What your install method allows

**Check this first.** Which backends can work depends on how Open ACE was installed:

| backend | package install (Linux) | Docker install | package install (macOS) |
|---|---|---|---|
| `shared` | ✅ | ✅ | ✅ |
| `plain` | ✅ accounts on the host | ✅ accounts inside the Open ACE container | ❌ (see below) |
| `bwrap` | ✅ | ❌ | ❌ |
| `local-gvisor` | ✅ | ❌ | ❌ |
| `local-kata` | ✅ (needs `/dev/kvm`) | ❌ | ❌ |
| `opensandbox` | ✅ | ✅ | ✅ |

The install method is known to the app (`OPENACE_INSTALL_METHOD`, set by the Docker image and by the
package installer's service units): a backend it cannot provide stops the server at startup, with
the reason and the alternative, and the running deployment lists what it could switch to in
`available_backends` (§4).

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
- Per-user `sandboxed` isolation comes from `opensandbox`.

**macOS:** Open ACE cannot create or verify per-user system accounts there, so an OS-account backend
is reported `unsupported/platform_unsupported`. Use a Linux host, or `opensandbox`.

**If you need per-user sandboxing without Kubernetes, choose the package install on Linux.**

## 3. Configure a backend

After changing `workspace.isolation`, restart Open ACE and verify (§4).

### 3.1 `shared` (level `none`)

The default; no `isolation` block is the same as `{"level": "none", "backend": "shared"}`.

### 3.2 `plain` (level `os_user`)

```json
"isolation": {"level": "os_user", "backend": "plain"}
```

- **Docker install:** set `WORKSPACE_ISOLATION_BACKEND=plain` and use the multi-user overlay (it runs
  the container as root so it can create accounts):
  ```bash
  ./scripts/bootstrap-compose-env.sh
  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
  ```
  The entrypoint writes the block above into the generated `config.json`. See
  [DEPLOYMENT](DEPLOYMENT.md#multi-user-workspace-deployment).
- **Package install:** run the installer with the multi-user workspace enabled
  (`WORKSPACE_MULTI_USER_MODE=true`, the installer's own input). It installs the `openace-webui-launch`
  wrapper and its sudoers rule and writes the block above. Each user needs an existing system account
  (uid ≥ 1000) set as their `system_account` in Open ACE.

Reference: [capabilities §5](WORKSPACE_ISOLATION_CAPABILITIES.md#5-multi-user-deployment-requirements).

### 3.3 `bwrap` (level `os_user`, confined)

Package install on Linux with systemd, `bubblewrap` ≥ 0.8 and `setpriv`. On Ubuntu 24.04+, load the
`bwrap-userns-restrict` AppArmor profile.

```json
"webui_callback_url": "http://10.0.0.5:19888",
"isolation": {
  "level": "os_user",
  "backend": "bwrap",
  "limits": {"memory": "4G", "cpu_percent": 200, "tasks": 512},
  "egress_allow": ["pypi.org:443"]
}
```

- `webui_callback_url` is **required**: it defines the Open ACE API / LLM proxy address the sandbox
  may reach, taken from server configuration rather than from the request.
- `limits` (all optional; the defaults are shown) and `egress_allow` apply to `bwrap`, `local-gvisor`
  and `local-kata` only. `egress_allow` adds `host:port` pairs to the proxy's allowlist; everything
  else is refused, and every decision is logged to `/var/log/openace-webui/<uid>.egress.log`.
- Rerun the package installer after setting it: it installs `/usr/local/bin/openace-webui-confine`,
  the root-owned policy `/etc/openace/webui-confine.json` and the sudoers rule, and removes the rule
  that could start an unconfined WebUI.

Reference: [capabilities §5.1](WORKSPACE_ISOLATION_CAPABILITIES.md#51-backend-bwrap-confined-os-account-3431).

### 3.4 `local-gvisor` (level `sandboxed`)

Package install on Linux with Docker and gVisor. Three pieces:

1. **Docker runtime** (`/etc/docker/daemon.json`, then restart Docker):
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
2. **WebUI image**, built with `scripts/docker/webui-sandbox.Dockerfile`, pinned by content in the
   **root policy** `/etc/openace/webui-confine.json`, in the section named after the backend:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
    "local-gvisor": {"image": "sha256:<64 hex>", "runtime": "runsc-openace"}}
   ```
3. **`config.json`:** the block of §3.3 with `"level": "sandboxed", "backend": "local-gvisor"`, then
   rerun the package installer.

The host must also have `fs.protected_symlinks=1` (the default on Debian/Ubuntu/RHEL).

Reference: [capabilities §5.2](WORKSPACE_ISOLATION_CAPABILITIES.md#52-backend-local-gvisor-local-gvisor-container-3431-option-2).

### 3.5 `local-kata` (level `sandboxed`)

Package install on a Linux host with `/dev/kvm` (bare metal, or a VM with nested virtualization),
Docker, and **Kata ≥ 3.32.0** (older Kata fails on Docker 29 with `invalid namespace type`).

1. **Runtime:** install `containerd-shim-kata-v2` as a root-owned binary in `/usr/local/bin` (or
   another standard system `bin` directory; the readiness probe only looks in those, so a shim that
   is only on dockerd's PATH, e.g. in `/opt/kata/bin`, is reported as missing). No `daemon.json`
   change or Docker restart is needed. Under nested virtualization, raise `dial_timeout` (e.g. to
   180) in `/etc/kata-containers/configuration.toml`.
2. **Root policy:** a `local-kata` section next to (or instead of) `local-gvisor`:
   ```json
   "local-kata": {"image": "sha256:<64 hex>", "runtime": "io.containerd.kata.v2"}
   ```
3. **`config.json`:** the block of §3.3 with `"level": "sandboxed", "backend": "local-kata"`, then
   rerun the package installer. `fs.protected_symlinks=1` is required here too.

Traffic between the host and the Kata VM runs over the container's stdin/stdout, so the VM has no
network interface at all; large downloads through the egress proxy are slower than with gVisor.

Reference: [capabilities §5.3](WORKSPACE_ISOLATION_CAPABILITIES.md#53-backend-local-kata-local-kata-container-3438).

### 3.6 `opensandbox` (level `sandboxed`)

A Kubernetes cluster running OpenSandbox (see [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md) §2–§3). In
`/etc/openace/sandbox-backends.json`, give a tier a digest-pinned `webui_image` that is also in
`image_allowlist`; then in `config.json`:

```json
"webui_callback_url": "https://openace.example.com",
"isolation": {"level": "sandboxed", "backend": "opensandbox", "tier": "kata"}
```

- Every user gets their own pod; no OS accounts are needed on the Open ACE host (in the Docker
  install, `WORKSPACE_ISOLATION_BACKEND=opensandbox` without the root overlay).
- `tier` is optional (default: the backend's `default_tier`). Resource limits and egress come from
  the tier, not from `limits` / `egress_allow`.
- Only a **single web process** can host pods today; a multi-replica deployment reports
  `sandbox_multi_process_unsupported` and refuses launches.

Reference: [capabilities §6](WORKSPACE_ISOLATION_CAPABILITIES.md#6-backend-opensandbox-requirements-and-honest-statement)
and [SANDBOX_BACKENDS §8](SANDBOX_BACKENDS.md#8-interactive-workspaces-the-sandboxed-isolation-level).

## 4. Verify what is in force

Never assume: ask the running deployment.

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities | python3 -m json.tool
```

- `isolation_level` and `backend`: the level verified on this host and the backend you configured.
- `local_workspace_multi_user`: `supported`, or `unsupported` with `reasons`. A configured backend
  that is not ready is reported unsupported with its reason; it **never falls back** to another
  backend, and launches are refused until it is fixed.
- `install_method` and `available_backends`: what this deployment could use at all (§2).

The reasons you are most likely to meet:

| code | fix |
|---|---|
| `launch_path_degraded` (with a `confinement_*` cause in parentheses) | see the rows below |
| `confinement_callback_url_missing` | set `workspace.webui_callback_url` |
| `confinement_wrapper_missing` | install the confine wrapper (rerun the package installer) |
| `confinement_systemd_unavailable`, `confinement_userns_unavailable` | the host lacks systemd or unprivileged user namespaces (Ubuntu 24.04+: load `bwrap-userns-restrict`) |
| `confinement_runtime_missing` | register the gVisor runtime / install the Kata shim root-owned in `/usr/local/bin` (§3.5) |
| `confinement_image_missing` | build the WebUI image on this host; nothing is pulled at launch |
| `confinement_kvm_unavailable` | Kata needs `/dev/kvm` |
| `confinement_kernel_unverified` | the runtime is not the one configured (e.g. runc named as Kata) |
| `webui_image_*`, `sandbox_proxy_unreachable`, `sandbox_multi_process_unsupported`, `sandbox_backend_unconfigured` | `opensandbox` configuration; see [capabilities §3.4](WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-probe-and-runtime-codes-3378-opensandbox-backend) |

The complete list is in [capabilities §3](WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-codes).

## 5. Requests and the floor

The `level` is the floor for every launch. A request can raise the requirement
(`/api/workspace/user-url?required_isolation=sandboxed`) but never lower it; a requirement the
deployment cannot meet gets a structured 400, never a weaker workspace.

Reference: [capabilities §7](WORKSPACE_ISOLATION_CAPABILITIES.md#7-for-integrators).

## 6. Common mistakes

- **Choosing the Docker install and then wanting a local sandbox.** See §2: plan the install method
  around the isolation you need.
- **Forgetting `webui_callback_url`.** Every backend except `shared` and `plain` refuses to run
  without it.
- **A broad `egress_allow` entry.** The egress proxy decides on host names and does not inspect TLS;
  allowing `*.example.com:443` allows exfiltration to anything under it.
- **Adding a user to a privileged group** (`sudo`, `docker`, `kvm`, `adm`, ...). The confined and
  local-container backends refuse such accounts, because group membership travels into the sandbox.
- **Treating `os_user` as a kernel boundary.** It is not: users share the host kernel. Use a
  `sandboxed` backend when that matters.

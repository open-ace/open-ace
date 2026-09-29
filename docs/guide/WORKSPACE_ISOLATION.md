# Workspace Isolation: Admin Guide — 工作区隔离:管理员指南

[English](#english) | [中文](#中文)

---

## English

How to choose, configure and verify isolation between users of the **interactive workspace** (the
chat WebUI, the file API and session history). The exact guarantees, every reason code and the
integrator contract are in the reference: [WORKSPACE_ISOLATION_CAPABILITIES](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md).
Autonomous agents are isolated separately; see [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md).

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
It reads each old key the way the old server did: a deployment that ran OpenSandbox pods stays on
`opensandbox` (with its tier), a declared floor is kept, and an `os_user_confinement` value the old
server refused stops the install so you choose the backend yourself. It prints the block it wrote. A config.json mounted read-only is not converted; convert it yourself with
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

Reference: [capabilities §5](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#5-multi-user-deployment-requirements).

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

Reference: [capabilities §5.1](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#51-backend-bwrap-confined-os-account-3431).

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

Reference: [capabilities §5.2](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#52-backend-local-gvisor-local-gvisor-container-3431-option-2).

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

Reference: [capabilities §5.3](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#53-backend-local-kata-local-kata-container-3438).

### 3.6 `opensandbox` (level `sandboxed`)

A Kubernetes cluster running OpenSandbox (see [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md) §2–§3). In
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

Reference: [capabilities §6](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#6-backend-opensandbox-requirements-and-honest-statement)
and [SANDBOX_BACKENDS §8](../dev/SANDBOX_BACKENDS.md#8-interactive-workspaces-the-sandboxed-isolation-level).

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
| `webui_image_*`, `sandbox_proxy_unreachable`, `sandbox_multi_process_unsupported`, `sandbox_backend_unconfigured` | `opensandbox` configuration; see [capabilities §3.4](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-probe-and-runtime-codes-3378-opensandbox-backend) |

The complete list is in [capabilities §3](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-codes).

## 5. Requests and the floor

The `level` is the floor for every launch. A request can raise the requirement
(`/api/workspace/user-url?required_isolation=sandboxed`) but never lower it; a requirement the
deployment cannot meet gets a structured 400, never a weaker workspace.

Reference: [capabilities §7](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#7-for-integrators).

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

---

## 中文

如何为**交互工作区**(聊天 WebUI、文件接口、会话历史)选择、配置并验证用户之间的隔离。确切的保证、全部原因码
与接入方契约见参考文档 [WORKSPACE_ISOLATION_CAPABILITIES](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md)。自主 agent 的
隔离是另一套机制,见 [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md)。

## 1. 一个设置:level 与 backend

隔离只在一个地方配置,即 `config.json` 中的 `workspace.isolation`:

```json
"workspace": {
  "enabled": true,
  "webui_callback_url": "http://10.0.0.5:19888",
  "isolation": {"level": "sandboxed", "backend": "local-kata"}
}
```

- **`level`** 是你要求的等级:`none` < `os_user` < `sandboxed`。它同时是下限:部署无法以该等级隔离的每一次
  工作区启动都会被拒绝并返回结构化错误,绝不会以更弱的隔离启动。
- **`backend`** 是提供该等级的方式。每个 backend 只属于一个等级;同一个名字也是运行中的部署所报告的(§4)、
  root 策略文件分节所用的,以及约束 wrapper 的 `--backend` 所接受的。

| level | backend | 用户之间靠什么隔开 | 内核 |
|---|---|---|---|
| `none` | `shared` | 不隔离:所有人共用一个 WebUI(默认) | 宿主 |
| `os_user` | `plain` | 每个用户独立的 OS 账户、home 与 WebUI 进程 | 宿主 |
| `os_user` | `bwrap` | 同 `plain`,再加 bubblewrap(看不到其他 home、除白名单代理外无网络)与 cgroup 限制 | 宿主 |
| `sandboxed` | `local-gvisor` | 同 `bwrap`,运行在 gVisor 用户态内核上的 Docker 容器里 | gVisor |
| `sandboxed` | `local-kata` | 同 `bwrap`,运行在一个即轻量虚拟机的 Docker 容器里 | 独立 guest 内核 |
| `sandboxed` | `opensandbox` | 每个用户一个 Kubernetes pod(gVisor 或 Kata),完全不接触宿主文件系统 | gVisor / Kata |

不匹配的 level/backend 组合(例如 `os_user` + `local-kata`)会让服务器在启动时停止,并列出允许的 backend。被这个
配置块取代的键(`multi_user_mode`、`required_isolation_level`、`os_user_confinement`、`sandbox_tier`、
`confinement_*`)同样会让启动停止,错误信息会给出替代的写法。

升级会自动转换它们:package 安装器(全新安装、本地升级、远程升级)和 Docker entrypoint(每次启动)都会对
`config.json` 运行 `scripts/convert_workspace_isolation.py`。它按旧服务器的方式读取每个旧键:原本运行 OpenSandbox pod 的部署保持
`opensandbox`(连同 tier),保留声明的下限;旧服务器拒绝的 `os_user_confinement` 值会让安装停止,由你自己选择
backend。它会打印写入的配置块。只读挂载的 config.json 不会被转换,请自行运行
`python3 scripts/convert_workspace_isolation.py /path/to/config.json`。

经验法则:
- **可信用户、一台机器:** `plain`。
- **agent 会运行不可信代码,或用户之间不能互相影响资源与网络:** 至少 `bwrap`。
- **需要内核边界:** 某个 `sandboxed` backend。没有 Kubernetes 时用 `local-gvisor` 或 `local-kata`;有
  Kubernetes 集群时用 `opensandbox`。

## 2. 安装方式决定了哪些可用

**先看这一节。** 哪些 backend 能用取决于 Open ACE 的安装方式:

| backend | 包安装(Linux) | Docker 安装 | 包安装(macOS) |
|---|---|---|---|
| `shared` | ✅ | ✅ | ✅ |
| `plain` | ✅ 账户在宿主上 | ✅ 账户在 Open ACE 容器内 | ❌(见下) |
| `bwrap` | ✅ | ❌ | ❌ |
| `local-gvisor` | ✅ | ❌ | ❌ |
| `local-kata` | ✅(需要 `/dev/kvm`) | ❌ | ❌ |
| `opensandbox` | ✅ | ✅ | ✅ |

应用知道自己的安装方式(`OPENACE_INSTALL_METHOD`,由 Docker 镜像和包安装脚本的 service unit 设置):它无法提供的
backend 会让服务器在启动时停止,并给出原因与替代方案;运行中的部署在 `available_backends` 中列出它能切换到的
backend(§4)。

**Docker 安装为什么提供不了三种本机沙箱:**
- **没有打包:** 镜像里不含约束 wrapper(`openace-webui-confine`)。
- **bubblewrap 无法在容器里运行:** 它需要 systemd 作为 PID 1(用于 `systemd-run --scope`),还需要非特权
  user namespace,而 Docker 默认的 seccomp profile 会拦截它。放开其中任何一项(`--privileged`、
  `seccomp=unconfined`)削弱外层容器的程度,都超过 bubblewrap 能加强内层的程度。
- **gVisor 与 Kata 需要宿主的 Docker:** 从 Open ACE 容器内部为每个用户启动容器,就得挂载
  `/var/run/docker.sock`,而它等同于宿主 root。此外 `/workspace/<user>` 是容器内的路径(一个命名卷),不是
  宿主路径,宿主的 Docker 会挂错东西。

**Docker 安装能提供的替代方案:**
- Open ACE 容器本身把所有用户与宿主隔开,但用户之间不隔开。把这个容器放在 gVisor 上运行(compose 中的
  `runtime: runsc`)能进一步加固,但不会改变报告的等级。
- 每用户的 `sandboxed` 隔离来自 `opensandbox`。

**macOS:** Open ACE 在 macOS 上无法创建或验证每用户系统账户,因此 OS 账户类 backend 报告为
`unsupported/platform_unsupported`。请使用 Linux 主机,或 `opensandbox`。

**如果需要不依赖 Kubernetes 的每用户沙箱,请选择 Linux 上的包安装。**

## 3. 配置各个 backend

修改 `workspace.isolation` 后,重启 Open ACE 并验证(§4)。

### 3.1 `shared`(level `none`)

默认值;没有 `isolation` 配置块等同于 `{"level": "none", "backend": "shared"}`。

### 3.2 `plain`(level `os_user`)

```json
"isolation": {"level": "os_user", "backend": "plain"}
```

- **Docker 安装:** 设置 `WORKSPACE_ISOLATION_BACKEND=plain`,并使用多用户叠加配置(它以 root 运行容器,以便
  创建账户):
  ```bash
  ./scripts/bootstrap-compose-env.sh
  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
  ```
  entrypoint 会把上面的配置块写入生成的 `config.json`。见 [DEPLOYMENT](DEPLOYMENT.md#多用户工作区部署)。
- **包安装:** 以启用多用户工作区的方式运行安装脚本(`WORKSPACE_MULTI_USER_MODE=true`,这是安装脚本自己的
  输入)。它会安装 `openace-webui-launch` wrapper 及其 sudoers 规则,并写入上面的配置块。每个用户都需要一个
  已存在的系统账户(uid ≥ 1000),并在 Open ACE 中设为其 `system_account`。

参考:[能力契约 §5](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#5-多用户模式部署要求)。

### 3.3 `bwrap`(level `os_user`,受约束)

Linux 上的包安装,需要 systemd、`bubblewrap` ≥ 0.8 与 `setpriv`。Ubuntu 24.04+ 需加载 AppArmor 的
`bwrap-userns-restrict` profile。

```json
"webui_callback_url": "http://10.0.0.5:19888",
"isolation": {
  "level": "os_user",
  "backend": "bwrap",
  "limits": {"memory": "4G", "cpu_percent": 200, "tasks": 512},
  "egress_allow": ["pypi.org:443"]
}
```

- `webui_callback_url` **必填**:它决定沙箱可以访问的 Open ACE API / LLM 代理地址,取自服务端配置,而不是
  请求。
- `limits`(均可选,上面是默认值)与 `egress_allow` 只适用于 `bwrap`、`local-gvisor` 与 `local-kata`。
  `egress_allow` 向代理白名单追加 `host:port`;其余一律拒绝,每次判定都记入
  `/var/log/openace-webui/<uid>.egress.log`。
- 设置后重跑包安装脚本:它会安装 `/usr/local/bin/openace-webui-confine`、root 所有的策略文件
  `/etc/openace/webui-confine.json` 与 sudoers 规则,并移除可以启动未约束 WebUI 的那条规则。

参考:[能力契约 §5.1](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#51-backend-bwrap受约束的-os-账户3431)。

### 3.4 `local-gvisor`(level `sandboxed`)

Linux 上的包安装,需要 Docker 与 gVisor。分三部分:

1. **Docker 运行时**(`/etc/docker/daemon.json`,然后重启 Docker):
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
2. **WebUI 镜像**:用 `scripts/docker/webui-sandbox.Dockerfile` 构建,并在 **root 策略文件**
   `/etc/openace/webui-confine.json` 中以 backend 命名的分节里按内容固定:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"], "docker": "/usr/bin/docker",
    "local-gvisor": {"image": "sha256:<64 hex>", "runtime": "runsc-openace"}}
   ```
3. **`config.json`**:§3.3 的配置块,改为 `"level": "sandboxed", "backend": "local-gvisor"`,然后重跑包安装
   脚本。

宿主还需要 `fs.protected_symlinks=1`(Debian/Ubuntu/RHEL 默认如此)。

参考:[能力契约 §5.2](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#52-backend-local-gvisor本机-gvisor-容器3431-option-2)。

### 3.5 `local-kata`(level `sandboxed`)

Linux 主机上的包安装,需要 `/dev/kvm`(物理机,或开启嵌套虚拟化的虚拟机)、Docker,以及 **Kata ≥ 3.32.0**
(更早的 Kata 在 Docker 29 上报 `invalid namespace type`)。

1. **运行时:** 把 `containerd-shim-kata-v2` 以 root 所有的二进制安装到 `/usr/local/bin`(或其他标准系统
   `bin` 目录;就绪探针只在这些目录中查找,只在 dockerd 的 PATH 上、例如位于 `/opt/kata/bin` 的 shim 会被
   报告为缺失)。无需修改 `daemon.json`,也无需重启 Docker。嵌套虚拟化下,在
   `/etc/kata-containers/configuration.toml` 中调大 `dial_timeout`(例如 180)。
2. **root 策略文件:** 在 `local-gvisor` 旁边(或代替它)加一个 `local-kata` 分节:
   ```json
   "local-kata": {"image": "sha256:<64 hex>", "runtime": "io.containerd.kata.v2"}
   ```
3. **`config.json`**:§3.3 的配置块,改为 `"level": "sandboxed", "backend": "local-kata"`,然后重跑包安装
   脚本。这里同样要求 `fs.protected_symlinks=1`。

宿主与 Kata 虚拟机之间的流量走容器的 stdin/stdout,所以虚拟机完全没有网络接口;经出口代理的大文件下载比
gVisor 慢。

参考:[能力契约 §5.3](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#53-backend-local-kata本机-kata-容器3438)。

### 3.6 `opensandbox`(level `sandboxed`)

需要运行 OpenSandbox 的 Kubernetes 集群(见 [SANDBOX_BACKENDS](../dev/SANDBOX_BACKENDS.md) §2–§3)。在
`/etc/openace/sandbox-backends.json` 中为某个 tier 配置 digest-pinned 且在 `image_allowlist` 中的
`webui_image`;然后在 `config.json` 中:

```json
"webui_callback_url": "https://openace.example.com",
"isolation": {"level": "sandboxed", "backend": "opensandbox", "tier": "kata"}
```

- 每个用户都有自己的 pod;Open ACE 主机上不需要 OS 账户(Docker 安装下:`WORKSPACE_ISOLATION_BACKEND=opensandbox`,
  不需要 root 叠加配置)。
- `tier` 可选(默认为后端的 `default_tier`)。资源限制与出口来自 tier,而不是 `limits` / `egress_allow`。
- 目前只有**单个 web 进程**能承载 pod;多副本部署会报告 `sandbox_multi_process_unsupported` 并拒绝启动。

参考:[能力契约 §6](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#6-backend-opensandbox部署要求与诚实声明) 与
[SANDBOX_BACKENDS §8](../dev/SANDBOX_BACKENDS.md#8-交互工作区sandboxed-隔离等级)。

## 4. 验证实际生效的隔离

不要假设,直接问正在运行的部署:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities | python3 -m json.tool
```

- `isolation_level` 与 `backend`:本机验证到的等级,以及你配置的 backend。
- `local_workspace_multi_user`:`supported`,或 `unsupported` 并附带 `reasons`。配置了但尚未就绪的 backend 会
  报告为 unsupported 并给出原因;它**绝不会回落**到别的 backend,修好之前启动一律被拒绝。
- `install_method` 与 `available_backends`:这个部署到底能用哪些 backend(§2)。

最常遇到的原因:

| 代码 | 处理 |
|---|---|
| `launch_path_degraded`(括号里是某个 `confinement_*` 原因) | 见下面各行 |
| `confinement_callback_url_missing` | 设置 `workspace.webui_callback_url` |
| `confinement_wrapper_missing` | 安装约束 wrapper(重跑包安装脚本) |
| `confinement_systemd_unavailable`、`confinement_userns_unavailable` | 宿主缺少 systemd 或非特权 user namespace(Ubuntu 24.04+:加载 `bwrap-userns-restrict`) |
| `confinement_runtime_missing` | 注册 gVisor 运行时 / 把 Kata shim 以 root 所有安装到 `/usr/local/bin`(§3.5) |
| `confinement_image_missing` | 在本机构建 WebUI 镜像;启动时不会拉取 |
| `confinement_kvm_unavailable` | Kata 需要 `/dev/kvm` |
| `confinement_kernel_unverified` | 实际运行时不是所配置的那个(例如把 runc 当作 Kata) |
| `webui_image_*`、`sandbox_proxy_unreachable`、`sandbox_multi_process_unsupported`、`sandbox_backend_unconfigured` | `opensandbox` 配置问题,见[能力契约 §3.4](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-探测与运行期原因码3378opensandbox-backend) |

完整列表见[能力契约 §3](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-code-对照)。

## 5. 请求与下限

`level` 是每一次启动的下限。请求可以抬高要求(`/api/workspace/user-url?required_isolation=sandboxed`),
但不能降低;部署无法满足的要求得到结构化的 400,绝不会得到更弱的工作区。

参考:[能力契约 §7](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md#7-接入方用法)。

## 6. 常见错误

- **选了 Docker 安装,之后又想要本机沙箱。** 见 §2:按所需的隔离来规划安装方式。
- **忘了 `webui_callback_url`。** 除 `shared` 与 `plain` 外,每个 backend 没有它都拒绝运行。
- **`egress_allow` 条目过宽。** 出口代理按主机名判定,不检查 TLS;放行 `*.example.com:443` 就意味着可以向它下面的
  任何主机外带数据。
- **把用户加进特权组**(`sudo`、`docker`、`kvm`、`adm`……)。受约束与本机容器类 backend 会拒绝这样的账户,因为
  组身份会被带进沙箱。
- **把 `os_user` 当作内核边界。** 它不是:用户共享宿主内核。需要内核边界时请使用 `sandboxed` backend。

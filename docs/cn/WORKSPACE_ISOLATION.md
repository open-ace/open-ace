# 工作区隔离:管理员指南

[English](../en/WORKSPACE_ISOLATION.md)

如何为**交互工作区**(聊天 WebUI、文件接口、会话历史)选择、配置并验证用户之间的隔离。确切的保证、全部原因码
与接入方契约见参考文档 [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md)。自主 agent 的
隔离是另一套机制,见 [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md)。

> 下面的配置键是当前版本的。Issue #3446 会把它们替换为一个 `workspace.isolation` 配置块(`level` + `backend`);
> 届时本指南会随之更新。

## 1. 隔离方式一览

Open ACE 对每个部署报告两件事:**隔离等级**(`none` < `os_user` < `sandboxed`)以及提供它的 **backend**。

| 方式 | 等级 | 报告的 backend | 用户之间靠什么隔开 | 内核 |
|---|---|---|---|---|
| 单用户 | `none` | `qwen-code-webui-shared` | 不隔离(一个共享进程) | 宿主 |
| 每用户 OS 账户 | `os_user` | `qwen-code-webui-per-user` | 每个用户独立的 OS 账户、home 与 WebUI 进程 | 宿主 |
| 受约束的 OS 账户 | `os_user` | `qwen-code-webui-per-user-confined` | 同上,再加 bubblewrap(看不到其他 home、除白名单代理外无网络)与 cgroup 限制 | 宿主 |
| 本机 gVisor 容器 | `sandboxed` | `local-container:runsc` | 同上,运行在 gVisor 用户态内核上的 Docker 容器里 | gVisor |
| 本机 Kata 容器 | `sandboxed` | `local-container:kata` | 同上,运行在一个即轻量虚拟机的 Docker 容器里 | 独立 guest 内核 |
| OpenSandbox pod | `sandboxed` | `opensandbox:<tier>` | 每个用户一个 Kubernetes pod(gVisor 或 Kata),完全不接触宿主文件系统 | gVisor / Kata |

经验法则:
- **可信用户、一台机器:** `os_user` 足够。
- **agent 会运行不可信代码,或用户之间不能互相影响资源与网络:** 至少使用受约束的 `os_user`。
- **需要内核边界:** 使用某种 `sandboxed` 方式。没有 Kubernetes 时,是本机 gVisor 或 Kata 容器;有
  Kubernetes 集群时,是 OpenSandbox pod。

## 2. 安装方式决定了哪些可用

**先看这一节。** 哪些方式能用取决于 Open ACE 的安装方式,而应用只会在你尝试之后才告诉你(例如 Docker 安装下的
`confinement_wrapper_missing`)。

| 方式 | 包安装(Linux) | Docker 安装 | 包安装(macOS) |
|---|---|---|---|
| 单用户 | ✅ | ✅ | ✅ |
| 每用户 OS 账户 | ✅ 账户在宿主上 | ✅ 账户在 Open ACE 容器内 | ❌(见下) |
| 受约束的 OS 账户 | ✅ | ❌ | ❌ |
| 本机 gVisor 容器 | ✅ | ❌ | ❌ |
| 本机 Kata 容器 | ✅(需要 `/dev/kvm`) | ❌ | ❌ |
| OpenSandbox pod | ✅ | ✅ | ✅ |

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
- 每用户的 `sandboxed` 隔离来自 OpenSandbox pod 方式。

**macOS:** Open ACE 在 macOS 上无法创建或验证每用户系统账户,因此多用户隔离报告为
`unsupported/platform_unsupported`。请使用 Linux 主机,或 OpenSandbox pod。

**如果需要不依赖 Kubernetes 的每用户沙箱,请选择 Linux 上的包安装。**

## 3. 配置各种方式

下面所有 `config.json` 键都在 `workspace` 下。修改后重启 Open ACE 并验证(§4)。

### 3.1 单用户(`none`)

默认值:`"multi_user_mode": false`。无需其他操作。

### 3.2 每用户 OS 账户(`os_user`)

```json
"workspace": {"enabled": true, "multi_user_mode": true}
```

- **Docker 安装:** 使用多用户叠加配置(它以 root 运行容器,以便创建账户):
  ```bash
  ./scripts/bootstrap-compose-env.sh
  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
  ```
  见 [DEPLOYMENT](DEPLOYMENT.md#多用户工作区部署)。
- **包安装:** 以启用多用户工作区的方式运行安装脚本(`WORKSPACE_MULTI_USER_MODE=true`)。它会安装
  `openace-webui-launch` wrapper 及其 sudoers 规则,并在 wrapper 就位后把 `required_isolation_level` 固定为
  `os_user`。每个用户都需要一个已存在的系统账户(uid ≥ 1000),并在 Open ACE 中设为其 `system_account`。

参考:[能力契约 §5](WORKSPACE_ISOLATION_CAPABILITIES.md#5-多用户模式部署要求policy-revision-2026-09-161)。

### 3.3 受约束的 OS 账户(`os_user`,约束)

Linux 上的包安装,需要 systemd、`bubblewrap` ≥ 0.8 与 `setpriv`。Ubuntu 24.04+ 需加载 AppArmor 的
`bwrap-userns-restrict` profile。

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

- `webui_callback_url` **必填**:它决定沙箱可以访问的 Open ACE API / LLM 代理地址,取自服务端配置,而不是
  请求。
- `confinement_egress_allow` 追加更多 `host:port`。其余一律拒绝,每次判定都记入
  `/var/log/openace-webui/<uid>.egress.log`。
- 启用后重跑包安装脚本:它会安装 `/usr/local/bin/openace-webui-confine`、root 所有的策略文件
  `/etc/openace/webui-confine.json` 与 sudoers 规则,并移除可以启动未约束 WebUI 的那条规则。

参考:[能力契约 §5.1](WORKSPACE_ISOLATION_CAPABILITIES.md#51-可选os_user-约束confinement3431policy-revision-2026-09-251)。

### 3.4 本机 gVisor 容器(`sandboxed`)

Linux 上的包安装,需要 Docker 与 gVisor。分三部分:

1. **Docker 运行时**(`/etc/docker/daemon.json`,然后重启 Docker):
   ```json
   {"runtimes": {"runsc-openace": {"path": "/usr/bin/runsc", "runtimeArgs": ["--host-uds=open"]}}}
   ```
2. **WebUI 镜像**:用 `scripts/docker/webui-sandbox.Dockerfile` 构建,并在 **root 策略文件**
   `/etc/openace/webui-confine.json` 中按内容固定:
   ```json
   {"webui": ["/usr/bin/qwen-code-webui"],
    "container": {"image": "sha256:<64 hex>", "docker": "/usr/bin/docker",
                  "runtimes": {"runsc": "runsc-openace"}}}
   ```
3. **`config.json`**:与 §3.3 相同的配置块,改为 `"os_user_confinement": "runsc"`,然后重跑包安装脚本(它会
   移除可以启动未约束 WebUI 的 sudoers 规则)。

宿主还需要 `fs.protected_symlinks=1`(Debian/Ubuntu/RHEL 默认如此)。

参考:[能力契约 §5.2](WORKSPACE_ISOLATION_CAPABILITIES.md#52-可选本机容器沙箱os_user_confinement--runsc3431-option-2policy-revision-2026-09-261)。

### 3.5 本机 Kata 容器(`sandboxed`)

Linux 主机上的包安装,需要 `/dev/kvm`(物理机,或开启嵌套虚拟化的虚拟机)、Docker,以及 **Kata ≥ 3.32.0**
(更早的 Kata 在 Docker 29 上报 `invalid namespace type`)。

1. **运行时:** 把 `containerd-shim-kata-v2` 以 root 所有的二进制安装到 `/usr/local/bin`(或其他标准系统
   `bin` 目录;就绪探针只在这些目录中查找,只在 dockerd 的 PATH 上、例如位于 `/opt/kata/bin` 的 shim 会被
   报告为缺失)。无需修改 `daemon.json`,也无需重启 Docker。嵌套虚拟化下,在
   `/etc/kata-containers/configuration.toml` 中调大 `dial_timeout`(例如 180)。
2. **root 策略文件:** 在同一个 `container` 段里加入 Kata 运行时:
   ```json
   "runtimes": {"runsc": "runsc-openace", "kata": "io.containerd.kata.v2"}
   ```
3. **`config.json`**:与 §3.3 相同的配置块,改为 `"os_user_confinement": "kata"`,然后重跑包安装脚本。这里同样
   要求 `fs.protected_symlinks=1`。

宿主与 Kata 虚拟机之间的流量走容器的 stdin/stdout,所以虚拟机完全没有网络接口;经出口代理的大文件下载比
gVisor 慢。

参考:[能力契约 §5.3](WORKSPACE_ISOLATION_CAPABILITIES.md#53-可选本机-kata-容器os_user_confinement--kata3438policy-revision-2026-09-262)。

### 3.6 OpenSandbox pod(`sandboxed`)

需要运行 OpenSandbox 的 Kubernetes 集群(见 [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md) §2–§3)。在
`/etc/openace/sandbox-backends.json` 中为某个 tier 配置 digest-pinned 且在 `image_allowlist` 中的
`webui_image`;然后在 `config.json` 中:

```json
"workspace": {
  "enabled": true,
  "multi_user_mode": true,
  "webui_callback_url": "https://openace.example.com",
  "sandbox_tier": "kata",
  "required_isolation_level": "sandboxed"
}
```

- **`multi_user_mode: true` 才是"每个用户一个 pod"的前提。** 不设置时,Open ACE 让所有用户共用一个 pod:快照
  仍显示 `sandboxed`,但用户之间并没有隔开。Docker 安装下,多用户模式需要 §3.2 的 root 叠加配置。
- **固定 `required_isolation_level: "sandboxed"`。** OpenSandbox 探测失败时(缺少 `webui_callback_url`、镜像
  问题、存在多个 web 进程),部署会回落到 `os_user` 形态。固定之后,启动会被拒绝,而不是静默以普通 `os_user`
  运行。
- `sandbox_tier` 可选(默认为后端的 `default_tier`)。
- 目前只有**单个 web 进程**能承载 pod;多副本部署会以 `sandbox_multi_process_unsupported` 回落到 `os_user`。
- 不要同时把 `os_user_confinement` 设为 `runsc` 或 `kata`。两者都配置且 OpenSandbox 探测通过时,pod 形态胜出,
  本机容器设置会被静默忽略。

参考:[能力契约 §6](WORKSPACE_ISOLATION_CAPABILITIES.md#6-sandboxed-等级部署要求与诚实声明语义最后变更于-2026-09-122所有快照一律报告当前-policy_revision) 与 [SANDBOX_BACKENDS §8](SANDBOX_BACKENDS.md#8-交互工作区sandboxed-隔离等级)。

## 4. 验证实际生效的隔离

不要假设,直接问正在运行的部署:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities | python3 -m json.tool
```

看三个字段:

- `isolation_level` 与 `backend`:是否与你配置的方式一致(§1)?
- `local_workspace_multi_user`:`supported`,或 `unsupported` 并附带 `reasons`。
- `reasons[]`:部署为什么低于你的配置。两类方式的行为不同:
  - **受约束与本机容器方式**(`bwrap`、`runsc`、`kata`)fail closed:宿主无法提供时,部署报告
    `launch_path_degraded`,在括号里给出确切的 `confinement_*` 原因,绝不会启动未约束的 WebUI。
  - **OpenSandbox pod** 会回落:pod 探测失败时,快照报告 `os_user` 等级,并把 `sandbox_*` 原因作为单独一条
    列出;除非你固定了 `required_isolation_level: "sandboxed"`(§3.6、§5),用户会得到普通的 `os_user` WebUI。

最常遇到的代码:

| 代码 | 处理 |
|---|---|
| `confinement_callback_url_missing` | 设置 `workspace.webui_callback_url` |
| `confinement_wrapper_missing` | 安装约束 wrapper(包安装脚本);Docker 安装见 §2 |
| `confinement_systemd_unavailable`、`confinement_userns_unavailable` | 宿主缺少 systemd 或非特权 user namespace(Ubuntu 24.04+:加载 `bwrap-userns-restrict`);Docker 安装见 §2 |
| `confinement_runtime_missing` | 注册 gVisor 运行时 / 把 Kata shim 以 root 所有安装到 `/usr/local/bin`(§3.5) |
| `confinement_image_missing` | 在本机构建 WebUI 镜像;启动时不会拉取 |
| `confinement_kvm_unavailable` | Kata 需要 `/dev/kvm` |
| `confinement_kernel_unverified` | 实际运行时不是所配置的那个(例如把 runc 当作 Kata) |
| `webui_image_*`、`sandbox_proxy_unreachable`、`sandbox_multi_process_unsupported` | OpenSandbox pod 配置问题(单独的 `reasons[]` 条目,不是 `launch_path_degraded` 的括注原因),见[能力契约 §3.4](WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-探测与运行期原因码3378) |

完整列表见[能力契约 §3](WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-code-对照)。

## 5. 强制最低等级

`workspace.required_isolation_level`(`none`、`os_user` 或 `sandboxed`)是下限:部署无法以该等级隔离的每一次
工作区启动都会被拒绝并返回结构化错误,而不是以更弱的隔离启动。多用户安装会固定为 `os_user`;sandboxed 方式
请自行固定为 `sandboxed`。不设置时,要求会跟随部署当前验证到的等级,启动路径退化时它可能静默下降。请求可以抬高要求
(`/api/workspace/user-url?required_isolation=sandboxed`),但不能降低。

参考:[能力契约 §7](WORKSPACE_ISOLATION_CAPABILITIES.md#7-接入方用法)。

## 6. 常见错误

- **选了 Docker 安装,之后又想要本机沙箱。** 见 §2:按所需的隔离来规划安装方式。
- **忘了 `webui_callback_url`。** 受约束与本机容器方式没有它会拒绝运行;OpenSandbox pod 方式会回落到
  `os_user`(固定 `sandboxed` 可把它变成拒绝)。
- **pod 方式没有设 `multi_user_mode: true`。** 所有用户会共用一个 pod(§3.6)。
- **`confinement_egress_allow` 条目过宽。** 出口代理按主机名判定,不检查 TLS;放行 `*.example.com:443` 就意味着
  可以向它下面的任何主机外带数据。
- **把用户加进特权组**(`sudo`、`docker`、`kvm`、`adm`……)。受约束与本机容器方式会拒绝这样的账户,因为组身份会
  被带进沙箱。
- **把 `os_user` 当作内核边界。** 它不是:用户共享宿主内核。需要内核边界时请使用 `sandboxed` 方式。

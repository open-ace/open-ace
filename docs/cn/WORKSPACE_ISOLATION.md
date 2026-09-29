# 工作区隔离:管理员指南

[English](../en/WORKSPACE_ISOLATION.md)

如何为**交互工作区**(聊天 WebUI、文件接口、会话历史)选择、配置并验证用户之间的隔离。确切的保证、全部原因码
与接入方契约见参考文档 [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md)。自主 agent 的
隔离是另一套机制,见 [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md)。

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

参考:[能力契约 §5](WORKSPACE_ISOLATION_CAPABILITIES.md#5-多用户模式部署要求)。

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

参考:[能力契约 §5.1](WORKSPACE_ISOLATION_CAPABILITIES.md#51-backend-bwrap受约束的-os-账户3431)。

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

参考:[能力契约 §5.2](WORKSPACE_ISOLATION_CAPABILITIES.md#52-backend-local-gvisor本机-gvisor-容器3431-option-2)。

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

参考:[能力契约 §5.3](WORKSPACE_ISOLATION_CAPABILITIES.md#53-backend-local-kata本机-kata-容器3438)。

### 3.6 `opensandbox`(level `sandboxed`)

需要运行 OpenSandbox 的 Kubernetes 集群(见 [SANDBOX_BACKENDS](SANDBOX_BACKENDS.md) §2–§3)。在
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

参考:[能力契约 §6](WORKSPACE_ISOLATION_CAPABILITIES.md#6-backend-opensandbox部署要求与诚实声明) 与
[SANDBOX_BACKENDS §8](SANDBOX_BACKENDS.md#8-交互工作区sandboxed-隔离等级)。

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
| `webui_image_*`、`sandbox_proxy_unreachable`、`sandbox_multi_process_unsupported`、`sandbox_backend_unconfigured` | `opensandbox` 配置问题,见[能力契约 §3.4](WORKSPACE_ISOLATION_CAPABILITIES.md#34-sandboxed-探测与运行期原因码3378opensandbox-backend) |

完整列表见[能力契约 §3](WORKSPACE_ISOLATION_CAPABILITIES.md#3-reason-code-对照)。

## 5. 请求与下限

`level` 是每一次启动的下限。请求可以抬高要求(`/api/workspace/user-url?required_isolation=sandboxed`),
但不能降低;部署无法满足的要求得到结构化的 400,绝不会得到更弱的工作区。

参考:[能力契约 §7](WORKSPACE_ISOLATION_CAPABILITIES.md#7-接入方用法)。

## 6. 常见错误

- **选了 Docker 安装,之后又想要本机沙箱。** 见 §2:按所需的隔离来规划安装方式。
- **忘了 `webui_callback_url`。** 除 `shared` 与 `plain` 外,每个 backend 没有它都拒绝运行。
- **`egress_allow` 条目过宽。** 出口代理按主机名判定,不检查 TLS;放行 `*.example.com:443` 就意味着可以向它下面的
  任何主机外带数据。
- **把用户加进特权组**(`sudo`、`docker`、`kvm`、`adm`……)。受约束与本机容器类 backend 会拒绝这样的账户,因为
  组身份会被带进沙箱。
- **把 `os_user` 当作内核边界。** 它不是:用户共享宿主内核。需要内核边界时请使用 `sandboxed` backend。

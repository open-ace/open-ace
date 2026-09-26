# 沙箱后端(Sandbox backends)

[English](../en/SANDBOX_BACKENDS.md) · 交互工作区隔离的管理员指南:[WORKSPACE_ISOLATION](WORKSPACE_ISOLATION.md)

Open ACE 运行自主编码 agent。这些 agent 在哪里执行、靠什么阻止它们触及不该触及的东西,由**沙箱后端**
按租户、按项目决定。

共有三种后端:

| 后端 | 隔离 | 适用场景 |
| --- | --- | --- |
| `legacy_posix` | 每任务独立 HOME/TMP/XDG、文件系统 ACL、cgroup 配额,全部在宿主上 | 单租户、仓库与贡献者完全可信 |
| `remote_machine` | 控制面无法验证任何隔离 | 由运维管理、本身就是信任边界的远端机器 |
| `opensandbox` | 容器 + gVisor 或 Kata、默认拒绝的出口、完全不接触宿主文件系统 | 多租户、不可信的仓库/PR/依赖,或任何合规要求 |

本文介绍 `opensandbox`。其测试的组织方式见 `docs/TEST_LAYERS.md`,manifest 见
`k8s/extras/opensandbox/README.md`。

---

## 1. 它是什么

[OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)(Apache-2.0,位于 CNCF landscape)
是面向 AI agent 的沙箱运行时。它负责 Kubernetes 与安全容器这一层;Open ACE 通过两组 REST 接口与它
交互,自身从不直接调用 Kubernetes API。

```
control plane                    OpenSandbox server            sandbox pod
─────────────                    ──────────────────            ───────────
OpenSandboxProvider  ──/v1──▶    lifecycle API        ──▶      gVisor / Kata
        │                                                       ├── execd :44772
        └──────────── execd + PTY WebSocket ────────────────────┤   ├── /command
                                                                │   ├── /files
                                                                │   └── /pty/ws
                                                                └── egress sidecar :18080
```

编码 agent 的 CLI 运行在 pod **内部**,通过 execd 的 PTY WebSocket 以管道模式驱动——正是这种方式提供了
它的 `--input-format stream-json` 协议所需的交互式 stdin。

---

## 2. 前置条件

**调度沙箱 pod 的节点**

- gVisor:`runsc` 与 `containerd-shim-runsc-v1`
- Kata:`kata-containers`、硬件虚拟化(VT-x / AMD-V)、KVM、内核 ≥ 5.10
- kubelet:`podPidsLimit: 512`(见 §5——这是唯一真正的 fork bomb 防线)

**集群**

- 一个**真正执行** `NetworkPolicy` 的 CNI(Calico、Cilium 与大多数托管方案会执行;kind 默认的
  `kindnet` 不会——它接受这些对象然后忽略)。每个 tier 的出口都依赖它,在 gVisor tier 上它是唯一的
  出口控制。它未生效时,provider 的启动探针以 `egress_cni_not_enforced` 拒绝。

```bash
kubectl apply -k k8s/extras/opensandbox/
kubectl get runtimeclass          # expect: gvisor, kata-qemu
```

---

## 3. 配置

`/etc/openace/sandbox-backends.json`(或 `$OPENACE_SANDBOX_BACKENDS`,或
`~/.open-ace/sandbox-backends.json`,按此优先级)。

```json
{
  "installation_id": "openace-prod-sg",
  "default_tier": "kata",
  "endpoints": {
    "kata": {
      "base_url": "http://opensandbox-kata.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_KATA",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_KATA",
      "runtime_class": "kata-qemu",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "webui_image": "ghcr.io/open-ace/webui@sha256:<64 hex>",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [
        "openace.open-ace.svc.cluster.local",
        "api.anthropic.com",
        "*.githubusercontent.com"
      ],
      "attestations": {
        "egress_enforced": true,
        "egress_mode_dns_nft": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "execd_runs_as_exec_identity": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      }
    },
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "ghcr.io/open-ace/agent@sha256:<64 hex>",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [],
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "execd_runs_as_exec_identity": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      }
    }
  },
  "tenant_tiers": {"42": "kata"},
  "rollout": {"mode": "allowlist", "tenants": ["42"], "projects": []},
  "production_required_tenants": ["42"],
  "image_allowlist": ["ghcr.io/open-ace/agent@sha256:<64 hex>"],
  "resource_defaults": {"cpu": "2", "memory": "4Gi", "ephemeral-storage": "8Gi"},
  "sandbox_ttl_seconds": 3600
}
```

编辑前值得了解的几点:

- **每个 endpoint 只声明(attest)一种出口机制,而且两者并不等价。**
  - `egress_enforced` 指 OpenSandbox 的 egress sidecar:每个沙箱独立、默认拒绝、按 FQDN 白名单放行。
    它在 gVisor 下无法使用:gVisor 的网络栈没有 sidecar 做 DNS 重定向所需的 iptables nat 表。
  - `egress_cni_default_deny` 仅指集群的 NetworkPolicy:所有沙箱共用一条静态 CIDR 规则,拒绝元数据服务
    与所有私网段,但**公网全部放开**。
  - 两者都不声明的 tier 在加载配置时被拒;两者都声明的也被拒,因为第二个标志与第一个矛盾。
  - 只有 sidecar 机制能产生 `network_egress_policy`,所以两种 tier 实际上是不同的产品,见 §5 与 §7。
- **agent 的 LLM 代理必须在集群内可达;在 sidecar tier 上还必须在出口白名单中。** 代理是一次运行离不开
  的那个主机。
  - 在 sidecar tier 上,它的主机名必须出现在该 tier 的 `egress_allow_hosts` 中。CNI tier 上该列表必须
    为空,因为没有东西会执行它。
  - 任何 tier 上它都不能是 loopback 地址:控制面的 `server_url` 默认是 `http://localhost:<port>`,在沙箱
    pod 里它解析到沙箱自己。
  - CNI tier 上它也不能是私网地址或集群内部名称,这两者都被那条 NetworkPolicy 拒绝。
  - 上述每种情况下,provider 都会直接拒绝这一轮,而不是让 agent 在每个请求上挂住。
- **`execd_endpoint_host_allowlist` 必须写 GATEWAY 的主机名。** 在 gateway 入口模式下,服务端返回的是
  gateway 的地址,而不是每个沙箱的集群内名称;客户端会拒绝主机不在该列表中的任何 execd URL。因此它必须
  与服务端 ConfigMap 中的 `ingress.gateway.address` 一致;direct 入口遗留的 `*.svc.cluster.local` 条目
  会让每次调用都被拒。
- **`webui_image` 是可选的,由能力探针校验,而不是由配置解析器校验。**
  - 设置了 `endpoints.<tier>.webui_image` 时,交互式 sandboxed WebUI pod(#3378,见 §8)从它启动。
  - 它被**有意**解析为普通的可选字符串:digest-pinned 与 `image_allowlist` 检查发生在隔离能力探针中。
    坏值因此只会降级 `sandboxed` 等级(原因码 `webui_image_not_pinned` / `webui_image_not_allowed`),
    绝不会破坏 autonomous 任务依赖的共享后端配置。
  - 运维上的要求与 `default_image` 相同(digest-pinned 且在白名单中),只是在另一层强制。
  - 参考构建:`scripts/docker/webui-sandbox.Dockerfile`。
- **`installation_id` 必填,且每个部署必须唯一。**
  - 它被写进每个沙箱的元数据;孤儿回收会销毁所有带本 provider 标记、却没有本地 workflow 行认领的沙箱。
  - 两个 Open ACE 安装若以相同标记(或都不设标记)共用一个 lifecycle server,会把对方正在运行的沙箱
    判为无人认领,并在运行中删掉。
  - 它必须在重启之间保持不变:改动会让旧值下创建的沙箱无人管理。
- **租户键是 `str(tenant_id)`**,即本代码库使用的整数,而不是 slug。代码中没有任何 name→id 映射,slug
  键不会匹配任何东西。
- **`rollout` 决定 Legacy 还是 OpenSandbox;`tenant_tiers` 决定用哪个 endpoint。** 两种 tier 都运行 agent
  负载,区别在于出口保证(见 §7)。这是两个不同的问题:`tenant_tiers` 无法把租户路由回 Legacy,因为每个
  tier 都是 OpenSandbox endpoint。
- **`production_required_tenants` 是不可降级名单。** 名单上的租户要么得到 OpenSandbox,要么得到异常;
  不存在从"必需"回退到 Legacy 的路径。
- **显式指定但不存在的配置路径会报错。** 它不会回退到系统文件——回退会静默返回"没有后端",也就是
  Legacy。
- **密钥只写名字,从不存储。** `api_key_env` / `execd_token_env` 存放的是环境变量的**名字**。
- **完全没有配置也是合法状态。** 此时本地路径的行为与本后端出现之前完全一致。

---

## 4. 选择 Legacy 还是 OpenSandbox

由两个设置决定,它们回答的是不同的问题。

**`rollout`——这个任务可以使用该后端吗?**

```json
"rollout": {
  "mode": "allowlist",
  "tenants": ["42"],
  "projects": ["/srv/repos/pilot"]
}
```

- `mode: "all"`(默认):本部署的所有任务都使用 OpenSandbox。
- `mode: "allowlist"`:只有列出的租户与项目路径使用它,**其余一切仍在 Legacy 上运行**,不受影响。

租户键是 `str(tenant_id)`;项目键是绝对路径,精确匹配。任务匹配任一列表即纳入。

**`production_required_tenants`——它必须使用吗?**

名单上的租户要么得到 OpenSandbox,要么得到异常,永远不会回退到 Legacy。这是更强的表述,两个设置必须一致:
一个**必需**却被排除在 rollout 之外的租户,会在加载配置时被拒绝,而不是让其中一个设置悄悄胜出。

完全没有配置文件时,一切都在 Legacy 上运行,行为与本后端出现之前完全相同。这也是回滚方式:删掉该文件。

### 建议的推进顺序

1. 部署一个 tier,`rollout.mode = "allowlist"`,只列一个项目路径。只有一个仓库迁移,其余不变。
   - 要更低的启动开销,选 gVisor。
   - 需要 FQDN 出口白名单,选 Kata。gVisor 无法运行 egress sidecar,只能执行粗粒度的集群
     NetworkPolicy(§7)。
2. 逐个租户扩大 `rollout.tenants`。
3. 需要独立的隔离域时,再加**第二个** tier,用 `tenant_tiers` 把高安全要求的租户路由过去。这一步选择的是
   **哪个** tier,而不是**是否**使用 tier。独立的隔离域例如:专用节点池、不同的镜像白名单、FQDN 出口
   白名单。一个 tier 足够时跳过此步。
4. 当"后端缺失"应当报错而不是降级时,把这些租户加入 `production_required_tenants`。
5. 后端在各处都成为默认时,切换到 `rollout.mode = "all"`。

---

## 5. 什么被强制,由什么强制

provider 声明的每项能力都对应一个你能指得出的机制。**没有**声明的那些,与声明了的同样重要。

| 能力 | 强制机制 |
| --- | --- |
| `NAMESPACE_ISOLATION` | 声明的 runtime class,由每个 endpoint 首个沙箱上的 `/proc/version` 探针检查。见下文 |
| `NETWORK_EGRESS_POLICY` | egress sidecar 在 `dns+nft` 模式下的 `deny_all`(通过探测其 `/policy` **验证**),加上集群 NetworkPolicy。**仅 sidecar tier。** 见下文 |
| `FILESYSTEM_ACL` | pod `securityContext`:非 root、只读 rootfs、去除 capabilities、seccomp `RuntimeDefault` |
| `CPU_MEM_PIDS_TIME_QUOTA` | cpu/memory 由 kubelet 执行 `resourceLimits`,pids 由 `podPidsLimit`,挂钟时间由沙箱 TTL |
| `PRIVATE_HOME_TMP_XDG` | 每个沙箱一个全新容器,显式设置 `HOME`/`TMPDIR`/`XDG_*` |
| `CREDENTIAL_TOKEN_BINDING` | 环境是**构造**出来的,从不继承;GitHub 写凭证从不进入 |
| `STORAGE_INODE_QUOTA` | **默认关闭**,见下文 |

`NAMESPACE_ISOLATION` 的 runtime_class 检查**是单向的**(one-directional),kernel 证据也仅此而已:
- gVisor 的声明被正向验证:它的内核会表明自己的身份。
- Kata 的声明只能确认**不是** gVisor:Kata 的 guest 内核与未隔离的 runc 容器的内核无法区分,因此该检查
  不能证明 Kata 真的生效。
- 请把 Kata runtime class 视为运维方的声明,其依据是 `[secure_runtime] k8s_runtime_class` 以及节点上
  确实存在该 RuntimeClass。

`NETWORK_EGRESS_POLICY` 的两种 tier:
- CNI tier(`egress_cni_default_deny`,gVisor 唯一能运行的机制)仍会执行出口控制,但所有沙箱共用一条
  静态 CIDR 规则,没有 FQDN 白名单。因此它不声明该能力,要求该能力的 spec 在那里 fail closed。
- 两种机制都依赖集群 NetworkPolicy。provider 在首个沙箱**内部**确认元数据服务与 Kubernetes API server
  不可达,以此验证它。

### 刻意不做的两项声明

**inode 配额。** `ulimit -f` 限制的是单个文件的大小;Kubernetes 的 `ephemeral-storage` 限制由 kubelet
的驱逐轮询执行,没有 inode 维度。两者都不限制 inode 数量,所以 `inode_quota_enforced` 默认为 `false`,
请求 inode 限制的任务会 fail closed,而不是在一个没有任何东西提供的保证下运行。

**沙箱内的权限。** execd 运行的每条命令都继承 execd 自己的环境(包括其访问令牌),而 `POST /command`
接受调用方提供的 `uid: 0`。因此沙箱内的 agent 可以访问 execd,并在**自己的沙箱内**获得 root。本后端没有
任何地方声称相反,也没有任何能力依赖于 `ulimit` 前缀这类沙箱内机制。

本后端真正保证的是影响范围:agent 无法触及控制面的凭证、其他租户的沙箱、宿主文件系统或 GitHub 写令牌。
沙箱之间以及与宿主之间的隔离由 gVisor/Kata 与 pod 安全上下文强制,两者 agent 都无法影响。

---

## 6. fail-closed 原因码

每一次拒绝都带有机器可读的代码,既在异常上,也在审计事件中。

| 原因码 | 含义 | 处理方式 |
| --- | --- | --- |
| `pool_not_attested` | 请求 warm pool,但没有同时声明 `egress_preapplied`、`recycle_delete`、`image_digest` | pool 模式会绕过镜像白名单、资源限制与出口策略;三者都声明,或停止使用 pool |
| `runtime_class_mismatch` | 沙箱内核与声明的运行时矛盾(只在看到 gVisor 内核时才会触发;单向检查见 §5) | 服务端的 `[secure_runtime]` 与 tier 的 `runtime_class` 不一致,或节点上缺少该 RuntimeClass |
| `egress_not_deny_default` | sidecar 报告 `allow` | 检查该 tier ConfigMap 中的 `[egress]` |
| `egress_mode_insufficient` | sidecar 报告 `dns` 而不是 `dns+nft` | 仅 DNS 无法阻止直连 IP;设置 `mode = "dns+nft"` |
| `egress_cni_not_enforced` | 沙箱连通了元数据服务或 Kubernetes API server | 集群 NetworkPolicy 没有限制这个 pod:应用 `networkpolicy.yaml`,检查其 `podSelector` 与沙箱 pod 标签匹配,并检查 service CIDR 落在其排除网段之一内 |
| `egress_probe_unavailable` | 集群出口探针没有给出结论 | 需要沙箱镜像的 `PATH` 中有 `python3`;无法验证的声明会被拒绝,而不是被信任 |
| `agent_state_unavailable` | 在无法携带 CLI transcript 的 provider 上续接会话线,或已存储的 transcript 存在但无法读取 | 在创建沙箱**之前**抛出,所以一个本来就无法续接的轮次不会产生任何开销;检查 `OPENACE_AGENT_STATE_ROOT` 是否可写 |
| `spec_refused` | 请求无法构建(镜像、卷、出口、pids) | 消息中给出字段名 |
| `stale_generation` | 句柄来自一次 reconcile 代次提升之前 | 无害;workflow 会重新创建 |
| `destroy_unconfirmed` | 已发出销毁,但从未观测到终态 | reconciler 会重试;检查服务端健康 |
| `not_an_agent_turn` | 对普通命令调用了 `get_transport` | 内部错误——agent 轮次需要 `OpenSandboxTurnSpec` |
| `command_too_long` *(计划中)* | 拼装后的 env + argv 超过 `MAX_ARG_STRLEN`;目前以不带原因码的普通 `SandboxError` 抛出 | 精简环境 |
| `pty_stream_lost` | PTY socket 在没有退出帧的情况下断开 | 报告为崩溃,绝不当作完成——见 §7 |
| `workspace_setup_failed` | 沙箱内的仓库合成命令失败 | 通常是镜像缺少 `git`,或 `/workspace` 不可写 |
| `manifest_producer_failed` / `manifest_missing` | ChangeSet 生成器失败或没有输出 | 通常是镜像缺少 `python3` |
| `pause_unconfirmed` / `resume_unconfirmed` | 沙箱从未报告预期状态 | 请求被接受但状态转换没有完成;检查服务端健康 |
| `invalid_snapshot` | 传给 `upload_workspace` 的不是 worktree 路径 | 内部错误 |
| `sandbox_unavailable` | 一次拒绝传到了 agent runner | 消息中带有底层原因码 |

ChangeSet 拒绝使用自己的一组代码:`absolute_path`、`path_escape`、`repo_integrity`、`symlink_escape`、
`file_too_large`、`too_many_files`、`total_too_large`、`unsafe_mode`、`secret_path`。

sandboxed 交互 WebUI 启动器(#3378,§8)另有两个运行期代码——`sandbox_create_failed` 与
`sandbox_endpoint_unresolved`——以及一套零 pod 探针词汇(`webui_image_*`、`sandbox_proxy_*`、
`sandbox_runtime_*`),见 [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md) §3.4。

---

## 7. 已知局限

**`pause` / `resume` 在 Kubernetes 上不会收敛。** 在真实集群、两套独立环境上各观测到一次:
- `pause` 被接受,但沙箱仍是 `Running`,provider 报告 `pause_unconfirmed`。这是正确的,而不是声称一次
  并未发生的暂停。
- 随后的 `resume` 被拒:`409 Cannot resume sandbox in state Running, expected Paused`。
- 上游的 pause 依赖容器 freezer,测试过的集群没有提供。

在 freezer 能工作的环境上验证之前,请把这两个调用视为**在 Kubernetes 运行时上不受支持**;目前 autonomous
workflow 不调用它们。无论如何拒绝都是如实的,所以失败方式是请求被拒,而绝不会是以为沙箱已暂停而它仍在
运行。

**PTY socket 断开即结束该轮。** 重连没有实现,这是刻意的,不是待办:
- 重新附着到已结束的会话,会让 execd 启动一个**新的** shell,即第二个 agent 进程,而不是恢复第一个的
  视图。
- 重放的数据是合并通道的,无法再拆回 stdout 与 stderr,喂给 stream-json 解析器会把它弄坏。
- `GET /pty/{id}` 不带退出码,错过的退出帧无法挽回。

因此 socket 断开是终态,报告为结构化的崩溃。

**warm pool 会绕过若干保证。** 上游拒绝在 `poolRef` 旁同时给出 `image`、`resourceLimits`、
`networkPolicy` 与 `volumes`,所以这些来自 Pool CRD,而 provider 读不到它。pool 模式需要三项显式声明,
缺少则拒绝。

**工作区是一个合成的仓库。** agent 得到的是 `git init` 加上快照的一次提交——没有 remote、没有凭证
helper、与可信仓库没有任何关联。commit 与 push 留在控制面一侧。`HOME` 在 `/home/agent`,刻意放在
`/workspace` 之外,这样 agent 的缓存永远不会进入那个仓库。

**镜像必须在 `PATH` 上提供 `git`、`python3` 与 agent CLI。** provider 在沙箱内运行仓库合成与 ChangeSet
manifest 生成器,缺少这些二进制时两者都以结构化原因码 fail closed。agent CLI(`claude`、`qwen`……)是按
**名字**调用的,而不是控制面解析出的路径——宿主上 `shutil.which` 的结果在镜像内没有意义——所以镜像自己的
`PATH` 必须能找到它。

**镜像必须包含配置的 `runtime_user` / `runtime_group`。**
- execd 会把每个上传文件 chown 给它们,并在容器**内**查找这个名字。容器里不存在的用户会让上传失败:
  `500 error chmoding file ...: failed to lookup user <name>`。
- `upload_workspace` 是每次运行做的第一件事,所以整次运行会在那里失败。
- 默认值是 `openace`/`openace`:要么把这个用户加入 agent 镜像,要么把两个字段都设成镜像中已有的用户。
  已在真实 execd 上验证。

**控制面也必须安装 agent CLI。**
- `_run_local` 在选择 provider 之前先在宿主上解析可执行文件,缺失时返回 `CLI tool '<name>' not found`,
  即使这次运行本会完全在容器内执行。
- 因此从不在本地运行 agent 的控制面目前还无法使用本后端。
- 已作为后续工作跟踪;围绕 provider 选择重构命令构造不在 #2023 的范围内。

**除非你自己终止 TLS,gateway endpoint 是明文 HTTP。**
- 服务端返回裸主机名,客户端默认使用 `http://`。
- 在 `direct` 下这些流量在集群内部;在 `gateway` 下,工作区快照与每个沙箱的凭证会经过任何通往
  `ingress.gateway.address` 的路径。
- `k8s/extras/opensandbox/` 中没有任何东西提供 TLS:请在你的 ingress 终止它,或把 gateway 地址放在你
  信任的网络上。
- 请把这当作部署要求,而不是锦上添花。

**gVisor 的出口控制比 Kata 粗,这个差别是真实的。**
- egress sidecar 通过 iptables nat 表重定向 DNS,而 gVisor 的网络栈没有实现它。
- 真实的服务端会在启动时记录这一不兼容,然后对每个携带 `networkPolicy` 的创建请求回复:
  `networkPolicy is not compatible with runtime 'gvisor': ... Use a compatible runtime (e.g. kata) or
  remove networkPolicy.`
- 这是运行真实服务端时才发现的。此前每次评审都认为出货的 gVisor tier 可用,而事实上它连一个沙箱都
  创建不了。

因此 gVisor tier 采用上游自己给出的办法:
- provider 完全省略 `networkPolicy`,出口改由下一层的集群 `NetworkPolicy` 强制
  (`k8s/extras/opensandbox/networkpolicy.yaml`)。CNI 在沙箱内核之外执行它,缺少 nat 表在那里无关紧要。
- 这样的 tier 声明 `egress_cni_default_deny`,而不是 `egress_enforced`。
- `parse_backend_config` 拒绝在 gVisor 下使用 sidecar,也拒绝两种机制都不声明或都声明的 endpoint。

**确切地说,你放弃了什么。** 集群策略基于 CIDR,对每个沙箱都相同。
- **它拒绝的:** 实例元数据服务、集群自己的 pod 与 service 网段,以及所有私网。provider 在首个沙箱内部
  验证这一点,而不是信任声明。
- **它放开的:** **整个公网仍然可达**。没有 FQDN 白名单,也没有按沙箱区分。

因此:

- gVisor tier 不声明 `network_egress_policy`,workflow 行上的有效策略快照会记录它的缺失;
- 自带 `network_egress` 的 spec 在那里被拒,而不是在一个该 tier 无法兑现的策略下运行;
- 这样的 tier 的 `egress_allow_hosts` 必须为空,因为没有东西会执行它。

当白名单本身就是你需要的控制时选 Kata:能访问任意公网主机的 agent,就能向任意公网主机外带数据。当更低的
启动开销更重要、CIDR 边界已经足够时选 gVisor。两种 tier 提供的 `namespace_isolation` 完全相同。

**gateway 入口是必需的,不是可选的。**
- `secureAccess` 是阻止一个沙箱访问另一个沙箱 execd 的每沙箱凭证。上游只对 `[ingress] mode = "gateway"`
  下的 Kubernetes 沙箱兑现它。
- `k8s/extras/opensandbox/` 相应地配置了 gateway 模式、`[ingress.gateway]` 与
  `OPENSANDBOX_SECURE_ACCESS_*` 签名密钥。**你必须为自己的部署设置 `ingress.gateway.address`**(一个
  通配域名,不带 scheme)。
- 无法声明 `secure_access_required` 的 tier 会在 `create()` 时被拒,而不是在对等边界敞开的情况下运行。
  在 `direct` 下,每个沙箱共用一个静态 `EXECD_ACCESS_TOKEN`,任何 agent 都能从 execd 的环境中读到它;
  #2023 的 `test_sandbox_cannot_read_host_or_peer_workspace` 正是为禁止这种情况而存在。

**BatchSandbox CRD 及其 controller 是前置条件。** 服务端配置为 `workload_provider = "batchsandbox"`,但
该 CRD 与协调这些对象的 controller 来自上游的 `opensandbox-controller` Helm chart,本 kustomization
刻意不打包它。请在应用这些 manifest **之前**安装并固定其版本,否则首个沙箱创建请求会被接受却永远不会被
协调。见 README。

**孤儿回收只按 workflow 行进行。**
- `reconcile_orphans()` 是对整个 lifecycle server 按元数据范围的清扫,它没有生产调用方;销毁通过
  `destroy_attribution` 作用于数据库已知的行。
- 归属信息现在在 `create()` 返回 id 的那一刻就持久化,所以可能留下无法命名的沙箱的崩溃窗口已经关闭。
  但 workflow 行完全丢失的沙箱,仍由其 TTL 而不是 Open ACE 回收。
- 如果这个清扫将来有了生产调用方,它**必须**保留 §8 所述的交互 WebUI 排除;否则第一次清扫就会销毁所有
  在线用户的 WebUI pod。

**多轮 `--resume` 只携带 CLI transcript,别无其他。**
- 每一轮都得到一个 `HOME` 为空的全新沙箱,所以 `--resume` 读取的 transcript 会在沙箱销毁前导出,并导入
  下一个沙箱(#3237)。
- 只移动一个文件,位置按各工具自己的布局:claude-code 为
  `$HOME/.claude/projects/-workspace/<id>.jsonl`,qwen-code-cli 为
  `$HOME/.qwen/projects/-workspace/chats/<id>.jsonl`(#3319)。
- 从不移动 `.claude.json`、`.credentials.json` 或设置文件:沙箱环境是构造出来的,从不继承,凭证不能经过
  控制面往返。
- 真实 CLI 证实,仅这一个文件就足以让 `--resume` 解析成功,并保留原会话 id。

**两个 stream-json 工具都支持携带(#3319)。**
- claude-code 与 qwen-code-cli 都会在 stdout 上输出 `session_id`。runner 在该轮中捕获它、持久化到
  `agent_sessions.cli_session_id`,下一个里程碑的 `_resolve_session_line` 再把该线的跟踪 id 映射过去,
  续接的是真实的 CLI 会话,而不是跟踪 id。
- **其他**工具的续接轮次(将来某个没有 provider transcript 路径的 stream-json CLI)仍会在创建沙箱之前以
  `agent_state_unavailable` 被**拒绝**,而不是冷启动并静默丢失历史。

其他工具不受影响,因为没有其他工具会走到这条路径。ZCode 使用自己的 app-server 协议;单次调用的工具
(codex、openclaw)没有 stdin 协议,所以两者都在选择沙箱 provider 之前就从 `_run_local` 返回,改为启动
本地进程——它们根本没有临时 `HOME` 需要携带东西。

transcript 存放在控制面的 `OPENACE_AGENT_STATE_ROOT` 下(默认与每任务运行时目录并列),以会话线稳定的
跟踪 id 为键,workflow 进入终态时清除。该默认位置在 tmpfs 的 `/run` 上——希望 transcript 在重启后保留,
请把覆盖路径指向持久存储。

> **部署要求:state root 必须由运行 workflow 的每个进程以及 web 角色共享。**
>
> 这不是调优选项。两个独立的事实使它成为正确性要求,而不是偏好:
>
> * **web 角色清除的是 scheduler 角色写入的内容。** `stop_workflow`、验收覆盖以及两个删除路由都会丢弃
>   某个 workflow 的 transcript,它们运行在 web 进程中——而 transcript 由 scheduler 进程写入。存储分离时,
>   这些清除删掉的是一个无关的空目录,真正的 transcript 会被无限期保留,因为之后已没有任何东西能识别一个
>   已删除的 workflow。
> * **autonomous 调度器不是按 leader 门控的。** `_run_loop` 在每个副本上轮询,并以数据库锁按 workflow
>   仲裁,所以 workflow 的归属会在里程碑之间正当地在副本间转移。按副本分离存储时,第 N 轮的 transcript
>   对接手第 N+1 轮的副本不可见,续接会静默冷启动——恰恰是这个功能要消除的失败。
>
> 出货的 manifest 已这样配置:`k8s/deployment.yaml` 与 `k8s/scheduler-deployment.yaml` 都把 RWX 的
> `open-ace-data` 卷挂到 `/var/lib/openace/agent-state`(subPath `agent-state`)并把
> `OPENACE_AGENT_STATE_ROOT` 设为它;`docker-compose.yml` 让 `open-ace` 与 `scheduler` 服务共用一个
> `agent-state` 卷。本部署本就需要 RWX(见 `k8s/storage.yaml`),所以这增加的是一个共享**位置**,而不是
> 新的存储类。
>
> 单进程部署(一个 systemd unit 运行两个角色)无需额外配置。

已存储 transcript **不存在**的会话线(它的第一轮,或清空了 tmpfs 的控制面重启)只会开始一个新会话,这不是
失败。在根本无法携带状态的 provider 上的会话线,或已存储 transcript 存在但无法**读取**的会话线,会在创建
沙箱**之前**以 `agent_state_unavailable` 被拒,所以一个本来就无法续接的轮次不会消耗 token。三种情况的区别
是刻意的,与 `scripts/openace-run-as.sh` 在其 fail-closed 捕获(`exit 70`)、仅记日志的 exit-trap 捕获与
尽力而为的恢复之间所做的划分相同。

---

## 8. 交互工作区(`sandboxed` 隔离等级)

自 #3378 起,本后端也承载**交互工作区**:部署可以把每个用户的 qwen-code-webui 放进各自的 OpenSandbox
pod 运行,而不是作为某个 OS 账户下的本地进程。
- 浏览器通过 web 进程中一个每实例的本地端口代理访问 pod。
- 身份是每实例的 token secret,而不是宿主 uid。
- 能力契约(探针原因码、维度表、TTL 链,以及如实声明清单:配置面验证与每 pod 验证之分、崩溃丢失窗口、
  快照上限)见 [WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md) §6。
- 本节只介绍涉及**本**后端配置文件及驱动它的 web 进程的部分。

不需要 Kubernetes 的 sandboxed 形态——本机 gVisor 或 Kata 容器(#3431/#3438)——不使用本文件,见
[WORKSPACE_ISOLATION_CAPABILITIES](WORKSPACE_ISOLATION_CAPABILITIES.md) §5.2、§5.3。

**配置。** 本文件中一个键,加上 config.json 中两个:

- `endpoints.<tier>.webui_image`:pod 镜像,见 §3 的说明。设置它才使 `sandboxed` 等级可被探测,探针通过
  会把部署的默认启动形态切换为 sandboxed。
- config.json `workspace.webui_callback_url`:**必填**。静态探针把它当作 pod 访问控制面 LLM 代理的 URL。
  sidecar tier 上,控制面主机名必须在该 tier 的 `egress_allow_hosts` 中;CNI tier 上它必须公网可达
  (loopback、私网与集群内地址在探测时被拒)。
- config.json `workspace.sandbox_tier`:可选。交互 pod 在哪个 endpoint tier 上启动,默认为后端的
  `default_tier`。

**web 进程的环境变量:**

- `OPENACE_WEBUI_ORPHAN_RECONCILE=1`:WebUI pod 孤儿回收的显式触发开关。
  - **只由** web 服务入口设置:docker-entrypoint.sh 的 gunicorn 路径(明确**不**包括 scheduler 容器),
    以及 server.py 的 dev `__main__`。
  - 管理脚本导入 `create_app` 时不设置它,因此永远不会清扫;测试进程被显式排除(`PYTEST_VERSION` /
    `TESTING`)。
  - 清扫本身 fail-soft:损坏的 sandbox-backends.json 只记日志并跳过,不会让 web 启动失败。
- `OPENACE_WEBUI_STATE_ROOT`:WebUI 会话历史的快照根目录,默认 `<CONFIG_DIR>/webui-agent-state`(即
  `~/.open-ace/webui-agent-state`),每个用户一个 `webui-<user_id>.tar`。
  - 实现说明:设计方案曾把它放在配置目录**旁边**,而出货的默认值在配置目录**里面**。CONFIG_DIR 是
    Docker 部署为持久化而挂载的目录,放在旁边就会位于容器本地,容器重建时丢失所有快照。
  - 回收器从不扫描这个根目录。
- `OPENACE_WEBUI_STATE_MAX_BYTES`:单个快照的上限,默认 16 MiB。超过上限时跳过导出并记 WARNING,该用户的
  历史冻结在最后一份完好快照(见能力契约文档 §6.4)。

> **承重的排除契约(N7):`reconcile_orphans` 永远不能认领 WebUI pod。**
>
> 交互 WebUI pod 带有 `openace.webui.kind=webui` 元数据,且**不**绑定 workflow——永远不会有 workflow 行
> 认领它们,所以没有该 kind 排除的 `reconcile_orphans()` 清扫会把每个在线用户的 WebUI pod 判为孤儿,并在
> **第一次运行时把它们全部销毁**。该排除在 `provider.reconcile_orphans` 中实现(元数据过滤 + 客户端复查),
> 并由测试钉住。任何把该清扫接到新的生产调用方的人**必须**保留该排除;WebUI pod 由 web 进程自己按代次
> 进行的 reconcile 回收(`app/services/webui_sandbox_opensandbox.py`),而不是由 workflow 在用集合回收。

**单 web 进程假设。** WebUI pod reconcile 会销毁 `openace.webui.process_generation` 元数据不属于当前 web
进程的 pod——只有当恰好一个 web 进程拥有该安装时,这才是可靠的判别依据。出货的 compose 部署(一个
`open-ace` 应用容器)满足这一点;**多个 web 副本或新旧重叠的滚动部署不受支持**,它们会互相销毁对方的 pod。

---

## 9. 后端对比

所有来源都已标注,这里没有来路不明的数字。

### 启动与隔离开销

| 运行时 | 隔离 | 启动开销 | 内存开销 |
| --- | --- | --- | --- |
| runc | 进程 cgroups | ~0 ms | 极小 |
| gVisor | 用户态内核,系统调用拦截 | ~10–50 ms | ~50 MB |
| Kata (QEMU) | 完整虚拟机 | ~500 ms | ~20–50 MB |
| Kata (Firecracker) | microVM | ~125 ms | ~5 MB |

*来源:OpenSandbox `docs/guides/secure-container.md`,上游公布的数据。本项目未测量。*

### 生命周期各阶段

| 阶段 | `legacy_posix` | `opensandbox` |
| --- | --- | --- |
| 冷启动 | 无——直接派生进程 | 拉取镜像,再加上面的运行时开销 |
| 创建沙箱 | `fork`/`exec` | 一次同步的 `POST /v1/sandboxes` |
| 传输工作区 | 无——worktree 已在本地 | 每个文件一次上传,再加仓库合成 |
| 执行 | 本地 `Popen` | PTY WebSocket,或 `POST /command` |
| 收集变更 | 本地 git | 下载 manifest 并由控制面校验 |
| 销毁 | 进程组信号 | `DELETE`,轮询直到终态 |

*出处:结构性的,由实现推导。**这些阶段没有给出挂钟时间,因为本项目尚未在集群上测量过。** 用于容量规划前,
请用你自己部署的数据填充此表;issue 要求的指标会在每次生命周期调用时以审计事件发出。*

### 兼容性

| 关注点 | gVisor | Kata |
| --- | --- | --- |
| 系统调用覆盖 | 有文档的子集;少见的系统调用可能失败 | 完整的 Linux 内核 |
| 硬件要求 | 无 | VT-x / AMD-V + KVM |
| 密度 | 高 | 较低——每个沙箱一台虚拟机 |
| 出口执行 | 仅集群 NetworkPolicy:CIDR、静态、公网放开 | egress sidecar:每沙箱 FQDN 白名单,默认拒绝 |
| 声明 `network_egress_policy` | 否 | 是 |
| 典型用途 | 所有租户的默认 | 出口必须白名单化的租户 |

*出处:前四行来自上游指南与各运行时项目自己的文档。出口相关行是本仓库自己的行为——见 §7——gVisor 的局限是
运行真实服务端时发现的,而不是从文档里读来的。*

**本节没有任何性能数字是本项目测量的**——上面的开销数据来自上游。这与后端能否**工作**是两回事,后者已经过
测试;以下准确说明哪些已经、哪些尚未在真实基础设施上运行过:

- **gVisor tier 的完整生命周期已在真实集群上以 `runsc` 端到端运行过。**
  - 流程:创建 → 内核探针 → CNI 探针 → 上传与 git 合成 → 带 SSE 的前台执行 → PTY agent 轮次 →
    证据 → `collect_changes` → `apply_changes` → 销毁。
  - 那次运行暴露了一整套绿色测试掩盖的线级缺陷:文件模式编码、SSE 分帧、execd 的身份模型、
    `networkPolicy` 不兼容、`/proc/version` 探针的误拒、podSelector 匹配不到任何 pod 的 NetworkPolicy、
    root 所有的 `/workspace` 上 git 的 `dubious ownership`,以及单位发错的命令超时。
- **CNI 出口机制已在执行策略的 CNI(Calico)上双向验证。**
  - 没有 manifest 时,启动探针读到 API server 可达,并以 `egress_cni_not_enforced` 拒绝。
  - 应用 manifest 后,两路都读到被阻断,运行继续。
  - 对配置为 gVisor 的服务端创建不带 `networkPolicy` 的沙箱,同样确认会被接受。
- **OpenSandbox 的 Kata tier 从未被实际运行过。** 它需要 `/dev/kvm`,当时搭建环境的尝试到达了嵌套 VT-x 与
  `kata-deploy`,然后因 guest 内核缺少 `vhost_net` 模块而失败。因此本文中关于 Kata **tier** 的每一处陈述都是
  设计意图,而非测量。(不依赖 Kubernetes 的本机 Kata 容器形态,#3438,已在嵌套 KVM 主机上用 Kata 3.32
  做过真实验收;那是交互工作区,不是本后端。)
- `dubious ownership` 修复中的一部分——覆盖 **agent 自己**运行的 git 命令(而不是仓库合成)的全局
  `safe.directory`——是在本地用 git 的 `GIT_TEST_ASSUME_DIFFERENT_OWNER` 钩子验证的,而不是在集群上。

**你的 CNI 必须真正执行 NetworkPolicy。** 若干常见的开发用 CNI(包括 kind 默认的 `kindnet`)接受
`NetworkPolicy` 对象然后忽略。在 sidecar tier 下,这只会削弱 `metadata_cidr_blocked`;在 CNI tier 下,这
意味着完全没有出口控制。这正是启动探针存在且 fail closed 的原因:CNI 忽略策略的集群会在首个沙箱时以
`egress_cni_not_enforced` 被拒,而不是在出口敞开的情况下运行 agent。

### 成本

成本主要由节点容量决定,而节点容量取决于上面的内存开销与你的沙箱并发数。Kata 的每沙箱一台虚拟机使密度成为
决定性因素;gVisor 的开销与 runc 足够接近,实际差别在调度而非占用。*这里不给出金额:它完全取决于你的集群
与云厂商。*

---

## 10. 故障排查

**每个 execd 调用都返回 401。** `execd_token_env` 未设置,或指向一个空变量。注意 execd 的鉴权中间件在
token 为空时会直接放行,所以不带 `EXECD_ACCESS_TOKEN` 启动的服务端会接受匿名调用——这就是
`execd_token_required` 必须声明的原因。

**首个沙箱报 `runtime_class_mismatch`。** 服务端的 `[secure_runtime]` 与 tier 的 `runtime_class` 不一致,
或调度该 pod 的节点上没有安装该 RuntimeClass。

**agent 无法编辑自己的文件。** 检查 `runtime_user`/`runtime_group`。execd 可能以 root 运行,权限严格的
root 所有文件对非 root 的 agent 不可写。

**重启后孤儿清扫什么都没销毁。** 确认 workflow 行带有 `sandbox_provider = "opensandbox"` 与 `sandbox_id`;
清扫以这两者为键。

**共享服务端上的沙箱越积越多。** 清扫按 `openace.provider` 元数据过滤,只销毁控制面不再认领的沙箱。其他
系统创建的沙箱永远不会被碰——这是有意的。

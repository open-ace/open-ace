# Workspace Isolation Capabilities

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
  "policy_revision": "2026-09-16.1"
}
```

- `local_workspace_multi_user`:本地交互工作区多用户隔离是否受支持。
- `backend`:实际运行器——`qwen-code-webui-per-user`(每用户独立 WebUI 进程)、
  `qwen-code-webui-shared`(共享单实例)或 `opensandbox:<tier>`(#3378:sandboxed
  等级,WebUI 运行于该 tier 的 OpenSandbox pod)。
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
#2022 sandbox 契约(`sandbox_effective_policy`),见 `docs/SANDBOX_BACKENDS.md`。

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
| `sandbox_backend_unconfigured` | 探测 | sandbox-backends.json 缺失、不可解析或未配置 | 提供/修复后端配置(见 `docs/sandbox-backends.md` §3) |
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
矩阵取值见上表(#3410 重标定);`filesystem_api` 在 sandboxed 下与 os_user 下的取值
不同是**刻意**的——本地强制不等于已接线到 pod。

## 5. 多用户模式部署要求(policy revision 2026-09-16.1)

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
4. HTTPS:沿用外部反代的端口段映射(`/webui/<port>`,见 `docs/cn/NGINX.md`)
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

**安全准入示例(#3410)**。下面这段判定与单元测试
`TestDocumentedAdmissionPredicate`(`tests/unit/test_workspace_isolation_contract_3374.py`)
**逐项一致**——改一处必须改另一处,否则文档会随版本漂移:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<open-ace>/api/workspace/isolation-capabilities > caps.json
python3 - caps.json <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
NEEDED = ("webui", "filesystem_api", "session_history")    # 本地工作台入口
KNOWN  = {"enforced", "partial", "remote_machine_scope",
          "separate_contract", "sandboxed_entry_not_wired", "disabled"}
REVIEWED = {"2026-09-16.1"}                                # 你已评审过的 revision
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
本身无法廉价验证);目标系统账户拒绝 uid 0 与保留段(uid<1000)。

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
5. 交互工作区 `sandboxed` 等级:#3378 已交付(§6);遗留 follow-up:真实集群
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

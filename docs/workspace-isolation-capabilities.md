# Workspace Isolation Capabilities

Open ACE 为本地交互工作区(聊天 WebUI、文件接口、会话历史)提供**版本化的多用户
隔离能力契约**。管理员与可信接入方应通过 API 查询实际生效的能力,而不是依赖
README 声明或客户端传入的 capability 布尔值。本文档说明契约语义、部署要求、
reason code 对照与已知缺口。关联 issue:#3374。

## 1. 能力契约字段

查询端点:`GET /api/workspace/isolation-capabilities`(需要登录会话)

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "qwen-code-webui-per-user | qwen-code-webui-shared",
  "isolation_level": "none | os_user",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress"],
  "reasons": [{"code": "...", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract"
  },
  "policy_revision": "2026-09-11.2"
}
```

- `local_workspace_multi_user`:本地交互工作区多用户隔离是否受支持。
- `backend`:实际运行器——`qwen-code-webui-per-user`(每用户独立 WebUI 进程)或
  `qwen-code-webui-shared`(共享单实例)。
- `isolation_level` / `enforced` / `unsupported`:见下节。
- `reasons`:unsupported 时的机器可读原因(可能为空)。
- `entry_points`:各入口的隔离覆盖状态(§4)。**仅在 supported 快照中输出**——
  入口矩阵描述的是多用户隔离的覆盖面,unsupported(单用户/未验证/降级)部署
  不携带该矩阵,避免"无隔离"与"webui: enforced"同帧自相矛盾。该矩阵为静态
  审计结论,随 `policy_revision` 版本化;各入口与代码的 conformance 绑定为
  后续工作(§7)。
- `policy_revision`:契约语义版本;推导逻辑或入口矩阵变化时递增。

## 2. 隔离等级语义

| 等级 | 语义 |
|---|---|
| `none` | 所有本地交互会话共享同一服务账户运行,无用户间文件/环境/进程隔离(单用户轻量模式,by design) |
| `os_user` | 每用户独立系统账户与 HOME/TMP/XDG;WebUI 进程以该用户 UID 经 `sudo -u` 启动;子进程环境为显式白名单(真实模型 API key 永不进入,以代理 token 替代);文件接口应用层 home 子树锁 + OS 权限 |
| `sandboxed` | 容器/微内核隔离(OpenSandbox 级)。**交互工作区当前不支持**;请求该等级会被结构化拒绝 |

**边界声明**:`os_user` 共享宿主内核,没有命名空间隔离、没有网络出口策略——
"分目录/更换 HOME"不构成强运行时隔离。`resources` 与 `network_egress` 两个维度
对交互工作区始终列在 `unsupported`(交互 WebUI 无 per-task cgroup,仅实例数上限
与空闲清理;无出口策略)。autonomous 任务的资源与沙箱策略沿用独立的
#2022 sandbox 契约(`sandbox_effective_policy`),见 `docs/sandbox-backends.md`。

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
| `identity_mapping_missing` | 用户在数据库中无 `system_account` 映射;显式要求隔离时不做 username 静默回退 |
| `per_user_launch_unavailable` | 启动路径无法以该用户 UID 运行(message 内嵌 §3.3 的原因码) |

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

## 4. 入口覆盖矩阵

| 入口 | 状态 | 说明 |
|---|---|---|
| webui(聊天工具) | enforced | 每用户独立实例/UID/端口/token;停止与 token 撤销按 user_id 隔离 |
| filesystem_api | partial | 上传/下载/删除有 home 子树锁(#1813);browse/check-path 依赖 OS 权限作用域 |
| session_history | enforced | 应用层所有权闸门 + 租户 fail-closed |
| terminal | partial | 终端令牌按机器授权,不绑定终端会话所有者(已知缺口) |
| vscode | partial | owner 记录为 machine.created_by;project_path 校验弱(已知缺口) |
| autonomous | separate_contract | 沿用 #2022 sandbox 契约,不在本契约范围 |

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

## 6. 接入方用法

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
本身无法廉价验证);目标系统账户拒绝 uid 0 与保留段(uid<1000)。

## 7. 已知缺口与后续路线

以下缺口已在审计中确认,按 issue #3374 的指示拆分为独立后续工作,本契约的
`entry_points` 矩阵如实反映:

1. 终端/VSCode 令牌与用户绑定:`terminal/<id>/status` 不向同机器其他授权用户返回
   browser token;attach 校验会话所有者;VSCode owner 改为请求者。
2. 终端 `work_dir` 服务端校验(对齐 fs.py base_dirs)。
3. `/fs/browse`、`/fs/check-path` 补 home 子树锁。
4. WebUI `token_secret` 未持久化时重启导致已发 token 失效的加固。
5. 交互工作区 `sandboxed` 等级(基于 OpenSandbox)。
6. 多用户模式真实 Linux 端到端隔离验收(并发用户 + 越权尝试矩阵)。

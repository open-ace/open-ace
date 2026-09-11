# Issue #3374 本地交互工作区多用户隔离:统一能力契约(第一增量)设计文档

日期:2026-09-11(修订 2:采纳独立评审第二轮 4 条意见)
Issue: open-ace/open-ace#3374
状态:待独立评审(修订版)

## 1. 背景与问题定位

Issue #3374 要求为"本地交互工作区多用户隔离"提供统一能力契约与端到端验收,并明确指示:
**优先复用已有机制、核对覆盖情况,发现缺口再拆分修复,不推倒重做。**

### 1.1 现状审计结论(源码基线:main @ 5a430fa4)

已有的隔离资产(复用,不重做):

| 领域 | 机制 | 位置 |
|---|---|---|
| 交互 WebUI | 每用户独立 qwen-code-webui 实例,按 user_id 键控,端口池 3100-3200;`sudo -u <system_account>` 经 `openace-webui-launch` 包装器启动 | `app/services/webui_manager.py:759-786, 1221-1301` |
| 环境隔离 | 子进程环境显式白名单构造,真实模型 API key 永不进入子进程(以 JWT 代理 token 替代),Issue #2298 | `webui_manager.py:1053-1090` |
| 文件接口 | base_dirs 前缀 + realpath 黑名单 + 写操作 home 子树锁(#1813)+ `openace-rm`/`openace-chown` 审计包装器 | `app/routes/fs.py:237-334` |
| 会话历史 | `_check_session_access` 所有权闸门,无租户 fail-closed | `app/routes/workspace.py:1317-1338, 254-268` |
| autonomous 沙箱 | 成熟能力契约(#2022):`SandboxCapability` 枚举、`CapabilityUnsupported` fail-closed、`sandbox_effective_policy` 快照 | `app/modules/workspace/autonomous/sandbox/` |
| 账号体系 | 多用户模式为每用户创建系统账户 + HOME、`openace-shared` 组共享项目(#2730) | `app/utils/workspace.py:122-441` |

核心缺口(本设计针对):

1. **没有统一、版本化的能力契约**:接入方无法查询"本地交互工作区多用户隔离是否支持、
   实际隔离等级、哪些维度已强制"。`GET /api/feature-flags` 只报功能开关,不报隔离。
2. **启动路径存在静默回退**,违反 issue 的"不静默切到共享服务账户":
   - `system_account` 缺失时静默回退 `username`(`app/routes/workspace.py:2366`);
   - 非 Docker 多用户模式 `ensure_system_user` 直接返回 True 跳过创建
     (`app/utils/workspace.py:139-142`,Issue #3130),后续失败只有泛化错误;
   - dev 目录模式(`webui_dir` 为真)以当前服务用户运行 WebUI,多用户模式下也不切换
     UID(`webui_manager.py:1186-1201`)。
3. **隔离等级无明确语义**:没有任何地方声明"分目录/换 HOME ≠ 强运行时隔离"。

### 1.2 范围决策(第一增量)

Issue 覆盖六个能力方向 + 端到端验收,单 PR 无法全部完成。按 issue 自身指示拆分:

**本 PR(第一增量)交付——"统一能力契约 + 启动路径结构化拒绝":**

1. 版本化能力契约模块与查询 API(`GET /api/workspace/isolation-capabilities`);
2. `/api/workspace/user-url` 的结构化拒绝机制(接入方显式声明隔离要求时,能力不足/
   身份映射缺失/无法按用户启动 → 结构化 400 拒绝,不静默回退);
3. 启动结果返回实际生效的策略快照(向后兼容的增量字段);
4. 能力矩阵文档(入口 × 隔离维度审计结果 + 已知缺口 + 后续拆分路线)。

**明确不在本 PR(列为后续 issue,见 §8):** 终端/VSCode 令牌用户绑定加固、
`/fs/browse` home 子树锁、WebUI token_secret 持久化加固、交互工作区 sandboxed 等级、
真实 Linux 端到端隔离验收。

**向后兼容的准确表述**:缺省(无 `required_isolation` 参数)路径除成功响应新增
`isolation` 回显字段外,响应键集合、状态码与全部既有行为不变——默认 UI、单用户
模式零退化。验收按此表述执行,不按"字节级完全一致"执行。

## 2. 能力契约设计

### 2.1 契约形状

遵循 issue 建议的字段,扩展 `reasons`(机器可读)与 `entry_points`(入口覆盖矩阵):

```json
{
  "local_workspace_multi_user": "supported | unsupported",
  "backend": "qwen-code-webui-per-user | qwen-code-webui-shared",
  "isolation_level": "none | os_user",
  "enforced": ["identity", "filesystem", "environment", "process"],
  "unsupported": ["resources", "network_egress"],
  "reasons": [{"code": "multi_user_mode_disabled", "message": "..."}],
  "entry_points": {
    "webui": "enforced",
    "filesystem_api": "partial",
    "session_history": "enforced",
    "terminal": "partial",
    "vscode": "partial",
    "autonomous": "separate_contract"
  },
  "policy_revision": "2026-09-11"
}
```

### 2.2 隔离等级语义(明确语义,禁止夸大)

| 等级 | 语义 | 判定条件 |
|---|---|---|
| `none` | 所有本地交互会话共享同一服务账户运行;无用户间文件/环境/进程隔离(单用户轻量模式,by design) | 多用户模式未启用,或平台/部署形态无法按用户隔离 |
| `os_user` | 每用户独立系统账户与 HOME/TMP/XDG;WebUI 进程以该用户 UID 经 `sudo -u` 启动;环境白名单;文件接口应用层 home 锁 + OS 权限。**共享宿主内核,无命名空间/出口隔离,不宣称强运行时隔离** | 多用户模式启用 + **linux** + `_is_docker_multi_user_mode()`(root + `WORKSPACE_BASE_DIR=/workspace`) |
| `sandboxed` | 容器/微内核隔离(OpenSandbox 级)。**交互工作区当前不支持,保留等级名用于拒绝语义** | 交互路径未实现,任何请求都结构化拒绝 |

等级序:`none < os_user < sandboxed`(用于请求门闸比较)。

**平台边界**:仅 linux 可申报 `os_user`。darwin 的 `sudo -u` 机制虽存在,但
`ensure_system_user` 在 macOS 直接跳过系统用户创建(`app/utils/workspace.py:163-165`),
Open ACE 无法建立/验证身份映射——若放行会在启动时以泛化错误失败,恰好复现本设计
要消除的静默失败模式。Windows 被强制单实例(`webui_manager.py:224-230`)。
两者契约均报 `unsupported/platform_unsupported`,文档注明 macOS 不在多用户隔离
支持范围内。

维度归属(诚实申报):
- `enforced`(os_user 等级下):identity、filesystem、environment、process;
- `unsupported`:resources(交互 WebUI 无 cgroup,仅实例数上限与空闲清理)、
  network_egress(无出口策略)。

### 2.3 契约推导逻辑(fail-honest,逐层判定)

```
build_workspace_isolation_snapshot() -> IsolationCapabilitySnapshot:
  1. webui 未启用(config.enabled=false)→ unsupported,reason=webui_disabled,
     backend=qwen-code-webui-shared, level=none
  2. 平台非 linux(darwin/windows/其他)→ unsupported,reason=platform_unsupported
  3. 多用户模式未启用(config.multi_user_mode=false)→ unsupported,
     reason=multi_user_mode_disabled(单用户轻量模式,by design,非缺陷)
  4. 非 _is_docker_multi_user_mode()(euid≠0 或 base dir≠/workspace)→
     unsupported,reason=identity_mapping_unverified
     (系统账户无法由 Open ACE 创建/验证,#3130 保守判定)
  5. 其余 → supported,backend=qwen-code-webui-per-user,level=os_user,
     reasons=[]
```

推导顺序说明:第 3 步先于第 4 步,单用户模式以 root 运行的 Docker 部署
(`docker-compose.yml` 默认非 root,但自定义部署可能 root)会正确落到
`multi_user_mode_disabled` 而非误报 `identity_mapping_unverified`。

推导输入全部来自运行时真实状态(config.json 经 manager 单例、环境变量、euid、
平台),不引入新配置项;快照计算无副作用、无文件系统/子进程探测,可被端点与
启动路径低成本复用。`policy_revision` 为模块常量,推导语义或入口矩阵变化时必须
递增。

reason message 不含部署细节(如 root/路径要求),部署要求只在
`docs/workspace-isolation-capabilities.md` 说明——契约对任意已认证用户可见,
不应披露部署形态。

### 2.4 入口覆盖矩阵(静态、随 policy_revision 版本化)

| 入口 | 状态 | 依据 |
|---|---|---|
| webui(聊天工具) | enforced | 每用户独立实例/UID/token |
| filesystem_api | partial | 写操作有 home 锁(#1813);browse/check-path 仅 OS 权限作用域 |
| session_history | enforced | 应用层所有权闸门 + 租户 fail-closed |
| terminal | partial | 令牌按机器授权,不绑定终端会话所有者 |
| vscode | partial | owner 记录为 machine.created_by;project_path 校验弱 |
| autonomous | separate_contract | 沿用 #2022 sandbox 契约(sandbox_effective_policy) |

## 3. 查询 API 设计

- 路由:`GET /api/workspace/isolation-capabilities`
- 鉴权:`@auth_required`(任何已认证用户)。**取舍说明**:issue 措辞是"管理员或
  可信接入方";契约不含机密(仅等级/维度/入口状态),普通用户可见无实质风险,
  且对齐 `GET /api/feature-flags` 惯例(`app/routes/feature_flags.py`);reason
  message 已去除部署细节(§2.3)。若后续需要收紧,加 admin 装饰器是一行变更。
- 响应:200 + §2.1 契约 JSON;401 未认证;500 构造失败(结构化 `{"error": ...}`)
- 新文件 `app/routes/workspace_isolation.py`(Blueprint `workspace_isolation_bp`),
  在 `app/__init__.py register_blueprints()` 注册——对齐 feature_flags.py 模式,
  不向 2578 行的 workspace.py 继续增肥

## 4. 启动路径结构化拒绝设计

### 4.1 请求侧

`GET /api/workspace/user-url?required_isolation=<none|os_user|sandboxed>`:

- 缺省/`none`:现行为不变(仅新增 `isolation` 回显字段,见 §4.3 与 §1.2 表述);
- `os_user`/`sandboxed`:进入门闸校验,不满足即结构化拒绝。

### 4.2 门闸校验顺序与错误码(HTTP 400 + error_code,对齐仓库惯例)

| 顺序 | 检查 | error_code |
|---|---|---|
| 1 | `required_isolation` 非法取值 | `invalid_isolation_level` |
| 2 | 请求等级 > 快照支持等级(含 sandboxed) | `isolation_level_unsupported` |
| 3 | 多用户已支持但 DB 无 `system_account`(显式要求隔离时不做 username 静默回退) | `identity_mapping_missing` |
| 4 | 启动路径无法按该用户 UID 运行(§4.4 探针) | `per_user_launch_unavailable` |

**reason code 全集**(两套,接入方文档将给出完整对照):

- 契约 `reasons[]`(部署级,快照为何 unsupported):
  `webui_disabled` / `platform_unsupported` / `multi_user_mode_disabled` /
  `identity_mapping_unverified`;
- 门闸 `error_code`(请求级):`invalid_isolation_level` /
  `isolation_level_unsupported` / `identity_mapping_missing` /
  `per_user_launch_unavailable`;
- 探针原因码(嵌在 `per_user_launch_unavailable` 的 message 中):
  `platform_unsupported` / `webui_executable_missing` /
  `current_user_unresolved` / `dev_directory_mode_shared_account` /
  `sudo_unavailable`。

易混对说明:`identity_mapping_unverified`(部署级:该部署形态无法建立身份映射)
vs `identity_mapping_missing`(用户级:该用户缺 system_account 映射)。

拒绝响应体(机器可读,含实际生效能力):

```json
{
  "success": false,
  "error": "human readable",
  "error_code": "isolation_level_unsupported",
  "reasons": [{"code": "...", "message": "..."}],
  "isolation": { ...§2.1 完整契约快照... }
}
```

检查 3/4 仅在显式 `required_isolation` 时执行:默认路径保持既有 username 回退
(auth.py:101-110 登录时幂等回写,是既有映射约定),避免破坏存量部署。

### 4.3 启动成功响应增量

`user-url` 成功响应追加 `"isolation": {…契约快照…}`(报告实际生效策略,
满足"启动结果应返回实际生效的策略")。除该新增字段外,响应键集合与既有行为
不变(见 §1.2 向后兼容表述)。

### 4.4 WebUIManager 增量方法

```python
def supports_per_user_launch(self, system_account: str) -> tuple[bool, str | None]:
    """判断 WebUI 是否能以 system_account 的 UID 运行;返回 (可行, 原因码)。"""
```

封装:平台检查 + dev 目录模式检测(`_find_webui_executable()` 返回 dir 非空即
dev 模式,以服务用户运行)+ 当前用户即目标账户 / sudo 可用性。避免外部模块触碰
私有方法。

**探针成本与副作用**:`_find_webui_executable()` 可能触发构建探测(极端情况执行
`npm run build`,webui_manager.py:1376-1385)。为避免 GET 路径反复触发:
解析结果在 manager 实例内记忆化——**仅缓存成功解析**(cmd 非空)的结果,解析
失败不缓存(安装 webui 后无需重启即可恢复);记忆化只影响本探针,不改变既有
启动路径的解析行为。该取舍在能力矩阵文档中注明。

**探针平台集合说明**:探针的平台检查沿用 `("linux", "darwin")`——它镜像
webui_manager 启动路径自身的平台支持(启动分支 webui_manager.py:1202-1301 在
linux/darwin 上均有 sudo 路径),属防御性检查;与契约申报口径(仅 linux 可报
os_user,§2.2)不同是有意的:经门闸路径 darwin 上的 `os_user` 请求在第 2 项
检查(等级比较)即被拒,探针的 darwin 分支不可达,不构成夸大申报。

## 5. 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/services/workspace_isolation_contract.py` | 新建 | 契约推导、等级比较、门闸校验、快照序列化(纯计算,无副作用) |
| `app/routes/workspace_isolation.py` | 新建 | `GET /api/workspace/isolation-capabilities` 蓝图 |
| `app/routes/workspace.py:2337-2430` | 修改 | user-url 门闸 + 响应增量 |
| `app/services/webui_manager.py` | 修改 | 新增 `supports_per_user_launch()`(含成功解析记忆化) |
| `app/__init__.py` | 修改 | 注册新蓝图 |
| `docs/workspace-isolation-capabilities.md` | 新建 | 能力矩阵、等级语义、部署要求、reason code 对照、已知缺口与后续路线 |
| `tests/unit/test_workspace_isolation_contract_3374.py` | 新建 | 推导逻辑全覆盖 |
| `tests/unit/test_workspace_isolation_api_3374.py` | 新建 | 端点鉴权与形状 |
| `tests/unit/test_webui_manager_per_user_launch_3374.py` | 新建 | 探针分支全覆盖 |
| `tests/unit/test_user_url_isolation_gate_3374.py` | 新建 | 门闸拒绝/兼容路径/401/404/HTTPS 分支 |

## 6. 测试设计(要点)

1. **推导**:六种部署形态(parametrize:未启用/Windows/darwin/单用户/非 docker
   多用户/docker 多用户)各自快照正确;`policy_revision` 恒存在;诚实性不变式
   (unsupported 时 reasons 非空;os_user 等级下 resources/network_egress 必不在
   enforced)。
2. **端点**:未认证 401;认证后 200 且形状与契约模块输出一致(patch 路由模块内
   的 `build_workspace_isolation_snapshot`,不依赖宿主 `~/.open-ace/config.json`);
   构造异常 → 结构化 500。轻量单蓝图 fixture 模式(对齐
   tests/unit/test_feature_flags_api.py)。
3. **门闸**:四个 error_code 各自触发;缺省参数时行为与改动前一致(username 回退
   保留、mock manager 返回透传);成功响应含 isolation 快照;未认证 401;用户
   不存在 404;HTTPS + 多用户时 `/webui/<port>/` 相对路径分支不回归(该分支紧邻
   插入点)。patch 目标为 `app.routes.workspace._load_user_from_token`
   (workspace.py:21-26 模块顶层 from-import 绑定,patch 定义处无效),常规用例
   携带 session cookie(对齐 conftest admin_client 惯例);跨 host 的 HTTPS 用例
   必须改用 `Authorization: Bearer` header(werkzeug cookie jar 按域匹配,
   localhost cookie 不随 `base_url="https://example.com/"` 发送,用 cookie 会
   得到 401 假失败)。
4. **探针**:`_LaunchProbeManager` 绕过 `__init__`,`pwd.getpwuid`/
   `shutil.which` 经 `app.services.webui_manager` 命名空间 monkeypatch,五个分支
   (平台/可执行缺失/已是目标用户/dev 目录模式/无 sudo)各自真实命中。
5. **标记**:新测试统一 `pytest.mark.issue(3374)`;仅两个回归哨兵测试(username
   回退兼容性、HTTPS 相对路径分支)追加 `pytest.mark.regression`(pytest.ini 对
   regression 的定义是"已报告缺陷的行为保持",feature 测试不整体套用)。全部置于
   `tests/unit/`(纯计算 + stub/monkeypatch,无 OS 依赖,CI unit lane 可跑)。

## 7. 验收对照(本增量覆盖的 issue 条目)

- 契约可查询、版本化、含 enforced/unsupported/policy_revision ✓(§2-3)
- 接入方请求隔离要求时返回实际生效策略或结构化拒绝,不静默回退 ✓(§4)
- "不能将分目录宣称为强隔离":os_user 语义显式声明共享内核 ✓(§2.2)
- 单用户模式与默认 UI 零退化(除 isolation 回显字段)✓(§4.1 + 测试 3)
- 发现缺口拆分修复 ✓(§8)
- 端到端真机验收:本增量提供契约与门闸,真机隔离验收随后续加固项一并交付(§8)

## 8. 后续拆分路线(不在本 PR,建议单独 issue)

1. 终端/VSCode 令牌与用户绑定:`terminal/<id>/status` 不向同机器其他授权用户
   返回 browser token;attach 校验会话所有者;VSCode owner 改为请求者
   (审计发现的风险点 A/D,`app/routes/remote.py:3830-3890, 3681-3827`)。
2. 终端 `work_dir` 服务端校验(对齐 fs.py base_dirs)(风险点 E,remote.py:3362)。
3. `/fs/browse`、`/fs/check-path` 补 home 子树锁(风险点 C,fs.py:586-618)。
4. WebUI `token_secret` 未持久化时重启失效问题文档化/加固(风险点 F)。
5. 交互工作区 sandboxed 等级(基于 OpenSandbox)。
6. 多用户模式真实 Linux 端到端隔离验收(两个并发用户 + 越权尝试矩阵)。

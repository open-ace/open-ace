# Issue #3376 本地工作区入口边界加固 设计文档

日期:2026-09-11(修订 2:采纳独立评审两轮共 22 条意见)
Issue: open-ace/open-ace#3376(拆分自 #3374;第一增量见 PR #3375)
状态:待独立评审(修订版)

## 1. 问题与目标

#3374 审计确认三个入口的边界是"机器级/目录级"而非"用户级",存在同部署水平越权:

| # | 缺口 | 证据 |
|---|---|---|
| 1 | 终端/VSCode 凭证不绑定会话所有者 | `GET /terminal/<id>/status` 把含 browser token 与 `original_token` 的完整 info 返回给任何机器授权用户(remote.py:3888);`attach` 不校验终端归属(remote.py:3681-3827);VSCode owner 记录为 `machine.created_by`(remote.py:2904-2905),status 向机器授权用户返回 `url?token=`(remote.py:4793-4801) |
| 2 | 终端 `work_dir`、VSCode `project_path` 客户端任意指定 | remote.py:3362 原样下发(3517),无遍历/黑名单校验;`/vscode/start` 的 `project_path` 同构(4697/4712) |
| 3 | `/fs/browse`、`/fs/check-path` 无 home 子树锁 | fs.py:609-618、893-907 仅 base_dirs 前缀 + 黑名单;写操作有 home 锁(#1813,fs.py:305-334)——不对称 |

**目标**:三个入口与聊天 WebUI/会话历史使用相同的用户边界;合法管理员、所有者与共享项目场景的兼容性影响见 §8。

## 2. 关键事实(调研结论,设计依据)

1. **终端所有权的权威来源已存在**:`start_terminal` 创建 `agent_sessions` 行(`session_id=terminal_id`,`user_id=g.user["id"]`,`tenant_id`=机器租户,remote.py:3391-3399;`/terminal/cli/start` 同样建行,3567-3582)。`AgentSession` 具 `user_id`/`tenant_id`/`context` 属性(session_manager.py:223-228)。会话 GC 是 30 天过期(session_manager.py:2497),terminal_info_store TTL 仅 24 小时——**会话行必然比终端内存条目活得久**,所有权缺失时 fail-closed 安全(terminal 条目唯一生产链都先建会话行)。
2. **VSCode 启动与状态上报之间无用户关联**:`/vscode/start`(4689-4716)只生成 vscode_id 并下发命令;agent 异步经 `/api/remote/agent/message` 的 `vscode_status` 消息上报(2865+),报告链路只能拿到 machine 上下文 → 目前 owner 取 `machine.created_by`。
3. **Web 进程是单 worker**:docker-entrypoint.sh:1625 `gunicorn --workers 1`(gevent)。vscode_info_store 本身就是内存态,启动时记录"请求者 → vscode_id"的内存映射与既有架构一致,不引入新的多进程假设。
4. **前端不依赖 `original_*` 字段**:TerminalTab 只消费 `wsUrl`/`token` props(TerminalTab.tsx:29-41,95),Workspace.tsx 的 status 消费只读 `ws_url/token/status`;`original_*` 的唯一 HTTP 消费者是 remote-agent/websocket_proxy.py:53-66,走 cookie session_token → status 的 proxy-token 早退路径(remote.py:468-492/3853-3860,凭 browser token 本身认证)——该路径保留即不破坏。
5. **共享项目模型**:`projects` 表有 `path`、`is_shared`、`tenant_id`(models/project.py:20-28);共享目录经 `openace-shared` 组 + 2775(utils/workspace.py:367+)。**共享项目路径可以在任意 base_dirs 位置**(含其他用户 home 内),硬 home 锁必须放行"本租户 is_shared 项目的路径"。
6. **远端机器路径的校验语义**:work_dir/project_path 是远端机器路径,后端 base_dirs 对其无意义;合理校验是"绝对路径 + 无 `..` + realpath 黑名单"(即 `is_valid_path` 无前缀模式,fs.py:237-286)。

## 3. 设计

### 3.1 终端会话所有权

**所有权判定助手**(remote.py 模块级,返回 `tuple[Response, int] | None`,None=放行):

```python
def _check_terminal_session_access(terminal_id: str):
```

允许集合(platform admin ∥ 同租户 tenant_admin ∥ 所有者,与 §3.2 VSCode 门闸在 admin/owner 维度对齐;**机器级 admin 分支仅 VSCode 有**——终端 status 直接发放 WS 凭证,凭证面更敏感,刻意不放开机器级 admin,见 §8):

1. `is_platform_admin_role(role)`(strict 口径,覆盖 platform_admin;非 strict 默认下也覆盖 legacy "admin")→ 放行(会话查询之前,无会话行也不受影响);
2. `tenant_admin` 且 `session.tenant_id == user.tenant_id`(会话无租户则放行)→ 放行——**跨租户 tenant_admin 关闭**(现状 `User.is_admin_role` 整块跳过使其能读他租户终端 token,与 VSCode 侧一致收紧);
3. 其余角色:必须 `session.user_id == g.user.id`,否则 403;
4. 会话行缺失:非(1)(2)者 404 fail-closed(§2.1 时序论证;同租户 tenant_admin 遇缺失会话行同样 404,见 §8)。

接线端点:
- `attach_terminal`:机器租户/授权检查块之后(函数级缩进)、生成新 proxy token 之前;
- `get_terminal_status`:**proxy-token 早退路径(3853-3860)原样保留**(内部 WS 代理,返回完整 info 含 original_*);标准路径在机器检查块之后加判定;所有者/管理员响应**剥离 `original_token` 与 `original_ws_url`**;
- `stop_terminal`(3646-3678,评审补充):同一判定——停止他人会话(还会 `complete_session`)属破坏行为,与 VSCode stop 同口径收紧。

非所有者:403;跨租户维持既有 404 惯例(#2538,机器层已拦截非管理员)。WS handler 不改:凭 browser token 占有即认证,token 发放面已被本设计关闭。

### 3.2 VSCode 所有权 = 发起请求者

1. **启动记录**:`remote_vscode_start` 在生成 vscode_id 后调用新模块 `vscode_owner_store.record(vscode_id, machine_id, user_id, tenant_id)`(内存态,TTL 对齐 VSCODE_SESSION_TTL=3600;单 worker 成立,见 §2.3)。`pop` 语义:消费制;机器不匹配时防御性消费并返回 None。
2. **上报消费**:`vscode_status` running 分支的 `owner_user_id` 经助手 `_resolve_vscode_reported_owner(agent_mgr, machine_id, vscode_id) -> int | None` 解析:优先 owner store 记录,无记录(legacy/服务重启后 agent 补报)回退 `machine.created_by` 并 logger.info 注明。
3. **访问门闸** `_check_vscode_session_access(machine_id, vscode_id, info) -> tuple | None`:允许集合与 #2183 代理鉴权(4915-4985)同口径——`is_platform_admin_role` ∥ `owner_user_id == g.user.id` ∥ 同租户 `tenant_admin` ∥ 机器 admin 权限(`get_user_permission == "admin"`);其余 403。接线到 `remote_vscode_status` / `remote_vscode_stop` / `remote_vscode_attach`,插入点为 `if not User.is_admin_role(...)` 机器检查**整块之后、函数级缩进**(4 空格)——不得落回块内 8 空格缩进,否则 tenant_admin 整块跳过含门闸。status 的 `url?token=` 仅对通过门闸者返回。stop/attach 在 store 无记录(vscode_id 未知)时维持现状(无 info 可保护)。
4. **审计**:门闸的跨租户拒绝复用 proxy 先例的 `CROSS_TENANT_VSCODE_ACCESS_ATTEMPT` 审计事件(remote.py:4946-4955 同款);其余拒绝(同租户非所有者)维持 `logger.warning` 结构化日志——admin 域之外的普通用户拒绝不属于 AuditAction 域,与既有 terminal 拒绝口径一致。
5. **代理与 WS 不改**:`remote_vscode_proxy` 的 #2183 鉴权已按 `owner_user_id` 判定——owner 修正后自动收紧;WS 凭证占有模型维持。

### 3.3 远端路径校验(work_dir 与 project_path)

- `start_terminal` 的 `work_dir`(:3362 后)与 `remote_vscode_start` 的 `project_path`(:4699 后)统一:`is_valid_path(value)` 无前缀模式——绝对、原始输入无 `..`、realpath 不落系统黑名单;空值放行(各自默认行为)。非法 → 400 结构化拒绝。
- **不套 base_dirs 前缀**:远端机器路径(§2.6)。
- **影响面必须知晓(评审 N9)**:后端黑名单含 `/opt`、`/var`、`/root`、`/usr` 等,远端机器以 `/opt/xxx`、`/var/www` 作工作目录的请求将被拒绝。取舍依据:黑名单默认覆盖的是"系统敏感目录"这一普适语义,远端工作区应使用用户目录(`/home/...`);该影响与放行面(work_dir 任意 → 受限)一并记录于 PR 描述,如后续有真实需求再评估远端可配置白名单(非目标)。
- 设计 §3.3 的取舍记录:随 #3375 合入后的能力矩阵统一更新(`docs/workspace-isolation-capabilities.md` 目前仅在 PR #3375 分支,不在 main;本 PR 不交叉依赖)。

### 3.4 `is_valid_path` 迁移到共享工具

- 新建 `app/utils/path_guard.py`:迁移 fs.py 的黑名单常量与 `is_valid_path()`(逐字迁移,含注释)。
- fs.py 改为 `from app.utils.path_guard import is_valid_path`(模块级名字保留,`app.routes.workspace.py:39`、orchestrator.py:2955 及既有测试的 `from app.routes.fs import is_valid_path` 不断)。
- 现有 `tests/unit/test_fs_path_validation.py` 必须零改动通过(回归哨兵)。

### 3.5 fs browse/check-path home 子树锁

- `ProjectRepository.get_shared_project_paths(tenant_id) -> list[str]`:active + is_shared 项目路径,realpath 去重;**`tenant_id` 为 None 时返回 `[]`**(无租户上下文只允许 home,不放大到全租户共享)。
- fs.py 助手:

```python
def _allowed_roots_for_user(user) -> list[str]:
    roots = [os.path.realpath(get_home_directory(user))]  # realpath:与已解析的请求路径同域比较
    roots.extend(ProjectRepository().get_shared_project_paths(user.get("tenant_id")))
    return roots

def _is_within_any_root(resolved: str, roots) -> bool:  # == 或 startswith(root + os.sep)
```

(判定对象 `path` 已 `os.path.realpath`(fs.py:618/907),home 根必须同样 realpath,否则符号链接场景失配——评审 B2。)
- `api_browse_directory` / `api_check_path`:在既有 base_dirs+黑名单校验与 realpath 之后追加 allowed-roots 判定;拒绝响应沿用各自现有 400 形状。"home"/空路径与不存在路径回退 home 的分支不变(home 是 allowed root)。
- 实际可读性仍由 OS 权限(run_as_user)决定,应用层锁只做边界声明;写操作锁(#1813)无管理员豁免,读锁同口径不加豁免(对称)。

## 4. 非目标(明确不做)

- WS 层改为用户绑定认证(凭证占有模型保留,泄漏面在发放端关闭);
- agent 侧(远端机器)路径强制与远端白名单配置;
- stores 的 Redis 化/多 Pod 一致性(#1851 既有课题);
- 能力矩阵 `entry_points` 升级与 `POLICY_REVISION` 递增——待本 PR 与 #3375 都合入后统一处理,避免交叉依赖。

## 5. 文件结构

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/utils/path_guard.py` | 新建 | 系统目录黑名单 + `is_valid_path`(自 fs.py 迁移) |
| `app/routes/fs.py` | 修改 | re-export;`_allowed_roots_for_user`/`_is_within_any_root`;browse/check-path 追加判定 |
| `app/modules/workspace/vscode_store.py` | 修改 | 新增 `VSCodeOwnerStore`(record/pop,TTL 清理) |
| `app/repositories/project_repo.py` | 修改 | `ProjectRepository.get_shared_project_paths(tenant_id)` |
| `app/routes/remote.py` | 修改 | work_dir/project_path 校验;`_check_terminal_session_access`(attach/status/stop);status 剥离 original_*;vscode owner 记录/消费;`_check_vscode_session_access`(status/stop/attach) |
| `tests/unit/test_path_guard_3376.py` | 新建 | 迁移行为 + re-export 兼容 |
| `tests/unit/test_terminal_work_dir_validation_3376.py` | 新建 | work_dir 校验分支 |
| `tests/unit/test_terminal_ownership_3376.py` | 新建 | attach/status/stop 所有权、剥离、proxy-token 早退、跨租户 tenant_admin |
| `tests/unit/test_vscode_ownership_3376.py` | 新建 | owner 记录/消费/回退/上报接线;status/stop/attach 门闸(含跨租户 tenant_admin) |
| `tests/unit/test_fs_home_lock_3376.py` | 新建 | browse/check-path home 锁 + 共享放行 + 符号链接 home |

## 6. 测试设计(要点)

全部 unit lane;fs 测试鉴权采用既有先例 `app.before_request_funcs["fs"] = []` + `@app.before_request` 注入 `g.user`(tests/integration/routes/test_fs_file_ops.py:142-155;**不得**改模块级蓝图回调表,会泄漏到其他测试文件);目录一律置于 `Path.home()` 下(macOS `/tmp`→`/private/tmp` 在黑名单,`/tmp` 作 base_dirs 会让旧门先拒,红/绿信号失真);remote 侧 patch `app.routes.remote._set_user_from_token` / `get_remote_agent_manager` / `app.modules.workspace.session_manager.get_session_manager`;vscode 上报接线测试经 `/api/remote/agent/message` + patch `_check_legacy_fallback` 返回 `(True, None)` 绕过 bearer,断言 store 内 `owner_user_id`。

1. **path_guard**:黑名单、`..`、前缀边界、相对路径拒绝;`fs_mod.is_valid_path is guard_mod.is_valid_path`。
2. **work_dir/project_path**:`..`、相对、`/etc/x` → 400 且命令未下发;合法绝对路径与空值透传(两入口都测)。
3. **终端所有权**:owner 200(无 original_*,有 token/ws_url);同租户非所有者 403;**跨租户 tenant_admin 403**;会话缺失非管理员 404;platform admin 放行;同租户 tenant_admin 放行;proxy-token 早退保留完整 info;attach/stop 非所有者 403 且命令未下发。
4. **VSCode**:owner store record/pop/过期/机器不匹配;`_resolve_vscode_reported_owner` 优先请求者、回退 created_by;**上报接线**(agent_message → store.owner_user_id == 请求者);status 非所有者 403 无 url、owner 有 `url?token=`、同租户 tenant_admin 与 platform_admin 放行、**跨租户 tenant_admin 403(status 与 attach 各一条)**;stop/attach 非所有者 403;start 记录请求者。
5. **fs home 锁**:越界(home 与共享根之外)→ 400;home/共享根/共享子目录放行;"home" 缺省与不存在回退分支不变;**符号链接 home**(realpath 对齐)放行;check-path 同口径。
6. **标记**:统一 `pytest.mark.issue(3376)`;行为兼容用例(username/共享/home 缺省/proxy 早退)加 `pytest.mark.regression`。

## 7. 验收对照(issue #3376 清单)

- terminal status/attach 所有权校验 + 不泄漏 token ✓(§3.1)
- VSCode owner=请求者,status/stop/attach 同口径收紧,跨租户审计保留 ✓(§3.2)
- work_dir 服务端校验、结构化拒绝 ✓(§3.3;project_path 同构加固)
- browse/check-path home 锁 + 共享项目放行 ✓(§3.5)
- 测试分层 ✓(§6,unit lane)
- 单用户/管理员/共享场景兼容性影响显式化 ✓(§8)

## 8. 兼容性影响与风险(评审补充,显式清单)

| 变更 | 从 | 到 | 依据/缓解 |
|---|---|---|---|
| 跨租户 tenant_admin:终端 status/attach/stop | 200(整块跳过) | 403 | 与 VSCode 侧 #2183 口径一致;平台管理员不受影响 |
| 同租户 tenant_admin + 会话行缺失:终端各端点 | 200(整块跳过) | 404 fail-closed | §2.1 时序论证(会话行必然比终端条目活得久,缺失即异常态) |
| 跨租户 tenant_admin:VSCode status/stop/attach | 200 | 403 | #2183 同口径;审计事件保留 |
| 跨租户机器级 admin(#2538 机制):VSCode status/stop/attach | 200 | 403 | 门闸的跨租户分支在机器 admin 检查之前;**同租户**机器级 admin 仍经 `get_user_permission=="admin"` 放行,跨租户指派按 #2183 proxy 口径一律 403 |
| 同租户机器级 admin(非所有者):终端 status/attach/stop | 200 | 403 | 终端 status 直接发放 WS 凭证,凭证面更敏感,机器级 admin 不放开(刻意不对称,§3.1);需要访问时由所有者或平台管理员操作 |
| strict 模式 legacy "admin":终端与 VSCode 各端点 | bypass | 需机器指派 admin 或 ownership | `is_platform_admin_role` strict 口径,与 proxy 现状一致;非 strict 默认无影响 |
| 远端 work_dir/project_path 落 `/opt`、`/var`、`/root` 等 | 放行 | 400 | §3.3 影响面;PR 描述明示 |
| 终端 status 响应剥离 original_* | 含 | 不含 | §2.4 唯一消费者走保留的 proxy 早退路径 |
| VSCode owner 由 created_by → 请求者 | — | — | 内存映射丢失时回退 created_by(日志注明),窗口 = TTL 3600s 内服务重启 |
| 共享项目根查询引入每请求 DB 开销 | — | — | 单条索引查询;browse 频度低 |

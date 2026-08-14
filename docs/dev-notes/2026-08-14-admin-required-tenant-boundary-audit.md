# `@admin_required` 租户边界审计（2026-08-14）

配套 PR 修的是**跨租户账号接管**。这份笔记记录顺带做完的全量审计，给后续 PR 留工作清单。

## 洞是什么

`admin_required` 认证 `admin` / `platform_admin` / `tenant_admin` 三种角色并写入
`g.tenant_id`，但**从不把这个租户和被操作资源的租户做比对**。在按 user_id 索引的端点上，
这等于任意租户管理员可以：

- 重置任意租户任意用户（含平台管理员）的密码，并从响应体里直接读到新密码
  （`app/routes/admin.py` 的 `/admin/users/<id>/reset-password`）
- 改密码 / 删用户 / 改配额 / 改用户资料
- `GET /admin/users` 不带 `tenant_id` 时返回**全部租户**的用户表（接管前的侦察步骤）
- 把自己租户内的用户提权成 `platform_admin`，或把用户搬进/搬出租户

`admin_required` 自身零租户校验这一点，在 #2179 把 `tenant_admin` 加进放行名单之后
才变成 P0：在那之前只有平台级角色能进这些端点。

## 本次修法

新增三个复用件（`app/auth/decorators.py`）：

| 名字 | 用途 |
|---|---|
| `resolve_admin_tenant_scope()` | 已认证 admin 的可操作租户；平台管理员 = 全局，租户管理员 = 自己那个，无租户的一律拒 |
| `same_tenant_user_required` | 装饰器，叠在 `admin_required` 下面，按路径里的 user_id 查目标用户并比对 |
| `enforce_requested_tenant_scope()` | list/create 类端点：平台管理员按请求走，租户管理员被收敛到自己租户，点名别人的租户直接 403 |

外加 `app/routes/admin.py` 里的 `reject_privilege_escalation()`，挡住租户管理员发放
`platform_admin` / `admin` 角色。

### 只查租户是不够的：还要查角色

第一版只做了**横向**比对（租户 == 租户），独立评审把它打穿了：

1. `api_create_user` 强制要求 `tenant_id`（缺了直接 400），所以**凡是从 admin API
   建出来的 platform_admin 都必然挂在某个租户下**；
2. schema 只约束 `tenant_admin` 必须有租户
   （`chk_2332_tenant_admin_requires_tenant`），没有任何约束禁止 platform_admin
   带租户；
3. 于是把一个 platform_admin 归到租户 A，租户 A 的管理员就能重置它的密码并从响应里
   读出来——**没有发放任何角色，所以 `reject_privilege_escalation` 完全不触发**。

修法是补一条**纵向**判断：租户级管理员不得操作平台级账号，无论租户是否相同。
判断走 `app/auth/permissions.py::is_platform_level_role`，它**刻意不受 strict mode
影响**——如果用受 flag 影响的 `is_platform_admin_role`，一旦打开 strict mode，
legacy `admin` 行就会突然失去保护，等于「开了更严的开关反而更不安全」。

选装饰器叠加而不是逐个端点手写 if，是因为后者「忘记调用」就是静默失效——
`mapping_rules.py` 那两个端点手写了同样的逻辑（#2180），能用但没有复用。

**行为不变的部分**：平台管理员（含 strict mode 关闭时的 legacy `admin`）跨租户能力
完全保留，只是会写一条 `ADMIN_CROSS_TENANT_ACCESS` 审计日志。

## 全量审计结果

用 AST 枚举（不是正则，正则会漏），`app/routes/` 下共 **95** 个函数挂了
`@admin_required`：其中 **92** 个是路由处理函数，另外 **3** 个是蓝图级
`before_request` 守卫（`feishu_config.py:27`、`model_gateway.py:27`、
`smtp_config.py:29` 的 `check_admin`）——对这三个，「按函数数」低估了覆盖面，
一个钩子守的是整个蓝图的所有路由。

| 文件 | 挂 `@admin_required` 的函数 |
|---|---|
| governance.py | 18 |
| admin.py | 17 |
| compliance.py | 17 |
| mapping_rules.py | 12 |
| sso.py | 9 |
| policy.py | 5 |
| remote.py | 5 |
| analytics.py | 4 |
| ai_agent_settings.py | 3 |
| fetch.py | 2 |
| feishu_config.py | 1（before_request） |
| model_gateway.py | 1（before_request） |
| smtp_config.py | 1（before_request） |

路径里带资源 id 的共 24 个，其中 19 个现在是租户感知的：

- **本次加装饰器的 7 个**：admin.py 的 5 个（改/删/改密/重置密码/改配额）、
  compliance.py:452 用户行为画像、governance.py:240 用户活动
- **本次没动、原本就有自建校验的 2 个用户端点**：mapping_rules.py:84 与 :237
  （#2180 手写的 `_validate_user_in_tenant`，读过，是有效的）
- 其余非用户资源已有校验的：remote.py 的 3 个（`_check_machine_tenant_access`）、
  sso.py 的 7 个（provider 按租户取）

也就是说「9 个用户端点现在都受控」是对的，但其中只有 **7 个**是本次加的装饰器，
另外 2 个是沿用既有的手写校验（重复实现，将来值得合并到同一个装饰器）。

### 剩余 5 个：有资源 id 且完全没有租户处理

这些**不是**账号接管，所以没有塞进本次 PR，但都是真实的跨租户读写面：

| 位置 | 端点 | 风险 |
|---|---|---|
| `app/routes/compliance.py:347` | `GET /reports/<report_id>` | 读到别的租户的合规报告 |
| `app/routes/governance.py:303` | `POST /quota/alerts/<alert_id>/acknowledge` | 消掉别的租户的配额告警 |
| `app/routes/governance.py:513` | `PUT /filter-rules/<rule_id>` | 改别的租户的内容过滤规则 |
| `app/routes/governance.py:552` | `DELETE /filter-rules/<rule_id>` | 删别的租户的内容过滤规则 |
| `app/routes/policy.py:169` | `PUT /policy/rules/<rule_id>/enabled` | 开关别的租户的策略规则 |

后两类（filter-rules / policy rules）值得优先，因为它们是**关掉别人的管控**，
不是读数据。修法和本次一样：加一个按资源 id 查出 owner tenant 再比对的守卫；
这几个资源不是 user，所以需要各自的 repo 查询，不能直接复用
`same_tenant_user_required`。

### 潜伏陷阱：`require_tenant_scope()` 把 tenant_admin 当全局管理员

`app/models/user.py:24` 的 `ADMIN_ROLES` 里**包含 `tenant_admin`**，所以
`resolve_tenant_scope()`（decorators.py:851）对租户管理员返回 `is_admin=True`，
`require_tenant_scope()` 随之返回 `tenant_id=None`（全局作用域）。

**目前不构成泄露**：三个调用点（`roi.py:129`、`usage.py:35`、`projects.py:98`）
全都写成 `_, error = require_tenant_scope()`，**丢掉了返回的 tenant_id**，只把它当
「无租户的普通用户一律拒」的闸门用；真正的查询作用域另外从 `g.user.tenant_id` /
`get_current_tenant_id()` 取，那里租户管理员拿到的是自己的租户。

但这是个上了膛的枪：三处的 docstring 都写着「Admins keep global scope」，作者指的是
平台管理员，而 `is_admin_role` 把租户管理员也算了进去。**下一个照着签名用返回值的调用者
就会给租户管理员全局作用域。** 要么收窄 `resolve_tenant_scope` 的 admin 判断，要么把
返回值改成不可忽略的形状。

### 剩余 68 个不带资源 id 的端点

多数是 list / 统计 / 配置类。它们的租户边界取决于底层 repo 有没有按 `g.tenant_id`
过滤，这份审计没有逐个核实 repo——那是 #2429 数据层收敛的范围，而且报告已经点明
根因：**26 个 repository 里 11 个完全没有 tenant_id 概念，隔离靠「记得调对装饰器」，
不是靠架构**。逐端点补装饰器只能压住症状。

## 该往哪走

`platform_admin_required` / `same_tenant_or_platform_admin` 这两个装饰器早就存在
（#2179/#2332），迁移完成度约 15%。`admin_required` 自己的 docstring 已经标了
DEPRECATED，每次调用还打一条 deprecation 日志。真正的收口是把 92 个使用点迁走，
但 `same_tenant_or_platform_admin` 依赖 `_extract_target_tenant_id()` 从请求里取
`tenant_id`——按 user_id 索引的端点请求里根本没有 tenant_id，直接换会全部 400
（fail-closed，安全但功能坏）。所以本次走的是「查资源再比对」的路子，
迁移那条路要先给 `same_tenant_or_platform_admin` 加一个从资源反查租户的钩子。

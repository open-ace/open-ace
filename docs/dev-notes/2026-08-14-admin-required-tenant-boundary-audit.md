# `@admin_required` 租户边界审计（2026-08-14）

配套 PR 修的是**跨租户账号接管**。这份笔记记录顺带做完的全量审计，给后续 PR 留工作清单。

> **2026-08-15 更新**：下面「剩余 6 个」以及从请求里取租户的那批，已由 follow-up
> PR（`fix/p0-remaining-tenant-scoped-endpoints`）全部修完。落地时又发现两处**同类
> 但两轮枚举都漏掉的洞**（见 [§ follow-up 落地](#follow-up-落地2026-08-15)），一并
> 收进同一 PR 以免修一半。

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

路径里带资源 id 的共 **29** 个，其中 23 个现在是租户感知的：

- **本次加装饰器的 7 个**：admin.py 的 5 个（改/删/改密/重置密码/改配额）、
  compliance.py:452 用户行为画像、governance.py:240 用户活动
- **本次没动、原本就有自建校验的 2 个用户端点**：mapping_rules.py:84 与 :237
  （#2180 手写的 `_validate_user_in_tenant`，读过，是有效的）
- 其余非用户资源已有校验的：remote.py 的 3 个（`_check_machine_tenant_access`）、
  sso.py 的 7 个（provider 按租户取）、mapping_rules.py 另外 4 个
  （`update_rule` / `delete_rule` / `suggest_mapping` / `manual_map_account`，
  同样是 #2180 手写的）

也就是说「9 个用户端点现在都受控」是对的，但其中只有 **7 个**是本次加的装饰器，
另外 2 个是沿用既有的手写校验（重复实现，将来值得合并到同一个装饰器）。

### 剩余 6 个：有资源 id 且完全没有租户处理（✅ follow-up PR 已修）

带路径参数的 `@admin_required` 路由共 **29** 个（第一版这里写 24 是错的：用正则枚举
时只匹配了固定的几个 id 名，漏掉了 `<int:id>` / `<sender_name>` / `<rule_key>` 这些
写法。AST 枚举才是准的）。29 个里 23 个已有校验，剩下这 6 个完全没有：

| 位置 | 端点 | 风险 |
|---|---|---|
| `app/routes/policy.py:149` | `PUT /policy/rules/<rule_key>` | **改掉别的租户的策略规则**（整版本 supersede） |
| `app/routes/policy.py:171` | `PATCH /policy/rules/<rule_id>/enabled` | 开关别的租户的策略规则 |
| `app/routes/governance.py:515` | `PUT /filter-rules/<rule_id>` | 改别的租户的内容过滤规则 |
| `app/routes/governance.py:554` | `DELETE /filter-rules/<rule_id>` | 删别的租户的内容过滤规则 |
| `app/routes/governance.py:305` | `POST /quota/alerts/<alert_id>/acknowledge` | 消掉别的租户的配额告警 |
| `app/routes/compliance.py:349` | `GET /reports/<report_id>` | 读到别的租户的合规报告（列表侧 `GET /reports/saved` 本次已收紧，单条读还要按 id 反查 owner，留给 follow-up） |

**`policy.py:149` 是这批里最严重的**，也是第一版漏掉的：它不是开关，而是
`PolicyRepository().create_rule(**fields)` 整条规则版本替换——外人可以把别的租户
当前生效的策略直接顶掉。它比同文件里那个已经列出的 toggle 危险得多。

修法不能直接复用 `same_tenant_user_required`（资源不是 user），要各自按 repo 反查
owner tenant。分两类：

- **`policy_rules` 与 `compliance_reports` 有 `tenant_id` 列** → 直接比对
- **`quota_alerts` 没有 tenant 列，但有 `user_id NOT NULL`** → 经 user 反查租户
- **`content_filter_rules` 一列 tenant 都没有**，repo 层 `get_filter_rule(rule_id)`
  也不收租户参数——这张表在设计上就是全局的。所以那两个端点不该「补租户比对」，
  应当收成 `platform_admin_required`：租户管理员改一条全局过滤规则，影响的是所有
  租户，本来就不该是租户级权限。

全部无需迁移。

### 不带资源 id 但同样泄露：从请求里取租户的端点

第一版只收紧了 `GET /admin/users`，漏了同类的一批。**教训**：按「路径里有没有资源
id」来枚举审计面是错的方法——list / 聚合 / 触发类端点从 query 或 body 取租户，
同样是跨租户面，而且往往泄露得更多。

改用「**谁从请求里读 tenant_id 却没过 `enforce_requested_tenant_scope`**」重新扫
（AST 枚举全部 route handler + 正则匹配 `data.get("tenant_id")` /
`request.args.get("tenant_id")`），本次一并收紧的：

| 端点 | 原问题 |
|---|---|
| `GET /admin/quota/usage` | `get_all_users()` 无过滤；返回的比 `/admin/users` **还多**（`SELECT *` 去密码哈希 + 用量），含 `role`——正好是「先找出谁是平台管理员」这一步要的数据 |
| `GET /admin/quota/stats` | 同上；且把所有租户的配额加总去和单租户上限比，数字本身就是错的 |
| `POST /admin/quota/health-check` | body 的 `tenant_id` 直接采信，可读任意租户的配额余量 |
| `POST /admin/feishu/sync`、`POST /admin/dingtalk/sync` | body 的 `tenant_id` 直接采信——**这是跨租户写**：同步会在目标租户里建/改用户和团队 |
| `GET /reports/saved` | query 的 `tenant_id` 直接透传给仓储；不传则返回所有租户的合规报告 |

扫描命中但**核实后是安全的**（各自有自建校验，未改动）：
`compliance.py:138 generate_report`（`resolve_tenant_scope` + 平台管理员显式门）、
`sso.py:511/1205`（`validate_tenant_access`）、
`remote.py:867/956`（#2180 的 fail-closed 处理）。

### 已知但本次未动：非角色的跨租户路径

`app/routes/remote.py:1043` 的 `assign_user` 不校验被指派的 `user_id` 是否属于该机器
的租户。平台管理员可以把租户 A 的普通用户设为租户 B 机器的管理员；那个账号
`role='user'`，所以 `is_platform_level_role` 保护不到它，租户 A 的管理员接管它之后就
继承了伸进租户 B 的机器管理权。先于本 PR 存在，范围外，但它是「除了角色以外还有没有
别的跨租户途径」这个问题的真实答案，记在这里。

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

`app/routes/analysis.py:36` 另有一份同模式的复制品，那一份**确实**把返回值当作用域用，
所以租户管理员在那里拿到的是全局作用域。先于本 PR 存在，未在本次改动范围内，一并记下。

### 副作用：跨租户读也会写审计

平台管理员的 `tenant_id` 是 NULL，所以任何带租户的目标都算跨租户，
`same_tenant_user_required` 会为它写一条 `ADMIN_CROSS_TENANT_ACCESS`。
`GET /audit/user/<id>/profile` 与 `GET /audit/user/<id>/activity` 是仪表盘轮询的
GET，于是每刷一次就多一行审计。这是刻意保留的——「谁看了别的租户的审计画像」正是
合规产品该留痕的事——但如果前端轮询频率高，值得改成按会话去重，而不是删掉。

### 剩余 68 个不带资源 id 的端点

多数是 list / 统计 / 配置类。它们的租户边界取决于底层 repo 有没有按 `g.tenant_id`
过滤，这份审计没有逐个核实 repo——那是 #2429 数据层收敛的范围，而且报告已经点明
根因：**26 个 repository 里 11 个完全没有 tenant_id 概念，隔离靠「记得调对装饰器」，
不是靠架构**。逐端点补装饰器只能压住症状。

## follow-up 落地（2026-08-15）

follow-up PR 收口了「剩余 6 个」和「从请求里取租户」两批，共触及三个蓝图。

### 新复用件

`app/auth/decorators.py::enforce_resource_tenant_scope(resource_tenant_id)`——
按**资源自己的租户**（先从仓储反查）判断当前 admin 能不能动它。与
`enforce_requested_tenant_scope` 的关键区别：那里 `None` 表示「没点名租户」会收敛到
调用者自己的租户；这里 `None` 表示**资源本身是全局的**，租户管理员必须**拒**而不是
被悄悄改写租户——否则租户管理员就能改一条治所有租户的规则。等价于
`enforce_target_user_tenant` 的租户那半段（NULL→拒），但没有纵向角色判断（这些资源不
带角色，接管它不继承任何账号权限）。平台管理员放行并写跨租户审计。

### 按资源反查 owner 再比对

| 端点 | 反查路径 | 说明 |
|---|---|---|
| `policy.py` `PATCH /policy/rules/<rule_id>/enabled` | `get_rule(id).tenant_id` | 全局规则(None)对租户管理员=拒；查不到=拒(fail closed，无存在性 oracle) |
| `policy.py` `PUT /policy/rules/<rule_key>` | `get_current_rule_by_key(key).tenant_id` | supersede 的 UPDATE 只按 rule_key，会顶掉当前 owner，故先校验既有版本；**新版本**的 scope 再走 `enforce_requested_tenant_scope`，租户管理员不能建全局/别租户规则 |
| `policy.py` `POST /policy/rules` | `get_current_rule_by_key(key)`（key 已存在时） | 两轮枚举都漏了：body 的 `tenant_id` 走 `_parse_rule_body` 间接读，正则没跟进去。**独立评审又抓到一层**：POST 和 PUT 一样落到按 rule_key 的 supersede，POST 一个**已存在**的 key 照样顶掉 owner——只收敛新版本 scope 不够。故与 PUT 共用 `_scope_policy_rule_write`（既有 owner 校验 + 新版本 scope 一处写），杜绝二者再次漂移 |
| `governance.py` `POST /quota/alerts/<id>/acknowledge` | `get_alert(id).user_id` → `user.tenant_id` | `quota_alerts` 无租户列但 `user_id NOT NULL`；alert/user 任一查不到=拒 |
| `compliance.py` `GET /reports/<report_id>` | `get_saved_report(id).metadata.tenant_id` | 单条读；列表侧 `/reports/saved` 上个 PR 已收紧 |

新增仓储方法：`PolicyRepository.get_current_rule_by_key`、`QuotaManager.get_alert`。

### 全局表 → 收成 `platform_admin_required`

`content_filter_rules` 一列租户都没有，`add_custom_pattern`/`add_custom_keyword` 也不带
租户参数——整个内容过滤特性在设计上就是全局的。改一条会影响所有租户，本就不该是租户级
权限。故把它的**全部 5 个写端点**收成平台管理员专属（`admin_required` →
`platform_admin_required`）：`POST /content/filter/patterns`、
`POST /content/filter/keywords`、`POST /filter-rules`、`PUT /filter-rules/<id>`、
`DELETE /filter-rules/<id>`。读端点（list/stats/check）不动。

其中 `POST /content/filter/patterns`、`/keywords`、`POST /filter-rules` 三个是**第二处
两轮都漏的洞**：它们既没有路径资源 id，又不从请求读 `tenant_id`，所以「按资源 id 枚举」
和「按 `data.get('tenant_id')` 枚举」都扫不到。只锁 PUT/DELETE 而留着 create 是不自洽的
（建一条 `effect=deny` 的全局规则比改一条更危险），故一并收口。

**独立评审又补一处**：`PUT /security-settings`（`governance.py`）也是同一类——
`security_settings` 无租户列（全局的 2FA 开关 / 密码策略 / 登录尝试上限 / IP 白名单 /
审计阈值），租户管理员能改一条就动了**所有租户**的安全基线，比内容过滤影响更大。同样收成
`platform_admin_required`（`GET` 不动）。这三处（policy POST、content-filter create/pattern/
keyword、security-settings PUT）都属「全局配置写但既无资源 id 又不读 tenant_id」，是两轴枚
举的共同盲区，教训记这里：**审「全局配置写端点」要单列一轴，不能靠资源 id / 请求租户去扫**。

### 一致性收敛

`compliance.py::generate_report` 的租户管理员分支原来点名别的租户时只记日志、仍返回**自己
租户**的报告（不是泄露，但和刚收紧的 list/单读兄弟端点行为不一致）。改成点名别人=403，
整个 compliance 面统一。

### 验证

新增 `tests/integration/test_admin_cross_tenant_followup.py`（57 项），覆盖每个端点的
「租户管理员拒 / 平台管理员放行 / fail-closed / 无 oracle」四类，平台管理员跨租户放行处
断言写了审计。9 处守卫逐一 source-mutation：破一个必挂一条具名测试（全部 CAUGHT）。

独立对抗评审跑了两轮，抓到 4 个真问题并已修：

- **R1**：`POST /policy/rules` 的 supersede 顶 owner（HIGH）、`PUT /security-settings` 漏收
  （同全局配置类）、`generate_report` 把 body 的字符串 `"1"` 当成 `!= int 1` 误拒本租户
  （LOW，改用 `enforce_requested_tenant_scope` 归一化）。
- **R2**：修 R1 后 `PUT /policy/rules/<key>` 仍可用**首/尾空格的 key** 绕过 owner 校验——
  owner 查 raw key 落空跳过，supersede 却按 `_parse_rule_body` strip 后的 key 顶掉受害规则
  （HIGH）。修法：`_scope_policy_rule_write` 里按同一规则 strip 后再查。**教训：校验用的 key
  必须和落库/supersede 用的 key 走同一归一化**——本轮两个 HIGH 都是「守卫的 key ≠ 实际写的
  key」这同一个根因的不同变体。

### 仍未动（范围外，记账）

- `remote.py:1043 assign_user`——非角色的跨租户路径（把 A 租户的普通用户设成 B 租户机器
  管理员）。`role='user'` 所以 `is_platform_level_role` 保护不到，先于本系列存在。
- **list/聚合端点靠仓储过滤**的那批（`GET /quota/alerts`、`GET /quota/status/all` 等）：
  底层 repo 没按 `g.tenant_id` 过滤，属 #2429 数据层收敛，不是逐端点补装饰器能治的。ack
  端点已按资源收口，但同特性的 list 侧读泄露仍在该边界内。
- **全局配置写但既无资源 id 又不读 tenant_id 的一批**（R2 评审点名，归 #2429 的专项 sweep）：
  `ai_agent_settings.py:44 PUT /ai-agent/settings`（`ai_agent_settings` 无租户列，与
  `security_settings` **完全同形**，只是不在本 PR 改的三个文件里）、`smtp_config.py` /
  `feishu_config.py` / `model_gateway.py` 的 `@admin_required` before_request 守卫（全局
  smtp / 飞书·钉钉 / model-gateway 配置写）、`notification_integrations.py:73,104` 的
  webhook / 钉钉配置写。全部先于本系列存在。本 PR 只收口它**已经在动**的 governance.py
  （内容过滤 + security-settings）；跨文件的「全局配置写」一轴留给 #2429 一次扫清，别在本
  PR 里零敲碎打（改一个就得改这一整批，等于把 #2429 提前拆进来）。

## 该往哪走

`platform_admin_required` / `same_tenant_or_platform_admin` 这两个装饰器早就存在
（#2179/#2332），迁移完成度约 15%。`admin_required` 自己的 docstring 已经标了
DEPRECATED，每次调用还打一条 deprecation 日志。真正的收口是把 92 个使用点迁走，
但 `same_tenant_or_platform_admin` 依赖 `_extract_target_tenant_id()` 从请求里取
`tenant_id`——按 user_id 索引的端点请求里根本没有 tenant_id，直接换会全部 400
（fail-closed，安全但功能坏）。所以本次走的是「查资源再比对」的路子，
迁移那条路要先给 `same_tenant_or_platform_admin` 加一个从资源反查租户的钩子。

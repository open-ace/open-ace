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

选装饰器叠加而不是逐个端点手写 if，是因为后者「忘记调用」就是静默失效——
`mapping_rules.py` 那两个端点手写了同样的逻辑（#2180），能用但没有复用。

**行为不变的部分**：平台管理员（含 strict mode 关闭时的 legacy `admin`）跨租户能力
完全保留，只是会写一条 `ADMIN_CROSS_TENANT_ACCESS` 审计日志。

## 全量审计结果

`app/routes/` 下共 **92** 个 `@admin_required` 端点：

| 文件 | 端点数 |
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

路径里带资源 id 的共 24 个，其中 19 个现在是租户感知的：

- **本次修掉的 9 个用户端点**：admin.py 的 5 个（改/删/改密/重置密码/改配额）、
  compliance.py:452 用户行为画像、governance.py:240 用户活动
- 已有自建校验的：mapping_rules.py 的 2 个（#2180 手写）、remote.py 的 3 个
  （`_check_machine_tenant_access`）、sso.py 的 7 个（provider 按租户取）

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

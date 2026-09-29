# 权限模型

## 概述

Open ACE 使用基于角色的访问控制（RBAC），包含 4 个内置角色、19 个权限和 3 个认证装饰器。

## 角色

| 角色 | 权限数 | 说明 |
|------|--------|------|
| **admin** | 全部 19 个 | 完整的系统管理员 |
| **manager** | 11 个 | 团队管理者，拥有查看和导出权限 |
| **user** | 4 个 | 普通用户，拥有基本查看权限 |
| **readonly** | 1 个 | 仅仪表盘查看 |

## 权限矩阵

| 权限 | admin | manager | user | readonly |
|------|-------|---------|------|----------|
| view_dashboard | ✓ | ✓ | ✓ | ✓ |
| view_messages | ✓ | ✓ | ✓ | |
| export_messages | ✓ | ✓ | | |
| view_analysis | ✓ | ✓ | ✓ | |
| run_analysis | ✓ | ✓ | | |
| export_analysis | ✓ | ✓ | | |
| view_users | ✓ | ✓ | | |
| create_user | ✓ | | | |
| edit_user | ✓ | | | |
| delete_user | ✓ | | | |
| manage_permissions | ✓ | | | |
| view_quota | ✓ | ✓ | ✓ | |
| manage_quota | ✓ | | | |
| view_audit_logs | ✓ | ✓ | | |
| export_audit_logs | ✓ | ✓ | | |
| view_content_filter | ✓ | ✓ | | |
| manage_content_filter | ✓ | | | |
| admin_access | ✓ | | | |
| system_config | ✓ | | | |

`admin_access` 权限作为超级用户绕过——持有者自动通过所有权限检查。

## 认证

### Token 提取

Token 按以下优先级提取：

1. **Cookie** — `session_token`（HttpOnly, SameSite=Lax）
2. **Authorization 头** — `Bearer <token>`
3. **查询参数** — `?token=<token>`

### 登录流程

1. 客户端发送 `POST /api/auth/login`，包含 `{username, password}`
2. 服务器验证凭证（bcrypt，12 轮）
3. 创建带过期时间的会话记录
4. 设置 `session_token` Cookie（HttpOnly，HTTPS 时为 Secure）
5. 返回用户资料和角色

### 速率限制

失败的登录尝试在 `login_attempts` 表中追踪。安全设置缓存 60 秒。

## 认证装饰器

### `@auth_required`

要求有效认证。可选 `ownership` 参数：

- `ownership='session'` — 验证 `user_id` 与会话所有者匹配
- `ownership='machine'` — 验证机器管理员权限

Admin 角色绕过所有所有权检查。

```python
@auth_required
def api_view():
    user = g.user  # 认证后可用

@auth_required(ownership='session')
def session_view(session_id):
    # 仅会话所有者或管理员可以访问
```

### `@admin_required`

要求 admin 角色。非管理员用户返回 403。

```python
@admin_required
def admin_only_view():
    pass
```

### `@public_endpoint`

将端点标记为有意公开。安全扫描器用它区分有意公开和意外未保护的端点。

```python
@public_endpoint
def health_check():
    pass
```

## 路由保护

### 管理模式 (`/manage/*`)

所有 `/manage/*` 路由需要 admin 角色。普通用户和机器管理员无法访问管理页面。

### API 路由

大多数 `/api/*` 路由通过 blueprint 级别的 `before_request` 使用 `@auth_required`。敏感操作使用 `@admin_required`。

如果用户被标记为 `must_change_password=true`，服务端会额外收紧访问范围，只允许认证检查、个人资料、密码修改、登出和密码策略等最小必要接口。

### 公开路由

- `/` — SPA 全局捕获（提供 index.html）
- `/api/auth/login` — 登录端点
- `/api/auth/check` — 认证状态检查
- `/health` — 健康检查

## 自定义权限

除了内置角色外，可以按用户授予自定义权限：

```python
# 授予用户特定权限
PermissionService.grant_permission(user_id, 'export_analysis', granted_by=admin_id)

# 检查权限
has_perm = PermissionService.has_permission(user_id, 'export_analysis')
```

自定义权限存储在 `user_permissions` 表中，与用户的角色权限合并计算。

## 多租户隔离

启用多租户模式时：
- 用户通过 `tenant_id` 关联到租户
- 租户配额强制执行每个租户的 token 和请求限制
- `QuotaEnforcementScheduler` 每 60 秒运行一次，检查并执行限制
- 超额用户会被终止会话并生成告警

当前边界：
- 用户、项目、工作区会话、会话消息、每日用量聚合、审计日志、远程机器、机器权限和配额均具备租户感知。
- 非管理员用户可见 API 会把认证租户范围应用到会话、项目、用量和审计查询；工作区会话变更也会在写入边界携带会话租户。
- 系统管理员有意保留全局运维可见性，用于支持和故障处理。

## API Key 管理租户授权（Issue #2327）

### 核心原则

API Key 管理接口的 `tenant_id` 参数是**目标选择**而非**授权凭据**。请求参数只能表达平台管理员的明确目标，不能扩大租户管理员的权限。

### 授权模型

#### tenant_admin

- **目标租户**：必须始终来自认证上下文（`g.tenant_id`）
- **不提供 tenant_id**：使用 actor tenant（向后兼容）
- **提供不同 tenant_id**：返回 403 Forbidden
- **跨租户操作**：严格禁止

**示例**：
```python
# tenant_admin (tenant_id=1) 的请求
GET /api/api-keys                    # 成功，返回 tenant 1 的 API Key
GET /api/api-keys?tenant_id=1       # 成功，返回 tenant 1 的 API Key
GET /api/api-keys?tenant_id=2       # 失败，返回 403（跨租户访问拒绝）
POST /api/api-keys {"tenant_id": 2} # 失败，返回 403（跨租户访问拒绝）
```

#### platform_admin / legacy admin

- **目标租户**：必须显式指定 `tenant_id`（fail-closed）
- **不提供 tenant_id**：返回 400 Bad Request（不返回全局列表）
- **跨租户操作**：允许，但产生审计记录
- **权限范围**：可管理所有租户

**示例**：
```python
# platform_admin 的请求
GET /api/api-keys                    # 失败，返回 400（缺少 tenant_id）
GET /api/api-keys?tenant_id=1       # 成功，返回 tenant 1 的 API Key
GET /api/api-keys?tenant_id=2       # 成功，返回 tenant 2 的 API Key（审计记录）
POST /api/api-keys {"tenant_id": 1} # 成功，在 tenant 1 创建 API Key
```

### API Key 所有权验证

对于 `PUT /api/api-keys/<key_id>` 和 `DELETE /api/api-keys/<key_id>`：

- Repository 层强制验证 `key_id` 属于目标租户
- 如果 API Key 不存在或不属于目标租户：返回 403 Forbidden
- 不返回 404 以避免信息泄露（避免攻击者探测 key_id 是否存在）

### 错误响应

| 状态码 | 错误消息 | 说明 |
|--------|---------|------|
| 400 | Target tenant_id is required | platform_admin 缺少 tenant_id |
| 400 | Invalid tenant_id | tenant_id 为负数、0 或格式错误 |
| 403 | Cross-tenant access denied | tenant_admin 跨租户访问 |
| 403 | Tenant admin must have tenant_id | tenant_admin 没有关联租户 |
| 403 | API key not found or access denied | API Key 不存在或不属于目标租户 |

### 审计记录

平台管理员跨租户操作必须产生审计记录，包含以下字段：

- `actor_user_id`: 操作者用户 ID
- `actor_tenant_id`: 操作者所属租户
- `target_tenant_id`: 目标租户
- `action`: 操作类型（API_KEY_CREATE, API_KEY_READ, API_KEY_UPDATE, API_KEY_DELETE）
- `api_key_id`: API Key ID（适用时）
- `api_key_name`: API Key 名称
- `result`: 操作结果（success, denied, not_found）
- `request_id`: 请求追踪 ID

### 实现架构

#### 集中式授权原语

使用 `resolve_authorized_target_tenant(actor, requested_tenant_id)` 统一处理：

```python
# tenant_admin: 强制租户边界
actor = {"id": 1, "role": "tenant_admin", "tenant_id": 1}
target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=2)
# 返回: (None, "Cross-tenant access denied")

# platform_admin: 必须显式指定
actor = {"id": 1, "role": "platform_admin", "tenant_id": None}
target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
# 返回: (1, None)
```

#### ActorScope 授权上下文

Service/Repository 层使用不可变的 `ActorScope` 对象：

```python
@dataclass(frozen=True)
class ActorScope:
    user_id: int
    role: str
    actor_tenant_id: int | None
    target_tenant_id: int
    is_cross_tenant: bool
    request_id: str | None
```

#### 路由层装饰器

使用 `@api_key_admin_required` 装饰器统一处理：

```python
@api_keys_bp.route("/api-keys", methods=["GET"])
@api_key_admin_required
def list_api_keys():
    scope = g.actor_scope  # 已验证的授权上下文
    keys = api_proxy.list_api_keys(scope.target_tenant_id)
    return jsonify({"success": True, "keys": keys})
```

#### Service 层防线

Service 层方法必须接收已验证的 `ActorScope`：

```python
@require_actor_scope()
def store_api_key(self, scope: ActorScope, ...):
    # 自动验证 scope 包含有效的 user_id, role, target_tenant_id
    ...
```

### Fail-Closed 原则

**不存在静默默认行为**：

- 不回退到 tenant 1 或其他默认租户
- 不忽略无效 tenant_id 继续执行
- 不返回全局列表或全局权限
- 所有异常情况必须明确报错

### 向后兼容性

- `tenant_admin` 不提供 `tenant_id` 时：使用 actor tenant（向后兼容）
- 错误响应格式符合 API 规范（包含 `error` 字段）
- 现有的租户隔离测试继续通过

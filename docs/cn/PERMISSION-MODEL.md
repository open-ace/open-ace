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

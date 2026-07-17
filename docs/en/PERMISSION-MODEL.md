# Permission Model

## Overview

Open ACE uses role-based access control (RBAC) with 4 built-in roles, 19 permissions, and 3 authentication decorators.

## Roles

| Role | Permissions | Description |
|------|-------------|-------------|
| **admin** | All 19 | Full system administrator |
| **manager** | 11 | Team manager with view and export |
| **user** | 4 | Regular user with basic view |
| **readonly** | 1 | Dashboard view only |

## Permission Matrix

| Permission | admin | manager | user | readonly |
|------------|-------|---------|------|----------|
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

The `admin_access` permission acts as a superuser bypass — holders automatically pass all permission checks.

## Authentication

### Token Extraction

Tokens are extracted in priority order:

1. **Cookie** — `session_token` (HttpOnly, SameSite=Lax)
2. **Authorization header** — `Bearer <token>`
3. **Query parameter** — `?token=<token>`

### Login Flow

1. Client sends `POST /api/auth/login` with `{username, password}`
2. Server validates credentials (bcrypt with 12 rounds)
3. Creates session record with expiry
4. Sets `session_token` cookie (HttpOnly, Secure over HTTPS)
5. Returns user profile with role

### Rate Limiting

Failed login attempts are tracked in the `login_attempts` table. Security settings are cached for 60 seconds.

## Auth Decorators

### `@auth_required`

Requires valid authentication. Optional `ownership` parameter:

- `ownership='session'` — Verifies `user_id` matches the session's owner
- `ownership='machine'` — Verifies machine admin permission

Admin role bypasses all ownership checks.

```python
@auth_required
def api_view():
    user = g.user  # Available after auth

@auth_required(ownership='session')
def session_view(session_id):
    # Only session owner or admin can access
```

### `@admin_required`

Requires admin role. Returns 403 for non-admin users.

```python
@admin_required
def admin_only_view():
    pass
```

### `@public_endpoint`

Marks endpoints as intentionally unauthenticated. Used by the security scanner to distinguish between intentionally public and accidentally unprotected endpoints.

```python
@public_endpoint
def health_check():
    pass
```

## Route Protection

### Manage Mode (`/manage/*`)

All `/manage/*` routes require admin role. Regular users and machine admins cannot access management pages.

### API Routes

Most `/api/*` routes use `@auth_required` via `before_request` at the blueprint level. Sensitive operations use `@admin_required`.

If a user is marked with `must_change_password=true`, the server further narrows access and only allows the minimum required auth, profile, password-change, logout, and password-policy endpoints.

### Public Routes

- `/` — SPA catch-all (serves index.html)
- `/api/auth/login` — Login endpoint
- `/api/auth/check` — Auth status check
- `/health` — Health check

## Custom Permissions

Beyond the built-in roles, custom permissions can be granted per-user:

```python
# Grant a specific permission to a user
PermissionService.grant_permission(user_id, 'export_analysis', granted_by=admin_id)

# Check permission
has_perm = PermissionService.has_permission(user_id, 'export_analysis')
```

Custom permissions are stored in the `user_permissions` table and combined with the user's role permissions.

## Multi-Tenant Isolation

When multi-tenant mode is enabled:
- Users are associated with a tenant via `tenant_id`
- Tenant quotas enforce per-tenant token and request limits
- `QuotaEnforcementScheduler` runs every 60s to check and enforce limits
- Exceeded users get sessions terminated and alerts generated

Current boundary:
- Remote machines, remote sessions, machine permissions, user identity, and quotas are tenant-aware.
- System administrators intentionally retain global operational visibility for support and incident response.
- Some historical analytics and project tables derive tenant context indirectly or do not yet carry an explicit `tenant_id`; tightening those schema/query boundaries is tracked in [#1781](https://github.com/open-ace/open-ace/issues/1781).

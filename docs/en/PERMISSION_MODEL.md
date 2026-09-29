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
- Users, projects, workspace sessions, session messages, daily usage aggregates, audit logs, remote machines, machine permissions, and quotas are tenant-aware.
- Non-admin user-facing APIs apply the authenticated tenant scope to session, project, usage, and audit queries. Workspace session mutations also include the session tenant in the write boundary.
- System administrators intentionally retain global operational visibility for support and incident response.

## API Key Management Tenant Authorization (Issue #2327)

### Core Principle

The `tenant_id` parameter in API Key management endpoints is a **target selector**, not an **authorization credential**. Request parameters can only express the platform admin's explicit target, not expand tenant admin's permissions.

### Authorization Model

#### tenant_admin

- **Target Tenant**: Must always come from authentication context (`g.tenant_id`)
- **No tenant_id provided**: Uses actor tenant (backward compatible)
- **Different tenant_id provided**: Returns 403 Forbidden
- **Cross-tenant operations**: Strictly prohibited

**Examples**:
```python
# tenant_admin (tenant_id=1) requests
GET /api/api-keys                    # Success, returns tenant 1's API keys
GET /api/api-keys?tenant_id=1       # Success, returns tenant 1's API keys
GET /api/api-keys?tenant_id=2       # Fail, returns 403 (cross-tenant access denied)
POST /api/api-keys {"tenant_id": 2} # Fail, returns 403 (cross-tenant access denied)
```

#### platform_admin / legacy admin

- **Target Tenant**: Must explicitly specify `tenant_id` (fail-closed)
- **No tenant_id provided**: Returns 400 Bad Request (no global list)
- **Cross-tenant operations**: Allowed, with audit logging
- **Permission Scope**: Can manage all tenants

**Examples**:
```python
# platform_admin requests
GET /api/api-keys                    # Fail, returns 400 (missing tenant_id)
GET /api/api-keys?tenant_id=1       # Success, returns tenant 1's API keys
GET /api/api-keys?tenant_id=2       # Success, returns tenant 2's API keys (audit logged)
POST /api/api-keys {"tenant_id": 1} # Success, creates API key in tenant 1
```

### API Key Ownership Verification

For `PUT /api/api-keys/<key_id>` and `DELETE /api/api-keys/<key_id>`:

- Repository layer enforces `key_id` belongs to target tenant
- If API Key doesn't exist or doesn't belong to target tenant: Returns 403 Forbidden
- Doesn't return 404 to avoid information leakage (prevents attackers from probing key_id existence)

### Error Responses

| Status Code | Error Message | Description |
|-------------|---------------|-------------|
| 400 | Target tenant_id is required | platform_admin missing tenant_id |
| 400 | Invalid tenant_id | tenant_id is negative, zero, or malformed |
| 403 | Cross-tenant access denied | tenant_admin cross-tenant access |
| 403 | Tenant admin must have tenant_id | tenant_admin has no associated tenant |
| 403 | API key not found or access denied | API Key doesn't exist or doesn't belong to target tenant |

### Audit Logging

Platform admin cross-tenant operations must generate audit records containing:

- `actor_user_id`: Operator user ID
- `actor_tenant_id`: Operator's tenant
- `target_tenant_id`: Target tenant
- `action`: Operation type (API_KEY_CREATE, API_KEY_READ, API_KEY_UPDATE, API_KEY_DELETE)
- `api_key_id`: API Key ID (when applicable)
- `api_key_name`: API Key name
- `result`: Operation result (success, denied, not_found)
- `request_id`: Request tracing ID

### Implementation Architecture

#### Centralized Authorization Primitive

Uses `resolve_authorized_target_tenant(actor, requested_tenant_id)` for unified handling:

```python
# tenant_admin: Enforce tenant boundary
actor = {"id": 1, "role": "tenant_admin", "tenant_id": 1}
target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=2)
# Returns: (None, "Cross-tenant access denied")

# platform_admin: Must explicitly specify
actor = {"id": 1, "role": "platform_admin", "tenant_id": None}
target_tenant_id, error = resolve_authorized_target_tenant(actor, requested_tenant_id=1)
# Returns: (1, None)
```

#### ActorScope Authorization Context

Service/Repository layer uses immutable `ActorScope` objects:

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

#### Route Layer Decorator

Uses `@api_key_admin_required` decorator for unified handling:

```python
@api_keys_bp.route("/api-keys", methods=["GET"])
@api_key_admin_required
def list_api_keys():
    scope = g.actor_scope  # Verified authorization context
    keys = api_proxy.list_api_keys(scope.target_tenant_id)
    return jsonify({"success": True, "keys": keys})
```

#### Service Layer Defense Line

Service layer methods must receive verified `ActorScope`:

```python
@require_actor_scope()
def store_api_key(self, scope: ActorScope, ...):
    # Automatically validates scope contains valid user_id, role, target_tenant_id
    ...
```

### Fail-Closed Principle

**No silent default behavior**:

- No fallback to tenant 1 or other default tenant
- No ignoring invalid tenant_id and continuing execution
- No returning global list or global permissions
- All exceptional cases must explicitly error

### Backward Compatibility

- `tenant_admin` without `tenant_id`: Uses actor tenant (backward compatible)
- Error response format conforms to API specification (contains `error` field)
- Existing tenant isolation tests continue to pass

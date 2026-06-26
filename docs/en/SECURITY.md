# Security Model

Open ACE is a self-hosted control plane for AI coding agents. Because it holds LLM API keys, proxies model traffic, and runs commands on remote machines, security is designed as **defense in depth**: no single layer is trusted on its own, and the most sensitive secrets never leave the server.

This document describes the complete security model — what is encrypted, who can access what, how remote machines authenticate, and which defaults are safe for development versus production.

> **At a glance**
> - API keys and SMTP passwords are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
> - Access control is role-based: 4 built-in roles, 19 permissions, with an admin superuser bypass.
> - Passwords are hashed with bcrypt (12 rounds). Failed logins trigger time-boxed lockouts.
> - Remote machines register with one-time 256-bit tokens; after registration they receive only short-lived proxy tokens — never the real API key.
> - Default credentials (`admin/admin123`) force a password change on first login.

---

## 1. Threat Model and Design Principles

Open ACE assumes an environment where:

- The **server** is the trust root. It holds the encryption key, the database, and the real LLM API keys.
- **Remote machines** are semi-trusted: they run AI CLIs and shells on behalf of users, so they *must not* receive long-lived API keys.
- **Users** are authenticated individuals scoped to a tenant, with permissions enforced per request.
- The **network** may be hostile — all inter-service traffic should run over TLS.

Five principles follow from this model:

| Principle | How it is enforced |
|-----------|--------------------|
| **Keys never leave the server** | Real API keys live only in `api_key_store` (encrypted). Remote agents get short-lived, scoped proxy tokens. |
| **Encrypt everything sensitive at rest** | API keys, SMTP passwords, and token secrets are encrypted; only hashes are queryable. |
| **Least privilege by default** | The built-in `user` role has 4 of 19 permissions. Nothing is granted until an admin assigns it. |
| **Authenticate every request** | A single decorator framework (`@auth_required` / `@admin_required` / `@public_endpoint`) guards all routes. |
| **Fail closed in production** | `SECRET_KEY` is mandatory in production; API key encryption refuses to fall back to a default key. |

---

## 2. Secret Encryption at Rest

### 2.1 What is encrypted

| Secret | Storage location | Encryption |
|--------|------------------|------------|
| LLM API keys (OpenAI, Anthropic, …) | `api_key_store.encrypted_key` | Fernet |
| SMTP passwords | `smtp_config` table | Fernet |
| Registration tokens | `registration_tokens.token_hash` | SHA-256 hash (plaintext never retrievable) |
| Proxy token signatures | (in-memory, per request) | HMAC-SHA256 |
| User passwords | `users.password_hash` | bcrypt |

### 2.2 How the encryption key is derived

Both API key and SMTP password encryption use the same key-derivation path (see `app/modules/workspace/api_key_proxy.py` and `app/utils/smtp_crypto.py`):

1. Read `OPENACE_ENCRYPTION_KEY` from the environment.
2. If unset, fall back to `SECRET_KEY`.
3. Derive a 32-byte key with `SHA-256(key_env)`.
4. Wrap it with `base64.urlsafe_b64encode` to produce a Fernet-compatible key.

```python
key_env = os.environ.get("OPENACE_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY")
fernet_key = base64.urlsafe_b64encode(hashlib.sha256(key_env.encode()).digest())
f = Fernet(fernet_key)
ciphertext = f.encrypt(plaintext.encode())
```

The derived key is **never persisted**. It is recomputed on each process start, so rotating `OPENACE_ENCRYPTION_KEY` immediately changes which ciphertext can be decrypted.

### 2.3 Fernet internals

Fernet provides authenticated symmetric encryption:

- **Cipher**: AES-128-CBC.
- **Integrity**: HMAC-SHA256 over the ciphertext, IV, timestamp, and version byte. Tampering with a stored token causes decryption to raise `InvalidToken`.
- **Timestamp**: embedded in the token; Fernet can optionally enforce a TTL, though Open ACE manages token lifetimes itself.

A SHA-256 **hash** of each plaintext API key is also stored (`api_key_store.key_hash`) so the server can detect duplicate keys and look up keys without decrypting them.

> **Note on the `cryptography` package.** Fernet requires the `cryptography` Python package. If it is not installed, API key storage raises a `RuntimeError` (production hardening) and SMTP password operations raise `ImportError`. Install it with `pip install cryptography`.

---

## 3. Role-Based Access Control (RBAC)

### 3.1 The four built-in roles

| Role | Permission count | Intended use |
|------|------------------|--------------|
| **admin** | 19 (all) | Full system administrator — the only role that can register machines, manage API keys, and configure the system. |
| **manager** | 11 | Team manager — view and export analytics, messages, audit logs, and quotas. No user management or system config. |
| **user** | 4 | Regular employee — view dashboard, own messages, analytics, and own quota. |
| **readonly** | 1 | Dashboard viewing only. |

> The `admin_access` permission is a **superuser bypass**: any role or custom grant that includes it automatically passes every permission check (see `Role.has_permission`).

### 3.2 The 19 permissions

| Permission | admin | manager | user | readonly |
|------------|:-----:|:-------:|:----:|:--------:|
| `view_dashboard` | ✓ | ✓ | ✓ | ✓ |
| `view_messages` | ✓ | ✓ | ✓ | |
| `export_messages` | ✓ | ✓ | | |
| `view_analysis` | ✓ | ✓ | ✓ | |
| `run_analysis` | ✓ | ✓ | | |
| `export_analysis` | ✓ | ✓ | | |
| `view_users` | ✓ | ✓ | | |
| `create_user` | ✓ | | | |
| `edit_user` | ✓ | | | |
| `delete_user` | ✓ | | | |
| `manage_permissions` | ✓ | | | |
| `view_quota` | ✓ | ✓ | ✓ | |
| `manage_quota` | ✓ | | | |
| `view_audit_logs` | ✓ | ✓ | | |
| `export_audit_logs` | ✓ | ✓ | | |
| `view_content_filter` | ✓ | ✓ | | |
| `manage_content_filter` | ✓ | | | |
| `admin_access` | ✓ | | | |
| `system_config` | ✓ | | | |

### 3.3 Custom and per-user permissions

Beyond the built-in roles, an admin can grant individual permissions to a user via the `user_permissions` table. A user's effective permissions are the union of their role's permissions and any custom grants:

```python
PermissionService.grant_permission(user_id, "export_analysis", granted_by=admin_id)
PermissionService.has_permission(user_id, "export_analysis")  # → True
```

Custom roles can also be created and stored in the `role_permissions` table.

### 3.4 Multi-tenant isolation

When multi-tenant mode is enabled:

- Every user is associated with a tenant via `tenant_id`.
- API keys, machines, and sessions are scoped to a tenant.
- Per-tenant quotas enforce token and request limits.
- A `QuotaEnforcementScheduler` runs every 60 seconds; users exceeding their quota have sessions terminated and alerts raised.

---

## 4. Authentication

### 4.1 Password hashing

User passwords are hashed with **bcrypt at 12 rounds** (`bcrypt.gensalt(rounds=12)`). Verification uses `bcrypt.checkpw`. The plaintext password is never logged or stored.

### 4.2 Session tokens

On successful login the server:

1. Validates credentials (bcrypt).
2. Generates a 256-bit random session token (`secrets.token_hex(32)`).
3. Persists a session row with an expiry (default 24 hours, configurable via `security_settings.session_timeout`).
4. Returns the token, which the browser stores in an `HttpOnly`, `SameSite=Lax` cookie named `session_token`.

Tokens are extracted on each request in priority order:

1. **Cookie** — `session_token` (HttpOnly, SameSite=Lax; `Secure` over HTTPS).
2. **Authorization header** — `Bearer <token>`.
3. **Query parameter** — `?token=<token>` (for WebSocket / download URLs).

### 4.3 Login lockout (brute-force protection)

Failed login attempts are tracked in the `login_attempts` table:

| Setting | Default | Source |
|---------|---------|--------|
| `max_login_attempts` | 5 | `security_settings` |
| `lockout_duration_minutes` | 15 | `security_settings` |
| Settings cache TTL | 60 s | in-memory cache |

After the threshold is reached, the account is locked until `lockout_duration_minutes` elapse. A successful login clears the counter. The lockout check degrades gracefully: if the database is unavailable, the login is allowed (no lockout) rather than locking everyone out.

### 4.4 Default credentials and first-login enforcement

The seed script (`scripts/init_db.py`) creates a single `admin/admin123` account with `must_change_password = True`. This forces a password change on first login, so the well-known default credential cannot be reused indefinitely. For production, set a strong `SECRET_KEY`, change the default password immediately, and prefer creating the admin with a non-default password.

---

## 5. The Auth Decorator Framework

All route protection flows through one consistent framework in `app/auth/decorators.py`, which replaced scattered auth checks with a single code path.

### 5.1 `@auth_required`

Requires a valid session. Optionally enforces ownership:

- `ownership='session'` — the caller must own the session (or be admin).
- `ownership='machine'` — the caller must be system admin or machine admin.

On success it sets `g.user`, `g.user_id`, and `g.user_role` for the handler.

```python
@auth_required
def api_view():
    user = g.user

@auth_required(ownership='session')
def session_view(session_id):
    # Only the session owner or an admin can proceed.
```

### 5.2 `@admin_required`

Requires the `admin` role; returns `403` otherwise.

### 5.3 `@public_endpoint`

Explicitly marks a route as intentionally unauthenticated (e.g. `/health`, `/api/auth/login`). The API security scanner uses the `_is_public_endpoint` marker to distinguish intentionally public routes from accidentally unprotected ones, instead of relying on a hardcoded list.

### 5.4 Route-level rules

| Route prefix | Protection |
|--------------|------------|
| `/manage/*` | Admin role required (regular users and machine admins cannot access). |
| `/api/*` (most) | `@auth_required` via blueprint-level `before_request`; sensitive operations add `@admin_required`. |
| `/`, `/api/auth/login`, `/api/auth/check`, `/health` | Public. |

---

## 6. Remote Machine Security

This is the most security-sensitive part of Open ACE, because remote machines run shells and AI CLIs on behalf of users. The design goal is simple: **the real API key is never transmitted to or stored on a remote machine.**

### 6.1 Machine registration with one-time tokens

```
┌─────────────┐  admin generates token   ┌─────────────┐
│   Admin      │ ──────────────────────→  │  Database    │  stores SHA-256(token)
│  (browser)   │                          └─────────────┘
└─────────────┘                                  │
       │ shares plaintext token out-of-band       │
       ↓                                          │
┌─────────────┐  POST /api/remote/register        │
│ Remote Agent │ ──────────────────────────────────┘
│ (remote)     │      server marks token consumed
└─────────────┘
```

- The admin generates a **256-bit random** registration token (`secrets.token_hex(32)`).
- Only the **SHA-256 hash** is stored in `registration_tokens`; the plaintext is returned once and cannot be retrieved again.
- The token is **one-time use**: `_consume_registration_token` atomically checks expiry and `is_consumed`, then marks it consumed in the same transaction. Replay attempts are rejected.
- Default TTL is **1 hour** (`REGISTRATION_TOKEN_TTL = 3600`).
- Registration, unregistration, and token generation are restricted to **system admins**.

### 6.2 The proxy token model

After a machine is registered and a user starts a session, the server issues a **proxy token** instead of the real API key:

| Property | Value |
|----------|-------|
| Format | `<base64url(payload)>.<hex_signature>` |
| Signature | HMAC-SHA256 using the encryption key |
| Payload | `user_id`, `session_id`, `tenant_id`, `provider`, `session_type`, `exp`, `jti`, optional HA metadata |
| Validity | Call-site dependent — workspace sessions use **15 minutes**; terminal sessions and the default use up to 24 hours. |
| Validation | Constant-time signature comparison (`hmac.compare_digest`) + expiry check + (for agent sessions) active-session check |

Validation rejects tokens whose signature does not match, whose `exp` has passed, or whose backing session is no longer `active`/`paused`.

### 6.3 The LLM proxy flow

When a remote CLI calls a model:

1. The CLI sends the request to `/api/remote/llm-proxy` with `Authorization: Bearer <proxy_token>`.
2. The server verifies the proxy token's HMAC-SHA256 signature and expiry.
3. The server decrypts the real API key from `api_key_store`.
4. The server replaces the Authorization header with the real key and forwards the request to the LLM provider.
5. The response is streamed back; token usage is parsed and recorded for quota/billing.

**The API key is never transmitted to or written to disk on the remote machine.** Path-traversal attempts in the proxy URL are rejected (`..` segments in the path return HTTP 400).

### 6.4 Machine access control

The `machine_assignments` table controls which users can use which machines, with a `permission` field of `user` or `admin`. Machine admins may delegate management of users and sessions on their own machine. Users can only access their own sessions; system admins and machine admins can view or stop other users' sessions on a machine they administer.

### 6.5 Strip-before-send settings

When building CLI settings (e.g. `settings.json` for Qwen Code), the server strips every sensitive field before sending it to the agent:

- Static credential env keys: `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`.
- Dynamic `envKey` names declared under `modelProviders`.
- `baseUrl` fields inside `modelProviders` entries.

Credentials are instead injected as environment variables at process launch time and are never persisted to the agent's config files.

---

## 7. Content Filtering and Compliance

For enterprise deployments, a content filter module (`app/modules/governance/content_filter.py`) inspects messages for sensitive data:

- **Detectable types**: PII (email, phone, SSN, credit card, address, passport, driver's license), sensitive keywords, profanity, and custom regex patterns.
- **Risk levels**: `low`, `medium`, `high`, `critical`.
- **Output**: each `FilterResult` reports whether content passed, which rules matched, a redacted version of the content, and a suggestion.
- **Governance**: results feed audit logs and compliance reports; access is gated by `view_content_filter` / `manage_content_filter` permissions.

---

## 8. Transport Security

| Concern | Behavior |
|---------|----------|
| Browser ↔ server | Set `Secure` on cookies and terminate TLS at a reverse proxy (see [NGINX guide](NGINX.md)). `app/__init__.py` refuses to start in production without `SECRET_KEY`. |
| Server ↔ LLM provider | Outbound HTTPS to provider APIs. |
| Server ↔ remote agent | The agent connects over WebSocket (and HTTPS for REST). **`skip_ssl_verify` defaults to `true`** in the agent config to ease local development; set `OPENACE_SKIP_SSL_VERIFY=false` (or `skip_ssl_verify: false` in the agent config) for any deployment that uses real TLS certificates. |

> ⚠️ **Development default.** `skip_ssl_verify: true` exists so first-run demos against self-signed certificates work. It must be disabled in any environment with a valid certificate, or the agent will trust any presented certificate.

---

## 9. Production Hardening Checklist

Before exposing Open ACE beyond a single trusted developer:

- [ ] Set a strong, unique `SECRET_KEY` (and preferably a separate `OPENACE_ENCRYPTION_KEY`).
- [ ] Change the default `admin/admin123` password (the seed forces this via `must_change_password`, but set your own at provisioning time).
- [ ] Install the `cryptography` package so Fernet encryption is active.
- [ ] Disable `skip_ssl_verify` on every remote agent and terminate TLS with a valid certificate.
- [ ] Configure `max_login_attempts` and `lockout_duration_minutes` in `security_settings` to match your policy.
- [ ] Grant the least-privileged role to each user; reserve `admin` for operators.
- [ ] Enable per-tenant quotas and confirm the `QuotaEnforcementScheduler` is running.
- [ ] Enable the content filter if your compliance regime requires PII redaction.
- [ ] Put Open ACE behind a reverse proxy (see the [NGINX guide](NGINX.md)) and restrict `/manage/*` to operator networks.

---

## 10. Reporting Vulnerabilities

Please do **not** report security vulnerabilities through public GitHub issues. Use GitHub Security Advisories or email the maintainers directly — see [`.github/SECURITY.md`](../../.github/SECURITY.md) for the full disclosure policy and response timelines.

---

## Further Reading

- [Permission Model](PERMISSION-MODEL.md) — RBAC, decorators, and token extraction in depth.
- [Remote Workspace](REMOTE-WORKSPACE.md) — server-side remote machine management and the LLM proxy flow.
- [Remote Agent](REMOTE-AGENT.md) — agent installation, config, and `skip_ssl_verify`.
- [Deployment](DEPLOYMENT.md) and [NGINX](NGINX.md) — TLS, secrets, and reverse proxy setup.
- [10-Minute Demo](DEMO-10-MINUTES.md) — an end-to-end walkthrough that exercises this security model.

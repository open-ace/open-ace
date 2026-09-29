# SSO Configuration — SSO 配置

[English](#english) | [中文](#中文)

---

## English

This guide explains how to configure Single Sign-On for Open ACE. It covers SAML 2.0, OIDC, and OAuth2 providers, the SAML Single Logout (SLO) endpoints, the `SSO_ALLOWED_REDIRECT_DOMAINS` production requirement, and how to test and troubleshoot a provider. It replaces the older SAML_CONFIG.md quick reference.

## SSO Overview and Provider Types

Open ACE acts as a relying party / Service Provider (SP) and supports three provider types (`provider_type`):

| Type | Protocol | Typical use | Notes |
|------|----------|-------------|-------|
| `saml` | SAML 2.0 | Enterprise IdPs (ADFS, Shibboleth, Keycloak, corporate IdP) | `client_id` is the SP entity ID; no `client_secret` required |
| `oidc` | OpenID Connect 1.0 | Google, Microsoft, Okta, Auth0 | ID-token signature verified against the provider JWKS; `issuer_url` required for verification |
| `oauth2` | OAuth 2.0 | GitHub and other plain OAuth2 APIs | User identity is read from the `userinfo_url` endpoint; no ID-token validation |

Predefined providers ship with default URLs and scopes: `google`, `microsoft` (both OIDC), `github` (OAuth2), and `okta` / `auth0` (OIDC, per-tenant URLs you must override at registration time). Any other provider is registered as a custom one with full URLs.

Providers are stored in the `sso_providers` table (one row per name, with a `tenant_id` for multi-tenant deployments). Secrets are stored encrypted (`client_secret_encrypted`, Fernet); they are never returned by the detail, list, or export endpoints.

## Core Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/sso/login/<provider_name>` | Generates a SAML AuthnRequest (or an OAuth2/OIDC authorization URL) and redirects to the IdP |
| `POST /api/sso/acs/<provider_name>` | Assertion Consumer Service for HTTP-POST SAMLResponse callbacks |
| `GET /api/sso/callback/<provider_name>` | OAuth2/OIDC redirect callback (authorization code exchange) |
| `GET /api/sso/providers/<provider_name>/metadata` | SP metadata XML for IdP configuration |
| `POST /api/sso/slo/<provider_name>` | SAML Single Logout, HTTP-POST binding |
| `GET /api/sso/slo-redirect/<provider_name>` | SAML Single Logout, HTTP-Redirect binding |

Management endpoints (admin only unless noted): `GET /api/sso/providers` (public; lists registered and predefined providers), `GET/POST /api/sso/providers`, `PUT /api/sso/providers/<name>` (optimistic locking on `updated_at`), `PATCH .../enable` and `PATCH .../disable`, `DELETE /api/sso/providers/<name>` (deprecated — disables instead of deleting), `POST .../reset` (restores a predefined provider's default URLs, keeping `client_id`/`client_secret`), `POST .../test`, and `GET /api/sso/providers/export` (secrets excluded).

## SAML Configuration in Five Steps

### Step 1: Register the provider

Register a SAML provider through `POST /api/sso/providers`:

```json
{
  "name": "corp-saml",
  "provider_type": "saml",
  "client_id": "https://openace.example.com/saml/metadata",
  "authorization_url": "https://idp.example.com/sso",
  "redirect_uri": "https://openace.example.com/api/sso/acs/corp-saml",
  "issuer_url": "https://idp.example.com/metadata",
  "extra_params": {
    "idp_x509_cert": "MIIC...",
    "idp_entity_id": "https://idp.example.com/metadata",
    "attribute_mapping": {
      "email": "email",
      "username": "uid",
      "name": "displayName"
    }
  }
}
```

`client_id` is the SP entity ID. SAML providers do not require `client_secret`.
IdP configuration can be supplied directly with `authorization_url`, `issuer_url`,
and `idp_x509_cert`, or via `extra_params.idp_metadata_xml` /
`extra_params.idp_metadata_url`.

Useful `extra_params` keys understood by the SAML provider: `sp_entity_id`, `acs_url`, `idp_entity_id`, `idp_sso_url`, `idp_slo_url`, `nameid_format` (default `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress`), `attribute_mapping`, `required_attributes`, `allow_email_linking`, `default_tenant_id`.

### Step 2: Import the SP metadata into the IdP

Download the SP metadata and import it into your IdP:

```bash
curl -o sp-metadata.xml https://openace.example.com/api/sso/providers/corp-saml/metadata
```

The metadata declares:

- `entityID` — the SP entity ID (from `client_id` / `extra_params.sp_entity_id`)
- `AssertionConsumerService`, HTTP-POST binding, at `/api/sso/acs/<name>`
- `SingleLogoutService`, HTTP-POST binding at `/api/sso/slo/<name>`, and HTTP-Redirect binding at `/api/sso/slo-redirect/<name>`
- `AuthnRequestsSigned="false"`, `WantAssertionsSigned="true"` (assertions or responses must be signed by the IdP)

If your IdP is configured by hand instead of via metadata, register exactly those ACS and SLO URLs.

The ACS URL is derived from the incoming request host by default. Behind a reverse proxy, set the `sso.canonical_base_url` configuration value (e.g. `https://openace.example.com`) so the ACS URL in the metadata, the AuthnRequest, and the validation comparison are pinned to one public scheme+host and cannot drift with spoofed `Host` headers. This setting affects only the SAML ACS URL; the OAuth2/OIDC callback URL stays request-derived because providers register it out of band.

### Step 3: Map attributes

SAML attributes are read from the `AttributeStatement`. Open ACE maps five user fields via `extra_params.attribute_mapping`; each value may be a single attribute name or a list of names tried in order:

| Open ACE field | Default candidate attribute names |
|----------------|-----------------------------------|
| `email` | `email`, `mail`, `Email`, `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` |
| `username` | `username`, `preferred_username`, `uid`, `UserName`, `.../claims/name` |
| `name` | `name`, `displayName`, `cn` |
| `first_name` | `givenName`, `first_name`, `.../claims/givenname` |
| `last_name` | `sn`, `surname`, `last_name`, `.../claims/surname` |

Notes:

- `required_attributes` (default `["email"]`) lists fields that must resolve, or the login fails with `missing_attribute`.
- If no email attribute matches but the NameID contains `@`, the NameID is used as the email.
- `email_verified` is treated as true only when the IdP explicitly attests it (an `email_verified` / `EmailVerified` attribute with value `true`/`1`/`yes`).
- The NameID is always stored as the provider-side user identifier (`provider_user_id`).

### Step 4: Know what the ACS validates

The ACS handler validates:

- XML Signature with the configured IdP certificate
- SAML success status
- IdP issuer
- audience restriction matching the SP entity ID
- response destination and subject recipient matching the ACS URL
- `InResponseTo` matching the request bound to `RelayState`
- assertion time windows with a small clock skew (180 seconds by default)
- required attributes, including email by default

Requests/responses larger than 256 KB are rejected with HTTP 413 before parsing.

### Step 5: Configure Single Logout (SLO)

Two endpoints back SAML Single Logout:

- `POST /api/sso/slo/<name>` — HTTP-POST binding. Accepts either an IdP-initiated `LogoutRequest` (Open ACE builds and returns a signed-out `LogoutResponse` posted back to the IdP SLO URL) or an SP-initiated `LogoutResponse`.
- `GET /api/sso/slo-redirect/<name>` — HTTP-Redirect binding. Handles `SAMLRequest`/`SAMLResponse` query parameters the same way. Called with no SAML parameter but a `session_token` cookie (or `?session_token=`), it initiates SP logout: it builds a `LogoutRequest` from the NameID/SessionIndex stored at login and redirects to the IdP. If the IdP has no SLO URL configured (`extra_params.idp_slo_url`, or discovered from metadata), the local session is simply deleted.

The plain `DELETE /api/sso/session` logout always cascades: the row in `sso_sessions` and the matching local session in `sessions` are deleted in one transaction, and the `session_token` cookie is cleared.

### Binding boundaries

Open ACE currently supports Redirect binding for AuthnRequest and HTTP-POST binding
for ACS. Signed assertions or signed responses are accepted when the signature
matches the configured IdP certificate. Local user provisioning/linking follows the
same SSO identity table used by OAuth2/OIDC.

## OIDC / OAuth2 Provider Configuration

Register an OIDC or OAuth2 provider with `POST /api/sso/providers`:

```json
{
  "name": "corp-oidc",
  "provider_type": "oidc",
  "client_id": "my-client-id",
  "client_secret": "my-client-secret",
  "authorization_url": "https://sso.example.com/oauth2/v1/authorize",
  "token_url": "https://sso.example.com/oauth2/v1/token",
  "userinfo_url": "https://sso.example.com/oauth2/v1/userinfo",
  "issuer_url": "https://sso.example.com",
  "redirect_uri": "https://openace.example.com/api/sso/callback/corp-oidc",
  "scope": ["openid", "profile", "email"]
}
```

Field reference:

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | Unique provider name used in all endpoint paths |
| `provider_type` | Yes | `oidc` or `oauth2` (default `oauth2`) |
| `client_id` / `client_secret` | Yes | Both required for non-SAML providers |
| `authorization_url` | Yes | Where the browser is redirected to log in |
| `token_url` | Yes | Authorization-code exchange endpoint |
| `userinfo_url` | Recommended | Fetches the user profile with the access token |
| `issuer_url` | OIDC | ID-token `iss` claim and JWKS discovery (`<issuer_url>/.well-known/jwks.json`) |
| `redirect_uri` | Recommended | Callback URL; must be `/api/sso/callback/<name>` on your public origin |
| `scope` | Recommended | Defaults to `["openid", "profile", "email"]` for OIDC; GitHub uses `["user:email", "read:user"]` |

For a predefined provider, POST with `"predefined": true` and just the credentials — default URLs are applied automatically. Okta and Auth0 have no default URLs (they depend on your tenant domain), so pass `authorization_url`, `token_url`, `userinfo_url`, and `issuer_url` explicitly.

Behavior after the callback:

- OIDC providers verify the ID token signature (JWKS, cached), `iss`, and `aud` before trusting any claims; user info falls back to the userinfo endpoint when needed.
- OAuth2 providers rely entirely on the userinfo endpoint.
- The `state` parameter carries an HMAC-SHA256 signature (key from `SSO_RELAYSTATE_SIGNING_KEY` or the instance encryption key). Legacy unsigned states are accepted only during a transition period ending 2027-01-31.
- The authenticated user is looked up in `sso_identities`; new users are provisioned when tenant policy allows, and the resulting session token is set as an `HttpOnly` cookie on the whitelisted frontend redirect.

## User Provisioning and Tenant Policy

- Auto-provisioning of local users requires the tenant setting `auto_provision_users` to be enabled; otherwise the login is refused with `auto_provision_disabled` (HTTP 403).
- The tenant for a new SSO user is resolved in order: provider `extra_params.default_tenant_id` → the registering admin's tenant → policy check. With the default `SSO_NULL_TENANT_POLICY=reject`, a user without a resolvable tenant is rejected (`warn` also rejects today and is deprecated).
- Email-based linking to an existing local account only happens when the provider sets `extra_params.allow_email_linking=true` (default off). A deactivated or soft-deleted account is refused before any identity binding, with `account_disabled`.

## SSO_ALLOWED_REDIRECT_DOMAINS (required in production)

`SSO_ALLOWED_REDIRECT_DOMAINS` is a comma-separated whitelist of frontend domains that may receive the post-login redirect (and the `session_token` cookie):

```bash
export SSO_ALLOWED_REDIRECT_DOMAINS="openace.example.com,console.example.com"
```

- Without it, only `localhost` / `127.0.0.1` / `[::1]` redirects are allowed — fine for development, broken for production.
- A configured domain matches itself and all subdomains (`example.com` matches `app.example.com`).
- If a login succeeds but the redirect target is not whitelisted, the flow returns `redirect_uri_not_allowed` (HTTP 400), and the freshly created session rows are deleted — the user cannot log in. This is the first thing to check when "login succeeds but the browser ends up on an error page".
- Only `http`/`https` redirect URIs are accepted.

## Testing and Troubleshooting

### Test a provider

`POST /api/sso/providers/<name>/test` (admin) performs basic checks and reports each one:

- `authorization_url` (or `idp_metadata_url` for SAML) is reachable over HTTP (follows at most two redirects, SSRF-guarded)
- `token_url` is reachable — for SAML, replaced by a check that an IdP signing certificate, metadata XML, or metadata URL is configured
- `client_id` looks sane (length)
- `scope` is a non-empty list (skipped for SAML)

At most 3 concurrent tests run; further requests get HTTP 429.

### Common failures

| Symptom | Likely cause and fix |
|---------|----------------------|
| `invalid_saml_response` | The `SAMLResponse` is not valid Base64/XML, or exceeds 256 KB |
| Signature verification failure | `idp_x509_cert` does not match the IdP signing certificate; rotate it, or refresh `idp_metadata_url` |
| `saml_status_not_success` | The IdP returned a non-Success status — check the IdP-side error |
| Audience / destination / recipient mismatch | The ACS URL or SP entity ID differs between what the IdP was given and what Open ACE uses; re-import SP metadata, and set `sso.canonical_base_url` behind a proxy |
| `missing_attribute` | `required_attributes` (default `["email"]`) not satisfied — fix `attribute_mapping` or release the attribute on the IdP |
| Assertion time-window failure | Clock skew beyond 180 s between servers — sync NTP |
| `redirect_uri_not_allowed` | `SSO_ALLOWED_REDIRECT_DOMAINS` missing or does not cover the frontend origin |
| `auto_provision_disabled` / tenant rejection | Enable `auto_provision_users` for the tenant or set `default_tenant_id` on the provider |
| `account_disabled` | The matching local account is deactivated or soft-deleted |
| Provider update returns 409 | Optimistic lock: another admin edited the provider; reload and retry with the fresh `updated_at` |

Frontend redirects carry the failure as `?sso_error=<code>` (for example `auth_failed`, `invalid_request`, `account_disabled`), so the exact code is visible in the browser URL and the audit log (`resource_type: sso_session`).

---

## 中文

本指南说明如何为 Open ACE 配置单点登录（SSO），覆盖 SAML 2.0、OIDC、OAuth2 三类 Provider，SAML 单点登出（SLO）端点，生产环境必配的 `SSO_ALLOWED_REDIRECT_DOMAINS`，以及测试与排障方法。本文替代旧的 SAML_CONFIG.md 速查文档。

## SSO 概览与 Provider 类型

Open ACE 作为依赖方 / Service Provider（SP），支持三种 Provider 类型（`provider_type`）：

| 类型 | 协议 | 典型场景 | 说明 |
|------|------|---------|------|
| `saml` | SAML 2.0 | 企业 IdP（ADFS、Shibboleth、Keycloak 等） | `client_id` 即 SP entity ID；不要求 `client_secret` |
| `oidc` | OpenID Connect 1.0 | Google、Microsoft、Okta、Auth0 | 用 Provider JWKS 校验 ID token 签名；校验需要 `issuer_url` |
| `oauth2` | OAuth 2.0 | GitHub 等纯 OAuth2 API | 用户身份来自 `userinfo_url` 端点；不做 ID token 校验 |

内置预定义 Provider 自带默认 URL 与 scope：`google`、`microsoft`（均为 OIDC）、`github`（OAuth2），以及 `okta` / `auth0`（OIDC，URL 依赖租户域名，注册时必须覆盖）。其他 Provider 按自定义方式注册并填写完整 URL。

Provider 存储在 `sso_providers` 表（每个 name 一行，多租户部署带 `tenant_id`）。密钥加密存储（`client_secret_encrypted`，Fernet）；详情、列表、导出接口都不会返回密钥。

## 核心端点

| 端点 | 用途 |
|------|------|
| `GET /api/sso/login/<provider_name>` | 生成 SAML AuthnRequest（或 OAuth2/OIDC 授权 URL）并跳转到 IdP |
| `POST /api/sso/acs/<provider_name>` | HTTP-POST SAMLResponse 回调的 ACS |
| `GET /api/sso/callback/<provider_name>` | OAuth2/OIDC 重定向回调（授权码交换） |
| `GET /api/sso/providers/<provider_name>/metadata` | 供 IdP 配置使用的 SP metadata XML |
| `POST /api/sso/slo/<provider_name>` | SAML 单点登出，HTTP-POST binding |
| `GET /api/sso/slo-redirect/<provider_name>` | SAML 单点登出，HTTP-Redirect binding |

管理端点（除特别说明外均需管理员权限）：`GET /api/sso/providers`（公开；返回已注册与预定义 Provider）、`GET/POST /api/sso/providers`、`PUT /api/sso/providers/<name>`（基于 `updated_at` 的乐观锁）、`PATCH .../enable` 与 `PATCH .../disable`、`DELETE /api/sso/providers/<name>`（已弃用——实际是停用而非删除）、`POST .../reset`（恢复预定义 Provider 的默认 URL，保留 `client_id`/`client_secret`）、`POST .../test`、`GET /api/sso/providers/export`（不含密钥）。

## SAML 配置五段式

### 第一步：注册 Provider

通过 `POST /api/sso/providers` 注册 SAML Provider：

```json
{
  "name": "corp-saml",
  "provider_type": "saml",
  "client_id": "https://openace.example.com/saml/metadata",
  "authorization_url": "https://idp.example.com/sso",
  "redirect_uri": "https://openace.example.com/api/sso/acs/corp-saml",
  "issuer_url": "https://idp.example.com/metadata",
  "extra_params": {
    "idp_x509_cert": "MIIC...",
    "idp_entity_id": "https://idp.example.com/metadata",
    "attribute_mapping": {
      "email": "email",
      "username": "uid",
      "name": "displayName"
    }
  }
}
```

`client_id` 是 SP entity ID。SAML Provider 不要求 `client_secret`。
IdP 配置可以直接提供 `authorization_url`、`issuer_url`、`idp_x509_cert`，
也可以通过 `extra_params.idp_metadata_xml` 或 `extra_params.idp_metadata_url` 提供。

SAML Provider 认识的常用 `extra_params` 键：`sp_entity_id`、`acs_url`、`idp_entity_id`、`idp_sso_url`、`idp_slo_url`、`nameid_format`（默认 `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress`）、`attribute_mapping`、`required_attributes`、`allow_email_linking`、`default_tenant_id`。

### 第二步：把 SP metadata 导入 IdP

下载 SP metadata 并导入 IdP：

```bash
curl -o sp-metadata.xml https://openace.example.com/api/sso/providers/corp-saml/metadata
```

metadata 中声明了：

- `entityID` — SP entity ID（来自 `client_id` / `extra_params.sp_entity_id`）
- `AssertionConsumerService`，HTTP-POST binding，位于 `/api/sso/acs/<name>`
- `SingleLogoutService`，HTTP-POST binding 位于 `/api/sso/slo/<name>`，HTTP-Redirect binding 位于 `/api/sso/slo-redirect/<name>`
- `AuthnRequestsSigned="false"`、`WantAssertionsSigned="true"`（断言或响应必须由 IdP 签名）

如果 IdP 不用 metadata 而是手工配置，请精确登记上述 ACS 与 SLO URL。

ACS URL 默认根据请求 Host 推导。反向代理后面请设置 `sso.canonical_base_url` 配置项（例如 `https://openace.example.com`），把 metadata、AuthnRequest 与校验比较三处使用的 ACS URL 固定到同一个公网 scheme+host，避免被伪造的 `Host` 头带偏。该设置只影响 SAML ACS URL；OAuth2/OIDC 回调 URL 仍按请求推导，因为需要在 Provider 侧预先登记。

### 第三步：属性映射

SAML 属性从 `AttributeStatement` 读取。Open ACE 通过 `extra_params.attribute_mapping` 映射五个用户字段；每个值可以是单个属性名，也可以是按顺序尝试的属性名列表：

| Open ACE 字段 | 默认候选属性名 |
|----------------|-----------------------------------|
| `email` | `email`、`mail`、`Email`、`http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` |
| `username` | `username`、`preferred_username`、`uid`、`UserName`、`.../claims/name` |
| `name` | `name`、`displayName`、`cn` |
| `first_name` | `givenName`、`first_name`、`.../claims/givenname` |
| `last_name` | `sn`、`surname`、`last_name`、`.../claims/surname` |

注意：

- `required_attributes`（默认 `["email"]`）列出必须解析成功的字段，否则登录以 `missing_attribute` 失败。
- 如果 email 属性没有匹配到，但 NameID 含 `@`，则用 NameID 作为 email。
- 只有 IdP 显式声明（存在值为 `true`/`1`/`yes` 的 `email_verified` / `EmailVerified` 属性）时，`email_verified` 才视为真。
- NameID 始终作为 Provider 侧的用户标识（`provider_user_id`）存储。

### 第四步：了解 ACS 校验清单

ACS 会校验：

- 使用配置的 IdP 证书验证 XML Signature
- SAML success status
- IdP issuer
- audience restriction 是否匹配 SP entity ID
- response destination 与 subject recipient 是否匹配 ACS URL
- `InResponseTo` 是否匹配 `RelayState` 绑定的请求
- assertion 时间窗口和小范围时钟偏移（默认 180 秒）
- 必需属性，默认要求 email

超过 256 KB 的请求/响应会在解析前被 HTTP 413 拒绝。

### 第五步：配置单点登出（SLO）

两个端点支撑 SAML 单点登出：

- `POST /api/sso/slo/<name>` — HTTP-POST binding。接受 IdP 主动发起的 `LogoutRequest`（Open ACE 构造 `LogoutResponse` 并以表单回发给 IdP SLO URL），或 SP 发起的 `LogoutResponse`。
- `GET /api/sso/slo-redirect/<name>` — HTTP-Redirect binding。以同样的方式处理 `SAMLRequest`/`SAMLResponse` 查询参数。若不带 SAML 参数但携带 `session_token` cookie（或 `?session_token=`），则发起 SP 登出：用登录时存储的 NameID/SessionIndex 构造 `LogoutRequest` 并跳转 IdP。若 IdP 未配置 SLO URL（`extra_params.idp_slo_url` 或从 metadata 发现），则只删除本地会话。

普通的 `DELETE /api/sso/session` 登出总是级联处理：在一个事务里删除 `sso_sessions` 中的记录和 `sessions` 中对应的本地会话，并清除 `session_token` cookie。

### 绑定边界

Open ACE 当前支持 Redirect binding 的 AuthnRequest，以及 HTTP-POST binding 的
ACS。只要签名与配置的 IdP 证书匹配，签名在 Response 或 Assertion 上均可接受。
本地用户创建和身份关联复用 OAuth2/OIDC 使用的 SSO identity 表。

## OIDC / OAuth2 Provider 配置

通过 `POST /api/sso/providers` 注册 OIDC 或 OAuth2 Provider：

```json
{
  "name": "corp-oidc",
  "provider_type": "oidc",
  "client_id": "my-client-id",
  "client_secret": "my-client-secret",
  "authorization_url": "https://sso.example.com/oauth2/v1/authorize",
  "token_url": "https://sso.example.com/oauth2/v1/token",
  "userinfo_url": "https://sso.example.com/oauth2/v1/userinfo",
  "issuer_url": "https://sso.example.com",
  "redirect_uri": "https://openace.example.com/api/sso/callback/corp-oidc",
  "scope": ["openid", "profile", "email"]
}
```

字段说明：

| 字段 | 必需 | 说明 |
|-------|----------|-------|
| `name` | 是 | 唯一的 Provider 名称，用于所有端点路径 |
| `provider_type` | 是 | `oidc` 或 `oauth2`（默认 `oauth2`） |
| `client_id` / `client_secret` | 是 | 非 SAML Provider 两者都必填 |
| `authorization_url` | 是 | 浏览器登录跳转地址 |
| `token_url` | 是 | 授权码交换端点 |
| `userinfo_url` | 建议 | 用 access token 拉取用户信息 |
| `issuer_url` | OIDC | ID token 的 `iss` 声明与 JWKS 发现（`<issuer_url>/.well-known/jwks.json`） |
| `redirect_uri` | 建议 | 回调 URL；必须是公网源上的 `/api/sso/callback/<name>` |
| `scope` | 建议 | OIDC 默认 `["openid", "profile", "email"]`；GitHub 使用 `["user:email", "read:user"]` |

注册预定义 Provider 时带 `"predefined": true` 并只传凭证即可——默认 URL 会自动套用。Okta 与 Auth0 没有默认 URL（依赖租户域名），必须显式传入 `authorization_url`、`token_url`、`userinfo_url` 和 `issuer_url`。

回调之后的行为：

- OIDC Provider 在信任任何 claim 之前先校验 ID token 签名（JWKS，带缓存）、`iss` 和 `aud`；必要时回退到 userinfo 端点获取用户信息。
- OAuth2 Provider 完全依赖 userinfo 端点。
- `state` 参数带 HMAC-SHA256 签名（密钥来自 `SSO_RELAYSTATE_SIGNING_KEY` 或实例加密密钥）。旧的无签名 state 只在过渡期内接受，过渡期于 2027-01-31 结束。
- 认证成功后在 `sso_identities` 中查找用户；租户策略允许时创建新用户，并把会话 token 以 `HttpOnly` cookie 设置到白名单内的前端重定向地址。

## 用户开通与租户策略

- 本地用户自动开通需要租户设置 `auto_provision_users` 开启；否则登录被拒绝并返回 `auto_provision_disabled`（HTTP 403）。
- 新 SSO 用户的租户按顺序解析：Provider 的 `extra_params.default_tenant_id` → 注册管理员的租户 → 策略检查。默认 `SSO_NULL_TENANT_POLICY=reject` 下，无法解析租户的用户被拒绝（`warn` 当前同样拒绝，已弃用）。
- 只有 Provider 显式设置 `extra_params.allow_email_linking=true`（默认关闭）时，才允许按 email 关联已存在的本地账号。已停用或软删除的账号在任何身份绑定之前就会被拒绝，返回 `account_disabled`。

## SSO_ALLOWED_REDIRECT_DOMAINS（生产必配）

`SSO_ALLOWED_REDIRECT_DOMAINS` 是逗号分隔的前端域名白名单，用于限制登录成功后的重定向目标（及 `session_token` cookie 的落点）：

```bash
export SSO_ALLOWED_REDIRECT_DOMAINS="openace.example.com,console.example.com"
```

- 不配置时只允许 `localhost` / `127.0.0.1` / `[::1]`——开发环境可用，生产环境不可用。
- 配置的域名匹配自身及全部子域名（`example.com` 匹配 `app.example.com`）。
- 如果登录成功但重定向目标不在白名单内，流程返回 `redirect_uri_not_allowed`（HTTP 400），并删除刚创建的会话记录——用户无法登录。"登录成功但浏览器落在错误页"时优先检查这一项。
- 只接受 `http`/`https` 的重定向 URI。

## 测试与排障

### 测试 Provider

`POST /api/sso/providers/<name>/test`（管理员）执行基础检查并逐项报告：

- `authorization_url`（SAML 为 `idp_metadata_url`）可通过 HTTP 访问（最多跟随两次重定向，带 SSRF 防护）
- `token_url` 可访问——SAML 改为检查是否配置了 IdP 签名证书、metadata XML 或 metadata URL
- `client_id` 格式合理（长度）
- `scope` 是非空列表（SAML 跳过）

最多 3 个并发测试；超出返回 HTTP 429。

### 常见故障

| 现象 | 可能原因与处理 |
|---------|----------------------|
| `invalid_saml_response` | `SAMLResponse` 不是合法的 Base64/XML，或超过 256 KB |
| 签名校验失败 | `idp_x509_cert` 与 IdP 签名证书不匹配；更换证书或刷新 `idp_metadata_url` |
| `saml_status_not_success` | IdP 返回非 Success 状态——在 IdP 侧排查 |
| audience / destination / recipient 不匹配 | IdP 登记的 ACS URL 或 SP entity ID 与 Open ACE 实际使用的不一致；重新导入 SP metadata，代理后面设置 `sso.canonical_base_url` |
| `missing_attribute` | `required_attributes`（默认 `["email"]`）未满足——修正 `attribute_mapping` 或让 IdP 释放属性 |
| 断言时间窗口校验失败 | 服务器之间时钟偏差超过 180 秒——同步 NTP |
| `redirect_uri_not_allowed` | `SSO_ALLOWED_REDIRECT_DOMAINS` 未配置或未覆盖前端源 |
| `auto_provision_disabled` / 租户拒绝 | 为租户开启 `auto_provision_users`，或在 Provider 上设置 `default_tenant_id` |
| `account_disabled` | 匹配的本地账号已停用或软删除 |
| 更新 Provider 返回 409 | 乐观锁冲突：其他管理员已修改；刷新后用新的 `updated_at` 重试 |

前端重定向会以 `?sso_error=<code>` 携带失败原因（如 `auth_failed`、`invalid_request`、`account_disabled`），因此浏览器 URL 与审计日志（`resource_type: sso_session`）中都能看到具体错误码。

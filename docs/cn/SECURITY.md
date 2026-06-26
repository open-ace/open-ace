# 安全模型

Open ACE 是面向 AI Coding Agent 的自托管控制面。由于它保存 LLM API Key、代理模型流量并在远程机器上执行命令，安全设计采用**纵深防御**：任何单一层都不被单独信任，最敏感的密钥永不离开服务端。

本文描述完整的安全模型——哪些内容被加密、谁能访问什么、远程机器如何认证，以及哪些默认值适用于开发、哪些适用于生产。

> **概览**
> - API Key 与 SMTP 密码使用 Fernet（AES-128-CBC + HMAC-SHA256）静态加密。
> - 访问控制基于角色：4 个内置角色、19 种权限，并带管理员超级 bypass。
> - 密码使用 bcrypt（12 轮）哈希；登录失败将触发限时锁定。
> - 远程机器用一次性 256 位令牌注册；注册后仅获得短期代理令牌——绝不接触真实 API Key。
> - 默认凭据（`admin/admin123`）在首次登录时强制改密。

---

## 1. 威胁模型与设计原则

Open ACE 假定运行环境如下：

- **服务端**是信任根，持有加密密钥、数据库与真实 LLM API Key。
- **远程机器**是半可信的：它们代表用户运行 AI CLI 与 shell，因此*绝不能*获得长效 API Key。
- **用户**是认证到租户的个体，权限按请求强制执行。
- **网络**可能被监听——所有服务间通信都应走 TLS。

由此衍生五条原则：

| 原则 | 落地方式 |
|------|----------|
| **密钥永不离开服务端** | 真实 API Key 仅存在于 `api_key_store`（加密）。远程 Agent 只拿到短期、有作用域的代理令牌。 |
| **敏感数据静态加密** | API Key、SMTP 密码与令牌机密均加密；仅哈希可被查询。 |
| **默认最小权限** | 内置 `user` 角色仅含 19 种权限中的 4 种，管理员分配前一律不授予。 |
| **每个请求都鉴权** | 统一装饰器框架（`@auth_required` / `@admin_required` / `@public_endpoint`）守护全部路由。 |
| **生产环境 fail closed** | 生产环境必须设置 `SECRET_KEY`；API Key 加密拒绝回退到默认密钥。 |

---

## 2. 密钥静态加密

### 2.1 哪些内容被加密

| 密钥 | 存储位置 | 加密方式 |
|------|----------|----------|
| LLM API Key（OpenAI、Anthropic……） | `api_key_store.encrypted_key` | Fernet |
| SMTP 密码 | `smtp_config` 表 | Fernet |
| 注册令牌 | `registration_tokens.token_hash` | SHA-256 哈希（明文不可检索） |
| 代理令牌签名 | （内存中，按请求） | HMAC-SHA256 |
| 用户密码 | `users.password_hash` | bcrypt |

### 2.2 加密密钥如何派生

API Key 与 SMTP 密码加密使用同一路径（见 `app/modules/workspace/api_key_proxy.py` 与 `app/utils/smtp_crypto.py`）：

1. 从环境变量读取 `OPENACE_ENCRYPTION_KEY`。
2. 若未设置，回退到 `SECRET_KEY`。
3. 用 `SHA-256(key_env)` 派生 32 字节密钥。
4. 用 `base64.urlsafe_b64encode` 包装为 Fernet 兼容密钥。

```python
key_env = os.environ.get("OPENACE_ENCRYPTION_KEY") or os.environ.get("SECRET_KEY")
fernet_key = base64.urlsafe_b64encode(hashlib.sha256(key_env.encode()).digest())
f = Fernet(fernet_key)
ciphertext = f.encrypt(plaintext.encode())
```

派生密钥**永不落库**，每次进程启动时重新计算，因此轮换 `OPENACE_ENCRYPTION_KEY` 会立即改变可解密的密文。

### 2.3 Fernet 细节

Fernet 提供带认证的对称加密：

- **算法**：AES-128-CBC。
- **完整性**：对密文、IV、时间戳与版本字节做 HMAC-SHA256；篡改存储令牌会在解密时抛出 `InvalidToken`。
- **时间戳**：嵌入令牌中；Fernet 可选地强制 TTL，但 Open ACE 自行管理令牌生命周期。

每个 API Key 明文的 SHA-256 **哈希**也一并存储（`api_key_store.key_hash`），以便在不解密的情况下检测重复 Key 并支持查找。

> **关于 `cryptography` 包**：Fernet 依赖 `cryptography`。若未安装，存储 API Key 会抛 `RuntimeError`（生产加固），SMTP 密码操作会抛 `ImportError`。安装方式：`pip install cryptography`。

---

## 3. 基于角色的访问控制（RBAC）

### 3.1 四个内置角色

| 角色 | 权限数 | 用途 |
|------|--------|------|
| **admin** | 19（全部） | 全权管理员——唯一可注册机器、管理 API Key 与系统配置的角色。 |
| **manager** | 11 | 团队管理员——查看并导出分析、消息、审计日志与配额；无用户管理与系统配置权限。 |
| **user** | 4 | 普通员工——查看仪表盘、自己的消息、分析与自己的配额。 |
| **readonly** | 1 | 仅查看仪表盘。 |

> `admin_access` 权限是**超级 bypass**：任何包含它的角色或自定义授权都会自动通过全部权限检查（见 `Role.has_permission`）。

### 3.2 19 种权限

| 权限 | admin | manager | user | readonly |
|------|:-----:|:-------:|:----:|:--------:|
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

### 3.3 自定义与按用户授权

除内置角色外，管理员可通过 `user_permissions` 表向单个用户授予权限。用户有效权限 = 角色权限 ∪ 自定义授权：

```python
PermissionService.grant_permission(user_id, "export_analysis", granted_by=admin_id)
PermissionService.has_permission(user_id, "export_analysis")  # → True
```

也可创建自定义角色并存入 `role_permissions` 表。

### 3.4 多租户隔离

启用多租户后：

- 每个用户通过 `tenant_id` 关联到租户。
- API Key、机器、会话均按租户隔离。
- 租户级配额强制 Token 与请求上限。
- `QuotaEnforcementScheduler` 每 60 秒运行一次，超额用户的会话将被终止并触发告警。

---

## 4. 认证

### 4.1 密码哈希

用户密码用 **bcrypt（12 轮）** 哈希（`bcrypt.gensalt(rounds=12)`），验证使用 `bcrypt.checkpw`。明文密码从不记录或存储。

### 4.2 会话令牌

登录成功后服务端：

1. 校验凭据（bcrypt）。
2. 生成 256 位随机会话令牌（`secrets.token_hex(32)`）。
3. 写入带过期时间的会话记录（默认 24 小时，可通过 `security_settings.session_timeout` 配置）。
4. 返回令牌，浏览器以 `HttpOnly`、`SameSite=Lax` 的 `session_token` cookie 保存。

每次请求按优先级提取令牌：

1. **Cookie** —— `session_token`（HttpOnly、SameSite=Lax；HTTPS 下带 `Secure`）。
2. **Authorization 头** —— `Bearer <token>`。
3. **查询参数** —— `?token=<token>`（用于 WebSocket / 下载 URL）。

### 4.3 登录锁定（防爆破）

登录失败记录在 `login_attempts` 表：

| 设置 | 默认值 | 来源 |
|------|--------|------|
| `max_login_attempts` | 5 | `security_settings` |
| `lockout_duration_minutes` | 15 | `security_settings` |
| 设置缓存 TTL | 60 秒 | 内存缓存 |

达到阈值后账户锁定至 `lockout_duration_minutes` 结束；登录成功会清零计数。锁定检查优雅降级：数据库不可用时允许登录（不锁定），避免全员被锁。

### 4.4 默认凭据与首次登录强制

种子脚本（`scripts/init_db.py`）创建唯一的 `admin/admin123`，且 `must_change_password = True`，首次登录强制改密，使广为人知的默认凭据无法被长期复用。生产环境请设置强 `SECRET_KEY`、立即改密，并优先用非默认密码创建管理员。

---

## 5. 认证装饰器框架

所有路由保护都走 `app/auth/decorators.py` 的统一框架，替代了过去分散的鉴权实现。

### 5.1 `@auth_required`

要求有效会话，可选地强制归属：

- `ownership='session'` —— 调用者必须是会话所有者（或管理员）。
- `ownership='machine'` —— 调用者必须是系统管理员或机器管理员。

成功后为处理器设置 `g.user`、`g.user_id`、`g.user_role`。

```python
@auth_required
def api_view():
    user = g.user

@auth_required(ownership='session')
def session_view(session_id):
    # 仅会话所有者或管理员可继续。
```

### 5.2 `@admin_required`

要求 `admin` 角色，否则返回 `403`。

### 5.3 `@public_endpoint`

显式标记路由为有意免鉴权（如 `/health`、`/api/auth/login`）。API 安全扫描器依据 `_is_public_endpoint` 标记区分“有意公开”与“意外未保护”，而不再依赖硬编码列表。

### 5.4 路由级规则

| 路由前缀 | 保护方式 |
|----------|----------|
| `/manage/*` | 要求管理员角色（普通用户与机器管理员不可访问）。 |
| `/api/*`（多数） | 蓝图级 `before_request` 调用 `@auth_required`；敏感操作叠加 `@admin_required`。 |
| `/`、`/api/auth/login`、`/api/auth/check`、`/health` | 公开。 |

---

## 6. 远程机器安全

这是 Open ACE 最敏感的部分，因为远程机器代表用户运行 shell 与 AI CLI。设计目标很明确：**真实 API Key 永不传送到、也永不存储在远程机器上。**

### 6.1 用一次性令牌注册机器

```
┌─────────────┐  管理员生成令牌            ┌─────────────┐
│   Admin      │ ──────────────────────→  │   数据库      │  存 SHA-256(token)
│  (浏览器)    │                          └─────────────┘
└─────────────┘                                  │
       │ 带外分享明文令牌                          │
       ↓                                          │
┌─────────────┐  POST /api/remote/register        │
│ Remote Agent │ ──────────────────────────────────┘
│ (远程)       │      服务端标记令牌已消费
└─────────────┘
```

- 管理员生成 **256 位随机**注册令牌（`secrets.token_hex(32)`）。
- 数据库只存其 **SHA-256 哈希**；明文只返回一次，无法再次获取。
- 令牌**一次性使用**：`_consume_registration_token` 在同一事务内原子地检查过期与 `is_consumed`，然后标记已消费，重放即被拒。
- 默认 TTL 为 **1 小时**（`REGISTRATION_TOKEN_TTL = 3600`）。
- 注册、注销与令牌生成仅限**系统管理员**。

### 6.2 代理令牌模型

机器注册、用户启动会话后，服务端签发**代理令牌**而非真实 API Key：

| 属性 | 取值 |
|------|------|
| 格式 | `<base64url(payload)>.<hex签名>` |
| 签名 | 用加密密钥做 HMAC-SHA256 |
| Payload | `user_id`、`session_id`、`tenant_id`、`provider`、`session_type`、`exp`、`jti`，可选 HA 元数据 |
| 有效期 | 随调用点不同——agent、终端、webui 会话默认 **24 小时**（1440 分钟）；HA-pool 令牌用 **15 分钟**。 |
| 校验 | 常数时间签名比较（`hmac.compare_digest`）+ 过期检查 +（agent 会话）活跃会话检查 |

签名不符、`exp` 已过或后端会话不再 `active`/`paused` 的令牌都会被拒。

### 6.3 LLM 代理流程

远程 CLI 调用模型时：

1. CLI 以 `Authorization: Bearer <proxy_token>` 请求 `/api/remote/llm-proxy`。
2. 服务端校验代理令牌的 HMAC-SHA256 签名与过期。
3. 从 `api_key_store` 解密真实 API Key。
4. 用真实 Key 替换 Authorization 头并转发给 LLM 供应商。
5. 响应流式回传；解析 Token 用量并计入配额/计费。

**API Key 永不传送到、也永不落盘到远程机器。** 代理 URL 中的路径穿越尝试（路径中的 `..`）会返回 HTTP 400。

### 6.4 机器访问控制

`machine_assignments` 表控制哪些用户可用哪些机器，`permission` 字段为 `user` 或 `admin`。机器管理员可在自己的机器上委派用户与会话管理。用户只能访问自己的会话；系统管理员与机器管理员可查看或停止其机器上其他用户的会话。

### 6.5 发送前剥离

服务端为 Agent 构建 CLI 配置（如 Qwen Code 的 `settings.json`）时，会在发送前剥离所有敏感字段：

- 静态凭据环境变量：`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`。
- `modelProviders` 下声明的动态 `envKey` 名。
- `modelProviders` 条目内的 `baseUrl`。

凭据仅在进程启动时以环境变量注入，绝不持久化到 Agent 配置文件。

---

## 7. 内容过滤与合规

面向企业部署，内容过滤模块（`app/modules/governance/content_filter.py`）检查消息中的敏感数据：

- **可检测类型**：PII（邮箱、电话、SSN、信用卡、地址、护照、驾照）、敏感关键词、脏话与自定义正则。
- **风险等级**：`low`、`medium`、`high`、`critical`。
- **输出**：每个 `FilterResult` 给出是否通过、命中规则、脱敏后内容与建议。
- **治理**：结果进入审计日志与合规报告；访问受 `view_content_filter` / `manage_content_filter` 权限约束。

---

## 8. 传输安全

| 关注点 | 行为 |
|--------|------|
| 浏览器 ↔ 服务端 | 在 cookie 上设置 `Secure`，并在反向代理终结 TLS（见 [NGINX 指南](NGINX.md)）。`app/__init__.py` 在生产环境未设 `SECRET_KEY` 时拒绝启动。 |
| 服务端 ↔ LLM 供应商 | 出站走 HTTPS。 |
| 服务端 ↔ 远程 Agent | Agent 通过 WebSocket（REST 用 HTTPS）连接。**`skip_ssl_verify` 默认为 `true`** 以便本地开发；任何使用真实 TLS 证书的部署都应设置 `OPENACE_SKIP_SSL_VERIFY=false`（或在 Agent 配置中 `skip_ssl_verify: false`）。 |

> ⚠️ **开发默认值**：`skip_ssl_verify: true` 仅为让首跑演示兼容自签证书。一旦具备有效证书必须关闭，否则 Agent 会信任任意被出示的证书。

---

## 9. 生产加固清单

在把 Open ACE 暴露给单个受信开发者之外前：

- [ ] 设置强且唯一的 `SECRET_KEY`（推荐再单独设置 `OPENACE_ENCRYPTION_KEY`）。
- [ ] 修改默认 `admin/admin123` 密码（种子已通过 `must_change_password` 强制，但请在部署时自设）。
- [ ] 安装 `cryptography` 包以启用 Fernet 加密。
- [ ] 在每台远程 Agent 上关闭 `skip_ssl_verify`，并用有效证书终结 TLS。
- [ ] 在 `security_settings` 中按策略配置 `max_login_attempts` 与 `lockout_duration_minutes`。
- [ ] 给每个用户授予最小权限角色；`admin` 仅留给运维。
- [ ] 启用按租户配额并确认 `QuotaEnforcementScheduler` 在运行。
- [ ] 若合规要求 PII 脱敏，启用内容过滤器。
- [ ] 将 Open ACE 放在反向代理后（见 [NGINX 指南](NGINX.md)），并把 `/manage/*` 限制在运维网络。

---

## 10. 漏洞上报

请**不要**通过公开 GitHub Issue 上报安全漏洞。请使用 GitHub Security Advisories 或直接邮件联系维护者——完整披露策略与响应时效见 [`../.github/SECURITY.md`](../../.github/SECURITY.md)。

---

## 延伸阅读

- [权限模型](PERMISSION-MODEL.md)——RBAC、装饰器与令牌提取的深入说明。
- [远程工作区](REMOTE-WORKSPACE.md)——服务端远程机器管理与 LLM 代理流程。
- [远程 Agent](REMOTE-AGENT.md)——Agent 安装、配置与 `skip_ssl_verify`。
- [部署指南](DEPLOYMENT.md)与 [NGINX](NGINX.md)——TLS、密钥与反向代理。
- [10 分钟演示](DEMO-10-MINUTES.md)——端到端体验本安全模型的演练。

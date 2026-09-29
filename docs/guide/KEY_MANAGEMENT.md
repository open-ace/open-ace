# Key Management — 密钥管理

[English](#english) | [中文](#中文)

---

## English

> Encryption key derivation, rotation, environment secret responsibilities, and security best practices for Open-ACE.

## Table of Contents

- [Overview](#overview)
- [Environment Secret Responsibility Matrix](#environment-secret-responsibility-matrix)
- [Key Derivation](#key-derivation)
- [Key Sharing Impact](#key-sharing-impact)
- [Key Rotation](#key-rotation)
- [The OPENACE_ENCRYPTION_KEYS Data-Key Registry](#the-openace_encryption_keys-data-key-registry)
- [Security Best Practices](#security-best-practices)
- [Database Schema](#database-schema)
- [Troubleshooting](#troubleshooting)
- [Related Documentation](#related-documentation)

## Overview

Open-ACE uses Fernet symmetric encryption to protect sensitive data at rest. The same key directly encrypts **all 8 stores** (authoritative list in [Key Sharing Impact](#key-sharing-impact) below):

- API keys for remote workspaces (`api_key_store` table)
- SMTP passwords (`smtp_settings` table)
- Model Gateway API keys (`model_gateway_config` table)
- SSO provider credentials (`sso_providers` table)
- DingTalk integration (`dingtalk_settings` table)
- Feishu integration (`feishu_settings` table)
- Webhook configs (`webhook_settings` table)
- Notification preferences (`notification_preferences` table)

Proxy tokens use HMAC-SHA256 signatures (not Fernet) for authentication.

## Environment Secret Responsibility Matrix

The 13 variables defined in `.env.example` split into secrets, gates, and plain configuration. Full details (defaults, sources) are in [ENV_REFERENCE.md](ENV_REFERENCE.md).

| Variable | Responsibility | Production | Rotation impact |
|----------|----------------|-------------|-----------------|
| `SECRET_KEY` | Flask session signing | Required, >= 32 chars | All logged-in sessions invalidated (everyone logged out); no data loss |
| `OPENACE_ENCRYPTION_KEY` | Encrypts all 8 secret stores; signs proxy tokens | Required, >= 32 chars | Old-key ciphertext becomes permanently undecryptable unless re-encrypted first (see [Key Rotation](#key-rotation)); active proxy tokens invalidated |
| `UPLOAD_AUTH_KEY` | Authenticates workspace file-upload endpoints | Recommended | Upload clients rejected until updated; empty value disables the endpoint |
| `DB_PASSWORD` | PostgreSQL authentication | Required, strong (>= 9 chars) | Change in `.env` AND in PostgreSQL, then restart; mismatch = connection failures |
| `OPENACE_SECURITY_MODE` | Not a secret: gates which secrets are mandatory | `production` | Switching modes changes startup strictness, not stored data |
| `SSO_ALLOWED_REDIRECT_DOMAINS` | Not a secret: SSO redirect allowlist | Required for non-localhost SSO | Changing the list changes where SSO logins may land |
| `DB_USER`, `DB_NAME` | Database identity | `ace` / `ace` | Not routine rotation; requires re-provisioning |
| `PORT`, `SERVER_IP`, `WORKSPACE_PORT_RANGE_START/END` | Network addressing | — | Changes URLs/bookmarks; no secret impact |
| `WORKSPACE_ISOLATION_BACKEND` | Workspace isolation backend switch (Issue #3446; replaces `WORKSPACE_MULTI_USER_MODE`) | — | Changes security posture, not secrets |

## Key Derivation

The encryption key is derived from the `OPENACE_ENCRYPTION_KEY` environment variable:

```
OPENACE_ENCRYPTION_KEY (env var, >= 32 chars)
         │
         │ SHA-256 hash
         ▼
    32-byte key
         │
         │ base64.urlsafe_b64encode
         ▼
    Fernet key (44 chars)
         │
         ├──────────────────────────────┐
         ▼                              ▼
   ALL 8 encrypted stores           Proxy Token
   (api_key_store / smtp_settings /   signing (HMAC-SHA256,
    model_gateway_config /             not Fernet)
    sso_providers / dingtalk_settings /
    feishu_settings / webhook_settings /
    notification_preferences —
    full list in "Key Sharing Impact")

```

**Key derivation code**:

```python
import hashlib
import base64

key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
derived_key = hashlib.sha256(key_env.encode()).digest()
fernet_key = base64.urlsafe_b64encode(derived_key)
```

## Key Sharing Impact

The same key (`OPENACE_ENCRYPTION_KEY`, SHA-256-derived Fernet key) directly encrypts every store below:

1. **API Key encryption** - `api_key_store.encrypted_key`
2. **SMTP password encryption** - `smtp_settings.encrypted_password`
3. **Model Gateway encryption** - `model_gateway_config.encrypted_api_key`
4. **SSO provider configs** - `sso_providers` (third-party credentials embedded in JSON)
5. **DingTalk integration** - `dingtalk_settings`
6. **Feishu integration** - `feishu_settings`
7. **Webhook configs** - `webhook_settings`
8. **Notification preferences** - `notification_preferences`

The same key also signs **Proxy Tokens** (HMAC-SHA256, remote agent authentication).

**Implications**:

- Rotation must re-encrypt **ALL of the stores above in one pass** — rotating only a subset leaves the remaining ciphertexts undecryptable after the key switch (this is exactly why `scripts/rotate_sso_encryption.py` rotates every store in a single transaction)
- Active proxy tokens signed with the old key will fail validation after rotation
- Key compromise affects all of the security domains above

## Key Rotation

### Rotation Capabilities and Boundaries

- **Atomic full-store rotation**: `scripts/rotate_sso_encryption.py` re-encrypts EVERY store bound to this key in a single transaction (full rollback on any failure); `--verify` provides a no-write pre-flight
- **Single-key Fernet**: No MultiFernet multi-key decryption (see "MultiFernet Support (Future enhancement)" below)
- **Restart on switch**: the service must be restarted after the environment variable switches to the new key

### Rotation Method

#### Atomic full-store rotation (`rotate_sso_encryption.py`)

**Prerequisites**:

- Database backup capability
- Planned maintenance window
- Root access to environment variables

**Steps**:

1. **Backup the database**

   ```bash
   # PostgreSQL
   pg_dump openace > openace_backup_$(date +%Y%m%d).sql

   # SQLite
   cp app.db app_backup_$(date +%Y%m%d).db
   ```

2. **Generate a new key (do NOT enable it yet)**

   ```bash
   NEW_KEY=$(openssl rand -hex 32)
   ```

3. **Stop every reader/writer that uses the old key** (app, scheduler/workers; the database itself stays up). This is a PRECONDITION of the script's safety, not an option: the script takes no table locks; each UPDATE is only optimistic on the value it scanned (a concurrent MODIFICATION of a scanned row → 0/2 affected rows → full rollback), but rows INSERTED after a table's scan are invisible inside the transaction, and the post-check can only REPORT committed mixed-key data, never roll it back — old-key ciphertext WILL remain if writers stay up.

4. **Keep the environment on the OLD key and run the pre-flight dry run**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY" --verify
   ```

   The pre-flight round-trips a probe with the new key and verifies every store decrypts under the old key; nothing is written on failure. The environment must stay on the OLD key for the whole rotation — switching early makes the old ciphertexts undecryptable and fails the pre-flight.

5. **Run the rotation (single transaction, per-UPDATE row-count validation; a concurrent modification rolls the whole rotation back)**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY"
   ```

   Every store the key directly protects is re-encrypted (registry-format ciphertexts prefixed `v1k<id>:` are bound to the `OPENACE_ENCRYPTION_KEYS` data keys and are skipped automatically), and the script re-verifies all stores with the new key afterwards.

6. **Switch the environment to the new key and restart ALL services**

   ```bash
   # Docker Compose: edit .env
   # Kubernetes: update Secret
   # Systemd: edit /etc/open-ace/environment
   docker compose restart   # or: sudo systemctl restart open-ace
   ```

7. **Verify functionality**: API keys, SMTP, Model Gateway, SSO login, DingTalk/Feishu/webhooks/notifications. Existing proxy tokens are invalidated (users must restart sessions).

8. **Secure cleanup**: archive or remove the database backup after verification.

#### MultiFernet Support (Future enhancement)

**Requirements**:

- Code modification to support `MultiFernet`
- Environment variable format: `KEY1;KEY2` (primary;fallback)
- Zero-downtime rotation capability

**Implementation needed**:

- Modify `_get_encryption_key()` to return key list
- Use `MultiFernet([key1, key2])` for decryption
- Use primary key for new encryption
- Gradual migration path

## The OPENACE_ENCRYPTION_KEYS Data-Key Registry

Besides the single `OPENACE_ENCRYPTION_KEY`, Open-ACE supports a versioned data-key registry read from the `OPENACE_ENCRYPTION_KEYS` environment variable (`app/utils/encryption_key_registry.py`):

```json
{
  "keys": [
    {"id": 1, "value": "<old-key-material>", "status": "deprecated"},
    {"id": 2, "value": "<new-key-material>", "status": "active"}
  ],
  "primary_key_id": 2
}
```

Rules enforced at load time:

- `id` must be an integer between 1 and 255, unique; at most **5** keys total
- Exactly **one** key may be `active`, and `primary_key_id` must point at it; other keys are `deprecated` (decrypt-only) or `revoked` (emergency decrypt-only)
- Each key value is SHA-256-derived exactly like `OPENACE_ENCRYPTION_KEY`
- When `OPENACE_ENCRYPTION_KEYS` is set it takes precedence over the single-key format; the legacy single key is registered as `key_id=0`
- The registry hot-reloads: environment changes are picked up lazily, checked roughly every 5 seconds
- A config hash/version is computed over the key set so multiple replicas can detect divergence

Ciphertexts produced through the registry carry a key-id prefix (`v1k<key_id>:<fernet_ciphertext>`), which is what allows old keys to keep decrypting old data while new writes use the primary key. `scripts/rotate_sso_encryption.py` skips these registry-bound ciphertexts automatically.

### Syncing key metadata into the database

`scripts/migrate_encryption_keys_to_db.py` copies key metadata (fingerprints, statuses — never plaintext values) from the environment into the database for the management UI:

```bash
# Preview (dry-run)
python scripts/migrate_encryption_keys_to_db.py --dry-run

# Execute
python scripts/migrate_encryption_keys_to_db.py --execute
```

### Management API (platform admin only)

Endpoints implemented in `app/routes/encryption_keys.py`:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/encryption-keys` | List all key metadata |
| `POST /api/encryption-keys/validate` | Validate a key's format, return its fingerprint |
| `POST /api/encryption-keys/rotate` | Rotate the data key (body: `{"confirmation": "ROTATE"}`, optional `expected_version` optimistic lock) |
| `POST /api/encryption-keys/generate-env-config` | Generate the `OPENACE_ENCRYPTION_KEYS` value for external systems |
| `GET /api/encryption-keys/audit-log` | Query key-operation audit log (`rotate`, `re-encrypt` filterable) |
| `GET /api/encryption-keys/sync-status` | Compare config versions across replicas (uses `OPENACE_REPLICA_ENDPOINTS`) |
| `POST /api/encryption-keys/re-encrypt/pre-check` | Scan stored ciphertexts (SSO providers, API keys): counts `v1k`-prefixed vs legacy formats, tests decryption |
| `POST /api/encryption-keys/re-encrypt` | Re-encrypt legacy ciphertexts through the registry (body: `{"confirmation": "RE-ENCRYPT"}`) |

## Security Best Practices

### Key Generation

```bash
# Generate a strong random key (256 bits = 32 bytes = 64 hex chars)
openssl rand -hex 32
```

### Key Storage

- **Never commit to source control**
- Use environment variables or secrets management:
  - Docker Compose: `.env` file (add to `.gitignore`)
  - Kubernetes: Secrets resource
  - Cloud: AWS Secrets Manager, Azure Key Vault, GCP Secret Manager

### Key Rotation Schedule

- **Recommended**: Every 90 days
- **Required**: Immediately after suspected compromise
- **Document**: Maintain rotation log with timestamps

### Key Compromise Response

1. Generate the new key but do **not** enable it yet; back up the affected stores and pause writes to them
2. Revoke all active proxy tokens (if applicable)
3. Keep `OPENACE_ENCRYPTION_KEY` set to the **old** key and rotate all encrypted credentials: `scripts/rotate_sso_encryption.py --new-key <NEW_KEY>` re-encrypts every store protected by the key in a **single transaction** (`sso_providers`, `api_key_store`, `smtp_settings`, `model_gateway_config`, `dingtalk_settings`, `feishu_settings`, `webhook_settings`, `notification_preferences`; `v1k<id>:`-prefixed key-registry ciphertexts are bound to the `OPENACE_ENCRYPTION_KEYS` data keys instead and are skipped automatically). Run the `--verify` pre-flight first. The script reads the environment variable as the old key and `--new-key` as the new one — switching the environment variable before rotating makes the old ciphertext undecryptable and fails the pre-flight
4. Only after the rotation completes (the script re-checks every store with the new key after writing) and no other store sharing this key remains, switch `OPENACE_ENCRYPTION_KEY` to the new key and restart the service
5. Audit access logs for suspicious activity; document incident and remediation steps

## Database Schema

### Encryption Version Field

Tables with encrypted data include an `encryption_version` field:

- `api_key_store.encryption_version` (default: 1)
- `smtp_settings.encryption_version` (default: 1)
- `model_gateway_config.encryption_version` (default: 1)

**Version mapping**:

| Version | Algorithm | Notes |
|---------|-----------|-------|
| 1 | Fernet (AES-128-CBC + HMAC-SHA256) | Current |
| 2+ | Reserved for future algorithms | e.g., AES-256-GCM |

Future algorithm upgrades will:

1. Support reading version 1 data
2. Write new data with version 2
3. Provide migration scripts for gradual transition

## Troubleshooting

### "Invalid Fernet key" errors

- Verify `OPENACE_ENCRYPTION_KEY` is set
- Check key format (should be hex or base64, >= 32 chars)
- Ensure no whitespace or newlines in the value

### Decryption failures after rotation

- Confirm you're using the correct key for the data's encryption version
- Check if data was encrypted with a different key
- Restore from backup if key is lost

### Proxy tokens invalid after rotation

- Expected behavior: tokens signed with old key
- Users need to restart remote sessions
- No action needed if sessions are short-lived

## Related Documentation

- [ENV_REFERENCE.md](ENV_REFERENCE.md) — environment variable table with rotation impact
- [UPGRADING.md](UPGRADING.md) — keeping keys compatible across upgrades
- [DEPLOYMENT.md](DEPLOYMENT.md) — production deployment guide
- [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md) — feature overview
- [Security Policy](https://github.com/open-ace/open-ace/security/policy) — security reporting and policy details

---

## 中文

> Open-ACE 的加密密钥派生、轮换、环境密钥职责与安全最佳实践。

## 目录

- [概述](#概述)
- [环境密钥职责矩阵](#环境密钥职责矩阵)
- [密钥派生](#密钥派生)
- [密钥共享影响面](#密钥共享影响面)
- [密钥轮换](#密钥轮换)
- [OPENACE_ENCRYPTION_KEYS 数据密钥注册表](#openace_encryption_keys-数据密钥注册表)
- [安全最佳实践](#安全最佳实践)
- [数据库 Schema](#数据库-schema)
- [故障排查](#故障排查)
- [相关文档](#相关文档)

## 概述

Open-ACE 使用 Fernet 对称加密保护静态敏感数据。同一密钥直接加密**全部 8 类存储**（权威完整清单见下文[密钥共享影响面](#密钥共享影响面)）：

- 远程工作区的 API Key（`api_key_store` 表）
- SMTP 密码（`smtp_settings` 表）
- Model Gateway API Key（`model_gateway_config` 表）
- SSO Provider 凭据（`sso_providers` 表）
- 钉钉集成（`dingtalk_settings` 表）
- 飞书集成（`feishu_settings` 表）
- Webhook 配置（`webhook_settings` 表）
- 通知偏好（`notification_preferences` 表）

Proxy Token 使用 HMAC-SHA256 签名（非 Fernet）进行认证。

## 环境密钥职责矩阵

`.env.example` 定义的 13 个变量分为密钥、门槛开关和普通配置三类。完整细节（默认值、出处）见 [ENV_REFERENCE.md](ENV_REFERENCE.md)。

| 变量 | 职责 | 生产环境 | 轮转影响 |
|------|------|----------|----------|
| `SECRET_KEY` | Flask 会话签名 | 必需，>= 32 字符 | 全员下线（所有已登录会话失效）；无数据丢失 |
| `OPENACE_ENCRYPTION_KEY` | 加密全部 8 类密文存储；签发 proxy token | 必需，>= 32 字符 | 不先重加密则旧密文永久无法解密（见[密钥轮换](#密钥轮换)）；活跃 proxy token 失效 |
| `UPLOAD_AUTH_KEY` | 工作区文件上传接口鉴权 | 建议设置 | 上传客户端需换新密钥，否则被拒；留空则禁用该接口 |
| `DB_PASSWORD` | PostgreSQL 认证 | 必需强密码（>= 9 字符） | 需同时改 `.env` 和 PostgreSQL 本体并重启；不一致 = 连接失败 |
| `OPENACE_SECURITY_MODE` | 非密钥：决定哪些密钥是强制项 | `production` | 切换只改变启动严格度，不影响已存数据 |
| `SSO_ALLOWED_REDIRECT_DOMAINS` | 非密钥：SSO 重定向白名单 | 非 localhost SSO 必需 | 修改名单即改变 SSO 可跳转范围 |
| `DB_USER`、`DB_NAME` | 数据库身份 | `ace` / `ace` | 非例行轮转；需重建 |
| `PORT`、`SERVER_IP`、`WORKSPACE_PORT_RANGE_START/END` | 网络寻址 | — | 改变 URL/书签；与密钥无关 |
| `WORKSPACE_ISOLATION_BACKEND` | 工作区隔离 backend 开关（Issue #3446；取代 `WORKSPACE_MULTI_USER_MODE`） | — | 改变安全形态，不涉及密钥 |

## 密钥派生

加密密钥从 `OPENACE_ENCRYPTION_KEY` 环境变量派生：

```
OPENACE_ENCRYPTION_KEY (环境变量，>= 32 字符)
         │
         │ SHA-256 哈希
         ▼
    32 字节密钥
         │
         │ base64.urlsafe_b64encode
         ▼
    Fernet 密钥 (44 字符)
         │
         ├──────────────────────────────┐
         ▼                              ▼
   ALL 8 encrypted stores           Proxy Token
   (api_key_store / smtp_settings /   signing (HMAC-SHA256,
    model_gateway_config /             not Fernet)
    sso_providers / dingtalk_settings /
    feishu_settings / webhook_settings /
    notification_preferences —
    full list in "Key Sharing Impact")

```

**密钥派生代码**：

```python
import hashlib
import base64

key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
derived_key = hashlib.sha256(key_env.encode()).digest()
fernet_key = base64.urlsafe_b64encode(derived_key)
```

## 密钥共享影响面

同一密钥（`OPENACE_ENCRYPTION_KEY`，SHA-256 派生 Fernet 密钥）直接加密以下存储：

1. **API Key 加密** - `api_key_store.encrypted_key`
2. **SMTP 密码加密** - `smtp_settings.encrypted_password`
3. **Model Gateway 加密** - `model_gateway_config.encrypted_api_key`
4. **SSO Provider 配置** - `sso_providers`（JSON 内嵌第三方凭据）
5. **钉钉集成** - `dingtalk_settings`
6. **飞书集成** - `feishu_settings`
7. **Webhook 配置** - `webhook_settings`
8. **通知偏好** - `notification_preferences`

同一密钥还用于 **Proxy Token 的 HMAC-SHA256 签名**（远程代理认证）。

**影响**：

- 密钥轮换必须**一次性重新加密上述全部存储**——只轮换其中一部分，切换密钥后其余存储的凭据将无法解密（这正是 `scripts/rotate_sso_encryption.py` 单事务全存储轮换的设计动机）
- 使用旧密钥签名的活跃 Proxy Token 轮换后验证失败
- 密钥泄露影响上述全部安全域

## 密钥轮换

### 轮换能力与边界

- **原子全存储轮换**：`scripts/rotate_sso_encryption.py` 在单事务内重加密该密钥保护的**全部**存储，任一步失败整体回滚；`--verify` 提供无写入的 pre-flight 干跑
- **单密钥 Fernet**：不支持 MultiFernet 多密钥解密（见下方"MultiFernet 支持（未来增强）"）
- **切换需重启**：环境变量换成新密钥后需重启服务生效

### 轮换方法

#### 全存储原子轮换（`rotate_sso_encryption.py`）

**前提条件**：

- 数据库备份能力
- 计划维护窗口
- 环境变量的 root 访问权限

**步骤**：

1. **备份数据库**

   ```bash
   # PostgreSQL
   pg_dump openace > openace_backup_$(date +%Y%m%d).sql

   # SQLite
   cp app.db app_backup_$(date +%Y%m%d).db
   ```

2. **生成新密钥（暂不启用）**

   ```bash
   NEW_KEY=$(openssl rand -hex 32)
   ```

3. **停止所有使用旧钥的读写者**（应用、scheduler/worker 等；数据库保持运行）。这是脚本安全性的**前提**而非可选项：脚本不持表锁，每条 UPDATE 仅按扫描到的旧值做乐观校验（并发**修改**已扫描行 → 影响 0/2 行 → 整体回滚），但扫描之后新**插入**的旧钥行在事务内不可见，postcheck 也只能**报告**已提交的混钥数据而无法回滚——不停写者必然残留旧钥密文。

4. **保持环境变量仍为旧密钥，先做 pre-flight 干跑**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY" --verify
   ```

   pre-flight 用新钥做往返探针、并用旧钥校验全部存储可解密；任何一步失败都不写入。轮换期间环境变量必须保持**旧密钥**——提前切成新钥会导致旧密文无法解密、pre-flight 失败。

5. **执行轮换（单事务，失败整体回滚；逐条 UPDATE 行数校验，并发修改触发整体回滚）**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY"
   ```

   脚本重加密上述清单中该密钥直接保护的全部存储（`v1k<id>:` 前缀的 registry 密文绑定 `OPENACE_ENCRYPTION_KEYS` 数据钥，自动跳过），写出后用新钥复查全部存储。

6. **切换环境变量到新密钥并统一重启全部服务**

   ```bash
   # Docker Compose: 编辑 .env 文件
   # Kubernetes: 更新 Secret
   # Systemd: 编辑 /etc/open-ace/environment
   docker compose restart   # 或 sudo systemctl restart open-ace
   ```

7. **验证功能**：测试 API Key、SMTP、Model Gateway、SSO 登录、钉钉/飞书/Webhook/通知；现有 Proxy Token 将失效（用户需重启会话）。

8. **安全清理**：验证后归档或删除数据库备份。

#### MultiFernet 支持（未来增强）

**需求**：

- 代码修改支持 `MultiFernet`
- 环境变量格式：`KEY1;KEY2`（主密钥;备用密钥）
- 零停机轮换能力

**需要实现**：

- 修改 `_get_encryption_key()` 返回密钥列表
- 使用 `MultiFernet([key1, key2])` 解密
- 新加密使用主密钥
- 渐进式迁移路径

## OPENACE_ENCRYPTION_KEYS 数据密钥注册表

除单一 `OPENACE_ENCRYPTION_KEY` 外，Open-ACE 还支持从 `OPENACE_ENCRYPTION_KEYS` 环境变量读取带版本的数据密钥注册表（`app/utils/encryption_key_registry.py`）：

```json
{
  "keys": [
    {"id": 1, "value": "<旧密钥材料>", "status": "deprecated"},
    {"id": 2, "value": "<新密钥材料>", "status": "active"}
  ],
  "primary_key_id": 2
}
```

加载时强制校验的规则：

- `id` 为 1-255 的整数且唯一；总计最多 **5** 把密钥
- 恰好**一把** `active`，且 `primary_key_id` 必须指向它；其余为 `deprecated`（仅解密）或 `revoked`（应急解密）
- 每个密钥值的派生方式与 `OPENACE_ENCRYPTION_KEY` 完全一致（SHA-256）
- 设置了 `OPENACE_ENCRYPTION_KEYS` 时优先于单密钥格式；旧单密钥注册为 `key_id=0`
- 注册表支持热加载：环境变量变更按约 5 秒的节奏惰性感知
- 会对密钥集合计算配置哈希/版本，供多副本检测分歧

经注册表产生的密文带密钥 ID 前缀（`v1k<key_id>:<fernet_ciphertext>`），因此旧密钥可继续解密旧数据、新写入使用主密钥。`scripts/rotate_sso_encryption.py` 会自动跳过这类绑定注册表的密文。

### 将密钥元数据同步进数据库

`scripts/migrate_encryption_keys_to_db.py` 把密钥元数据（指纹、状态——绝不含明文）从环境变量同步进数据库，供管理界面使用：

```bash
# 预览（dry-run）
python scripts/migrate_encryption_keys_to_db.py --dry-run

# 执行迁移
python scripts/migrate_encryption_keys_to_db.py --execute
```

### 管理 API（仅 platform admin）

端点实现位于 `app/routes/encryption_keys.py`：

| 端点 | 用途 |
|------|------|
| `GET /api/encryption-keys` | 列出全部密钥元数据 |
| `POST /api/encryption-keys/validate` | 校验密钥格式，返回指纹 |
| `POST /api/encryption-keys/rotate` | 轮换数据密钥（请求体 `{"confirmation": "ROTATE"}`，可选 `expected_version` 乐观锁） |
| `POST /api/encryption-keys/generate-env-config` | 生成供外部系统使用的 `OPENACE_ENCRYPTION_KEYS` 值 |
| `GET /api/encryption-keys/audit-log` | 查询密钥操作审计日志（可按 `rotate`、`re-encrypt` 过滤） |
| `GET /api/encryption-keys/sync-status` | 比较多副本的配置版本（使用 `OPENACE_REPLICA_ENDPOINTS`） |
| `POST /api/encryption-keys/re-encrypt/pre-check` | 扫描存量密文（SSO Provider、API Key）：统计 `v1k` 前缀与 legacy 格式数量，并测试解密 |
| `POST /api/encryption-keys/re-encrypt` | 经注册表重加密 legacy 密文（请求体 `{"confirmation": "RE-ENCRYPT"}`） |

## 安全最佳实践

### 密钥生成

```bash
# 生成强随机密钥（256 位 = 32 字节 = 64 个十六进制字符）
openssl rand -hex 32
```

### 密钥存储

- **永不提交到源代码管理**
- 使用环境变量或密钥管理：
  - Docker Compose: `.env` 文件（添加到 `.gitignore`）
  - Kubernetes: Secret 资源
  - 云平台: AWS Secrets Manager、Azure Key Vault、GCP Secret Manager

### 密钥轮换周期

- **推荐**：每 90 天
- **必须**：疑似泄露后立即轮换
- **文档**：维护带时间戳的轮换日志

### 密钥泄露响应

1. 生成新密钥但**暂不启用**；备份受影响的存储并暂停相关写入
2. 撤销所有活跃 Proxy Token（如适用）
3. 保持 `OPENACE_ENCRYPTION_KEY` 仍为**旧密钥**，轮换所有加密凭据：`scripts/rotate_sso_encryption.py --new-key <NEW_KEY>` 会在**单事务**内重加密该密钥保护的全部存储（`sso_providers`、`api_key_store`、`smtp_settings`、`model_gateway_config`、`dingtalk_settings`、`feishu_settings`、`webhook_settings`、`notification_preferences`；`v1k<id>:` 前缀的 registry 格式密文绑定的是 `OPENACE_ENCRYPTION_KEYS` 数据钥，不受影响、自动跳过）。先以 `--verify` 做 pre-flight 干跑。脚本以环境变量为旧钥、`--new-key` 为新钥——若在轮换前就把环境变量切成新钥，旧密文将无法解密，pre-flight 会失败
4. 轮换完成（脚本会在写出后用新钥复查全部存储）且确认无其它共享该密钥的存储后，才把 `OPENACE_ENCRYPTION_KEY` 切换为新密钥并重启服务
5. 审计访问日志查找可疑活动，记录事件和修复步骤

## 数据库 Schema

### 加密版本字段

包含加密数据的表都有 `encryption_version` 字段：

- `api_key_store.encryption_version`（默认：1）
- `smtp_settings.encryption_version`（默认：1）
- `model_gateway_config.encryption_version`（默认：1）

**版本映射**：

| 版本 | 算法 | 说明 |
|------|------|------|
| 1 | Fernet (AES-128-CBC + HMAC-SHA256) | 当前 |
| 2+ | 保留用于未来算法 | 如 AES-256-GCM |

未来算法升级将：

1. 支持读取版本 1 数据
2. 新数据使用版本 2 写入
3. 提供渐进式迁移脚本

## 故障排查

### "Invalid Fernet key" 错误

- 验证 `OPENACE_ENCRYPTION_KEY` 已设置
- 检查密钥格式（应为十六进制或 base64，>= 32 字符）
- 确保值中无空格或换行

### 轮换后解密失败

- 确认使用正确的密钥匹配数据的加密版本
- 检查数据是否用不同密钥加密
- 若密钥丢失则从备份恢复

### 轮换后 Proxy Token 无效

- 预期行为：使用旧密钥签名的 Token
- 用户需重启远程会话
- 会话短暂时无需操作

## 相关文档

- [ENV_REFERENCE.md](ENV_REFERENCE.md)——含轮转影响的环境变量总表
- [UPGRADING.md](UPGRADING.md)——升级时的密钥兼容
- [DEPLOYMENT.md](DEPLOYMENT.md)——生产部署指南
- [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md)——功能概述
- [Security Policy](https://github.com/open-ace/open-ace/security/policy)——安全报告与策略说明

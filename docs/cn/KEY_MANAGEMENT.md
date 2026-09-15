# 密钥管理

> Open-ACE 的加密密钥派生、轮换和安全最佳实践。

## 概述

Open-ACE 使用 Fernet 对称加密保护静态敏感数据。同一密钥直接加密**全部 8 类存储**
（权威完整清单见下文[密钥共享影响面](#密钥共享影响面)）：

- 远程工作区的 API Key（`api_key_store` 表）
- SMTP 密码（`smtp_settings` 表）
- Model Gateway API Key（`model_gateway_config` 表）
- SSO Provider 凭据（`sso_providers` 表）
- 钉钉集成（`dingtalk_settings` 表）
- 飞书集成（`feishu_settings` 表）
- Webhook 配置（`webhook_settings` 表）
- 通知偏好（`notification_preferences` 表）

Proxy Token 使用 HMAC-SHA256 签名（非 Fernet）进行认证。

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
   全部 8 类加密存储                 Proxy Token
   （api_key_store / smtp_settings /   签名（HMAC-SHA256，
    model_gateway_config /             非 Fernet）
    sso_providers / dingtalk_settings /
    feishu_settings / webhook_settings /
    notification_preferences，
    完整清单见"密钥共享影响面"）

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

3. **停止所有使用旧钥的读写者**（应用、scheduler/worker 等；数据库保持运行）。
   这是脚本安全性的**前提**而非可选项：脚本不持表锁，每条 UPDATE 仅按扫描到的
   旧值做乐观校验（并发**修改**已扫描行 → 影响 0/2 行 → 整体回滚），但扫描之后
   新**插入**的旧钥行在事务内不可见，postcheck 也只能**报告**已提交的混钥数据
   而无法回滚——不停写者必然残留旧钥密文。

4. **保持环境变量仍为旧密钥，先做 pre-flight 干跑**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY" --verify
   ```

   pre-flight 用新钥做往返探针、并用旧钥校验全部存储可解密；任何一步失败都不写入。轮换期间环境变量必须保持**旧密钥**——提前切成新钥会导致旧密文无法解密、pre-flight 失败。

5. **执行轮换（单事务，失败整体回滚；逐条 UPDATE 行数校验，并发修改触发整体回滚）**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY"
   ```

   脚本重加密上述清单中该密钥直接保护的全部存储（`v1k<id>:` 前缀的 registry
   密文绑定 `OPENACE_ENCRYPTION_KEYS` 数据钥，自动跳过），写出后用新钥复查全部存储。

6. **切换环境变量到新密钥并统一重启全部服务**

   ```bash
   # Docker Compose: 编辑 .env 文件
   # Kubernetes: 更新 Secret
   # Systemd: 编辑 /etc/open-ace/environment
   docker-compose restart   # 或 sudo systemctl restart open-ace
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
3. 保持 `OPENACE_ENCRYPTION_KEY` 仍为**旧密钥**，轮换所有加密凭据：
   `scripts/rotate_sso_encryption.py --new-key <NEW_KEY>` 会在**单事务**内
   重加密该密钥保护的全部存储（`sso_providers`、`api_key_store`、
   `smtp_settings`、`model_gateway_config`、`dingtalk_settings`、
   `feishu_settings`、`webhook_settings`、`notification_preferences`；
   `v1k<id>:` 前缀的 registry 格式密文绑定的是 `OPENACE_ENCRYPTION_KEYS`
   数据钥，不受影响、自动跳过）。先以 `--verify` 做 pre-flight 干跑。
   脚本以环境变量为旧钥、`--new-key` 为新钥——若在轮换前就把环境变量
   切成新钥，旧密文将无法解密，pre-flight 会失败
4. 轮换完成（脚本会在写出后用新钥复查全部存储）且确认无其它共享该
   密钥的存储后，才把 `OPENACE_ENCRYPTION_KEY` 切换为新密钥并重启服务
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

- [远程工作区](./REMOTE_WORKSPACE.md) - 功能概述
- [部署](./DEPLOYMENT.md) - 生产部署指南
- [Security Policy](https://github.com/open-ace/open-ace/security/policy) - 安全报告与策略说明

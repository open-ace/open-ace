# 密钥管理

> Open-ACE 的加密密钥派生、轮换和安全最佳实践。

## 概述

Open-ACE 使用 Fernet 对称加密保护静态敏感数据：

- 远程工作区的 API Key（`api_key_store` 表）
- SMTP 密码（`smtp_settings` 表）
- Model Gateway API Key（`model_gateway_config` 表）

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
         ├────────────────┬────────────────┐
         ▼                ▼                ▼
   API Key          SMTP 密码        Model Gateway
   加密             加密              加密
         │
         │ 同一密钥用于 HMAC-SHA256
         ▼
   Proxy Token
   签名
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

同一密钥用于：

1. **API Key 加密** - `api_key_store.encrypted_key`
2. **SMTP 密码加密** - `smtp_settings.encrypted_password`
3. **Model Gateway 加密** - `model_gateway_config.encrypted_api_key`
4. **Proxy Token 签名** - 远程代理认证的 HMAC-SHA256 签名

**影响**：

- 密钥轮换需要重新加密三个数据存储
- 使用旧密钥签名的活跃 Proxy Token 轮换后验证失败
- 密钥泄露影响四个安全域

## 密钥轮换

### 数据库密码轮换

数据库密码应定期轮换，与加密密钥轮换分开进行。

**步骤**：

1. **连接 PostgreSQL 修改密码**

   ```bash
   # 使用 Docker 容器连接
   docker exec -it open-ace-postgres psql -U ace -d ace

   # 修改密码
   ALTER USER ace WITH PASSWORD 'new-strong-password';

   # 退出
   \q
   ```

2. **更新 .env 文件**

   ```bash
   # 编辑 .env 文件
   DB_PASSWORD=new-strong-password
   ```

3. **重启服务**

   ```bash
   docker compose restart
   ```

4. **验证连接**

   ```bash
   # 查看日志确认数据库连接成功
   docker compose logs open-ace | grep "PostgreSQL is ready"
   ```

**连接池刷新说明**：

- PostgreSQL 容器：无需重启，密码修改立即生效
- 应用连接池：下次连接时自动使用新密码
- 如使用 PgBouncer 等连接池中间件：需重启中间件服务

**生成强密码**：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(16))"
```

### OPENACE_ENCRYPTION_KEY 轮换警告

> **严重警告**：轮换 `OPENACE_ENCRYPTION_KEY` 后，此前加密的数据将**永久不可解密**。

**影响范围**：

- API Key（`api_key_store` 表）— 存储的 API Key 将无法解密
- SMTP 密码（`smtp_settings` 表）— SMTP 配置将无法解密
- Model Gateway API Key（`model_gateway_config` 表）— Gateway 配置将无法解密
- Proxy Token 签名 — 所有活跃的远程会话 Token 将失效

**轮换前的必要步骤**：

1. **备份数据库**：`pg_dump openace > backup.sql`
2. **导出加密数据**：使用 `scripts/export_encrypted_data.py` 导出明文数据
3. **记录所有密钥**：确保新密钥安全存储
4. **计划维护窗口**：轮换期间服务将不可用

**推荐做法**：

- 仅在计划维护窗口执行轮换
- 提前通知用户会话将中断
- 轮换后立即测试数据解密功能
- 保留旧密钥备份至少 30 天（安全存储）

### 当前限制

- **单密钥 Fernet**：不支持 MultiFernet 多密钥解密
- **轮换需停机**：无法在不重启服务的情况下轮换密钥
- **手动流程**：无自动化密钥轮换机制

### 轮换方法

#### 方法 A：停机轮换（推荐用于小规模部署）

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

2. **导出加密数据**

   ```bash
   python scripts/export_encrypted_data.py --output encrypted_data_backup.json
   ```

   导出内容：
   - `api_key_store.encrypted_key` → 明文 API Key
   - `smtp_settings.encrypted_password` → 明文 SMTP 密码
   - `model_gateway_config.encrypted_api_key` → 明文 Gateway Key

3. **生成并设置新密钥**

   ```bash
   # 生成新的 32 字节密钥
   NEW_KEY=$(openssl rand -hex 32)
   echo "新密钥: $NEW_KEY"

   # 更新环境变量
   # Docker Compose: 编辑 .env 文件
   # Kubernetes: 更新 Secret
   # Systemd: 编辑 /etc/open-ace/environment
   ```

4. **重启服务**

   ```bash
   # Docker Compose
   docker-compose restart

   # Systemd
   sudo systemctl restart open-ace
   ```

5. **重加密并导入数据**

   ```bash
   python scripts/import_encrypted_data.py --input encrypted_data_backup.json
   ```

6. **验证功能**

   - 测试 API Key 存储和读取
   - 测试 SMTP 邮件发送
   - 测试 Model Gateway 调用
   - 注意：现有 Proxy Token 将失效（用户需重启会话）

7. **安全清理**

   ```bash
   # 验证后删除明文备份
   rm encrypted_data_backup.json

   # 可选：归档加密数据库备份
   gzip openace_backup_*.sql
   ```

#### 方法 B：MultiFernet 支持（未来增强）

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

1. 立即生成并设置新密钥
2. 撤销所有活跃 Proxy Token（如适用）
3. 轮换所有加密凭据
4. 审计访问日志查找可疑活动
5. 记录事件和修复步骤

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

- [远程工作区](./REMOTE-WORKSPACE.md) - 功能概述
- [部署](./DEPLOYMENT.md) - 生产部署指南
- [安全架构](./SECURITY.md) - 安全模型详情

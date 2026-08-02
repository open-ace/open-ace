# SSH 密钥同步安全边界

**关联 Issue**: #2182

## 概述

Open ACE 实现了安全的 SSH 密钥同步机制，防止将平台控制面的私钥传播给工作区用户。

## 安全边界

```
┌─────────────────────────────────────────────────────────────┐
│  Platform Layer (root)                                      │
│  /root/.ssh                                                 │
│  ├── id_rsa (禁止同步)                                      │
│  ├── id_ed25519 (禁止同步)                                  │
│  ├── *.pem, *.key (禁止同步)                                │
│  ├── known_hosts (允许同步)                                 │
└── allowed_keys/*.pub (显式配置后允许同步)                   │
├─────────────────────────────────────────────────────────────┤
│  User/Tenant Layer (per-user)                               │
│  /home/<user>/.ssh                                          │
│  ├── known_hosts (从平台同步)                               │
│  ├── id_rsa (用户自行配置，不来自 root 同步)                │
  └── deploy_key_<project> (per-project 或显式挂载)          │
└─────────────────────────────────────────────────────────────┘
```

## 核心原则

### 1. 默认拒绝

- 默认情况下，不同步任何文件到用户 `~/.ssh`
- 只有显式白名单中的文件才允许同步
- 禁止清单中的文件绝对不可同步，即使配置也无效

### 2. 白名单机制

默认允许同步的安全文件：

- `known_hosts` - 已知主机列表
- `known_hosts.old` - known_hosts 备份
- `known_hosts.old.*` - known_hosts 历史备份

扩展白名单（需要安全评审）：

- 公司公钥文件 (`*.pub`)
- 经过安全评审的 SSH 配置片段

### 3. 禁止清单

绝对禁止同步的文件：

**私钥文件**：
- `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`
- `id_*` (所有私钥模式)
- `*_rsa`, `*_dsa`, `*_ecdsa`, `*_ed25519`

**证书/密钥文件**：
- `*.pem`, `*.key`, `*.p12`, `*.pfx`

**Socket 文件**：
- `*.socket`, `agent.*`, `control_*`

**Token 文件**：
- `token_*`, `*.token`

**SSH 配置文件**：
- `config`, `config_*`

**文件类型**：
- 符号链接（symlink）
- 硬链接
- Unix socket
- 设备文件
- 命名管道（FIFO）

## 安全机制

### 1. TOCTOU 防护

使用文件描述符操作防止竞态条件：

- 使用 `os.open()` + `O_NOFOLLOW` 标志
- 使用 `fstat()` 而非 `stat()`
- 从文件描述符读取数据

### 2. 硬链接检测

检测 `st_nlink > 1` 防止硬链接攻击。

### 3. 内容检测

检测文件内容是否包含私钥标记：

- `-----BEGIN RSA PRIVATE KEY-----`
- `-----BEGIN OPENSSH PRIVATE KEY-----`
- 其他私钥格式

### 4. 路径验证

验证 canonical path：

- 源路径必须在 `/root/.ssh` 下
- 目标路径必须在 `/home/<user>/.ssh` 下
- 防止路径逃逸攻击

### 5. Owner 验证

确保同步文件的 owner 正确：

- 验证用户存在
- 验证 UID/GID 有效
- 设置正确的 owner/group

## 升级处理

### Legacy 私钥检测

当从旧版本升级时，系统会检测：

1. 文件权限为 600 的私钥
2. 文件名匹配 `id_*` 模式
3. 内容与 `/root/.ssh` 中的同名文件相同（内容指纹）

### 处理策略

- `warn`: 仅告警，不删除
- `backup`: 备份到 `~/.ssh/legacy_backup_YYYYMMDD_HHMMSS/`，然后删除
- `delete`: 直接删除（仅删除内容指纹匹配的文件）

## 安全替代方案

### 方案 A：Per-User Deploy Key（推荐）

为每个用户生成独立的 deploy key：

```bash
ssh-keygen -t ed25519 -f /home/<user>/.ssh/id_ed25519_<project>
```

将公钥添加到 Git 仓库的 deploy key。

### 方案 B：显式挂载到指定用户

在 Docker Compose 中配置：

```yaml
volumes:
  - ./secrets/user1/.ssh:/home/user1/.ssh:ro
```

### 方案 C：SSH Certificate Broker（企业级）

部署独立的 SSH Broker 服务，签发短期证书。

## 审计日志

所有同步操作记录到：`/var/log/openace/ssh-sync.log`

日志格式：

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

示例：

```
[2024-01-15T10:30:00] [INFO] [alice] SYNC_SUCCESS file=known_hosts
[2024-01-15T10:30:00] [WARN] [alice] SYNC_DENIED file=id_rsa reason=denylist
[2024-01-15T10:30:00] [ERROR] [alice] LEGACY_KEY_DETECTED file=/home/alice/.ssh/id_rsa
```

## 环境变量配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `OPENACE_SSH_UPGRADE_ACTION` | 升级处理策略 | `backup` |
| `OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM` | 是否需要人工确认 | `false` |
| `OPENACE_SSH_DETECT_ENCODED_KEYS` | 是否检测编码私钥 | `false` |

## 最佳实践

1. **不要将私钥放入 `/root/.ssh`**
   - 使用 per-user deploy key
   - 或使用显式挂载

2. **定期审查审计日志**
   - 检查 SYNC_DENIED 记录
   - 检查 LEGACY_KEY_DETECTED 告警

3. **升级前备份数据**
   - 备份用户 `~/.ssh` 目录
   - 检查升级告警

4. **使用安全替代方案**
   - 避免依赖 root 私钥
   - 为每个用户/项目配置独立凭据

## 相关文档

- [SSH 密钥同步配置参考](./ssh-sync-configuration.md)
- [部署文档](../cn/DEPLOYMENT.md)
- [升级迁移指南](./ssh-sync-configuration.md#升级迁移指南)

## 变更历史

- **2024-01-15**: 初始版本（Issue #2182）
  - 实现安全 SSH 密钥同步机制
  - 默认阻断私钥同步
  - 提供 per-user deploy key 方案
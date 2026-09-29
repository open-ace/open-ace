# SSH Key Sync Security Boundary — SSH 密钥同步安全边界

[English](#english) | [中文](#中文)

---

## English

**Related Issues**: #2182, #2328

### Security Guarantee

**Open ACE never propagates root private keys to workspace users.**

When secure SSH sync is unavailable or fails, the system:

1. Skips all file sync (it never falls back to the legacy sync logic)
2. Writes a structured security log to `/var/log/openace/ssh-sync-failure.json`
3. Creates a deployment alert file `/var/log/openace/ssh-sync-failure.warning`
4. Returns a non-zero exit status to signal failure

**Under no circumstances** does the system fall back to an implementation that copies root private keys. This is the core of the fail-closed security principle.

### Overview

Open ACE implements a secure SSH key synchronization mechanism that prevents private keys of the platform control plane from being propagated to workspace users.

### Security Boundary

```
┌─────────────────────────────────────────────────────────────┐
│  Platform Layer (root)                                      │
│  /root/.ssh                                                 │
│  ├── id_rsa (sync denied)                                   │
│  ├── id_ed25519 (sync denied)                               │
│  ├── *.pem, *.key (sync denied)                             │
│  ├── known_hosts (sync allowed)                             │
└── allowed_keys/*.pub (allowed only when explicitly enabled) │
├─────────────────────────────────────────────────────────────┤
│  User/Tenant Layer (per-user)                               │
│  /home/<user>/.ssh                                          │
│  ├── known_hosts (synced from platform)                     │
│  ├── id_rsa (user-configured, not synced from root)         │
  └── deploy_key_<project> (per-project or explicitly mounted)│
└─────────────────────────────────────────────────────────────┘
```

### Core Principles

#### 1. Default Deny

- By default, no files are synced to a user's `~/.ssh`
- Only files explicitly listed in the allowlist may be synced
- Files in the denylist can never be synced, even if configured to be

#### 2. Allowlist Mechanism

Safe files allowed to sync by default:

- `known_hosts` - known hosts list
- `known_hosts.old` - known_hosts backup
- `known_hosts.old.*` - historical known_hosts backups

Extended allowlist (requires security review):

- Company public key files (`*.pub`)
- SSH config snippets that have passed a security review

#### 3. Denylist

Files that are absolutely forbidden from syncing:

**Private key files**:
- `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`
- `id_*` (all private key patterns)
- `*_rsa`, `*_dsa`, `*_ecdsa`, `*_ed25519`

**Certificate/key files**:
- `*.pem`, `*.key`, `*.p12`, `*.pfx`

**Socket files**:
- `*.socket`, `agent.*`, `control_*`

**Token files**:
- `token_*`, `*.token`

**SSH config files**:
- `config`, `config_*`

**File types**:
- Symbolic links (symlink)
- Hard links
- Unix sockets
- Device files
- Named pipes (FIFO)

### Security Mechanisms

#### 1. TOCTOU Protection

File descriptor operations are used to prevent race conditions:

- Use `os.open()` with the `O_NOFOLLOW` flag
- Use `fstat()` instead of `stat()`
- Read data from the file descriptor

#### 2. Hard-link Detection

Detect `st_nlink > 1` to guard against hard-link attacks.

#### 3. Content Detection

Check whether file content contains private key markers:

- RSA private key format (markers starting with `-----BEGIN RSA...`)
- OpenSSH private key format (markers starting with `-----BEGIN OPENSSH...`)
- Other private key formats

**Note**: Ellipses are used above to avoid complete markers, so that this document itself does not trigger security detection.

#### 4. Path Validation

Validate the canonical path:

- The source path must be under `/root/.ssh`
- The destination path must be under `/home/<user>/.ssh`
- Prevents path escape attacks

#### 5. Owner Validation

Ensure synced files have the correct owner:

- Verify the user exists
- Verify the UID/GID are valid
- Set the correct owner/group

### Upgrade Handling

#### Legacy Private Key Detection

When upgrading from an older version, the system detects:

1. Private keys with file permission 600
2. File names matching the `id_*` pattern
3. Content identical to the file with the same name in `/root/.ssh` (content fingerprint)

#### Handling Policies

- `warn`: warn only, do not delete
- `backup`: back up to `~/.ssh/legacy_backup_YYYYMMDD_HHMMSS/`, then delete
- `delete`: delete directly (only files whose content fingerprint matches)

### Secure Alternatives

#### Option A: Per-User Deploy Key (recommended)

Generate a dedicated deploy key for each user:

```bash
ssh-keygen -t ed25519 -f /home/<user>/.ssh/id_ed25519_<project>
```

Add the public key to the deploy keys of the Git repository.

#### Option B: Explicit Mount for a Specific User

Configure in Docker Compose:

```yaml
volumes:
  - ./secrets/user1/.ssh:/home/user1/.ssh:ro
```

#### Option C: SSH Certificate Broker (enterprise grade)

Deploy a standalone SSH broker service that issues short-lived certificates.

### Audit Log

All sync operations are recorded in: `/var/log/openace/ssh-sync.log`

Log format:

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

Example:

```
[2026-01-15T10:30:00] [INFO] [alice] SYNC_SUCCESS file=known_hosts
[2026-01-15T10:30:00] [WARN] [alice] SYNC_DENIED file=id_rsa reason=denylist
[2026-01-15T10:30:00] [ERROR] [alice] LEGACY_KEY_DETECTED file=/home/alice/.ssh/id_rsa
```

### Environment Variables

| Variable | Description | Default |
|----------|------|--------|
| `OPENACE_SSH_UPGRADE_ACTION` | Upgrade handling policy | `backup` |
| `OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM` | Whether manual confirmation is required | `false` |
| `OPENACE_SSH_DETECT_ENCODED_KEYS` | Whether to detect encoded private keys | `false` |

### Best Practices

1. **Do not put private keys into `/root/.ssh`**
   - Use per-user deploy keys
   - Or use explicit mounts

2. **Review audit logs regularly**
   - Check SYNC_DENIED records
   - Check LEGACY_KEY_DETECTED alerts

3. **Back up data before upgrading**
   - Back up user `~/.ssh` directories
   - Check upgrade alerts

4. **Use secure alternatives**
   - Avoid depending on root private keys
   - Configure dedicated credentials for each user/project

### Related Documents

- [SSH Key Sync Configuration Reference](./SSH_SYNC_CONFIGURATION.md)
- [Deployment Guide](../guide/DEPLOYMENT.md)
- [Upgrade and Migration Guide](./SSH_SYNC_CONFIGURATION.md#升级迁移指南)

### Changelog

- **2026-01-15**: Initial version (Issue #2182)
  - Implemented the secure SSH key sync mechanism
  - Blocked private key sync by default
  - Provided the per-user deploy key solution

---

## 中文

**关联 Issue**: #2182, #2328

### 安全保证

**Open ACE 不会将 root 私钥传播给工作区用户。**

当安全 SSH 同步不可用或失败时，系统会：
1. 跳过所有文件同步（不会回退到旧的同步逻辑）
2. 写入结构化安全日志到 `/var/log/openace/ssh-sync-failure.json`
3. 创建部署告警文件 `/var/log/openace/ssh-sync-failure.warning`
4. 返回非零退出状态码表示失败

**任何情况下**，系统都不会回退到复制 root 私钥的实现。这是 fail-closed（失败时关闭）安全原则的核心。

### 概述

Open ACE 实现了安全的 SSH 密钥同步机制，防止将平台控制面的私钥传播给工作区用户。

### 安全边界

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

### 核心原则

#### 1. 默认拒绝

- 默认情况下，不同步任何文件到用户 `~/.ssh`
- 只有显式白名单中的文件才允许同步
- 黑名单中的文件绝对不可同步，即使配置也无效

#### 2. 白名单机制

默认允许同步的安全文件：

- `known_hosts` - 已知主机列表
- `known_hosts.old` - known_hosts 备份
- `known_hosts.old.*` - known_hosts 历史备份

扩展白名单（需要安全评审）：

- 公司公钥文件 (`*.pub`)
- 经过安全评审的 SSH 配置片段

#### 3. 黑名单

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

### 安全机制

#### 1. TOCTOU 防护

使用文件描述符操作防止竞态条件：

- 使用 `os.open()` + `O_NOFOLLOW` 标志
- 使用 `fstat()` 而非 `stat()`
- 从文件描述符读取数据

#### 2. 硬链接检测

检测 `st_nlink > 1` 防止硬链接攻击。

#### 3. 内容检测

检测文件内容是否包含私钥标记：

- RSA 私钥格式（`-----BEGIN RSA...` 开头的标记）
- OpenSSH 私钥格式（`-----BEGIN OPENSSH...` 开头的标记）
- 其他私钥格式

**注意**：以上使用省略号避免完整标记，防止触发安全检测。

#### 4. 路径验证

验证 canonical path：

- 源路径必须在 `/root/.ssh` 下
- 目标路径必须在 `/home/<user>/.ssh` 下
- 防止路径逃逸攻击

#### 5. Owner 验证

确保同步文件的 owner 正确：

- 验证用户存在
- 验证 UID/GID 有效
- 设置正确的 owner/group

### 升级处理

#### Legacy 私钥检测

当从旧版本升级时，系统会检测：

1. 文件权限为 600 的私钥
2. 文件名匹配 `id_*` 模式
3. 内容与 `/root/.ssh` 中的同名文件相同（内容指纹）

#### 处理策略

- `warn`: 仅告警，不删除
- `backup`: 备份到 `~/.ssh/legacy_backup_YYYYMMDD_HHMMSS/`，然后删除
- `delete`: 直接删除（仅删除内容指纹匹配的文件）

### 安全替代方案

#### 方案 A：Per-User Deploy Key（推荐）

为每个用户生成独立的 deploy key：

```bash
ssh-keygen -t ed25519 -f /home/<user>/.ssh/id_ed25519_<project>
```

将公钥添加到 Git 仓库的 deploy key。

#### 方案 B：显式挂载到指定用户

在 Docker Compose 中配置：

```yaml
volumes:
  - ./secrets/user1/.ssh:/home/user1/.ssh:ro
```

#### 方案 C：SSH Certificate Broker（企业级）

部署独立的 SSH Broker 服务，签发短期证书。

### 审计日志

所有同步操作记录到：`/var/log/openace/ssh-sync.log`

日志格式：

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

示例：

```
[2026-01-15T10:30:00] [INFO] [alice] SYNC_SUCCESS file=known_hosts
[2026-01-15T10:30:00] [WARN] [alice] SYNC_DENIED file=id_rsa reason=denylist
[2026-01-15T10:30:00] [ERROR] [alice] LEGACY_KEY_DETECTED file=/home/alice/.ssh/id_rsa
```

### 环境变量配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `OPENACE_SSH_UPGRADE_ACTION` | 升级处理策略 | `backup` |
| `OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM` | 是否需要人工确认 | `false` |
| `OPENACE_SSH_DETECT_ENCODED_KEYS` | 是否检测编码私钥 | `false` |

### 最佳实践

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

### 相关文档

- [SSH 密钥同步配置参考](./SSH_SYNC_CONFIGURATION.md)
- [部署文档](../guide/DEPLOYMENT.md)
- [升级迁移指南](./SSH_SYNC_CONFIGURATION.md#升级迁移指南)

### 变更历史

- **2026-01-15**: 初始版本（Issue #2182）
  - 实现安全 SSH 密钥同步机制
  - 默认阻断私钥同步
  - 提供 per-user deploy key 方案

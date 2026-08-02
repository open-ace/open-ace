# SSH 密钥同步配置参考

**关联 Issue**: #2182

## 概述

本文档详细说明 SSH 密钥同步的配置选项、安全评审流程和最佳实践。

## 配置文件

### 位置

`/etc/openace/ssh_sync_allowlist.yaml`

### 权限

- 权限：`600`
- Owner：`root:root`
- 原因：配置文件可能包含敏感信息，需要保护

### 格式示例

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
      reviewed_at: "2024-01-15"
      review_notes: "允许公司公钥同步"

# 禁止清单（强制，不可配置）
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

## 环境变量

### OPENACE_SSH_UPGRADE_ACTION

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

### OPENACE_SSH_UPGRADE_REQUIRE_CONFIRM

是否需要人工确认处理 legacy 私钥。

**可选值**：
- `true`: 需要人工输入 "yes" 确认
- `false`: 自动处理

**默认值**: `false`

**使用场景**：
- 重要系统升级时设置 `true`
- 自动化部署时设置 `false`

### OPENACE_SSH_DETECT_ENCODED_KEYS

是否检测编码后的私钥（Base64、Hex）。

**可选值**：
- `true`: 检测编码私钥
- `false`: 仅检测明文私钥

**默认值**: `false`

**注意**：启用会增加性能开销和误判风险。

## 安全评审流程

### 评审要求

以下情况需要安全评审：

1. 添加新的文件模式到白名单
2. 同步 SSH 配置文件片段
3. 同步非标准的公钥文件

### 评审人员授权

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

### 评审有效期

- 评审有效期为 1 年（可配置）
- 过期评审需要重新评审
- 未授权人员的评审无效

### 评审记录

每次评审必须记录：

```yaml
security_review:
  reviewed_by: "security-team"  # 评审人员（必须在授权列表中）
  reviewed_at: "2024-01-15"      # 评审日期
  review_notes: "允许公司公钥同步" # 评审说明
```

## 审计日志

### 日志位置

`/var/log/openace/ssh-sync.log`

### 日志格式

```
[TIMESTAMP] [LEVEL] [USER] MESSAGE
```

### 日志级别

- `INFO`: 正常操作
- `WARN`: 需要关注的问题
- `ERROR`: 严重问题

### 日志事件

| 事件 | 级别 | 说明 |
|------|------|------|
| `SYNC_SUCCESS` | INFO | 成功同步文件 |
| `SYNC_DENIED` | WARN | 拒绝同步文件 |
| `LEGACY_KEY_DETECTED` | ERROR | 检测到 legacy 私钥 |
| `LEGACY_KEY_BACKUP` | INFO | 备份 legacy 私钥 |
| `LEGACY_KEY_DELETED` | WARN | 删除 legacy 私钥 |

### 日志轮转

- 最大大小：100MB
- 保留天数：30 天
- 自动轮转：每日
- 自动压缩：gzip

## 命令行工具

### 查看帮助

```bash
/usr/local/bin/openace-ssh-sync --help
```

### 同步 SSH 密钥

```bash
/usr/local/bin/openace-ssh-sync --user <username>
```

### 检测 legacy 私钥

```bash
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

### Dry-run 模式

```bash
/usr/local/bin/openace-ssh-sync --user <username> --dry-run
```

### 指定配置文件

```bash
/usr/local/bin/openace-ssh-sync --user <username> --config /path/to/config.yaml
```

## 升级迁移指南

### 从 1.x 版本升级

#### 1. 升级前检查

```bash
# 检查是否有 legacy 同步的私钥
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy
```

#### 2. 配置迁移

旧版本：
- 自动同步所有 `/root/.ssh` 文件

新版本：
- 只同步白名单文件
- 私钥默认不同步

#### 3. 推荐行动

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

#### 4. 处理 legacy 私钥

```bash
# 设置处理策略
export OPENACE_SSH_UPGRADE_ACTION=backup

# 运行检测
/usr/local/bin/openace-ssh-sync --user <username> --detect-legacy

# 检查备份
ls -la /home/<username>/.ssh/legacy_backup_*/
```

### 回滚方案

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

## 故障排查

### 问题：known_hosts 未同步

**检查**：
1. 文件是否在 `/root/.ssh` 下
2. 文件名是否匹配白名单
3. 查看审计日志

```bash
tail -f /var/log/openace/ssh-sync.log
```

### 问题：legacy 私钥未检测

**检查**：
1. 文件权限是否为 600
2. 文件名是否匹配 `id_*` 模式
3. 内容指纹是否匹配

### 问题：脚本执行失败

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

## 最佳实践

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

## 相关文档

- [SSH 密钥安全边界](./ssh-key-boundary.md)
- [部署文档](../cn/DEPLOYMENT.md)

## 变更历史

- **2024-01-15**: 初始版本（Issue #2182）

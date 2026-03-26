# Open ACE Remote Sync

增量同步守护进程 - 从所有工具（OpenClaw、Qwen、Claude）获取数据并上传到中央服务器。

## 特性

- **增量同步**: 只上传上次同步后的新消息
- **自动发现**: 自动查找工具数据目录
- **状态追踪**: 记录上次同步时间，避免重复上传
- **一键部署**: 单脚本完成所有部署

## 快速部署

### 部署到远程机器

```bash
./deploy-remote.sh \
    --host user@remote-host \
    --server http://CENTRAL_SERVER:5001 \
    --auth-key YOUR_AUTH_KEY \
    --hostname remote-hostname
```

### 本地部署

```bash
./deploy.sh \
    --server http://CENTRAL_SERVER:5001 \
    --auth-key YOUR_AUTH_KEY \
    --hostname $(hostname)
```

## 部署选项

| 选项 | 描述 | 默认值 |
|------|-------------|---------|
| `--host` | 远程主机 (user@hostname) | - |
| `--server` | 中央服务器 URL | 必填 |
| `--auth-key` | 认证密钥 | 必填 |
| `--hostname` | 机器主机名 | $(hostname) |
| `--interval` | 同步间隔（秒） | 300 (5分钟) |
| `--user` | 运行服务的用户 | 当前用户 |
| `--install-dir` | 安装目录 | ~/upload-to-central |
| `--uninstall` | 卸载服务和文件 | - |

## 示例

### 部署到 ai-lab

```bash
./deploy-remote.sh \
    --host open-ace@ai-lab \
    --server http://192.168.31.208:5001 \
    --auth-key deploy-remote-machine-key-2026 \
    --hostname ai-lab
```

### 自定义同步间隔

```bash
./deploy-remote.sh \
    --host user@server \
    --server http://192.168.1.100:5001 \
    --auth-key YOUR_KEY \
    --interval 60  # 每分钟同步
```

### 卸载

```bash
./deploy-remote.sh --host user@server --uninstall
```

## 部署后操作

### 检查状态

```bash
ssh user@server "sudo systemctl status upload-to-central"
```

### 查看日志

```bash
ssh user@server "sudo journalctl -u upload-to-central -f"
```

### 手动同步

```bash
ssh user@server "cd ~/upload-to-central && ./upload.sh"
```

### 强制全量同步

```bash
ssh user@server "cd ~/upload-to-central && ./upload.sh --full"
```

## 同步状态

状态保存在 `~/.open-ace/sync_state.json`：

```json
{
  "hostname": {
    "last_sync_time": "2026-03-25 05:15:34",
    "last_upload": "2026-03-25T13:15:35",
    "total_uploaded": 68
  }
}
```

## 文件

| 文件 | 描述 |
|------|-------------|
| `deploy.sh` | 本地部署脚本 |
| `deploy-remote.sh` | 远程部署脚本（通过 SSH） |
| `upload_to_server.py` | 主同步脚本（内嵌在 deploy.sh） |
| `config.json` | 配置文件 |
| `upload-to-central.service` | systemd 服务文件 |

## 注意事项

1. **SSH 访问**: 远程部署需要 SSH 访问权限
2. **Root 权限**: 安装 systemd 服务需要 root 权限
3. **数据目录**: 脚本会自动查找 OpenClaw、Qwen、Claude 的数据目录
4. **增量同步**: 每 5 分钟检查一次新数据，只上传新增消息
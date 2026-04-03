# Open ACE - Docker 部署指南

本目录包含 Open ACE 的 Docker 部署相关脚本。

## 快速部署

### 1. 构建并启动

```bash
# 在项目根目录执行
docker compose up -d --build
```

这将启动：
- **open-ace-web**: Web 应用（端口 5000）
- **open-ace-postgres**: PostgreSQL 数据库（内部端口 5432）

### 2. 访问应用

- 地址: http://localhost:5000
- 默认账号: admin / admin123

> ⚠️ 生产环境请务必修改默认密码！

## 生产环境部署

### 使用安装脚本（推荐）

```bash
# 交互式安装
./install.sh

# 非交互式安装（使用默认配置）
./install.sh --non-interactive
```

安装脚本会：
1. 检查并安装 Docker（如需要）
2. 创建部署用户和目录
3. 生成配置文件
4. 启动 PostgreSQL 和应用服务
5. 初始化数据库和默认管理员账号

### 离线部署

适用于无网络环境：

```bash
# 1. 在有网络的机器上导出镜像
./export-image.sh --build --compress

# 2. 将以下文件拷贝到目标服务器
#    - open-ace-images.tar.gz
#    - install.sh

# 3. 在目标服务器上执行
./install.sh
```

## 脚本说明

| 脚本 | 说明 |
|------|------|
| `install.sh` | 主安装脚本，支持交互式和非交互式模式 |
| `export-image.sh` | 导出 Docker 镜像用于离线部署 |
| `upgrade-remote.sh` | 远程升级脚本 |
| `uninstall.sh` | 卸载脚本 |
| `quick-install-mac.sh` | Mac 一键部署脚本 |

## 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SECRET_KEY` | Flask 密钥 | 随机生成 |
| `UPLOAD_AUTH_KEY` | 上传认证密钥 | 随机生成 |
| `DB_USER` | PostgreSQL 用户名 | ace |
| `DB_PASSWORD` | PostgreSQL 密码 | ace-secret |
| `DB_NAME` | PostgreSQL 数据库名 | ace |

### Workspace 多用户模式

多用户模式为每个用户启动独立的 `qwen-code-webui` 进程，确保用户数据隔离。

**相关环境变量：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WORKSPACE_MULTI_USER_MODE` | 启用多用户模式 | false |
| `WORKSPACE_PORT_RANGE_START` | 端口池起始端口 | 3100 |
| `WORKSPACE_PORT_RANGE_END` | 端口池结束端口 | 3200 |
| `WORKSPACE_MAX_INSTANCES` | 最大实例数 | 20 |
| `WORKSPACE_IDLE_TIMEOUT` | 空闲超时(分钟) | 30 |

**部署前准备：**

1. 安装 `qwen-code-webui`：
   ```bash
   npm install -g @ivycomputing/qwen-code-webui
   ```

2. 确保每个用户有对应的系统账号和 `~/.qwen/` 目录

**自动配置（推荐）：**

安装脚本在启用多用户模式时会自动：
- 检测 `qwen-code-webui` 安装位置
- 创建 `/etc/sudoers.d/open-ace-webui` 配置文件
- 验证 sudoers 语法
- 配置防火墙开放端口池范围

```bash
# 交互式安装时选择启用多用户模式
./install.sh

# 或使用环境变量
WORKSPACE_MULTI_USER_MODE=true ./install.sh --non-interactive
```

**手动配置 sudoers：**

如果自动配置失败，可手动配置：

```bash
sudo visudo -f /etc/sudoers.d/open-ace-webui
```

添加内容：
```
open-ace ALL=(ALL) NOPASSWD: /usr/local/bin/qwen-code-webui *
```

**详细配置说明请参考 [部署文档](../../../docs/DEPLOYMENT.md#multi-user-workspace-deployment)**

### 数据持久化

- **配置文件**: `./config/` → `/home/open-ace/.open-ace/`
- **日志**: `./logs/` → `/app/logs/`
- **数据库**: Docker volume `postgres-data`

## 管理命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 完全清理（包括数据卷）
docker compose down -v
```

## Mac 部署

Apple Silicon (M1/M2/M3/M4) 和 Intel Mac 部署请参考 [README-MAC-DEPLOY.md](README-MAC-DEPLOY.md)

## 常见问题

### Q: 端口被占用？

```bash
# 查找占用进程
lsof -i :5000

# 或修改端口
PORT=5002 docker compose up -d
```

### Q: 数据库连接失败？

检查 PostgreSQL 容器是否健康：
```bash
docker compose ps
docker compose logs postgres
```

### Q: 如何修改密码？

登录后点击右上角用户头像 → 修改密码

## 更多文档

- [架构说明](../../../docs/ARCHITECTURE.md)
- [开发指南](../../../docs/DEVELOPMENT.md)
- [API 文档](../../../docs/API.md)
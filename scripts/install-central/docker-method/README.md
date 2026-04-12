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

### 数据库自动初始化

容器首次启动时，`docker-entrypoint.sh` 会自动完成：

1. 等待 PostgreSQL 就绪
2. 检查数据库是否已初始化（通过 `users` 表判断）
3. 如果未初始化：执行 `schema/schema-postgres.sql` → `alembic stamp head` → 创建默认管理员
4. 如果已初始化：执行 `alembic upgrade head`（升级迁移）

无需手动执行 SQL 或迁移命令。

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
3. 生成配置文件（config.json、docker-compose.yml、.env）
4. 启动 PostgreSQL 和应用服务
5. 容器自动初始化数据库和默认管理员账号

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

**Docker 容器已内置所需依赖**（Node.js 20、qwen-code-webui、@qwen-code/qwen-code），无需在宿主机额外安装。

**相关环境变量：**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WORKSPACE_MULTI_USER_MODE` | 启用多用户模式 | false |
| `WORKSPACE_BASE_DIR` | 用户项目存储根目录 | /workspace（Docker）/home（二进制） |
| `WORKSPACE_PORT_RANGE_START` | 端口池起始端口 | 3100 |
| `WORKSPACE_PORT_RANGE_END` | 端口池结束端口 | 3200 |
| `WORKSPACE_MAX_INSTANCES` | 最大实例数 | 20 |
| `WORKSPACE_IDLE_TIMEOUT` | 空闲超时(分钟) | 30 |

**启动方式：**

```bash
# 启用多用户模式
WORKSPACE_MULTI_USER_MODE=true docker compose up -d
```

**用户创建（自动管理）：**

在多用户模式下，**不需要手动创建系统用户**。通过 Open-ACE 管理后台创建用户时，设置 `system_account` 字段，系统会自动：

1. 在容器内创建对应的 Linux 用户
2. 创建用户的 workspace 目录（`/workspace/<username>/`）
3. 创建 `~/.qwen/` 目录

```bash
# 通过 API 创建用户（system_account 会自动创建系统用户）
curl -X POST http://localhost:5000/api/admin/users \
  -H "Content-Type: application/json" \
  -b "session_token=YOUR_TOKEN" \
  -d '{
    "username": "alice",
    "email": "alice@example.com",
    "password": "SecurePass123!",
    "role": "user",
    "system_account": "alice"
  }'
```

**项目存储结构：**

```
/workspace/                    # Docker volume
├── alice/                     # 用户 alice 的 workspace
│   ├── .qwen/                 # qwen 配置
│   ├── project-1/             # 项目目录
│   └── project-2/
└── bob/                       # 用户 bob 的 workspace
    ├── .qwen/
    └── my-project/
```

**自动配置（entrypoint）：**

容器启动时 `docker-entrypoint.sh` 会自动完成：
- 配置 `/etc/sudoers.d/open-ace-webui`（允许 open-ace 以其他用户身份启动 qwen-code-webui）
- 确保 workspace 基础目录存在

**手动配置 sudoers：**

如果自动配置失败，可进入容器手动配置：

```bash
docker compose exec open-ace-web bash
cat > /etc/sudoers.d/open-ace-webui << 'EOF'
open-ace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *
EOF
chmod 440 /etc/sudoers.d/open-ace-webui
```

### 数据持久化

- **配置文件**: `./config/` → `/home/open-ace/.open-ace/`
- **日志**: `./logs/` → `/app/logs/`
- **数据库**: Docker volume `postgres-data`
- **Workspace**: Docker volume `workspace-data` → `/workspace/`

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

### Q: 多用户模式下 workspace 实例无法启动？

进入容器检查：
```bash
# 查看 qwen-code-webui 是否可用
docker compose exec open-ace-web which qwen-code-webui

# 检查 sudoers 配置
docker compose exec open-ace-web cat /etc/sudoers.d/open-ace-webui

# 检查系统用户是否创建成功
docker compose exec open-ace-web id alice

# 检查 workspace 目录
docker compose exec open-ace-web ls -la /workspace/

# 手动测试启动
docker compose exec open-ace-web sudo -u alice qwen-code-webui --port 3100 --host 0.0.0.0
```

### Q: 端口范围映射不生效？

Docker Compose 会将 `3100-3200:3100-3200` 展开为 101 个端口映射。如果启动缓慢或端口冲突，可以在 `.env` 中缩小范围：

```bash
WORKSPACE_PORT_RANGE_START=3100
WORKSPACE_PORT_RANGE_END=3110
```

### Q: 如何访问宿主机上的已有项目？

默认情况下 workspace 数据存储在 Docker volume 中。如需访问宿主机上的已有项目，可在 `docker-compose.yml` 中添加宿主机目录挂载：

```yaml
volumes:
  - workspace-data:/workspace
  # 额外挂载宿主机项目目录
  - /path/to/projects:/host-projects:ro
```

用户在 workspace 中可以通过文件浏览器访问 `/host-projects/` 目录。

## 更多文档

- [架构说明](../../../docs/ARCHITECTURE.md)
- [开发指南](../../../docs/DEVELOPMENT.md)
- [API 文档](../../../docs/API.md)

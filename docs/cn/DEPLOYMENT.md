# 部署指南

> **ACE** = **AI Computing Explorer**

本指南涵盖 Open ACE 在各种场景下的部署方法。

## 目录

- [快速开始](#快速开始)
- [Docker 部署](#docker-部署)
- [配置](#配置)
- [部署场景](#部署场景)
- [系统服务](#系统服务)
- [数据采集](#数据采集)
- [升级](#升级)
- [故障排查](#故障排查)
- [安全注意事项](#安全注意事项)
- [多用户工作区部署](#多用户工作区部署)

## 快速开始

### 本地部署

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化配置
python3 cli.py config init

# 运行数据库迁移
alembic upgrade head

# 启动 Web 服务器
python3 server.py

# 访问 http://localhost:19888
```

## Docker 部署

### 前提条件

- 已安装 Docker 和 Docker Compose
- Open ACE Docker 镜像（`open-ace:latest`）
- PostgreSQL 镜像（`postgres:15-alpine`）

### 非 root 运行（默认）

Open ACE 镜像默认以非 root 用户 `open-ace`（uid 1000）运行。生产阶段中的
`USER 1000` 指令意味着 `docker run`、docker-compose 与 Kubernetes 都会以
uid 1000 执行入口脚本，而不再仅依赖清单中的 `securityContext`。uid/gid 1000
是稳定的，并与镜像中内置的文件属主、K8s 的 `runAsUser`/`runAsGroup: 1000`
保持一致。

多用户工作区模式（`WORKSPACE_MULTI_USER_MODE=true` 或配置中的
`workspace.multi_user_mode: true`）确实需要 root——它会创建系统用户
（`useradd`）、修复属主（`chown`）并在 `/home` 下切换身份
（`sudo -u <user>`）。多用户部署必须显式回退到 root：

```bash
docker run --user 0 -e WORKSPACE_MULTI_USER_MODE=true \
  -e OPENACE_ALLOW_ROOT_MULTI_USER=1 ...
```

必须同时设置 `--user 0`（或清单中的 `runAsUser: 0`）与
`OPENACE_ALLOW_ROOT_MULTI_USER=1`，否则入口脚本会以清晰的错误信息退出，
而不是默默吞掉非 root 多用户部署会遇到的 `useradd`/`chown` 权限失败。

### 初始部署

```bash
# 1. 导出 Docker 镜像（在开发机上）
./scripts/export-image.sh --compress

# 2. 复制到服务器
scp dist/open-ace-images.tar.gz user@server:~
scp scripts/deploy.sh user@server:~

# 3. 运行部署脚本
chmod +x deploy.sh
sudo ./deploy.sh

# 4. 按照交互提示操作
```

### 部署配置

部署脚本将提示以下配置：

| 设置 | 说明 | 默认值 |
|------|------|--------|
| 运行用户 | 运行应用的用户 | `open-ace` |
| 部署目录 | 安装目录 | `/home/open-ace/open-ace` |
| Web 端口 | Web 服务器端口 | `19888` |
| 主机名 | 服务器主机名 | 自动检测 |
| 数据库用户 | PostgreSQL 用户名 | `open-ace` |
| 数据库名称 | PostgreSQL 数据库名 | `ace` |
| OpenClaw | 启用 OpenClaw 工具 | `yes` |
| Claude | 启用 Claude 工具 | `yes` |
| Qwen | 启用 Qwen 工具 | `yes` |
| 工作区 | 启用工作区 | `no` |

**注意**：工作区在单独的容器中运行。启用后，Open ACE 将连接到指定 URL 的工作区服务。请确保工作区容器正在运行且端口可访问。

**URL 配置**：如果在工作区或 OpenClaw URL 中输入 `localhost`，部署脚本会自动将其转换为服务器 IP 地址。这是因为：
- URL 由前端（浏览器）使用，而非容器
- 浏览器无法将 `localhost` 解析为服务器地址
- 示例：`http://localhost:3000` → `http://192.168.1.100:3000`

### 默认凭证

部署完成后，使用以下凭证登录：

```
用户名: admin
密码: admin123
```

**重要**：首次登录后请立即修改默认密码！

在启动生产环境前，请先在 `.env` 或密钥管理系统中提供以下密钥：

- `SECRET_KEY` — Flask 会话密钥，必须是强随机唯一值
- `OPENACE_ENCRYPTION_KEY` — 专用于 API Key / SMTP 密码存储加密的独立密钥
- `UPLOAD_AUTH_KEY` — 上传接口使用的共享认证密钥

### 目录结构

```
/home/open-ace/open-ace/
├── config/                  # 配置文件
│   └── config.json          # 主配置文件
├── docker-compose.yml       # Docker Compose 配置
└── .env                     # 环境变量（敏感信息！）
```

**注意**：数据存储在 PostgreSQL 容器的卷（`postgres-data`）中，而非主机文件系统。

### 管理命令

```bash
cd /home/open-ace/open-ace

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 仅查看 open-ace 日志
docker compose logs -f open-ace

# 重启服务
docker compose restart

# 仅重启 open-ace
docker compose restart open-ace

# 停止服务
docker compose down

# 启动服务
docker compose up -d
```

### 更新 Open ACE 镜像

发布新版本时，只需更新 Docker 镜像：

#### 方法一：简单重启（推荐）

```bash
cd /home/open-ace/open-ace

# 1. 加载新镜像
gunzip -c open-ace-images.tar.gz | docker load

# 2. 重启 open-ace 容器
docker compose up -d open-ace

# 3. 验证启动
docker compose logs -f open-ace
```

#### 方法二：完整重建

```bash
cd /home/open-ace/open-ace

# 1. 加载新镜像
gunzip -c open-ace-images.tar.gz | docker load

# 2. 停止并删除旧容器
docker compose stop open-ace
docker compose rm -f open-ace

# 3. 启动新容器
docker compose up -d open-ace

# 4. 验证启动
docker compose logs -f open-ace
```

#### 方法三：使用版本标签

```bash
# 1. 加载特定版本
docker load -i open-ace-v1.2.0.tar

# 2. 更新 docker-compose.yml
sed -i 's|image: open-ace:latest|image: open-ace:v1.2.0|' docker-compose.yml

# 3. 重建容器
docker compose up -d open-ace
```

**注意**：
- 数据存储在 `./data` 目录和 PostgreSQL 卷中，**不会丢失**
- `./config` 中的配置会被保留
- PostgreSQL 容器继续运行，只更新 open-ace 容器

### 数据库迁移

如果新版本包含数据库模式变更：

```bash
cd /home/open-ace/open-ace

# 运行迁移
docker compose run --rm open-ace alembic upgrade head

# 重启应用
docker compose restart open-ace
```

### 卸载

#### Docker 方式卸载

如果使用纯 Docker 部署（无 deploy.sh 脚本），可手动卸载：

```bash
# 停止并删除容器
docker compose down

# 删除镜像
docker rmi openace/open-ace:latest postgres:15-alpine

# 删除数据卷（彻底清理）
docker volume rm open-ace_postgres-data open-ace_config-data open-ace_workspace-data

# 删除本地配置（可选）
rm -rf ~/.open-ace ./logs
```

#### 脚本方式卸载

如果使用 deploy.sh 脚本部署：

```bash
cd /home/open-ace/open-ace

# 交互式卸载（保留数据）
./uninstall.sh

# 完全卸载（删除所有内容）
./uninstall.sh --purge
```

## 配置

### 配置文件

配置存储在 `~/.open-ace/config.json`：

```json
{
  "host_name": "my-machine",
  "tools": {
    "claude": {
      "enabled": true,
      "log_path": "~/.claude/projects"
    },
    "qwen": {
      "enabled": true,
      "log_path": "~/.qwen/projects"
    },
    "openclaw": {
      "enabled": true,
      "log_path": "~/.openclaw/agents"
    }
  },
  "email": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "sender": "noreply@example.com"
  }
}
```

### 环境变量

| 变量 | 说明 |
|------|------|
| `OPENCLAW_TOKEN` | OpenClaw API token |
| `SMTP_PASSWORD` | 邮件 SMTP 密码 |
| `OPENACE_CORS_ALLOWED_ORIGINS` | 非 loopback WebUI 源的显式 API CORS 白名单，多个值用逗号分隔 |
| `OPENACE_WS_MAX_MESSAGE_BYTES` | 浏览器侧终端 / VSCode 原始桥接允许的入站 WebSocket 最大消息大小（默认 `8388608`） |

### 出站 URL 安全

管理员配置的 SSO/OIDC 端点 URL 会在 Open ACE 发起测试、token、userinfo 或 JWKS
请求前进行校验。默认仅允许公网 `http` 和 `https` 目标；loopback、localhost、内网地址、
link-local 网段、云 metadata 服务主机、带账号密码的 URL，以及解析到非公网地址的 DNS
结果都会被拦截，以降低 SSRF 风险。

### 端口配置

Open ACE 默认监听 19888 端口。如需修改端口，可根据部署方式选择以下方法。

#### macOS 端口冲突

macOS Monterey (12) 及以后版本默认启用 **AirPlay Receiver**，监听 19888 端口，会与 Open ACE 冲突。

**解决方案**：
1. 关闭 AirPlay Receiver：系统设置 → 通用 → AirDrop 与接力 → 关闭「AirPlay 接收器」
2. 或修改 Open ACE 端口（见下方方法）

#### 二进制方式

修改配置文件 `~/.open-ace/config.json`：

```json
{
  "server": {
    "web_port": 5001,
    "web_host": "0.0.0.0"
  }
}
```

修改后重启服务即可生效。

#### Docker 方式

**临时修改**（命令行）：

```bash
PORT=5001 docker compose up -d
```

**永久修改**（.env 文件）：

```bash
# 在项目根目录创建/编辑 .env 文件
echo "PORT=5001" >> .env

# 重启容器
docker compose down
docker compose up -d
```

**验证端口映射**：

```bash
docker ps
# 应显示 0.0.0.0:19888->19888/tcp
```

#### 防火墙设置（如需外网访问）

```bash
# Ubuntu/Debian
sudo ufw allow 19888/tcp

# CentOS/RHEL
sudo firewall-cmd --add-port=19888/tcp --permanent
sudo firewall-cmd --reload
```

#### 总结

| 方式 | 配置位置 | 修改方法 |
|------|----------|----------|
| 二进制 | `~/.open-ace/config.json` | 修改 `server.web_port` |
| Docker | 环境变量 `PORT` | `.env` 文件或命令行传入 |

## 部署场景

### 场景一：单机部署（推荐个人使用）

所有组件运行在一台机器上：

```bash
# 启动 Web 服务器
python3 server.py

# 设置定时数据采集
crontab -e
```

添加到 crontab：
```bash
# 每天 00:30 采集数据
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
```

### 场景二：中心服务器 + 远程采集器

适用于分布式环境：

#### 中心服务器

```bash
# 部署
python3 scripts/manage.py local deploy

# 启动 Web 服务
python3 scripts/manage.py local start
```

#### 远程机器

```bash
# 部署到远程
python3 scripts/manage.py remote deploy

# 或手动配置
scp -r open-ace user@remote:/path/to/
ssh user@remote "cd /path/to/open-ace && python3 scripts/fetch_openclaw.py"
```

## 系统服务

### Linux (systemd)

创建服务文件 `/etc/systemd/system/open-ace.service`：

```ini
[Unit]
Description=Open ACE Web Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/open-ace
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable open-ace
sudo systemctl start open-ace
```

### macOS (launchd)

创建 `~/Library/LaunchAgents/com.open-ace.web.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.open-ace.web</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/path/to/open-ace/server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardInPath</key>
    <string>/dev/null</string>
    <key>StandardOutPath</key>
    <string>/path/to/open-ace/server.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/open-ace/server-error.log</string>
</dict>
</plist>
```

加载服务：

```bash
launchctl load ~/Library/LaunchAgents/com.open-ace.web.plist
```

## 数据采集

### 手动采集

```bash
# 从所有工具采集
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py

# 采集指定天数
python3 scripts/fetch_claude.py --days 7
```

### 定时采集

使用 cron：

```bash
# 编辑 crontab
crontab -e

# 添加定时任务
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

## 管理命令

```bash
# 使用 manage.py
python3 scripts/manage.py local start     # 启动本地服务
python3 scripts/manage.py local stop      # 停止本地服务
python3 scripts/manage.py local status    # 检查状态
python3 scripts/manage.py remote deploy   # 部署到远程
python3 scripts/manage.py remote sync     # 同步文件到远程
```

## 升级

```bash
# 备份数据
cp ~/.open-ace/usage.db ~/.open-ace/usage.db.backup

# 拉取最新代码
git pull

# 如有需要，运行数据库迁移
alembic upgrade head

# 重启服务
python3 scripts/manage.py local stop
python3 scripts/manage.py local start
```

## 故障排查

### 端口被占用

如果启动时提示端口被占用，可以选择：

1. **修改 Open ACE 端口** - 参见 [端口配置](#端口配置)
2. **终止占用进程**：

```bash
# 查找占用 19888 端口的进程
lsof -i :19888

# 终止进程
kill -9 <PID>
```

**macOS 用户注意**：macOS Monterey (12) 及以后版本默认启用 AirPlay Receiver，监听 19888 端口。建议关闭 AirPlay Receiver 或修改 Open ACE 端口。

### 数据库被锁定

```bash
# 检查运行中的进程
ps aux | grep python

# 维护前停止所有服务
```

### 权限问题

```bash
# 修复权限
chmod -R 755 ~/.open-ace/
```

## 安全注意事项

1. **认证**：在生产环境中启用用户认证
2. **HTTPS**：使用反向代理（nginx/Apache）配合 SSL
3. **防火墙**：限制对 19888 端口的访问
4. **密钥管理**：使用环境变量或密钥管理系统存储敏感数据
5. **独立加密密钥**：显式设置 `OPENACE_ENCRYPTION_KEY`；加密后的敏感数据不再从 `SECRET_KEY` 派生
6. **禁止占位密钥**：不要在生产环境使用以下占位符作为密钥：
   - `change-me-in-production`
   - `replace-with-random-*`（k8s 清单占位符）
   - `dev-secret-key`、`dev-smtp-password-key`、`default-secret-key` 等开发环境占位符

   使用这些占位符会导致应用在生产环境拒绝启动（`SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`）或功能被禁用（`UPLOAD_AUTH_KEY`）。

### 升级注意：已加密敏感数据

近期安全加固将 Flask 会话签名与敏感数据加密拆分。已有部署如果已经保存了加密的 SSO client secret、SMTP 密码或 API Key，升级前请先把 `OPENACE_ENCRYPTION_KEY` 设置为旧版曾用于 `SECRET_KEY` 的同一个值。确认服务启动后能读取既有密文，再在计划维护窗口中按需轮换为新的专用加密密钥。

Docker Compose 现在要求显式设置 `SECRET_KEY`、`OPENACE_ENCRYPTION_KEY` 和 `UPLOAD_AUTH_KEY`。重启 stack 前，请先更新 `.env` 或密钥管理系统。

## 多用户工作区部署

启用 `workspace.multi_user_mode` 时，Open ACE 为每个用户以各自的 `system_account` 身份启动独立的 `qwen-code-webui` 进程。这需要额外的部署配置。

### 前提条件

1. **服务器已安装 qwen-code-webui**
2. **已配置 sudo** 以支持用户切换
3. **每个 system_account 对应的用户账户已存在**

### sudo 配置（必需）

创建 sudoers 文件以允许 Open ACE 服务账户以其他用户身份运行 webui：

```bash
# 创建 sudoers 文件
sudo visudo -f /etc/sudoers.d/open-ace-webui
```

添加以下内容：

```bash
# 允许 open-ace 服务账户以任意用户身份运行 qwen-code-webui
# 将 'open-ace' 替换为你的实际服务账户名

open-ace ALL=(ALL) NOPASSWD: /usr/local/bin/qwen-code-webui *
open-ace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *
open-ace ALL=(ALL) NOPASSWD: /opt/qwen-code-webui/bin/qwen-code-webui *

# 允许 open-ace 以其他用户身份执行文件系统操作
# 多用户模式下目录浏览器和项目创建需要此权限
open-ace ALL=(ALL) NOPASSWD: /usr/bin/test, /usr/bin/ls, /usr/bin/cat, /usr/bin/stat, /usr/bin/mkdir
```

**安全注意事项：**
- 使用完整路径以防止路径操作攻击
- `NOPASSWD` 标志是非交互式服务运行所必需的
- 仅限于特定的可执行文件路径，不要授予通用的 `sudo` 权限

### qwen-code-webui 安装

在以下位置之一安装 `qwen-code-webui`：

```bash
# 方法一：npm 全局安装（推荐）
npm install -g @ivycomputing/qwen-code-webui

# 验证安装
which qwen-code-webui
# 应输出: /usr/local/bin/qwen-code-webui

# 方法二：手动安装
git clone https://github.com/ivycomputing/qwen-code-webui.git
cd qwen-code-webui
npm install && npm run build
ln -s $(pwd)/bin/qwen-code-webui /usr/local/bin/qwen-code-webui
```

### 用户账户要求

每个设置了 `system_account` 的用户必须具备：

1. **Linux 账户已存在**：
   ```bash
   # 检查用户是否存在
   id <system_account>

   # 如需要则创建
   sudo useradd -m <system_account>
   ```

2. **qwen 目录可访问**：
   ```bash
   # 确保用户有 .qwen 目录
   sudo mkdir -p /home/<system_account>/.qwen/projects
   sudo chown -R <system_account>:<system_account> /home/<system_account>/.qwen
   ```

3. **项目目录可访问**（如适用）

### 端口范围配置

选择不与其他服务冲突的端口范围：

```json
{
  "workspace": {
    "port_range_start": 3100,
    "port_range_end": 3200
  }
}
```

**建议：**
- 使用 3000 以上的端口（避免常用服务端口）
- 为预期并发用户数分配足够的端口（如 100 个端口支持 100 个用户）
- 验证端口未被占用：`sudo netstat -tlnp | grep 3100-3200`

### systemd 服务配置

当以 systemd 服务运行 Open ACE 时，确保权限正确：

```ini
[Unit]
Description=Open ACE Web Server
After=network.target

[Service]
Type=simple
User=open-ace
Group=open-ace
WorkingDirectory=/home/open-ace/open-ace
ExecStart=/usr/bin/python3 server.py
Restart=always

# 多用户模式必需
# 允许 sudo 执行
AmbientCapabilities=CAP_SETUID CAP_SETGID

[Install]
WantedBy=multi-user.target
```

### 多用户模式故障排查

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| "sudo: no tty present" | sudo 需要密码 | 在 sudoers 中添加 NOPASSWD |
| "qwen-code-webui not found" | 可执行文件未安装 | 在 PATH 中安装 webui |
| "Permission denied" | 用户缺少权限 | 检查 sudoers 配置 |
| 端口分配失败 | 所有端口已占用 | 增加端口范围或减少 max_instances |
| 进程无法启动 | 用户账户缺失 | 创建 system_account 用户 |

### 检查多用户状态

```bash
# 查看运行中的实例
curl http://localhost:19888/api/workspace/instances

# 查看日志
tail -f /home/open-ace/open-ace/logs/open-ace.log | grep WebUIManager
```

### Windows 兼容性

**Windows 不支持多用户模式。** 在 Windows 系统上，配置会自动降级为单用户模式（不进行用户切换的直接执行）。这是平台限制，因为 Windows 没有等同于 `sudo -u` 的功能。

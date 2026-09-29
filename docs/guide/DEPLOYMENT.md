# Deployment — 部署

[English](#english) | [中文](#中文)

---

## English

> **ACE** = **AI Computing Explorer**

This guide covers deploying Open ACE: the Docker production path, a local trial without Docker, configuration, data persistence, data collection, and troubleshooting.

## Table of Contents

- [Read This First: Security Mode](#read-this-first-security-mode)
- [Quick Start (Docker)](#quick-start-docker)
- [Local Trial (without Docker)](#local-trial-without-docker)
- [Configuration](#configuration)
- [Data Persistence](#data-persistence)
- [Management Commands](#management-commands)
- [Data Collection](#data-collection)
- [Deployment Scenarios](#deployment-scenarios)
- [System Services](#system-services)
- [Multi-User Workspaces](#multi-user-workspaces)
- [Upgrading](#upgrading)
- [Uninstallation](#uninstallation)
- [Troubleshooting](#troubleshooting)
- [Related Documentation](#related-documentation)

## Read This First: Security Mode

Before any production deployment, set the security mode explicitly (Issue #2331):

```bash
OPENACE_SECURITY_MODE=production
```

- `production` — strict checks. `SECRET_KEY` and `OPENACE_ENCRYPTION_KEY` (>= 32 characters each) and a strong `DB_PASSWORD` must be set explicitly; weak or placeholder values refuse startup.
- `pilot` — trial environments. Missing secrets are auto-generated with strong warnings.
- `development` — local development. Missing secrets are auto-generated with general warnings.

The fastest correct path is the bootstrap script, which writes and validates `.env` for you (`OPENACE_SECURITY_MODE=production`, strong `SECRET_KEY` / `OPENACE_ENCRYPTION_KEY` / `DB_PASSWORD`):

```bash
./scripts/bootstrap-compose-env.sh
```

Notes:

- The single-user `docker-compose.yml` defaults to `development` so a one-click local start works; production must set the variable explicitly.
- The multi-user overlay fails fast with `Run ./scripts/bootstrap-compose-env.sh first` if the required secrets are missing.
- In production, also set `SSO_ALLOWED_REDIRECT_DOMAINS` (comma-separated domain list); without it, SSO login from non-localhost origins is blocked (Issue #3224).
- See [ENV_REFERENCE.md](ENV_REFERENCE.md) for the authoritative environment variable table and [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) for secret rotation impact.

## Quick Start (Docker)

The Docker Compose stack runs the Open ACE container plus a PostgreSQL 15 database, with all persistent data in named volumes.

```bash
# 1. Clone the repository on the server
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 2. Generate and validate .env (SECRET_KEY, OPENACE_ENCRYPTION_KEY, DB_PASSWORD, ...)
./scripts/bootstrap-compose-env.sh

# 3. Start (pulls the pre-built openace/open-ace:latest image by default)
docker compose up -d --wait

# 4. Verify
docker compose ps
docker compose logs -f open-ace
```

Then open http://localhost:19888 and log in with the default credentials:

```
Username: admin
Password: admin123
```

**Important**: change the default password immediately after first login.

Details:

- The image is `openace/open-ace:latest` (override with `IMAGE_NAME`, e.g. to pin `openace/open-ace:v1.2.0` or a locally built `open-ace:dev`). Developers can build locally with `docker build -t open-ace:dev --target production .`.
- `docker compose down` does not delete data; all state lives in named volumes (see [Data Persistence](#data-persistence)).
- The container runs as the non-root `open-ace` user (uid 1000) by default. Only multi-user mode needs root (see [Multi-User Workspaces](#multi-user-workspaces)).
- The scheduler worker is an optional profile in single-user mode: `docker compose --profile scheduler up -d`.
- For HTTPS, put a reverse proxy in front following [NGINX.md](NGINX.md). For Kubernetes, see [KUBERNETES.md](KUBERNETES.md).

### Offline Installation

For servers without internet access, use the image export script from `scripts/install-central/docker-method/`:

```bash
# On a connected machine: build and export both images
./scripts/install-central/docker-method/export-image.sh --build --compress

# Copy open-ace-images.tar.gz (and install.sh) to the target server, then
./install.sh
```

The plain Docker equivalent also works:

```bash
docker pull openace/open-ace:latest
docker save openace/open-ace:latest | gzip > open-ace-images.tar.gz
gunzip -c open-ace-images.tar.gz | docker load
```

For mainland China networks where Docker Hub is unreachable, set `BASE_REGISTRY=docker.m.daocloud.io` in `.env` so both the build and the PostgreSQL image resolve from a mirror.

## Local Trial (without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize configuration
python3 cli.py config init

# Apply database migrations (required before first start)
alembic upgrade head

# Start web server
python3 server.py

# Visit http://localhost:19888
```

Development and pilot modes support zero-config startup (Issue #2667): a missing `OPENACE_ENCRYPTION_KEY` is auto-generated on first start and persisted to `~/.open-ace/generated-secrets.env` (reused across restarts, never rotated). Production mode still requires explicit secrets.

## Configuration

Configuration has two layers:

1. **Environment variables** (`.env` for Docker Compose) control deployment-level concerns: ports, database, security mode, multi-user switches. The authoritative table with defaults and rotation impact is [ENV_REFERENCE.md](ENV_REFERENCE.md).
2. **`config.json`** controls application behavior (tools to collect, email, workspace). On bare metal it lives at `~/.open-ace/config.json`. In Docker it is auto-generated inside the `config-data` volume at `/home/open-ace/.open-ace/config.json` on first start; you can also mount a custom file read-only at that path. See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) for the full reference.

Environment variables most deployments touch:

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `19888` | Host port for the web UI (container always listens on 19888) |
| `SERVER_IP` | auto-detected | Address browsers use to reach the server (Issue #1306); set explicitly when accessing from other machines, e.g. `SERVER_IP=192.168.1.100` |
| `WORKSPACE_PORT_RANGE_START` / `WORKSPACE_PORT_RANGE_END` | `3100` / `3200` | Published port range for per-user workspace WebUI instances |
| `WORKSPACE_MULTI_USER_MODE` | `false` | Enable multi-user workspace mode |
| `DB_USER` / `DB_NAME` | `ace` / `ace` | PostgreSQL user and database name |
| `DB_PASSWORD` | dev default | Strong password required in production |
| `IMAGE_NAME` | `openace/open-ace:latest` | Application image |
| `BASE_REGISTRY` | `docker.io` | Base image registry override |

### Port Configuration

- Bare metal: set `server.web_port` / `server.web_host` in `~/.open-ace/config.json`, then restart.
- Docker: temporary change with `PORT=5001 docker compose up -d`, or permanent via `echo "PORT=5001" >> .env` followed by `docker compose down && docker compose up -d`.

Firewall (for external access):

```bash
# Ubuntu/Debian
sudo ufw allow 19888/tcp

# CentOS/RHEL
sudo firewall-cmd --add-port=19888/tcp --permanent
sudo firewall-cmd --reload
```

## Data Persistence

There is no `./data` directory. All persistent data lives in four named volumes declared by `docker-compose.yml`:

| Volume | Mount point | Content |
|--------|-------------|---------|
| `config-data` | `/home/open-ace/.open-ace` | `config.json` and `generated-secrets.env` |
| `postgres-data` | `/var/lib/postgresql/data` | PostgreSQL database (users, sessions, all application data) |
| `workspace-data` | `/workspace` | Per-user project directories |
| `agent-state` | `/var/lib/openace` | Carried CLI transcripts, shared by the app and scheduler services (Issue #3237) |

Additionally, `./logs` on the host is bind-mounted to `/app/logs`. Multi-user deployments add a fifth volume, `home-data` (mounted at `/home`), for user home directories (Issue #2729).

Inspect volumes:

```bash
docker volume ls | grep open-ace
# expect: agent-state, config-data, postgres-data, workspace-data
```

Back up the database with `pg_dump` (see [DATABASE_BACKUP.md](DATABASE_BACKUP.md)); the `postgres-data` volume is the source of truth.

## Management Commands

```bash
cd /path/to/open-ace

# View status
docker compose ps

# View logs
docker compose logs -f

# View open-ace logs only
docker compose logs -f open-ace

# Restart services
docker compose restart

# Restart open-ace only
docker compose restart open-ace

# Stop services (volumes are preserved)
docker compose down

# Start services
docker compose up -d
```

## Data Collection

Open ACE aggregates usage from local AI CLI tools via fetcher scripts in `scripts/`:

| Script | Source |
|--------|--------|
| `fetch_claude.py` | `~/.claude/projects` (Claude Code JSONL) |
| `fetch_qwen.py` | `~/.qwen/projects` (Qwen Code JSONL) |
| `fetch_codex.py` | `~/.codex/sessions` (Codex CLI JSONL) |
| `fetch_zcode.py` | `~/.zcode/cli/db/db.sqlite` (ZCode CLI SQLite) |
| `fetch_openclaw.py` | `~/.openclaw/agents` (OpenClaw) |

Manual collection:

```bash
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_codex.py
python3 scripts/fetch_zcode.py
python3 scripts/fetch_openclaw.py

# Collect for specific days
python3 scripts/fetch_claude.py --days 7
```

Scheduled collection (bare metal deployments):

```bash
crontab -e
```

```bash
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_codex.py >> logs/cron.log 2>&1
45 0 * * * cd /path/to/open-ace && python3 scripts/fetch_zcode.py >> logs/cron.log 2>&1
50 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

## Deployment Scenarios

### 1. Single Machine (recommended for personal use)

All components on one machine: `python3 server.py` plus the cron jobs above.

### 2. Central Server + Remote Collectors

For distributed environments, deploy with `scripts/manage.py`. Its usage is a positional action plus an optional `--remote` switch:

```bash
python3 scripts/manage.py init      # Initialize configuration
python3 scripts/manage.py deploy    # Deploy to ~/open-ace/
python3 scripts/manage.py install   # Install as a system service
python3 scripts/manage.py start     # Start web server (deployment dir)
python3 scripts/manage.py stop      # Stop web server
python3 scripts/manage.py restart   # Restart web server
python3 scripts/manage.py status    # Check service status
python3 scripts/manage.py show      # Show current configuration
python3 scripts/manage.py --remote deploy  # Deploy to remote machine
python3 scripts/manage.py --remote sync    # Sync files to remote
python3 scripts/manage.py --remote status  # Check remote status
```

`--remote` supports only `deploy`, `sync`, and `status`; `sync` is remote-only.

Alternatively, configure a remote machine manually:

```bash
scp -r open-ace user@remote:/path/to/
ssh user@remote "cd /path/to/open-ace && python3 scripts/fetch_openclaw.py"
```

## System Services

### Linux (systemd)

Create `/etc/systemd/system/open-ace.service`:

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

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable open-ace
sudo systemctl start open-ace
```

### macOS (launchd)

Create `~/Library/LaunchAgents/com.open-ace.web.plist`:

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

Load the service:

```bash
launchctl load ~/Library/LaunchAgents/com.open-ace.web.plist
```

## Multi-User Workspaces

Multi-user mode gives each user an isolated system account and a dedicated `qwen-code-webui` instance, running the container as root. See [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) for the three startup methods, sudoers configuration, and version requirements.

## Upgrading

Pull the new image and restart; database migrations run automatically on container startup, subject to the minimum upgrade baseline `baseline_2026_06_23`. Steps, migration failure handling, and rollback are covered in [UPGRADING.md](UPGRADING.md).

## Uninstallation

```bash
# Stop and remove containers
docker compose down

# Remove images
docker rmi openace/open-ace:latest postgres:15-alpine

# Remove data volumes (complete cleanup)
docker volume rm open-ace_agent-state open-ace_config-data open-ace_postgres-data open-ace_workspace-data

# Remove local configuration (optional)
rm -rf ~/.open-ace ./logs
```

Multi-user deployments also have the `home-data` volume (`open-ace_home-data`). Volume names carry the Compose project prefix (`open-ace_` for a default clone); list yours with `docker volume ls | grep open-ace`.

## Troubleshooting

### Port Already in Use

```bash
# Find process using port 19888
lsof -i :19888

# Kill process
kill -9 <PID>
```

Or change the Open ACE port (see [Port Configuration](#port-configuration)).

#### macOS AirPlay Legacy Note

Historically, macOS Monterey (12) and later enabled **AirPlay Receiver** by default, which listened on port **5000** and conflicted with services on that port. Open ACE listens on 19888, so this conflict no longer applies; the note is kept for history. If you do hit a port clash, disable AirPlay Receiver (System Settings -> General -> AirDrop & Handoff -> turn off "AirPlay Receiver") or change the Open ACE port.

### Database Connection Failures

```bash
docker compose ps
docker compose logs postgres
docker compose logs -f open-ace
```

Ensure PostgreSQL is healthy (`pg_isready` healthcheck) and `DB_PASSWORD` in `.env` matches.

### Permission Issues

```bash
# Fix permissions
chmod -R 755 ~/.open-ace/
```

### code-server Verification

code-server powers the "Open VS Code" button in local workspace sessions:

```bash
# Verify in Docker container
docker run --rm <image> which code-server
docker run --rm <image> code-server --version
```

| Error Message | Cause | Solution |
|--------------|-------|----------|
| `code-server is not installed` | Image version outdated or not built correctly | Rebuild Docker image |
| `code-server: command not found` | PATH issue | Check if `/usr/bin/code-server` exists |
| Installation failed | Network issue | Check network connection, consider using proxy |

## Related Documentation

- [ENV_REFERENCE.md](ENV_REFERENCE.md) — authoritative environment variable table
- [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) — config.json reference
- [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) — multi-user deployment
- [UPGRADING.md](UPGRADING.md) — upgrade, migration failure handling, rollback
- [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) — secrets and encryption key rotation
- [NGINX.md](NGINX.md) — HTTPS reverse proxy
- [KUBERNETES.md](KUBERNETES.md) — Kubernetes deployment
- [REMOTE_AGENT.md](REMOTE_AGENT.md) / [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md) — remote integrations

---

## 中文

> **ACE** = **AI Computing Explorer**

本指南涵盖 Open ACE 的部署：Docker 生产路径、无 Docker 的本地试用、配置、数据持久化、数据采集与故障排查。

## 目录

- [安全模式必读](#安全模式必读)
- [快速开始（Docker）](#快速开始docker)
- [本地试用（无 Docker）](#本地试用无-docker)
- [配置](#配置)
- [数据持久化](#数据持久化)
- [管理命令](#管理命令)
- [数据采集](#数据采集)
- [部署场景](#部署场景)
- [系统服务](#系统服务)
- [多用户工作区](#多用户工作区)
- [升级](#升级)
- [卸载](#卸载)
- [故障排查](#故障排查)
- [相关文档](#相关文档)

## 安全模式必读

任何生产部署之前，必须显式设置安全模式（Issue #2331）：

```bash
OPENACE_SECURITY_MODE=production
```

- `production`——强制安全检查。必须显式设置 `SECRET_KEY` 和 `OPENACE_ENCRYPTION_KEY`（各不少于 32 字符）以及强 `DB_PASSWORD`；弱值或占位符会拒绝启动。
- `pilot`——试用环境。缺失的密钥自动生成，但输出强警告。
- `development`——本地开发。缺失的密钥自动生成，输出一般警告。

最快且正确的做法是使用 bootstrap 脚本，它会写入并校验 `.env`（`OPENACE_SECURITY_MODE=production`、强随机的 `SECRET_KEY` / `OPENACE_ENCRYPTION_KEY` / `DB_PASSWORD`）：

```bash
./scripts/bootstrap-compose-env.sh
```

注意事项：

- 单用户 `docker-compose.yml` 默认 `development`，便于一键本地启动；生产环境必须显式设置该变量。
- 多用户 overlay 在缺少必需密钥时会以 `Run ./scripts/bootstrap-compose-env.sh first` 快速失败。
- 生产环境还需设置 `SSO_ALLOWED_REDIRECT_DOMAINS`（逗号分隔的域名白名单）；未配置时，非 localhost 来源的 SSO 登录会被拒绝（Issue #3224）。
- 权威环境变量总表见 [ENV_REFERENCE.md](ENV_REFERENCE.md)，密钥轮转影响见 [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md)。

## 快速开始（Docker）

Docker Compose 栈会运行 Open ACE 容器和一个 PostgreSQL 15 数据库，所有持久化数据都存放在命名卷中。

```bash
# 1. 在服务器上克隆仓库
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 2. 生成并校验 .env（SECRET_KEY、OPENACE_ENCRYPTION_KEY、DB_PASSWORD 等）
./scripts/bootstrap-compose-env.sh

# 3. 启动（默认拉取 openace/open-ace:latest 预构建镜像）
docker compose up -d --wait

# 4. 验证
docker compose ps
docker compose logs -f open-ace
```

然后访问 `http://localhost:19888`，使用默认凭证登录：

```
用户名: admin
密码: admin123
```

**重要**：首次登录后请立即修改默认密码。

说明：

- 镜像为 `openace/open-ace:latest`（可用 `IMAGE_NAME` 覆盖，例如固定为 `openace/open-ace:v1.2.0` 或本地构建的 `open-ace:dev`）。开发者可本地构建：`docker build -t open-ace:dev --target production .`。
- `docker compose down` 不会删除数据；所有状态都在命名卷中（见[数据持久化](#数据持久化)）。
- 容器默认以非 root 用户 `open-ace`（uid 1000）运行。只有多用户模式需要 root（见[多用户工作区](#多用户工作区)）。
- 单用户模式下 scheduler 调度worker是可选 profile：`docker compose --profile scheduler up -d`。
- HTTPS 请按 [NGINX.md](NGINX.md) 配置反向代理；Kubernetes 部署见 [KUBERNETES.md](KUBERNETES.md)。

### 离线安装

无外网服务器可使用 `scripts/install-central/docker-method/` 下的镜像导出脚本：

```bash
# 在有网络的机器上：构建并导出全部镜像
./scripts/install-central/docker-method/export-image.sh --build --compress

# 将 open-ace-images.tar.gz（及 install.sh）拷贝到目标服务器后执行
./install.sh
```

也可以用原生 Docker 命令完成：

```bash
docker pull openace/open-ace:latest
docker save openace/open-ace:latest | gzip > open-ace-images.tar.gz
gunzip -c open-ace-images.tar.gz | docker load
```

国内网络无法访问 Docker Hub 时，在 `.env` 中设置 `BASE_REGISTRY=docker.m.daocloud.io`，让构建和 PostgreSQL 镜像都走镜像源。

## 本地试用（无 Docker）

```bash
# 安装依赖
pip install -r requirements.txt

# 初始化配置
python3 cli.py config init

# 运行数据库迁移（首次启动前必需）
alembic upgrade head

# 启动 Web 服务器
python3 server.py

# 访问 http://localhost:19888
```

开发/试用模式支持零配置启动（Issue #2667）：缺失的 `OPENACE_ENCRYPTION_KEY` 会在首次启动自动生成并持久化到 `~/.open-ace/generated-secrets.env`（重启复用、不轮转）。生产模式仍要求显式设置。

## 配置

配置分两层：

1. **环境变量**（Docker Compose 用 `.env`）控制部署层事项：端口、数据库、安全模式、多用户开关。含默认值与轮转影响的权威总表见 [ENV_REFERENCE.md](ENV_REFERENCE.md)。
2. **`config.json`** 控制应用行为（采集哪些工具、邮件、工作区）。裸机部署位于 `~/.open-ace/config.json`；Docker 部署在首次启动时于 `config-data` 卷内自动生成（`/home/open-ace/.open-ace/config.json`），也可以把自定义文件以只读方式挂载到该路径。完整参考见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)。

多数部署会碰到的环境变量：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `PORT` | `19888` | 宿主机 Web 端口（容器内固定监听 19888） |
| `SERVER_IP` | 自动探测 | 浏览器访问服务使用的地址（Issue #1306）；从其他机器访问时显式设置，如 `SERVER_IP=192.168.1.100` |
| `WORKSPACE_PORT_RANGE_START` / `WORKSPACE_PORT_RANGE_END` | `3100` / `3200` | 发布的每用户工作区 WebUI 实例端口段 |
| `WORKSPACE_MULTI_USER_MODE` | `false` | 启用多用户工作区模式 |
| `DB_USER` / `DB_NAME` | `ace` / `ace` | PostgreSQL 用户与数据库名 |
| `DB_PASSWORD` | 开发默认值 | 生产环境必须设置强密码 |
| `IMAGE_NAME` | `openace/open-ace:latest` | 应用镜像 |
| `BASE_REGISTRY` | `docker.io` | 基础镜像仓库覆盖 |

### 端口配置

- 裸机：修改 `~/.open-ace/config.json` 的 `server.web_port` / `server.web_host`，重启服务。
- Docker：临时修改 `PORT=5001 docker compose up -d`；永久修改 `echo "PORT=5001" >> .env` 后 `docker compose down && docker compose up -d`。

防火墙（外网访问）：

```bash
# Ubuntu/Debian
sudo ufw allow 19888/tcp

# CentOS/RHEL
sudo firewall-cmd --add-port=19888/tcp --permanent
sudo firewall-cmd --reload
```

## 数据持久化

不存在 `./data` 目录。所有持久化数据都在 `docker-compose.yml` 声明的 4 个命名卷中：

| 卷 | 挂载点 | 内容 |
|----|--------|------|
| `config-data` | `/home/open-ace/.open-ace` | `config.json` 与 `generated-secrets.env` |
| `postgres-data` | `/var/lib/postgresql/data` | PostgreSQL 数据库（用户、会话、全部应用数据） |
| `workspace-data` | `/workspace` | 各用户的项目目录 |
| `agent-state` | `/var/lib/openace` | CLI 会话记录，由应用与 scheduler 服务共享（Issue #3237） |

此外，宿主机 `./logs` 以绑定挂载方式映射到 `/app/logs`。多用户部署额外增加第 5 个卷 `home-data`（挂载 `/home`），存放用户家目录（Issue #2729）。

查看卷：

```bash
docker volume ls | grep open-ace
# 应看到：agent-state、config-data、postgres-data、workspace-data
```

数据库备份使用 `pg_dump`（见 [DATABASE_BACKUP.md](DATABASE_BACKUP.md)）；`postgres-data` 卷是唯一可信数据源。

## 管理命令

```bash
cd /path/to/open-ace

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

# 停止服务（卷会保留）
docker compose down

# 启动服务
docker compose up -d
```

## 数据采集

Open ACE 通过 `scripts/` 下的采集脚本汇聚本地 AI CLI 工具的用量：

| 脚本 | 数据来源 |
|------|----------|
| `fetch_claude.py` | `~/.claude/projects`（Claude Code JSONL） |
| `fetch_qwen.py` | `~/.qwen/projects`（Qwen Code JSONL） |
| `fetch_codex.py` | `~/.codex/sessions`（Codex CLI JSONL） |
| `fetch_zcode.py` | `~/.zcode/cli/db/db.sqlite`（ZCode CLI SQLite） |
| `fetch_openclaw.py` | `~/.openclaw/agents`（OpenClaw） |

手动采集：

```bash
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_codex.py
python3 scripts/fetch_zcode.py
python3 scripts/fetch_openclaw.py

# 采集指定天数
python3 scripts/fetch_claude.py --days 7
```

定时采集（裸机部署）：

```bash
crontab -e
```

```bash
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_codex.py >> logs/cron.log 2>&1
45 0 * * * cd /path/to/open-ace && python3 scripts/fetch_zcode.py >> logs/cron.log 2>&1
50 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

## 部署场景

### 场景一：单机部署（推荐个人使用）

所有组件在同一台机器：`python3 server.py` 加上文的 cron 任务。

### 场景二：中心服务器 + 远程采集器

分布式环境使用 `scripts/manage.py` 部署。其用法为「位置参数 action + 可选 `--remote` 开关」：

```bash
python3 scripts/manage.py init      # 初始化配置
python3 scripts/manage.py deploy    # 部署到 ~/open-ace/
python3 scripts/manage.py install   # 安装为系统服务
python3 scripts/manage.py start     # 启动 Web 服务器（部署目录）
python3 scripts/manage.py stop      # 停止 Web 服务器
python3 scripts/manage.py restart   # 重启 Web 服务器
python3 scripts/manage.py status    # 检查服务状态
python3 scripts/manage.py show      # 查看当前配置
python3 scripts/manage.py --remote deploy  # 部署到远程机器
python3 scripts/manage.py --remote sync    # 同步文件到远程
python3 scripts/manage.py --remote status  # 检查远程状态
```

`--remote` 只支持 `deploy`、`sync`、`status`；`sync` 仅远程可用。

也可以手动配置远程机器：

```bash
scp -r open-ace user@remote:/path/to/
ssh user@remote "cd /path/to/open-ace && python3 scripts/fetch_openclaw.py"
```

## 系统服务

### Linux (systemd)

创建 `/etc/systemd/system/open-ace.service`：

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

## 多用户工作区

多用户模式为每个用户提供隔离的系统账号和独立的 `qwen-code-webui` 实例，容器以 root 运行。三种启动方式、sudoers 配置与版本要求见 [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md)。

## 升级

拉取新镜像并重启即可；数据库迁移在容器启动时自动执行，且受最低升级基线 `baseline_2026_06_23` 约束。详细步骤、迁移失败处置与回滚见 [UPGRADING.md](UPGRADING.md)。

## 卸载

```bash
# 停止并删除容器
docker compose down

# 删除镜像
docker rmi openace/open-ace:latest postgres:15-alpine

# 删除数据卷（彻底清理）
docker volume rm open-ace_agent-state open-ace_config-data open-ace_postgres-data open-ace_workspace-data

# 删除本地配置（可选）
rm -rf ~/.open-ace ./logs
```

多用户部署还包含 `home-data` 卷（`open-ace_home-data`）。卷名带有 Compose 项目前缀（默认克隆目录为 `open-ace_`）；用 `docker volume ls | grep open-ace` 列出你的卷。

## 故障排查

### 端口被占用

```bash
# 查找占用 19888 端口的进程
lsof -i :19888

# 终止进程
kill -9 <PID>
```

或修改 Open ACE 端口（见[端口配置](#端口配置)）。

#### macOS AirPlay 历史注记

历史上，macOS Monterey (12) 及以后版本默认启用 **AirPlay Receiver**，监听 **5000** 端口，会与占用该端口的服务冲突。Open ACE 现监听 19888 端口，此冲突已不存在；保留此注记仅作历史参考。若确实遇到端口冲突，可关闭 AirPlay Receiver（系统设置 → 通用 → AirDrop 与接力 → 关闭「AirPlay 接收器」）或修改 Open ACE 端口。

### 数据库连接失败

```bash
docker compose ps
docker compose logs postgres
docker compose logs -f open-ace
```

确认 PostgreSQL 健康（`pg_isready` 健康检查通过）且 `.env` 中的 `DB_PASSWORD` 匹配。

### 权限问题

```bash
# 修复权限
chmod -R 755 ~/.open-ace/
```

### code-server 安装验证

code-server 用于本地工作区会话的「打开 VS Code」功能：

```bash
# 在 Docker 容器中验证
docker run --rm <image> which code-server
docker run --rm <image> code-server --version
```

| 错误信息 | 原因 | 解决方案 |
|---------|------|----------|
| `code-server is not installed` | 镜像版本过旧或未正确构建 | 重新构建 Docker 镜像 |
| `code-server: command not found` | PATH 问题 | 检查 `/usr/bin/code-server` 是否存在 |
| 安装失败 | 网络问题 | 检查网络连接，考虑使用代理 |

## 相关文档

- [ENV_REFERENCE.md](ENV_REFERENCE.md)——权威环境变量总表
- [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)——config.json 参考
- [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md)——多用户部署
- [UPGRADING.md](UPGRADING.md)——升级、迁移失败处置、回滚
- [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md)——密钥与加密钥轮换
- [NGINX.md](NGINX.md)——HTTPS 反向代理
- [KUBERNETES.md](KUBERNETES.md)——Kubernetes 部署
- [REMOTE_AGENT.md](REMOTE_AGENT.md) / [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md)——远程集成

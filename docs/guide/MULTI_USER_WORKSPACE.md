# Multi-User Workspace — 多用户工作区

[English](#english) | [中文](#中文)

---

## English

Multi-user workspace mode gives every user an isolated system account and a dedicated `qwen-code-webui` process, so each person's projects, sessions, and files stay separated. It requires running the Open ACE container (or process) as **root**, because it creates system users (`useradd`), fixes ownership (`chown`), and switches identity (`sudo -u <user>`).

> Windows does **not** support multi-user mode: the configuration is automatically downgraded to single-user mode (direct execution without user switching), because Windows has no equivalent of `sudo -u`.

## Table of Contents

- [Three Ways to Start](#three-ways-to-start)
- [Deployment Modes: Docker vs Bare Metal](#deployment-modes-docker-vs-bare-metal)
- [Port Range Configuration](#port-range-configuration)
- [sudoers Configuration](#sudoers-configuration)
- [User Account Requirements](#user-account-requirements)
- [Migrating from Single-User Mode](#migrating-from-single-user-mode)
- [Troubleshooting](#troubleshooting)

## Three Ways to Start

### Option 1: One-click startup script (recommended)

```bash
./scripts/start-multi-user.sh
```

The script automatically detects the Docker Compose version (v1/v2), validates that `docker-compose.yml` and `docker-compose.multi-user.yml` exist, starts the multi-user containers, and prints the access URL and status. Extra arguments pass through to compose (e.g. `./scripts/start-multi-user.sh --build`).

### Option 2: Docker Compose overlay

```bash
# One-shot: generate and validate .env for production
./scripts/bootstrap-compose-env.sh
docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
```

The overlay (`docker-compose.multi-user.yml`) automatically configures:

- Container runs as root (`user: "0"`)
- `WORKSPACE_MULTI_USER_MODE=true`
- Explicit root authorization (`OPENACE_ALLOW_ROOT_MULTI_USER=1`)
- Configuration persistence (`OPENACE_CONFIG_DIR=/home/open-ace/.open-ace`, matching the `config-data` volume mount)
- Production security mode by default, and hard failures (`Run ./scripts/bootstrap-compose-env.sh first`) if `DB_PASSWORD` / `SECRET_KEY` / `OPENACE_ENCRYPTION_KEY` are missing
- A `home-data` volume at `/home` shared with the scheduler service for Qwen session collection (Issue #2729)
- The scheduler service enabled by default (its single-user `profiles: [scheduler]` gate is cleared)

### Option 3: Manual configuration

```bash
docker run --user 0 -e WORKSPACE_MULTI_USER_MODE=true \
  -e OPENACE_ALLOW_ROOT_MULTI_USER=1 \
  -e OPENACE_CONFIG_DIR=/home/open-ace/.open-ace ...
```

or with environment variables on compose:

```bash
WORKSPACE_MULTI_USER_MODE=true \
OPENACE_ALLOW_ROOT_MULTI_USER=1 \
docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d
```

Configuration consistency (Issue #2242): when configuring multi-user mode manually, ALL of the following must hold, otherwise the entrypoint exits at startup with a specific fix hint instead of silently failing on `useradd`/`chown` permission errors:

1. The container runs as root (`docker run --user 0`, compose `user: "0"`, or manifest `runAsUser: 0`)
2. `WORKSPACE_MULTI_USER_MODE=true`
3. `OPENACE_ALLOW_ROOT_MULTI_USER=1` (explicit opt-in)
4. `OPENACE_CONFIG_DIR=/home/open-ace/.open-ace` (config persistence)

## Deployment Modes: Docker vs Bare Metal

### Docker root mode

The Docker image already contains everything: Node.js 22, `qwen-code-webui@0.2.43`, and `@qwen-code/qwen-code@0.23.3` (pinned in the Dockerfile). Nothing is installed on the host — the webui/CLI processes and the sudoers file all live inside the container:

- `docker-entrypoint.sh` generates `/etc/sudoers.d/open-ace-webui` at container startup (see below).
- System users are created automatically: when an admin creates a user with a `system_account` in the Open ACE admin UI, the entrypoint creates the Linux user, the workspace directory (`/workspace/<username>/`), and the `~/.qwen/` directory. No manual `useradd` needed.

### Bare-metal mode (package / binary installs)

When running Open ACE directly on the host (package-method install, systemd service), you provision the qwen stack yourself:

1. **Node.js >= 22** (the CLI's `engines.node` is >= 22; npm only warns on a mismatch and still exits 0, so verify the version yourself):

   ```bash
   # NodeSource setup (Rocky Linux/CentOS)
   curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
   yum install -y nodejs

   # Or Debian/Ubuntu
   curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
   apt-get install -y nodejs
   ```

2. **Install the pinned version pair** (the same validated combination shipped by the installers and the Docker image):

   ```bash
   npm install -g qwen-code-webui@0.2.43 @qwen-code/qwen-code@0.23.3

   # Verify installation
   which qwen-code-webui
   # Should output: /usr/local/bin/qwen-code-webui
   ```

   Notes: the CLI package name is `@qwen-code/qwen-code` (not `qwen-code`); the CLI command is `qwen` (not `qwen-code`). Building `qwen-code-webui` from source is not recommended — upstream git tags lag the npm releases, so cloning HEAD yields an unvalidated version.

3. **Generate the sudoers file** with the unified generator (see [sudoers Configuration](#sudoers-configuration)).

4. **systemd service**: ensure the unit allows sudo execution, e.g. `AmbientCapabilities=CAP_SETUID CAP_SETGID` and `NoNewPrivileges=false`.

### Security requirements (Issue #1893)

Multi-user mode requires root and is suitable for controlled environments only. In production:

1. Set `OPENACE_SECURITY_MODE=production` (the overlay does this by default)
2. Set strong secrets: `DB_PASSWORD`, `SECRET_KEY`, `OPENACE_ENCRYPTION_KEY`
3. Set `OPENACE_ALLOW_ROOT_MULTI_USER=1` explicitly to acknowledge root execution

## Port Range Configuration

Each running user instance occupies one port from the workspace pool.

**Docker Compose** publishes the range via `WORKSPACE_PORT_RANGE_START` / `WORKSPACE_PORT_RANGE_END` (defaults `3100` / `3200`, set them in `.env`):

```bash
WORKSPACE_PORT_RANGE_START=3100
WORKSPACE_PORT_RANGE_END=3200
```

Note: Compose expands `3100-3200:3100-3200` into 101 port mappings. If startup is slow or ports clash, shrink the range (e.g. `3100-3110`).

**The application itself** reads the pool from `config.json` (the install scripts write these env values into it):

```json
{
  "workspace": {
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30
  }
}
```

Recommendations:

- Use ports above 3000 (avoid common service ports)
- Allocate enough ports for expected concurrent users (e.g. 100 ports for up to 100 users); `max_instances` defaults to 30
- Verify ports are not used: `sudo netstat -tlnp | grep 3100-3200`

## sudoers Configuration

Docker and package deployments share one generator, `scripts/generate-sudoers.sh` (Issue #2334 — single source of truth). It resolves all paths before writing, validates with `visudo -c`, installs atomically with mode 440, keeps a `.bak` backup, and appends to an audit log.

- **Docker**: `docker-entrypoint.sh` generates `/etc/sudoers.d/open-ace-webui` automatically at startup. No host-side sudoers is ever written.
- **Bare metal** (run as root or with sudo):

  ```bash
  ./scripts/generate-sudoers.sh --output /etc/sudoers.d/open-ace-webui --user open-ace
  ```

  Options: `--output <file>` (required), `--user <user>` (default `open-ace`), `--install-dir <dir>`, `--dry-run` (print without writing).

The generated file authorizes only wrapper binaries and narrow command aliases — direct `qwen-code-webui`, `git`, `gh`, `cat`, `chown`, `useradd`, `rm` wildcards are deliberately NOT granted:

| Rule group | Grants |
|------------|--------|
| `GIT_SAFE` / `GH_SAFE` | `/usr/local/bin/openace-git *` / `/usr/local/bin/openace-gh *` — cross-user git/gh grammar validated by root-owned wrappers |
| `MKDIR_SAFE` | `/usr/bin/mkdir *`, `/bin/mkdir *` — cross-user verifier worktree directories (Issue #2674) |
| `FETCH_WRAPPER` | `/usr/local/bin/openace-fetch-wrapper` — reading tool data under permission-700 user home directories (Issue #2543) |
| WebUI launcher | `/usr/local/bin/openace-webui-launch * "<webui-path>" *` — fail-closed; the wrapper must exist (Issue #2334) |
| Agent launch | `/usr/local/bin/openace-run-as --isolated *` — all AI CLIs launch through this wrapper with `env -i` isolation |
| Security wrappers | `openace-chown`, `openace-useradd`, `openace-cat`, `openace-mkdir`, `openace-write-as`, `openace-rm` — each validates paths/users internally |
| `OPENACE_UTILS` | read-only utilities: `test`, `ls`, `stat`, `id`, `find` |

`Defaults env_keep` preserves only non-sensitive variables (`OPENACE_PROXY_TOKEN`, `OPENACE_PROXY_URL`, `OPENACE_MODEL`, `OPENACE_LOG_DIR`, `GIT_*` signing identity, `SESSION_TIMEOUT_MS`, `KEEPALIVE_INTERVAL_MS`); API keys, `OPENCLAW_TOKEN`, and `GH_TOKEN` are excluded, and `secure_path` is pinned. A legacy minimal fallback (older setups only, without the wrapper hardening) is:

```bash
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *
```

## User Account Requirements

**Docker mode**: no manual account creation. Create the user in the admin UI with a `system_account` (or via `POST /api/admin/users` with `"system_account": "alice"`); the entrypoint creates the Linux user, `/workspace/<username>/`, and `~/.qwen/` automatically.

**Bare-metal mode**: each user with a `system_account` must have:

1. A Linux account:

   ```bash
   # Check if user exists
   id <system_account>

   # Create if needed
   sudo useradd -m <system_account>
   ```

2. An accessible qwen directory:

   ```bash
   sudo mkdir -p /home/<system_account>/.qwen/projects
   sudo chown -R <system_account>:<system_account> /home/<system_account>/.qwen
   ```

3. Accessible project directories, if applicable.

Project storage layout (Docker, inside the `workspace-data` volume):

```
/workspace/                    # Docker volume
├── alice/                     # user alice's workspace
│   ├── .qwen/                 # qwen config
│   ├── project-1/
│   └── project-2/
└── bob/                       # user bob's workspace
    ├── .qwen/
    └── my-project/
```

## Migrating from Single-User Mode

1. **Stop the existing containers**

   ```bash
   docker compose down
   ```

   > Note: no data is lost — Docker volumes are preserved.

2. **Check the data volumes**

   ```bash
   docker volume ls | grep open-ace
   # expect: agent-state, config-data, postgres-data, workspace-data
   ```

3. **Start in multi-user mode**

   ```bash
   ./scripts/start-multi-user.sh
   ```

   or

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d
   ```

4. **Verify data integrity**: users and session data intact, configuration loads, workspace functionality works.

Verification checklist:

- [ ] Database data preserved (users, sessions, configuration)
- [ ] Docker volume data preserved (config-data, workspace-data)
- [ ] Users can log in normally
- [ ] Workspaces can be created and used normally
- [ ] Existing AI sessions can be restored

If you previously set `"multi_user_mode": true` in config.json, prefer the overlay file (or env vars) and set the config value back to `false`.

## Troubleshooting

### Startup errors

| Error | Cause | Solution |
|-------|-------|----------|
| `multi-user workspace mode requires root` | multi-user mode enabled while running as non-root | use `./scripts/start-multi-user.sh` or the overlay file |
| `OPENACE_ALLOW_ROOT_MULTI_USER=1 is not set` | running as root without explicit authorization | use `./scripts/start-multi-user.sh` or set the env var |
| `Run ./scripts/bootstrap-compose-env.sh first` | production secrets missing | run `./scripts/bootstrap-compose-env.sh` |
| `Workspace failed to load` | iframe load failure or timeout | check container logs, verify config, restart the container |
| `docker-compose.multi-user.yml not found` | overlay file missing | make sure the repo was cloned correctly, or download the file from GitHub |

### Runtime issues

| Issue | Cause | Solution |
|-------|-------|----------|
| "sudo: no tty present" | sudo requires password | add NOPASSWD to sudoers |
| "qwen-code-webui not found" | executable not installed | install webui in PATH (bare metal) |
| "Permission denied" | user lacks permissions | check sudoers configuration |
| Port allocation failed | all ports in use | increase port range or reduce `max_instances` |
| Process won't start | user account missing | create the `system_account` user |

### Debugging a workspace instance (Docker)

```bash
# Check qwen-code-webui is available
docker compose exec open-ace which qwen-code-webui

# Check the sudoers configuration
docker compose exec open-ace cat /etc/sudoers.d/open-ace-webui

# Check the system user was created
docker compose exec open-ace id alice

# Check workspace directories
docker compose exec open-ace ls -la /workspace/

# Try launching an instance manually
docker compose exec open-ace sudo -u alice qwen-code-webui --port 3100 --host 0.0.0.0
```

### Checking multi-user status

```bash
# View running instances
curl http://localhost:19888/api/workspace/instances

# Check logs
tail -f /home/open-ace/open-ace/logs/open-ace.log | grep WebUIManager
```

See also [DEPLOYMENT.md](DEPLOYMENT.md) for the base deployment and [ENV_REFERENCE.md](ENV_REFERENCE.md) for the workspace-related environment variables.

---

## 中文

多用户工作区模式为每个用户提供隔离的系统账号和独立的 `qwen-code-webui` 进程，使每个人的项目、会话与文件互不干扰。该模式要求以 **root** 运行 Open ACE 容器（或进程），因为它需要创建系统用户（`useradd`）、修复属主（`chown`）并切换身份（`sudo -u <user>`）。

> Windows **不支持**多用户模式：配置会自动降级为单用户模式（不切换用户的直接执行），因为 Windows 没有等同于 `sudo -u` 的机制。

## 目录

- [三种启动方式](#三种启动方式)
- [部署形态：Docker 与裸机](#部署形态docker-与裸机)
- [端口范围配置](#端口范围配置)
- [sudoers 配置](#sudoers-配置)
- [用户账户要求](#用户账户要求)
- [从单用户模式迁移](#从单用户模式迁移)
- [故障排查](#故障排查)

## 三种启动方式

### 方式一：一键启动脚本（推荐）

```bash
./scripts/start-multi-user.sh
```

脚本自动检测 Docker Compose 版本（v1/v2）、验证 `docker-compose.yml` 与 `docker-compose.multi-user.yml` 存在、启动多用户容器，并输出访问地址和状态。附加参数会透传给 compose（如 `./scripts/start-multi-user.sh --build`）。

### 方式二：Docker Compose overlay

```bash
# 一次性生成并校验生产用 .env
./scripts/bootstrap-compose-env.sh
docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d --wait
```

overlay 文件（`docker-compose.multi-user.yml`）自动配置：

- 容器以 root 运行（`user: "0"`）
- `WORKSPACE_MULTI_USER_MODE=true`
- 显式 root 授权（`OPENACE_ALLOW_ROOT_MULTI_USER=1`）
- 配置持久化（`OPENACE_CONFIG_DIR=/home/open-ace/.open-ace`，与 `config-data` 卷挂载路径一致）
- 默认生产安全模式；缺少 `DB_PASSWORD` / `SECRET_KEY` / `OPENACE_ENCRYPTION_KEY` 时直接失败（提示 `Run ./scripts/bootstrap-compose-env.sh first`）
- 新增 `home-data` 卷挂载 `/home`，与 scheduler 服务共享用于 Qwen 会话采集（Issue #2729）
- scheduler 服务默认启用（清除了单用户模式的 `profiles: [scheduler]` 门槛）

### 方式三：手动配置

```bash
docker run --user 0 -e WORKSPACE_MULTI_USER_MODE=true \
  -e OPENACE_ALLOW_ROOT_MULTI_USER=1 \
  -e OPENACE_CONFIG_DIR=/home/open-ace/.open-ace ...
```

或在 compose 上使用环境变量：

```bash
WORKSPACE_MULTI_USER_MODE=true \
OPENACE_ALLOW_ROOT_MULTI_USER=1 \
docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d
```

配置一致性要求（Issue #2242）：手动配置多用户模式时，以下条件必须**全部满足**，否则入口脚本会在启动时以明确的修复提示退出，而不是默默吞掉 `useradd`/`chown` 权限错误：

1. 容器以 root 运行（`docker run --user 0`、compose `user: "0"` 或清单 `runAsUser: 0`）
2. `WORKSPACE_MULTI_USER_MODE=true`
3. `OPENACE_ALLOW_ROOT_MULTI_USER=1`（显式授权）
4. `OPENACE_CONFIG_DIR=/home/open-ace/.open-ace`（配置持久化）

## 部署形态：Docker 与裸机

### Docker root 模式

Docker 镜像已内置全部依赖：Node.js 22、`qwen-code-webui@0.2.43`、`@qwen-code/qwen-code@0.23.3`（版本固定在 Dockerfile 中）。宿主机无需安装任何东西——webui/CLI 进程与 sudoers 全部在容器内：

- `docker-entrypoint.sh` 在容器启动时生成 `/etc/sudoers.d/open-ace-webui`（见下文）。
- 系统用户自动创建：管理员在 Open ACE 后台创建带 `system_account` 的用户时，入口脚本会自动创建 Linux 用户、workspace 目录（`/workspace/<username>/`）和 `~/.qwen/` 目录，无需手动 `useradd`。

### 裸机模式（package / 二进制安装）

直接在宿主机运行 Open ACE（package 方式安装、systemd 服务）时，需自行准备 qwen 技术栈：

1. **Node.js >= 22**（CLI 的 `engines.node` 要求 >= 22；npm 对 engines 不匹配只警告仍返回成功，需自行核对版本）：

   ```bash
   # NodeSource 安装（Rocky Linux/CentOS）
   curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
   yum install -y nodejs

   # 或 Debian/Ubuntu
   curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
   apt-get install -y nodejs
   ```

2. **安装固定版本对**（与安装脚本、Docker 镜像一致的已验证组合）：

   ```bash
   npm install -g qwen-code-webui@0.2.43 @qwen-code/qwen-code@0.23.3

   # 验证安装
   which qwen-code-webui
   # 应输出: /usr/local/bin/qwen-code-webui
   ```

   注意：CLI 包名是 `@qwen-code/qwen-code`（不是 `qwen-code`）；CLI 命令名是 `qwen`（不是 `qwen-code`）。不建议从源码构建 `qwen-code-webui`——上游 git tag 滞后于 npm 发布，clone HEAD 得到的是未经本部署验证的版本。

3. **用统一生成器生成 sudoers**（见 [sudoers 配置](#sudoers-配置)）。

4. **systemd 服务**：确保 unit 允许 sudo 执行，如 `AmbientCapabilities=CAP_SETUID CAP_SETGID` 与 `NoNewPrivileges=false`。

### 安全要求（Issue #1893）

多用户模式需要 root，仅适用于受控环境。生产环境必须：

1. 设置 `OPENACE_SECURITY_MODE=production`（overlay 默认即生产模式）
2. 设置强密钥：`DB_PASSWORD`、`SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`
3. 显式设置 `OPENACE_ALLOW_ROOT_MULTI_USER=1` 确认 root 运行

## 端口范围配置

每个运行中的用户实例占用端口池中的一个端口。

**Docker Compose** 通过 `WORKSPACE_PORT_RANGE_START` / `WORKSPACE_PORT_RANGE_END`（默认 `3100` / `3200`，在 `.env` 中设置）发布端口段：

```bash
WORKSPACE_PORT_RANGE_START=3100
WORKSPACE_PORT_RANGE_END=3200
```

注意：Compose 会把 `3100-3200:3100-3200` 展开为 101 个端口映射。如启动缓慢或端口冲突，可缩小范围（如 `3100-3110`）。

**应用本身**从 `config.json` 读取端口池（安装脚本会把这些环境变量值写入其中）：

```json
{
  "workspace": {
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30
  }
}
```

建议：

- 使用 3000 以上的端口（避开常用服务端口）
- 为预期并发用户数分配足够端口（如 100 个端口支持 100 个用户）；`max_instances` 默认 30
- 验证端口未被占用：`sudo netstat -tlnp | grep 3100-3200`

## sudoers 配置

Docker 与 package 部署共用同一生成器 `scripts/generate-sudoers.sh`（Issue #2334——唯一事实来源）。它在写入前解析全部路径、用 `visudo -c` 校验、以 440 权限原子安装、保留 `.bak` 备份，并写审计日志。

- **Docker**：`docker-entrypoint.sh` 在启动时自动生成 `/etc/sudoers.d/open-ace-webui`，不写宿主机 sudoers。
- **裸机**（以 root 或 sudo 执行）：

  ```bash
  ./scripts/generate-sudoers.sh --output /etc/sudoers.d/open-ace-webui --user open-ace
  ```

  选项：`--output <file>`（必需）、`--user <user>`（默认 `open-ace`）、`--install-dir <dir>`、`--dry-run`（只打印不写入）。

生成的文件只授权 wrapper 二进制和窄化的命令别名——故意**不**授予裸 `qwen-code-webui`、`git`、`gh`、`cat`、`chown`、`useradd`、`rm` 通配：

| 规则组 | 授权内容 |
|--------|----------|
| `GIT_SAFE` / `GH_SAFE` | `/usr/local/bin/openace-git *` / `/usr/local/bin/openace-gh *`——跨用户 git/gh 命令语法由 root 属主 wrapper 校验 |
| `MKDIR_SAFE` | `/usr/bin/mkdir *`、`/bin/mkdir *`——跨用户 verifier worktree 目录（Issue #2674） |
| `FETCH_WRAPPER` | `/usr/local/bin/openace-fetch-wrapper`——读取权限 700 用户 home 下的工具数据（Issue #2543） |
| WebUI launcher | `/usr/local/bin/openace-webui-launch * "<webui-path>" *`——fail-closed，wrapper 必须存在（Issue #2334） |
| Agent 启动 | `/usr/local/bin/openace-run-as --isolated *`——所有 AI CLI 必须经此 wrapper 以 `env -i` 隔离启动 |
| 安全 wrapper | `openace-chown`、`openace-useradd`、`openace-cat`、`openace-mkdir`、`openace-write-as`、`openace-rm`——各自在内部校验路径/用户 |
| `OPENACE_UTILS` | 只读工具：`test`、`ls`、`stat`、`id`、`find` |

`Defaults env_keep` 只保留非敏感变量（`OPENACE_PROXY_TOKEN`、`OPENACE_PROXY_URL`、`OPENACE_MODEL`、`OPENACE_LOG_DIR`、`GIT_*` 签名身份、`SESSION_TIMEOUT_MS`、`KEEPALIVE_INTERVAL_MS`）；API Key、`OPENCLAW_TOKEN`、`GH_TOKEN` 均被排除，且固定了 `secure_path`。旧版最小化回退（仅限老部署，不含 wrapper 加固）：

```bash
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *
```

## 用户账户要求

**Docker 模式**：无需手动创建账户。在管理后台创建用户时设置 `system_account`（或通过 `POST /api/admin/users` 传 `"system_account": "alice"`），入口脚本会自动创建 Linux 用户、`/workspace/<username>/` 和 `~/.qwen/`。

**裸机模式**：每个设置了 `system_account` 的用户必须具备：

1. Linux 账户存在：

   ```bash
   # 检查用户是否存在
   id <system_account>

   # 如需要则创建
   sudo useradd -m <system_account>
   ```

2. qwen 目录可访问：

   ```bash
   sudo mkdir -p /home/<system_account>/.qwen/projects
   sudo chown -R <system_account>:<system_account> /home/<system_account>/.qwen
   ```

3. 项目目录可访问（如适用）。

项目存储结构（Docker，位于 `workspace-data` 卷内）：

```
/workspace/                    # Docker 卷
├── alice/                     # 用户 alice 的 workspace
│   ├── .qwen/                 # qwen 配置
│   ├── project-1/
│   └── project-2/
└── bob/                       # 用户 bob 的 workspace
    ├── .qwen/
    └── my-project/
```

## 从单用户模式迁移

1. **停止现有容器**

   ```bash
   docker compose down
   ```

   > 注意：数据不会丢失，Docker 卷会保留。

2. **检查数据卷**

   ```bash
   docker volume ls | grep open-ace
   # 应看到：agent-state、config-data、postgres-data、workspace-data
   ```

3. **以多用户模式启动**

   ```bash
   ./scripts/start-multi-user.sh
   ```

   或

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d
   ```

4. **验证数据完整性**：用户与会话数据完好、配置正常加载、工作区功能可用。

验证清单：

- [ ] 数据库数据保留（用户、会话、配置）
- [ ] Docker 卷数据保留（config-data、workspace-data）
- [ ] 用户可以正常登录
- [ ] 工作区可以正常创建和使用
- [ ] 已有的 AI 会话可以恢复

若之前在 config.json 中设置过 `"multi_user_mode": true`，建议改用 overlay 文件（或环境变量），并将该配置值改回 `false`。

## 故障排查

### 启动错误

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `multi-user workspace mode requires root` | 以非 root 运行但启用了多用户模式 | 使用 `./scripts/start-multi-user.sh` 或 overlay 文件 |
| `OPENACE_ALLOW_ROOT_MULTI_USER=1 is not set` | 以 root 运行但未显式授权 | 使用 `./scripts/start-multi-user.sh` 或设置环境变量 |
| `Run ./scripts/bootstrap-compose-env.sh first` | 缺少生产密钥 | 执行 `./scripts/bootstrap-compose-env.sh` |
| `工作区加载失败` | iframe 加载失败或超时 | 检查容器日志、验证配置、重启容器 |
| `docker-compose.multi-user.yml not found` | overlay 文件不存在 | 确保正确克隆了仓库，或从 GitHub 下载文件 |

### 运行期问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| "sudo: no tty present" | sudo 需要密码 | 在 sudoers 中添加 NOPASSWD |
| "qwen-code-webui not found" | 可执行文件未安装 | 在 PATH 中安装 webui（裸机） |
| "Permission denied" | 用户缺少权限 | 检查 sudoers 配置 |
| 端口分配失败 | 所有端口已占用 | 增加端口范围或减少 `max_instances` |
| 进程无法启动 | 用户账户缺失 | 创建 `system_account` 用户 |

### 调试工作区实例（Docker）

```bash
# 查看 qwen-code-webui 是否可用
docker compose exec open-ace which qwen-code-webui

# 检查 sudoers 配置
docker compose exec open-ace cat /etc/sudoers.d/open-ace-webui

# 检查系统用户是否创建成功
docker compose exec open-ace id alice

# 检查 workspace 目录
docker compose exec open-ace ls -la /workspace/

# 手动测试启动
docker compose exec open-ace sudo -u alice qwen-code-webui --port 3100 --host 0.0.0.0
```

### 检查多用户状态

```bash
# 查看运行中的实例
curl http://localhost:19888/api/workspace/instances

# 查看日志
tail -f /home/open-ace/open-ace/logs/open-ace.log | grep WebUIManager
```

另见 [DEPLOYMENT.md](DEPLOYMENT.md)（基础部署）与 [ENV_REFERENCE.md](ENV_REFERENCE.md)（工作区相关环境变量）。

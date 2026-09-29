# Environment Variable Reference — 环境变量参考

[English](#english) | [中文](#中文)

---

## English

This is the authoritative table of environment variables recognized by Open ACE, grouped by where they are declared: `.env.example` (the 13 user-facing variables for Docker Compose), the Compose files themselves, the scheduler service, the Kubernetes ConfigMap, and application code. Bare-metal deployments set the same variables through their service manager or shell.

Rotation impact describes what happens when you change the value on an existing deployment.

## Table of Contents

- [Security Mode and Secrets (.env.example)](#security-mode-and-secrets-envexample)
- [Database (.env.example)](#database-envexample)
- [Networking (.env.example)](#networking-envexample)
- [Workspace Mode (.env.example)](#workspace-mode-envexample)
- [Image and Compose Internals (docker-compose.yml)](#image-and-compose-internals-docker-composeyml)
- [Scheduler Service (docker-compose.yml)](#scheduler-service-docker-composeyml)
- [Kubernetes (k8s/configmap.yaml)](#kubernetes-k8sconfigmapyaml)
- [Application-Level Variables (code)](#application-level-variables-code)
- [Auto-Generated Secrets](#auto-generated-secrets)

## Security Mode and Secrets (.env.example)

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `OPENACE_SECURITY_MODE` | unset (Compose default: `development`) | Security mode: `production` (strict checks, secrets required), `pilot` (auto-generate with strong warnings), `development` (auto-generate). Required explicitly in production (Issue #2331) | Switching to `production` without explicit secrets refuses startup; switching away weakens checks | `.env.example`, `docker-compose.yml`, `app/utils/security_mode.py` |
| `SECRET_KEY` | empty | Flask session signing key (>= 32 characters in production) | Rotation invalidates every logged-in session — all users are logged out; no data loss | `.env.example`, `docker-compose.yml` |
| `OPENACE_ENCRYPTION_KEY` | empty | Fernet key material (>= 32 characters) encrypting all 8 secret stores (SSO, SMTP, API keys, integrations); also signs proxy tokens | Rotation without re-encrypting makes stored secrets permanently undecryptable and invalidates proxy tokens — see [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) | `.env.example`, `docker-compose.yml` |
| `UPLOAD_AUTH_KEY` | empty | Shared secret for workspace file-upload endpoints; empty disables the endpoint | Rotation breaks upload clients until they use the new key | `.env.example`, `docker-compose.yml` |
| `SSO_ALLOWED_REDIRECT_DOMAINS` | empty (only `localhost` allowed) | Comma-separated allowlist of domains SSO may redirect to after login (Issue #3224) | Changing the list changes where SSO logins may land; removing a domain blocks its redirects | `.env.example` |

## Database (.env.example)

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `DB_USER` | `ace` | PostgreSQL user (also `POSTGRES_USER`) | Changing it requires re-provisioning the database user; not a routine rotation | `.env.example`, `docker-compose.yml` |
| `DB_PASSWORD` | empty (Compose falls back to `dev-password-change-in-production`) | PostgreSQL password; passed both inside `DATABASE_URL` and standalone for the security baseline check (Issue #3095). Strong value (>= 9 chars) required in production (Issue #1893) | Must be changed in `.env` AND in PostgreSQL (`ALTER USER ... WITH PASSWORD`), then the stack restarted; mismatch = connection failures | `.env.example`, `docker-compose.yml` |
| `DB_NAME` | `ace` | PostgreSQL database name (also `POSTGRES_DB`) | Renaming requires migrating the database; not a routine rotation | `.env.example`, `docker-compose.yml` |

## Networking (.env.example)

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `PORT` | `19888` | Host port mapping for the web UI; the container always listens on 19888 (Issue #1372) | None on data; users and bookmarks must use the new port | `.env.example`, `docker-compose.yml` |
| `SERVER_IP` | empty (auto-detected) | Address browsers use to reach the service (Issue #1306). The entrypoint auto-detects, rejects unreachable reserved ranges (e.g. `0.0.0.0/8` falls back to `localhost`); set explicitly for access from other machines, e.g. `SERVER_IP=192.168.1.100` | Changes generated URLs (workspace links, config `server_url`) | `.env.example`, `docker-compose.yml`, `docker-entrypoint.sh` |
| `WORKSPACE_PORT_RANGE_START` | `3100` | Start of the published port range for per-user workspace WebUI instances; Compose expands the range into individual port mappings | Shrinking the range blocks instances on removed ports; requires recreating containers | `.env.example`, `docker-compose.yml` |
| `WORKSPACE_PORT_RANGE_END` | `3200` | End of the published workspace port range | Same as above | `.env.example`, `docker-compose.yml` |

## Workspace Mode (.env.example)

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `WORKSPACE_MULTI_USER_MODE` | `false` | `true` enables multi-user workspace mode (per-user system accounts + WebUI instances, container runs as root). When set manually, `OPENACE_ALLOW_ROOT_MULTI_USER=1` and `OPENACE_CONFIG_DIR=/home/open-ace/.open-ace` must be set too (Issue #2242) | Toggling changes the runtime security posture; see [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) | `.env.example`, `docker-compose.yml` |

## Image and Compose Internals (docker-compose.yml)

Declared inline by the Compose files; override in `.env` when needed.

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `IMAGE_NAME` | `openace/open-ace:latest` | Application image for the `open-ace` and `scheduler` services | Pinning a different tag is the standard upgrade/rollback lever (see [UPGRADING.md](UPGRADING.md)) | `docker-compose.yml` |
| `BASE_REGISTRY` | `docker.io` | Base-image registry override (e.g. `docker.m.daocloud.io` for mainland China); affects the build stage and the PostgreSQL image reference | Rebuild/re-pull required | `docker-compose.yml` |
| `DATABASE_URL` | constructed | `postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}` — assembled by Compose from the `DB_*` variables | Follows `DB_*` rotation | `docker-compose.yml` |
| `WORKSPACE_BASE_DIR` | `/workspace` (Compose) | Root directory for user projects; supports comma-separated multiple directories. Bare-metal root runs must set it explicitly (`/root` is rejected by the blacklist) | Moving it orphans existing project directories | `docker-compose.yml`, `app/utils/workspace.py` |
| `OPENACE_CONFIG_DIR` | `/home/open-ace/.open-ace` (overlay) | Config persistence directory; must match the `config-data` volume mount path | Pointing it elsewhere loses auto-generated config and secrets | `docker-compose.multi-user.yml` |
| `OPENACE_ALLOW_ROOT_MULTI_USER` | `1` (overlay) | Explicit opt-in for root multi-user execution (Issue #1893) | Removing it makes multi-user mode refuse to start | `docker-compose.multi-user.yml` |
| `OPENACE_AGENT_STATE_ROOT` | `/var/lib/openace/agent-state` | Carried CLI transcripts; must match exactly between the app and scheduler services (Issue #3237) | Mismatch breaks transcript purging between services | `docker-compose.yml` |
| `FETCH_USE_SUDO` | `false` | Whether fetchers use sudo (container runs as root, so sudo is unnecessary; Issue #1121) | — | `docker-compose.yml` |
| `FLASK_ENV` | `production` (scheduler service) | Legacy environment flag; as a security-mode signal it is deprecated and removed in v2.1.0 — migrate to `OPENACE_SECURITY_MODE` (use `scripts/migrate_security_mode.sh`) | Relying on it for security mode stops working in v2.1.0 | `docker-compose.yml`, `.env.example`, `app/utils/security_mode.py` |

## Scheduler Service (docker-compose.yml)

The `scheduler` service (profile `scheduler` in single-user mode; enabled by default under the multi-user overlay) uses:

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `SCHEDULER_MODE` | `scheduler` | Marks the process as the background scheduler worker (leader election ensures one active scheduler, Issue #2187) | — | `docker-compose.yml` |
| `SCHEDULER_HEARTBEAT_INTERVAL` | `10` | Heartbeat interval (seconds) between scheduler beats | Mismatched values across replicas skew liveness detection | `docker-compose.yml` |
| `SCHEDULER_HEARTBEAT_TIMEOUT` | `60` | Seconds after which a silent scheduler is considered dead | Same as above | `docker-compose.yml` |
| `SCHEDULER_LOCK_TIMEOUT` | `1800` | Seconds before the scheduler leadership lock expires | Too-low values can cause duplicate schedulers during long tasks | `docker-compose.yml` |
| `SCHEDULER_METRICS_PORT` | `9090` | Port for the scheduler metrics/health endpoint (healthcheck target) | Container-internal; no host mapping | `docker-compose.yml` |

## Kubernetes (k8s/configmap.yaml)

Declared by the Kubernetes manifests (see [KUBERNETES.md](KUBERNETES.md)); several overlap with the variables above.

| Variable | Default (manifest) | Purpose | Rotation impact | Source |
|----------|--------------------|---------|-----------------|--------|
| `LOG_LEVEL` | `INFO` | Application log level | None; takes effect after restart | `k8s/configmap.yaml` |
| `AUDIT_LOG_RETENTION_DAYS` | `90` | Audit log retention window (days) | Shortening deletes older audit entries on the next cleanup | `k8s/configmap.yaml` |
| `DATA_RETENTION_DAYS` | `365` | Data retention window (days) for cleanup jobs | Shortening deletes older retained data on the next cleanup | `k8s/configmap.yaml` |
| `DB_HOST` / `DB_PORT` | `postgres` / `5432` | Database endpoint inside the cluster | Restart required | `k8s/configmap.yaml` |
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Redis endpoint | Restart required | `k8s/configmap.yaml` |
| `ENABLE_SSO` / `ENABLE_MULTI_TENANT` / `ENABLE_AUDIT_LOG` / `ENABLE_CONTENT_FILTER` | `true` | Feature flags | Toggling enables/disables features after restart | `k8s/configmap.yaml` |
| `REDIS_PASSWORD` | placeholder | Redis password (secret; replace the placeholder before production) | Must match the Redis deployment | `k8s/configmap.yaml` (Secret section) |

## Application-Level Variables (code)

| Variable | Default | Purpose | Rotation impact | Source |
|----------|---------|---------|-----------------|--------|
| `OPENACE_PLATFORM_ADMIN_STRICT_MODE` | `false` | When `true`, only explicit `platform_admin` role passes platform-admin checks; legacy `admin` no longer does (Issue #2332). Migrate existing `role='admin'` accounts to `platform_admin` FIRST, then enable and restart every process; each logs `Platform admin strict mode: ENABLED` at startup | Enabling without migrating accounts locks legacy admins out | `app/auth/permissions.py` |
| `OPENACE_ENCRYPTION_KEYS` | unset | JSON data-key registry: `{"keys": [{"id": 1-255, "value": "...", "status": "active|deprecated|revoked"}, ...], "primary_key_id": N}`; max 5 keys, exactly 1 active; takes precedence over `OPENACE_ENCRYPTION_KEY`; hot-reloaded roughly every 5 seconds | Key changes re-bind new ciphertexts (`v1k<id>:` prefix); see [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) | `app/utils/encryption_key_registry.py` |
| `OPENACE_CORS_ALLOWED_ORIGINS` | unset | Comma-separated explicit API CORS allowlist for non-loopback WebUI origins | Tightening blocks requests from removed origins | `app/__init__.py` |
| `OPENACE_WS_MAX_MESSAGE_BYTES` | `8388608` | Maximum inbound browser WebSocket message size for the terminal / VSCode raw bridges | Lower values reject large paste payloads | `app/ws_frame.py` |
| `OPENACE_TEST_MODE` | unset | `1` marks a CI/test context: skips production-level security checks | Never enable in production | `app/utils/security_mode.py` |
| `OPENCLAW_TOKEN` | unset | OpenClaw API token for data collection | Rotation requires updating the collector | `app/__init__.py` |
| `SMTP_PASSWORD` | unset | Email SMTP password | Rotation requires matching the mail server | `app/__init__.py` |
| `OPENACE_REPLICA_ENDPOINTS` | unset | Comma-separated replica endpoints queried for encryption-key config-version sync status | Informational only (sync dashboard) | `app/routes/encryption_keys.py` |

## Auto-Generated Secrets

For the single-user development path, missing secrets are not an error: `docker-entrypoint.sh` generates strong random values on first start and persists them to `generated-secrets.env` inside the `config-data` volume (`/home/open-ace/.open-ace/generated-secrets.env`); later restarts reuse them without rotating. Bare-metal development works the same via `~/.open-ace/generated-secrets.env` (Issue #2667). Explicitly set values in `.env` always take precedence and are never written to that file. Production mode requires explicit secrets — see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 中文

这是 Open ACE 环境变量的权威总表，按声明位置分组：`.env.example`（Docker Compose 面向用户的 13 个变量）、Compose 文件自身、scheduler 服务、Kubernetes ConfigMap，以及应用代码。裸机部署通过服务管理器或 shell 设置同名变量。

「轮转影响」描述在既有部署上修改该值时会发生什么。

## 目录

- [安全模式与密钥（.env.example）](#安全模式与密钥envexample)
- [数据库（.env.example）](#数据库envexample)
- [网络（.env.example）](#网络envexample)
- [工作区模式（.env.example）](#工作区模式envexample)
- [镜像与 Compose 内部变量（docker-compose.yml）](#镜像与-compose-内部变量docker-composeyml)
- [Scheduler 服务（docker-compose.yml）](#scheduler-服务docker-composeyml)
- [Kubernetes（k8s/configmap.yaml）](#kubernetesk8sconfigmapyaml)
- [应用层变量（代码）](#应用层变量代码)
- [自动生成的密钥](#自动生成的密钥)

## 安全模式与密钥（.env.example）

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `OPENACE_SECURITY_MODE` | 未设置（Compose 默认 `development`） | 安全模式：`production`（强制检查，必须显式设密钥）、`pilot`（自动生成但强警告）、`development`（自动生成）。生产必须显式设置（Issue #2331） | 未显式设密钥时切到 `production` 会拒绝启动；切走则降低安全检查 | `.env.example`、`docker-compose.yml`、`app/utils/security_mode.py` |
| `SECRET_KEY` | 空 | Flask 会话签名密钥（生产要求 >= 32 字符） | 轮转 = 全员下线（所有已登录会话失效）；无数据丢失 | `.env.example`、`docker-compose.yml` |
| `OPENACE_ENCRYPTION_KEY` | 空 | Fernet 密钥材料（>= 32 字符），加密全部 8 类密文存储（SSO、SMTP、API Key、集成凭据），并签发 proxy token | 不重加密直接轮转 = 已存敏感数据永久无法解密，proxy token 全部失效——见 [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) | `.env.example`、`docker-compose.yml` |
| `UPLOAD_AUTH_KEY` | 空 | 工作区文件上传接口共享密钥；留空则该接口禁用 | 轮转后上传客户端需换用新密钥 | `.env.example`、`docker-compose.yml` |
| `SSO_ALLOWED_REDIRECT_DOMAINS` | 空（仅允许 `localhost`） | SSO 登录成功后允许重定向的域名白名单，逗号分隔（Issue #3224） | 修改名单即改变 SSO 可跳转范围；删除域名会拒绝其重定向 | `.env.example` |

## 数据库（.env.example）

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `DB_USER` | `ace` | PostgreSQL 用户（同时作为 `POSTGRES_USER`） | 修改需重建数据库用户，非例行轮转 | `.env.example`、`docker-compose.yml` |
| `DB_PASSWORD` | 空（Compose 回退 `dev-password-change-in-production`） | PostgreSQL 密码；同时写入 `DATABASE_URL` 并以独立变量传递供安全基线检查（Issue #3095）。生产要求强密码 >= 9 字符（Issue #1893） | 需同时改 `.env` 和 PostgreSQL 本体（`ALTER USER ... WITH PASSWORD`）并重启栈；不一致 = 连接失败 | `.env.example`、`docker-compose.yml` |
| `DB_NAME` | `ace` | PostgreSQL 数据库名（同时作为 `POSTGRES_DB`） | 改名需迁移数据库，非例行轮转 | `.env.example`、`docker-compose.yml` |

## 网络（.env.example）

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `PORT` | `19888` | 宿主机 Web 端口映射；容器内固定监听 19888（Issue #1372） | 不影响数据；用户与书签需改用新端口 | `.env.example`、`docker-compose.yml` |
| `SERVER_IP` | 空（自动探测） | 浏览器访问服务使用的地址（Issue #1306）。entrypoint 自动探测，不可达保留段会被拒绝（如 `0.0.0.0/8` 回退 `localhost`）；跨机访问需显式设置，如 `SERVER_IP=192.168.1.100` | 改变生成的 URL（工作区链接、config `server_url`） | `.env.example`、`docker-compose.yml`、`docker-entrypoint.sh` |
| `WORKSPACE_PORT_RANGE_START` | `3100` | 发布的每用户工作区 WebUI 端口段起点；Compose 会把区间展开为逐个端口映射 | 缩小范围会使被移除端口上的实例失联；需重建容器 | `.env.example`、`docker-compose.yml` |
| `WORKSPACE_PORT_RANGE_END` | `3200` | 工作区端口段终点 | 同上 | `.env.example`、`docker-compose.yml` |

## 工作区模式（.env.example）

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `WORKSPACE_MULTI_USER_MODE` | `false` | `true` 启用多用户工作区模式（每用户系统账号 + WebUI 实例，容器以 root 运行）。手动设置时必须同时设置 `OPENACE_ALLOW_ROOT_MULTI_USER=1` 与 `OPENACE_CONFIG_DIR=/home/open-ace/.open-ace`（Issue #2242） | 切换改变运行时安全形态；见 [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) | `.env.example`、`docker-compose.yml` |

## 镜像与 Compose 内部变量（docker-compose.yml）

由 Compose 文件内联声明；需要时在 `.env` 中覆盖。

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `IMAGE_NAME` | `openace/open-ace:latest` | `open-ace` 与 `scheduler` 服务使用的应用镜像 | 固定不同 tag 是标准的升级/回滚手段（见 [UPGRADING.md](UPGRADING.md)） | `docker-compose.yml` |
| `BASE_REGISTRY` | `docker.io` | 基础镜像仓库覆盖（如国内 `docker.m.daocloud.io`）；影响构建阶段与 PostgreSQL 镜像引用 | 需重新构建/拉取 | `docker-compose.yml` |
| `DATABASE_URL` | 拼接生成 | `postgresql://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}`——由 Compose 从 `DB_*` 变量拼装 | 跟随 `DB_*` 轮转 | `docker-compose.yml` |
| `WORKSPACE_BASE_DIR` | `/workspace`（Compose） | 用户项目根目录；支持逗号分隔多目录。裸机以 root 运行时必须显式设置（`/root` 在黑名单中会被拒绝） | 移动后既有项目目录将脱离管理 | `docker-compose.yml`、`app/utils/workspace.py` |
| `OPENACE_CONFIG_DIR` | `/home/open-ace/.open-ace`（overlay） | 配置持久化目录；必须与 `config-data` 卷挂载路径一致 | 指向别处会丢失自动生成的配置与密钥 | `docker-compose.multi-user.yml` |
| `OPENACE_ALLOW_ROOT_MULTI_USER` | `1`（overlay） | root 多用户运行的显式授权（Issue #1893） | 去掉后多用户模式拒绝启动 | `docker-compose.multi-user.yml` |
| `OPENACE_AGENT_STATE_ROOT` | `/var/lib/openace/agent-state` | CLI 转写记录目录；应用与 scheduler 服务必须完全一致（Issue #3237） | 不一致会破坏跨服务的转写清理 | `docker-compose.yml` |
| `FETCH_USE_SUDO` | `false` | 采集器是否使用 sudo（容器内以 root 运行，无需 sudo；Issue #1121） | — | `docker-compose.yml` |
| `FLASK_ENV` | `production`（scheduler 服务） | 传统环境标志；作为安全模式信号已废弃，v2.1.0 移除——请迁移到 `OPENACE_SECURITY_MODE`（用 `scripts/migrate_security_mode.sh`） | v2.1.0 起依赖它设置安全模式将失效 | `docker-compose.yml`、`.env.example`、`app/utils/security_mode.py` |

## Scheduler 服务（docker-compose.yml）

`scheduler` 服务（单用户模式为 `scheduler` profile；多用户 overlay 下默认启用）使用：

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `SCHEDULER_MODE` | `scheduler` | 将进程标记为后台调度 worker（leader 选举保证只有一个活跃 scheduler，Issue #2187） | — | `docker-compose.yml` |
| `SCHEDULER_HEARTBEAT_INTERVAL` | `10` | scheduler 心跳间隔（秒） | 多副本取值不一致会扭曲存活检测 | `docker-compose.yml` |
| `SCHEDULER_HEARTBEAT_TIMEOUT` | `60` | scheduler 静默多少秒后视为失联 | 同上 | `docker-compose.yml` |
| `SCHEDULER_LOCK_TIMEOUT` | `1800` | scheduler leader 锁过期时间（秒） | 过小可能在长任务期间出现重复 scheduler | `docker-compose.yml` |
| `SCHEDULER_METRICS_PORT` | `9090` | scheduler 指标/健康端点端口（健康检查目标） | 容器内部端口，无宿主机映射 | `docker-compose.yml` |

## Kubernetes（k8s/configmap.yaml）

由 Kubernetes 清单声明（见 [KUBERNETES.md](KUBERNETES.md)）；部分与上面的变量重叠。

| 变量 | 默认值（清单） | 作用 | 轮转影响 | 出处 |
|------|----------------|------|----------|------|
| `LOG_LEVEL` | `INFO` | 应用日志级别 | 无；重启后生效 | `k8s/configmap.yaml` |
| `AUDIT_LOG_RETENTION_DAYS` | `90` | 审计日志保留天数 | 调小会在下次清理时删除更早的审计记录 | `k8s/configmap.yaml` |
| `DATA_RETENTION_DAYS` | `365` | 数据保留天数（清理任务用） | 调小会在下次清理时删除更早的保留数据 | `k8s/configmap.yaml` |
| `DB_HOST` / `DB_PORT` | `postgres` / `5432` | 集群内数据库端点 | 需重启 | `k8s/configmap.yaml` |
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | Redis 端点 | 需重启 | `k8s/configmap.yaml` |
| `ENABLE_SSO` / `ENABLE_MULTI_TENANT` / `ENABLE_AUDIT_LOG` / `ENABLE_CONTENT_FILTER` | `true` | 功能开关 | 切换后重启即启用/停用对应功能 | `k8s/configmap.yaml` |
| `REDIS_PASSWORD` | 占位符 | Redis 密码（secret；生产前必须替换占位符） | 必须与 Redis 部署一致 | `k8s/configmap.yaml`（Secret 段） |

## 应用层变量（代码）

| 变量 | 默认值 | 作用 | 轮转影响 | 出处 |
|------|--------|------|----------|------|
| `OPENACE_PLATFORM_ADMIN_STRICT_MODE` | `false` | 为 `true` 时只有显式 `platform_admin` 角色能通过平台管理员检查，legacy `admin` 不再算数（Issue #2332）。必须先把存量 `role='admin'` 账户迁移为 `platform_admin`，再开启并重启所有进程；每个进程启动时会记录 `Platform admin strict mode: ENABLED` | 未迁移账户就开启会把 legacy 管理员锁在门外 | `app/auth/permissions.py` |
| `OPENACE_ENCRYPTION_KEYS` | 未设置 | JSON 数据密钥注册表：`{"keys": [{"id": 1-255, "value": "...", "status": "active|deprecated|revoked"}, ...], "primary_key_id": N}`；最多 5 把、恰好 1 把 active；优先于 `OPENACE_ENCRYPTION_KEY`；约每 5 秒热加载 | 密钥变更会重新绑定新密文（`v1k<id>:` 前缀）；见 [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md) | `app/utils/encryption_key_registry.py` |
| `OPENACE_CORS_ALLOWED_ORIGINS` | 未设置 | 非 loopback WebUI 源的显式 API CORS 白名单，逗号分隔 | 收紧会拒绝被移除源的请求 | `app/__init__.py` |
| `OPENACE_WS_MAX_MESSAGE_BYTES` | `8388608` | 浏览器侧终端 / VSCode 原始桥接入站 WebSocket 最大消息字节数 | 调小会拒绝大的粘贴内容 | `app/ws_frame.py` |
| `OPENACE_TEST_MODE` | 未设置 | `1` 标记 CI/测试上下文：跳过生产级安全检查 | 生产环境绝不可开启 | `app/utils/security_mode.py` |
| `OPENCLAW_TOKEN` | 未设置 | OpenClaw 数据采集 API token | 轮转需同步更新采集端 | `app/__init__.py` |
| `SMTP_PASSWORD` | 未设置 | 邮件 SMTP 密码 | 轮转需与邮件服务器一致 | `app/__init__.py` |
| `OPENACE_REPLICA_ENDPOINTS` | 未设置 | 逗号分隔的副本端点，用于查询加密密钥配置版本同步状态 | 仅信息展示（同步面板） | `app/routes/encryption_keys.py` |

## 自动生成的密钥

单用户开发路径下，缺失密钥不算错误：`docker-entrypoint.sh` 会在首次启动生成强随机值并持久化到 `config-data` 卷内的 `generated-secrets.env`（`/home/open-ace/.open-ace/generated-secrets.env`），后续重启复用、不轮转。裸机开发同理（`~/.open-ace/generated-secrets.env`，Issue #2667）。`.env` 中显式设置的值始终优先，且不会写入该文件。生产模式必须显式设置密钥——见 [DEPLOYMENT.md](DEPLOYMENT.md)。

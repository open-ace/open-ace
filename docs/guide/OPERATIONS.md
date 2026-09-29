# Operations Guide — 运维指南

[English](#english) | [中文](#中文)

---

## English

This guide covers day-2 operations of a running Open ACE deployment: health probes and monitoring endpoints, the scheduler worker, data collection, TLS certificates, data retention and compliance, and logs and alerts. For initial installation see [DEPLOYMENT.md](DEPLOYMENT.md); for cluster deployment see [KUBERNETES.md](KUBERNETES.md); for reverse proxy see [NGINX.md](NGINX.md).

## Health and Monitoring Endpoints

All endpoints below are served by the web application on port 19888 (defined in `app/__init__.py`).

| Endpoint | Purpose | Returns | Failure behavior |
|----------|---------|---------|-------------------|
| `/livez` | Liveness probe | `{"status": "alive", "timestamp": ...}` | Always 200 while the process can respond. Does NOT check dependencies, to avoid restart storms. |
| `/readyz` | Readiness probe | `{"status": "ready" \| "not_ready", "checks": {...}, "action": ...}` | 503 if any critical check fails. |
| `/metrics` | Prometheus metrics | Prometheus text format (via `prometheus_flask_exporter`, `group_by_endpoint`, latency buckets 0.01–5 s) | 503 JSON fallback if `prometheus_flask_exporter` is not installed. |
| `/security-status` | Security baseline | Baseline check results plus `security_mode` (mode / source / explicit), `pilot_metadata`, and `migration_status` | 503 when the baseline reports `unhealthy` or security mode resolution errors. |
| `/health` | **Deprecated** | Delegates to `/readyz`, then adds `deprecated: true`, a migration message, and `version` (git commit) | Same as `/readyz`. Use `/livez` + `/readyz` instead. |

`/readyz` runs nine checks and reports each under `checks`:

- `security_mode` — must be explicitly set (`OPENACE_SECURITY_MODE`); pilot metadata in production mode produces a warning
- `database` — probe connection with 2 s timeout (dedicated connection, not from the pool)
- `schema_version` — Alembic head check via `SchemaCompatibilityService` (PostgreSQL production only; SQLite reports `skipped`; an active emergency bypass forces 503)
- `config_dir`, `workspace_dir` — exist and are writable/readable
- `encryption_keys` — encryption key registry initialized
- `init_status` — application initialization completed
- `ssh_sync` — no SSH sync failure warning file present (see Logs and Alerts)
- `frontend_build` — management UI build artifacts present (fails readiness only in production mode)

When not ready, the response includes an `action` hint such as running `alembic upgrade head`.

### Which endpoint a Kubernetes probe should use

`k8s/deployment.yaml` wires the web pod as follows (port `http` = 19888):

```yaml
          livenessProbe:
            httpGet:
              path: /livez
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 10
            failureThreshold: 3
          # Startup probe for slow initialization (Issue #2186)
          startupProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 0
            periodSeconds: 5
            timeoutSeconds: 10
            failureThreshold: 60  # 300s max startup time
```

- Liveness → `/livez` (process alive only; dependency failures must not trigger pod restarts)
- Readiness → `/readyz` (dependency failures remove the pod from Service endpoints)
- Startup → `/readyz` with `failureThreshold: 60` (up to 300 s for first boot and migrations)

The web pod also carries Prometheus scrape annotations (`prometheus.io/scrape: "true"`, port `19888`, path `/metrics`); with multiple gunicorn workers the deployment sets `PROMETHEUS_MULTIPROC_DIR` for multi-process metric aggregation. The Docker Compose `healthcheck` for the `open-ace` service polls `http://localhost:19888/readyz` (30 s interval, 3 retries, 30 s start period).

## Scheduler Service

Background work (data fetch, quota enforcement, autonomous workflows, alert compensation, scheduler health monitor, SSO cleanup) is decoupled from web workers (Issue #2187). The split is controlled by `SCHEDULER_MODE`:

- `SCHEDULER_MODE=scheduler` — the process starts all background schedulers with database leader election (the `scheduler_leaders` table guarantees a single active leader)
- `SCHEDULER_MODE=web` or unset — no schedulers are started (web worker)
- Development `python3 server.py` runs web and schedulers in one process

### Docker Compose

The scheduler is an opt-in Compose profile (`docker-compose.yml`, service `scheduler`):

```yaml
  scheduler:
    image: ${IMAGE_NAME:-openace/open-ace:latest}
    container_name: open-ace-scheduler
    restart: unless-stopped
    profiles:
      - scheduler
    environment:
      - FLASK_ENV=production
      - PYTHONUNBUFFERED=1
      - SCHEDULER_MODE=scheduler
      - SECRET_KEY=${SECRET_KEY:-}
      - OPENACE_ENCRYPTION_KEY=${OPENACE_ENCRYPTION_KEY:-}
      - DATABASE_URL=postgresql://${DB_USER:-ace}:${DB_PASSWORD:-dev-password-change-in-production}@postgres:5432/${DB_NAME:-ace}
      # Issue #3095: Pass DB_PASSWORD as standalone env var for security baseline check.
      - DB_PASSWORD=${DB_PASSWORD:-}
      - SCHEDULER_HEARTBEAT_INTERVAL=${SCHEDULER_HEARTBEAT_INTERVAL:-10}
      - SCHEDULER_HEARTBEAT_TIMEOUT=${SCHEDULER_HEARTBEAT_TIMEOUT:-60}
      - SCHEDULER_LOCK_TIMEOUT=${SCHEDULER_LOCK_TIMEOUT:-1800}
      - SCHEDULER_METRICS_PORT=9090
      # #3237: must match the app service exactly.
      - OPENACE_AGENT_STATE_ROOT=/var/lib/openace/agent-state
```

Enable it with `docker compose --profile scheduler up -d`. It shares the `config-data`, `workspace-data`, and `agent-state` volumes with the app service — the shared `agent-state` volume is deliberate (#3237), because purge routes run in the web container while the scheduler writes the transcripts. Its health check polls `http://localhost:9090/metrics`.

Heartbeat and election parameters (read in `app/services/leader_election.py`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `SCHEDULER_HEARTBEAT_INTERVAL` | `10` (seconds) | How often the leader refreshes its heartbeat row |
| `SCHEDULER_HEARTBEAT_TIMEOUT` | `60` (seconds) | Heartbeat age after which leadership is considered lost |
| `SCHEDULER_LOCK_TIMEOUT` | `1800` (seconds) | Distributed lock expiration |
| `SCHEDULER_METRICS_PORT` | `9090` | Port of the scheduler's own metrics HTTP server |

The scheduler worker (`python -m app.scheduler_worker`) starts its own metrics server on port 9090 with three endpoints: `/livez` (alive), `/health` (alive plus `is_leader` from leader election), and `/metrics` (Prometheus). At startup it validates explicit security mode, schema version (`REQUIRE_HEAD`), scheduler tables, and data-fetch configuration drift (`FETCH_USE_SUDO` wrapper/sudoers, Issues #2543/#3145 — with critical drift it marks fetch degraded and skips collection jobs rather than running doomed tasks).

### Kubernetes

`k8s/scheduler-deployment.yaml` runs `open-ace-scheduler` as the counterpart: 2 replicas (main + backup, only one active leader), `command: ["python", "-m", "app.scheduler_worker"]`, the same heartbeat env values (10/60/1800/9090), probes against `/livez` and `/health` on port `metrics` (9090), a `PodDisruptionBudget` with `minAvailable: 1`, a `PriorityClass` (`open-ace-scheduler`, value 1000000) to prevent eviction, pod anti-affinity, and the shared `open-ace-data` PVC for `/workspace`, agent state, and config — `OPENACE_AGENT_STATE_ROOT` must match the web deployment exactly.

On bare metal, reference units are `scripts/openace.service` (web, no schedulers) and `scripts/openace-scheduler.service` (`SCHEDULER_MODE=scheduler`, runs all background services via leader election).

## Data Collection (cron)

Usage data is collected from CLI tool data directories by per-tool scripts in `scripts/`:

| Script | Collects |
|--------|----------|
| `scripts/fetch_claude.py` | Claude Code sessions |
| `scripts/fetch_codex.py` | Codex CLI sessions |
| `scripts/fetch_qwen.py` | Qwen Code sessions |
| `scripts/fetch_openclaw.py` | OpenClaw sessions |
| `scripts/fetch_zcode.py` | ZCode CLI databases |

Two collection paths exist:

1. **In-app scheduler** (default in container deployments): the data fetch scheduler runs the fetch scripts as subprocesses every 300 s by default (minimum 60 s). Configure with `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` environment variables or the `data_fetch` section of `config.json` (environment variables win). In multi-user containers the scripts run through `/usr/local/bin/openace-fetch-wrapper` with sudoers rules (`FETCH_USE_SUDO=true`, generate rules with `scripts/generate-sudoers.sh`); in the default single-user container `FETCH_USE_SUDO=false` and the venv Python is used directly.
2. **Host cron** (bare-metal deployments): the classic crontab from the deployment guide:

```bash
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

Add `fetch_codex.py` / `fetch_zcode.py` lines the same way if those tools are in use. Manual runs support `--days N` to backfill, e.g. `python3 scripts/fetch_claude.py --days 7`.

### Central server plus remote collectors: `scripts/upload-to-central/`

For distributed environments, remote machines run the incremental sync daemon instead of cron. Deployment is one command (from `scripts/upload-to-central/README.md`):

```bash
./deploy-remote.sh \
    --host user@remote-host \
    --server http://CENTRAL_SERVER:19888 \
    --auth-key YOUR_AUTH_KEY \
    --hostname remote-hostname
```

For the local machine use `deploy.sh` with the same `--server` / `--auth-key` / `--hostname` options. Useful options: `--interval` (sync interval in seconds, default 300), `--user`, `--install-dir` (default `~/upload-to-central`), `--uninstall`.

Operational notes:

- The daemon installs a systemd unit `upload-to-central`; check `sudo systemctl status upload-to-central` and `sudo journalctl -u upload-to-central -f`
- Manual sync: `./upload.sh`; forced full re-upload: `./upload.sh --full`
- Sync state lives in `~/.open-ace/sync_state.json` (per-host `last_sync_time`, `last_upload`, `total_uploaded`); incremental sync only uploads new messages
- The daemon POSTs to `/api/upload/batch` on the central server with the `X-Auth-Key` header — the key must match `server.upload_auth_key` in the central server's `~/.open-ace/config.json`
- Remote deployment requires SSH access; installing the systemd service requires root

## TLS Certificates

- **Compose / bare metal**: TLS terminates at the nginx reverse proxy. [NGINX.md](NGINX.md) shows the full configuration: listen on 443 with `ssl_certificate /path/to/ssl/fullchain.pem` and `ssl_certificate_key /path/to/ssl/privkey.pem`, TLSv1.2/1.3 only, and an HTTP→HTTPS redirect on port 80. The repository does not ship a certbot configuration; obtain certificates with certbot (webroot or standalone) for your domain and point nginx at the Let's Encrypt paths. Renewal is automated by certbot's own timer — make sure the renewal hook reloads nginx so the new certificate is picked up.
- **Kubernetes**: the Ingress in `k8s/service.yaml` is pre-configured for cert-manager with the `letsencrypt-prod` ClusterIssuer (annotation `cert-manager.io/cluster-issuer: "letsencrypt-prod"`, TLS secret `open-ace-tls`, `ssl-redirect: "true"`). cert-manager must be installed before applying the manifests:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

After that, issuing and renewing the certificate is handled entirely by cert-manager; there is no manual renewal step.

## Data Retention and Compliance

- `k8s/configmap.yaml` declares `AUDIT_LOG_RETENTION_DAYS: "90"` and `DATA_RETENTION_DAYS: "365"` (lines 40–41). These values document the intended retention horizon and match the built-in defaults (tenant settings default to 90/365 days; the response-time cleaner defaults to 90 days raw / 365 days aggregated). Note: no background job in the current code base reads these two environment variables directly — runtime retention is driven by the mechanisms below.
- **Retention rules** (`app/modules/compliance/retention.py`, `DataRetentionManager`), per data type:

| Data type | Default | Action |
|-----------|---------|--------|
| `audit_logs` | 90 days | delete |
| `quota_alerts` | 30 days | delete |
| `sessions` | 7 days | delete |
| `sso_sessions` | 1 day | delete |
| `usage_data` | 365 days | archive |
| `messages` | 90 days | anonymize |
| `user_activity` | 365 days | archive |

- **Admin API** (blueprint `/api/compliance`, admin only): `GET/PUT /api/compliance/retention/rules`, `POST /api/compliance/retention/cleanup` (supports `?dry_run=true`), and `GET /api/compliance/retention/history|storage|status`. There is no built-in scheduler that fires retention automatically — run or schedule cleanup through the API; the compliance status check flags instances whose last cleanup is older than a week. Every run is recorded in the `retention_history` table.
- **Schema groundwork (Issue #2188 Phase 1)**: migration `20260803_002_create_retention_tables` created `retention_policies`, `retention_executions`, `legal_holds`, `retention_evidence`, `archive_files`, and `recycle_bin`. These persistent policy/execution tables (including legal holds and the recycle bin) are schema preparation; treat legal holds and recycle bin as not yet wired to runtime automation and verify your version's coverage before relying on them operationally.
- **Audit log sync**: `scripts/openace-ssh-sync` prunes its own audit log (`/var/log/openace/ssh-sync.log`) after 30 days.

## Logs and Alerts

- **Log level**: `LOG_LEVEL: "INFO"` is set in `k8s/configmap.yaml`. The application factory currently fixes the level in code (`logging.basicConfig(level=logging.INFO)` in `app/__init__.py`; the scheduler worker also logs at INFO), so treat INFO as the effective level.
- **Container logs**: `docker compose logs -f open-ace` / `docker compose logs -f scheduler`. All Compose services use the `json-file` driver with `max-size: 10m`, `max-file: 3` rotation. The application also writes to `/app/logs` inside the container, bind-mounted to `./logs` on the host in Compose (an `emptyDir` in Kubernetes).
- **Alert notification history**: the `alerts_history` table records every outgoing notification (alert type, severity, message, channels, recipients, `status` sent/failed, `sent_at`).
- **Alert failure queue**: when alert creation fails, the alert compensation worker (part of the scheduler service) retries from a queue. Inspect with `GET /api/alerts/failure-queue` and force a processing pass with `POST /api/alerts/failure-queue/retry` (admin). Scheduler status endpoints `GET /api/schedulers/data-fetch` and `GET /api/schedulers/quota-enforcement` report the corresponding background jobs. The full Alerts API is documented in [API.md](../dev/API.md).
- **SSH sync failures affect readiness** (Issue #2328): when secure SSH sync fails, the entrypoint writes `/var/log/openace/ssh-sync-failure.warning` (human-readable, with remediation hints) and `.../ssh-sync-failure.json` (structured). While the warning file exists, the `ssh_sync` check in `/readyz` fails and the pod reports not ready. Remove the file after fixing the cause; details are in `ssh-sync.log` in the same directory.

## Related Documents

- [DEPLOYMENT.md](DEPLOYMENT.md) — installation, Docker Compose, data persistence
- [KUBERNETES.md](KUBERNETES.md) — cluster manifests, scaling, TLS with cert-manager
- [NGINX.md](NGINX.md) — HTTPS reverse proxy, WebSocket/timeouts, 413 fixes
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — cross-component troubleshooting index
- [DATABASE_BACKUP.md](DATABASE_BACKUP.md) — backup and recovery

---

## 中文

本指南覆盖 Open ACE 日常运维：健康探针与监控端点、调度器服务、数据采集、TLS 证书、数据保留与合规、日志与告警。首次安装见 [DEPLOYMENT.md](DEPLOYMENT.md)；集群部署见 [KUBERNETES.md](KUBERNETES.md)；反向代理见 [NGINX.md](NGINX.md)。

## 健康与监控端点

以下端点均由 Web 应用在 19888 端口提供（定义于 `app/__init__.py`）。

| 端点 | 用途 | 返回 | 失败行为 |
|------|------|------|----------|
| `/livez` | 存活探针 | `{"status": "alive", "timestamp": ...}` | 进程能响应即 200。不检查任何依赖，避免重启风暴。 |
| `/readyz` | 就绪探针 | `{"status": "ready" \| "not_ready", "checks": {...}, "action": ...}` | 任一关键检查失败返回 503。 |
| `/metrics` | Prometheus 指标 | Prometheus 文本格式（`prometheus_flask_exporter`，按端点分组，延迟桶 0.01–5 秒） | 未安装 `prometheus_flask_exporter` 时返回 503 JSON 降级响应。 |
| `/security-status` | 安全基线 | 基线检查结果，附 `security_mode`（mode / source / explicit）、`pilot_metadata` 与 `migration_status` | 基线报告 `unhealthy` 或安全模式解析出错时返回 503。 |
| `/health` | **已废弃** | 委托 `/readyz`，再附加 `deprecated: true`、迁移提示消息与 `version`（git commit） | 与 `/readyz` 相同。请改用 `/livez` + `/readyz`。 |

`/readyz` 共执行九项检查，逐项体现在 `checks` 中：

- `security_mode` —— 必须显式设置（`OPENACE_SECURITY_MODE`）；production 模式下存在 pilot 元数据会产生告警
- `database` —— 2 秒超时的探测连接（专用连接，不占用连接池）
- `schema_version` —— 通过 `SchemaCompatibilityService` 校验 Alembic head（仅 PostgreSQL 生产模式；SQLite 报 `skipped`；紧急旁路生效时强制 503）
- `config_dir`、`workspace_dir` —— 存在且可写/可读
- `encryption_keys` —— 加密密钥注册表已初始化
- `init_status` —— 应用初始化已完成
- `ssh_sync` —— 不存在 SSH 同步失败告警文件（见"日志与告警"）
- `frontend_build` —— 管理端 UI 构建产物存在（仅生产模式失败才影响就绪）

未就绪时响应带 `action` 提示，例如执行 `alembic upgrade head`。

### Kubernetes 探针该用哪个端点

`k8s/deployment.yaml` 对 Web Pod 的配置如下（端口 `http` = 19888）：

```yaml
          livenessProbe:
            httpGet:
              path: /livez
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 10
            failureThreshold: 3
          # Startup probe for slow initialization (Issue #2186)
          startupProbe:
            httpGet:
              path: /readyz
              port: http
            initialDelaySeconds: 0
            periodSeconds: 5
            timeoutSeconds: 10
            failureThreshold: 60  # 300s max startup time
```

- 存活探针 → `/livez`（只看进程存活；依赖故障不得触发 Pod 重启）
- 就绪探针 → `/readyz`（依赖故障时把 Pod 摘出 Service 端点）
- 启动探针 → `/readyz`，`failureThreshold: 60`（首次启动与迁移最长 300 秒）

Web Pod 还带有 Prometheus 抓取注解（`prometheus.io/scrape: "true"`，端口 `19888`，路径 `/metrics`）；gunicorn 多 worker 场景下部署清单设置了 `PROMETHEUS_MULTIPROC_DIR` 以聚合多进程指标。Docker Compose 中 `open-ace` 服务的 `healthcheck` 轮询 `http://localhost:19888/readyz`（30 秒间隔、3 次重试、30 秒 start period）。

## 调度器服务

后台任务（数据采集、配额执行、自主开发工作流、告警补偿、调度器健康监控、SSO 清理）已与 Web worker 解耦（Issue #2187），由 `SCHEDULER_MODE` 控制：

- `SCHEDULER_MODE=scheduler` —— 进程启动全部后台调度器，使用数据库领导选举（`scheduler_leaders` 表保证同一时刻只有一个活跃 leader）
- `SCHEDULER_MODE=web` 或未设置 —— 不启动任何调度器（Web worker）
- 开发模式 `python3 server.py` 在单进程中同时运行 Web 与调度器

### Docker Compose

调度器是可选的 Compose profile（`docker-compose.yml` 的 `scheduler` 服务）：

```yaml
  scheduler:
    image: ${IMAGE_NAME:-openace/open-ace:latest}
    container_name: open-ace-scheduler
    restart: unless-stopped
    profiles:
      - scheduler
    environment:
      - FLASK_ENV=production
      - PYTHONUNBUFFERED=1
      - SCHEDULER_MODE=scheduler
      - SECRET_KEY=${SECRET_KEY:-}
      - OPENACE_ENCRYPTION_KEY=${OPENACE_ENCRYPTION_KEY:-}
      - DATABASE_URL=postgresql://${DB_USER:-ace}:${DB_PASSWORD:-dev-password-change-in-production}@postgres:5432/${DB_NAME:-ace}
      # Issue #3095: Pass DB_PASSWORD as standalone env var for security baseline check.
      - DB_PASSWORD=${DB_PASSWORD:-}
      - SCHEDULER_HEARTBEAT_INTERVAL=${SCHEDULER_HEARTBEAT_INTERVAL:-10}
      - SCHEDULER_HEARTBEAT_TIMEOUT=${SCHEDULER_HEARTBEAT_TIMEOUT:-60}
      - SCHEDULER_LOCK_TIMEOUT=${SCHEDULER_LOCK_TIMEOUT:-1800}
      - SCHEDULER_METRICS_PORT=9090
      # #3237: must match the app service exactly.
      - OPENACE_AGENT_STATE_ROOT=/var/lib/openace/agent-state
```

通过 `docker compose --profile scheduler up -d` 启用。它与应用服务共享 `config-data`、`workspace-data` 与 `agent-state` 卷 —— 共享 `agent-state` 卷是有意为之（#3237）：清理路由跑在 Web 容器里，而调度器负责写入 transcript。其健康检查轮询 `http://localhost:9090/metrics`。

心跳与选举参数（在 `app/services/leader_election.py` 中读取）：

| 变量 | 默认值 | 含义 |
|------|--------|------|
| `SCHEDULER_HEARTBEAT_INTERVAL` | `10`（秒） | leader 刷新心跳行的频率 |
| `SCHEDULER_HEARTBEAT_TIMEOUT` | `60`（秒） | 心跳超过该时长视为失去领导权 |
| `SCHEDULER_LOCK_TIMEOUT` | `1800`（秒） | 分布式锁过期时间 |
| `SCHEDULER_METRICS_PORT` | `9090` | 调度器独立指标 HTTP 服务端口 |

调度器 worker（`python -m app.scheduler_worker`）在 9090 端口启动自己的指标服务，提供三个端点：`/livez`（存活）、`/health`（存活并附领导选举的 `is_leader`）、`/metrics`（Prometheus）。启动时依次校验显式安全模式、schema 版本（`REQUIRE_HEAD`）、调度器表是否存在，以及数据采集配置漂移（`FETCH_USE_SUDO` 的 wrapper/sudoers，Issues #2543/#3145 —— 出现严重漂移时会把采集置为 degraded 并跳过注定失败的采集任务）。

### Kubernetes

`k8s/scheduler-deployment.yaml` 是对应的集群版本：`open-ace-scheduler` 以 2 副本运行（主 + 备，仅一个活跃 leader），`command: ["python", "-m", "app.scheduler_worker"]`，心跳参数同上（10/60/1800/9090），探针打向 `metrics` 端口（9090）的 `/livez` 与 `/health`，并配置 `PodDisruptionBudget`（`minAvailable: 1`）、`PriorityClass`（`open-ace-scheduler`，值 1000000）防止驱逐、Pod 反亲和，以及共享 `open-ace-data` PVC 挂载 `/workspace`、agent state 与 config —— `OPENACE_AGENT_STATE_ROOT` 必须与 Web 部署完全一致。

裸机部署可参考 `scripts/openace.service`（Web，不跑调度器）与 `scripts/openace-scheduler.service`（`SCHEDULER_MODE=scheduler`，经领导选举运行全部后台服务）。

## 数据采集（cron）

用量数据由 `scripts/` 下按工具划分的脚本从 CLI 工具数据目录采集：

| 脚本 | 采集内容 |
|------|----------|
| `scripts/fetch_claude.py` | Claude Code 会话 |
| `scripts/fetch_codex.py` | Codex CLI 会话 |
| `scripts/fetch_qwen.py` | Qwen Code 会话 |
| `scripts/fetch_openclaw.py` | OpenClaw 会话 |
| `scripts/fetch_zcode.py` | ZCode CLI 数据库 |

采集有两条路径：

1. **应用内调度器**（容器部署默认）：数据采集调度器默认每 300 秒（最小 60 秒）以子进程方式运行采集脚本。可用环境变量 `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` 或 `config.json` 的 `data_fetch` 段配置（环境变量优先）。多用户容器中脚本经 `/usr/local/bin/openace-fetch-wrapper` 与 sudoers 规则执行（`FETCH_USE_SUDO=true`，用 `scripts/generate-sudoers.sh` 生成规则）；默认单用户容器中 `FETCH_USE_SUDO=false`，直接使用 venv Python。
2. **宿主机 cron**（裸机部署）：部署指南中的经典 crontab：

```bash
30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_claude.py >> logs/cron.log 2>&1
35 0 * * * cd /path/to/open-ace && python3 scripts/fetch_qwen.py >> logs/cron.log 2>&1
40 0 * * * cd /path/to/open-ace && python3 scripts/fetch_openclaw.py >> logs/cron.log 2>&1
```

如使用 Codex / ZCode，按相同方式追加 `fetch_codex.py` / `fetch_zcode.py` 行。手动执行支持 `--days N` 回填，例如 `python3 scripts/fetch_claude.py --days 7`。

### 中心服务器 + 远程采集器：`scripts/upload-to-central/`

分布式环境下，远程机器改跑增量同步守护进程而非 cron。一条命令完成部署（来自 `scripts/upload-to-central/README.md`）：

```bash
./deploy-remote.sh \
    --host user@remote-host \
    --server http://CENTRAL_SERVER:19888 \
    --auth-key YOUR_AUTH_KEY \
    --hostname remote-hostname
```

本机部署使用 `deploy.sh`，`--server` / `--auth-key` / `--hostname` 参数相同。常用选项：`--interval`（同步间隔秒数，默认 300）、`--user`、`--install-dir`（默认 `~/upload-to-central`）、`--uninstall`。

运维要点：

- 守护进程安装 systemd 服务 `upload-to-central`；用 `sudo systemctl status upload-to-central` 与 `sudo journalctl -u upload-to-central -f` 检查
- 手动同步：`./upload.sh`；强制全量重传：`./upload.sh --full`
- 同步状态保存在 `~/.open-ace/sync_state.json`（按主机记录 `last_sync_time`、`last_upload`、`total_uploaded`）；增量同步只上传新消息
- 守护进程向中央服务器的 `/api/upload/batch` 发起 POST，携带 `X-Auth-Key` 头 —— 该密钥必须与中央服务器 `~/.open-ace/config.json` 中的 `server.upload_auth_key` 一致
- 远程部署需要 SSH 权限；安装 systemd 服务需要 root

## TLS 证书

- **Compose / 裸机**：TLS 在 nginx 反向代理终结。[NGINX.md](NGINX.md) 给出完整配置：443 端口监听并指定 `ssl_certificate /path/to/ssl/fullchain.pem` 与 `ssl_certificate_key /path/to/ssl/privkey.pem`，仅 TLSv1.2/1.3，80 端口 HTTP→HTTPS 跳转。仓库不附带 certbot 配置；请用 certbot（webroot 或 standalone 方式）为域名获证，并让 nginx 指向 Let's Encrypt 路径。续期由 certbot 自带的定时器自动完成 —— 确保续期钩子会 reload nginx，使新证书生效。
- **Kubernetes**：`k8s/service.yaml` 的 Ingress 已为 cert-manager 预配置 `letsencrypt-prod` ClusterIssuer（注解 `cert-manager.io/cluster-issuer: "letsencrypt-prod"`，TLS secret `open-ace-tls`，`ssl-redirect: "true"`）。应用清单前必须先安装 cert-manager：

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

之后证书的签发与续期完全由 cert-manager 处理，无需手动续期。

## 数据保留与合规

- `k8s/configmap.yaml` 声明了 `AUDIT_LOG_RETENTION_DAYS: "90"` 与 `DATA_RETENTION_DAYS: "365"`（第 40–41 行）。这两个值声明了预期的保留周期，并与内置默认值一致（租户设置默认 90/365 天；响应时长清理器默认原始数据 90 天 / 聚合数据 365 天）。注意：当前代码中没有后台任务直接读取这两个环境变量 —— 运行时保留由下列机制驱动。
- **保留规则**（`app/modules/compliance/retention.py` 的 `DataRetentionManager`），按数据类型：

| 数据类型 | 默认 | 动作 |
|----------|------|------|
| `audit_logs` | 90 天 | delete |
| `quota_alerts` | 30 天 | delete |
| `sessions` | 7 天 | delete |
| `sso_sessions` | 1 天 | delete |
| `usage_data` | 365 天 | archive |
| `messages` | 90 天 | anonymize |
| `user_activity` | 365 天 | archive |

- **管理 API**（blueprint `/api/compliance`，仅管理员）：`GET/PUT /api/compliance/retention/rules`、`POST /api/compliance/retention/cleanup`（支持 `?dry_run=true`）、`GET /api/compliance/retention/history|storage|status`。没有内置调度器自动触发保留 —— 请通过 API 执行或排期；合规状态检查会把"上次清理超过一周"的实例标记为不合规。每次执行都会记录到 `retention_history` 表。
- **Schema 铺垫（Issue #2188 Phase 1）**：迁移 `20260803_002_create_retention_tables` 创建了 `retention_policies`、`retention_executions`、`legal_holds`、`retention_evidence`、`archive_files` 与 `recycle_bin`。这些持久化策略/执行表（含 legal hold 与回收站）属于 schema 准备；运维上应把 legal hold 与回收站视为尚未接入运行时自动化，依赖前请先核实所用版本的覆盖情况。
- **审计日志同步**：`scripts/openace-ssh-sync` 自行按 30 天清理其审计日志（`/var/log/openace/ssh-sync.log`）。

## 日志与告警

- **日志级别**：`k8s/configmap.yaml` 设置 `LOG_LEVEL: "INFO"`。应用工厂当前在代码中固定级别（`app/__init__.py` 的 `logging.basicConfig(level=logging.INFO)`；调度器 worker 同为 INFO），可视为生效级别就是 INFO。
- **容器日志**：`docker compose logs -f open-ace` / `docker compose logs -f scheduler`。所有 Compose 服务使用 `json-file` 驱动，`max-size: 10m`、`max-file: 3` 轮转。应用还会写容器内 `/app/logs`，Compose 中绑定挂载到宿主机 `./logs`（Kubernetes 中为 `emptyDir`）。
- **告警通知历史**：`alerts_history` 表记录每次外发通知（告警类型、严重级、消息、通道、接收者、`status` sent/failed、`sent_at`）。
- **告警失败队列**：告警创建失败时，告警补偿 worker（调度器服务的一部分）从队列重试。用 `GET /api/alerts/failure-queue` 查看、`POST /api/alerts/failure-queue/retry` 强制处理一轮（管理员）。调度器状态端点 `GET /api/schedulers/data-fetch` 与 `GET /api/schedulers/quota-enforcement` 报告对应后台任务。完整 Alerts API 见 [API.md](../dev/API.md)。
- **SSH 同步失败影响就绪**（Issue #2328）：安全 SSH 同步失败时，entrypoint 会写 `/var/log/openace/ssh-sync-failure.warning`（人类可读，含修复提示）与 `.../ssh-sync-failure.json`（结构化）。告警文件存在期间，`/readyz` 的 `ssh_sync` 检查失败、Pod 报未就绪。修复原因后删除该文件；详情见同目录的 `ssh-sync.log`。

## 相关文档

- [DEPLOYMENT.md](DEPLOYMENT.md) —— 安装、Docker Compose、数据持久化
- [KUBERNETES.md](KUBERNETES.md) —— 集群清单、扩缩容、cert-manager TLS
- [NGINX.md](NGINX.md) —— HTTPS 反向代理、WebSocket/超时、413 修复
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) —— 跨组件排障索引
- [DATABASE_BACKUP.md](DATABASE_BACKUP.md) —— 备份与恢复

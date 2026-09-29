# Kubernetes Deployment — Kubernetes 部署

[English](#english) | [中文](#中文)

---

## English

## Prerequisites

- Kubernetes cluster (1.24+)
- `kubectl` configured
- StorageClass available with `ReadWriteMany` support for the shared app PVC
- Ingress controller (nginx-ingress recommended)
- cert-manager (optional, for TLS)

## Quick Deploy

```bash
kubectl apply -k k8s/
```

## Resource Manifests

All manifests are in the `k8s/` directory, organized by concern:

```
k8s/
├── namespace.yaml      # Namespace: open-ace
├── configmap.yaml      # ConfigMap + Secret
├── storage.yaml        # PVC + ServiceAccount + RBAC
├── database.yaml       # PostgreSQL + Redis StatefulSets
├── deployment.yaml     # App Deployment + HPA
├── scheduler-deployment.yaml  # Autonomous scheduler Deployment
├── scheduler-service.yaml     # Scheduler Service
├── service.yaml        # Service + Ingress
├── policies.yaml       # PDB + NetworkPolicy
└── kustomization.yaml  # Kustomize configuration
```

### Namespace

Creates the `open-ace` namespace with standard Kubernetes labels.

### Deployment

| Setting | Value |
|---------|-------|
| Replicas | 3 |
| Image | `open-ace:latest` |
| Container port | 19888 |
| Strategy | RollingUpdate (maxSurge=1, maxUnavailable=0) |
| Security context | `runAsNonRoot: true`, `runAsUser: 1000`, `allowPrivilegeEscalation: false` |

**Resource Limits:**

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 100m | 500m |
| Memory | 256Mi | 512Mi |

**Health Checks (Issue #2186):**
- Liveness: HTTP GET `/livez`, initialDelay=10s, period=10s (process alive only)
- Readiness: HTTP GET `/readyz`, initialDelay=5s, period=5s (database, config, dependencies)
- Startup: HTTP GET `/readyz`, failureThreshold=60, period=5s (max 300s wait)
- Prometheus scrape: `/metrics` endpoint (Prometheus format)

**Endpoint Responsibilities:**
- `/livez`: Process liveness check, no dependency checks to avoid restart storms
- `/readyz`: Readiness check, validates database connection, schema compatibility, config dir, workspace
- `/metrics`: Prometheus metrics exposition
- `/health`: Deprecated, delegates to `/readyz`, response includes `deprecated: true`

**Pod Anti-Affinity:** Preferred across nodes to preserve availability when capacity allows.

**HorizontalPodAutoscaler:** The reference manifest keeps at least 3 replicas and can scale to 10 replicas based on CPU and memory utilization.

**Sticky routing:** The Service uses `sessionAffinity: ClientIP`, and the nginx Ingress uses cookie affinity. Remote session HTTP control state is persisted and can cross pods, but live terminal relay WebSocket bridges still belong to one web process; sticky routing remains the safest default for active terminal sessions.

**Note** (Issue #1821 F5): With a ClusterIP-type Service, `sessionAffinity: ClientIP` only sees the ingress controller's source IP, not the end-user IP. The effective sticky routing mechanism is the nginx Ingress cookie affinity (`openace_route`, max-age 10800). The Service-level ClientIP affinity provides minimal benefit in this configuration and is retained for consistency with the reference deployment.

**HA Support (Issue #1851):**

Live terminal and VSCode WebSocket connections use a "reconnection recovery" HA model:

- Relay state is registered in Redis for cross-Pod awareness
- When a browser connects to a non-owner Pod, it receives a redirect close frame (code 3010) and reconnects to the owner Pod
- Terminal history is not persisted; reconnection shows "Connection recovered" without restoring previous output
- Redis failure triggers automatic fallback to in-memory mode (local Pod only)
- `preStop` hook (30s) provides graceful shutdown during rolling updates

Recommended configuration:
- Maintain sticky routing for best experience
- Monitor Redis health and circuit breaker state
- Use `preStop` hook to allow active connections to drain

**Multi-user workspace note:** The Docker image itself defaults to the non-root `open-ace` user (uid 1000) via a `USER 1000` directive, and the default Kubernetes manifest reinforces this with `runAsNonRoot: true` / `runAsUser: 1000`. If you use the `plain` isolation backend (`workspace.isolation`) and need dynamic Linux user creation inside the container, deploy a dedicated overlay that intentionally runs the web pod as root (`runAsUser: 0`) **and** sets `OPENACE_ALLOW_ROOT_MULTI_USER=1`; the entrypoint fail-fasts without both, and you should document that exception in your cluster change process.

### Service & Ingress

**ClusterIP Service:** Port 80 → targetPort 19888

**Ingress:**
- Host: `open-ace.example.com` (change this)
- TLS via cert-manager (`letsencrypt-prod`)
- Body size: 50m
- Timeouts: 300s read/send

### PostgreSQL StatefulSet

| Setting | Value |
|---------|-------|
| Image | `postgres:15-alpine` |
| Port | 5432 |
| Database | `openace` |
| PVC | 10Gi, ReadWriteOnce |
| CPU | 100m-500m |
| Memory | 256Mi-1Gi |

Credentials from Secret `open-ace-secrets` (keys: `DB_USER`, `DB_PASSWORD`).

#### Database Backup Responsibility

**IMPORTANT:** The reference Kubernetes manifest does **not** include automated database backups. You are responsible for:

1. **Backup Strategy**: Choose between:
   - **Managed PostgreSQL** (recommended for production): Cloud provider handles backups and PITR
   - **Self-managed backups**: Use the optional CronJob in `k8s/extras/backup/`

2. **RPO/RTO**: These are your responsibility to define and verify:
   - RPO (Recovery Point Objective): Depends on backup frequency
   - RTO (Recovery Time Objective): Depends on restore testing

3. **Restore Testing**: Regular recovery drills are recommended (at least monthly)

For detailed backup/restore procedures, see [DATABASE_BACKUP.md](./DATABASE_BACKUP.md).

#### Optional Backup CronJob

A backup CronJob is provided in `k8s/extras/backup/`:

```bash
# Deploy backup infrastructure
kubectl apply -k k8s/extras/backup/

# Verify CronJob
kubectl get cronjob -n open-ace
```

**Backup CronJob Features:**
- Daily PostgreSQL backup at 02:00 UTC
- Integrity verification with `pg_restore --list`
- Upload to S3-compatible object storage
- Resource limits: 512Mi memory, 500m CPU
- Timeout: 1 hour (adjust for large databases)

**NetworkPolicy Compatibility:** The backup Job works with the existing NetworkPolicy without modification (egress to PostgreSQL and external HTTPS is already allowed).

**RBAC Requirements:** The backup Job uses a separate ServiceAccount (`open-ace-backup`) with minimal permissions.

### Redis StatefulSet

| Setting | Value |
|---------|-------|
| Image | `redis:7-alpine` |
| Port | 6379 |
| Max memory | 256mb (allkeys-lru) |
| PVC | 5Gi, ReadWriteOnce |
| CPU | 50m-200m |
| Memory | 128Mi-512Mi |

### ConfigMap

Application configuration keys: `OPENACE_SECURITY_MODE` (required, set to `"production"` here — missing or invalid values fail startup), `FLASK_APP`, `PYTHONUNBUFFERED`, `LOG_LEVEL`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `REDIS_HOST`, `REDIS_PORT`, `ENABLE_SSO`, `ENABLE_MULTI_TENANT`, `ENABLE_AUDIT_LOG`, `ENABLE_CONTENT_FILTER`, `WORKSPACE_BASE_DIR`, `AUDIT_LOG_RETENTION_DAYS`, `DATA_RETENTION_DAYS`

### Secret

**IMPORTANT:** Change all placeholder values before deploying to production. Use sealed-secrets or an external secret management tool.

Keys: `SECRET_KEY`, `OPENACE_ENCRYPTION_KEY`, `UPLOAD_AUTH_KEY`, `DB_USER`, `DB_PASSWORD`, `REDIS_PASSWORD`

### PersistentVolumeClaim

- Name: `open-ace-data`
- Size: 10Gi
- Access: ReadWriteMany
- Mounts:
  - `/workspace` via subPath `workspace`
  - `/home/open-ace/.open-ace` via subPath `config`

### RBAC

- ServiceAccount: `open-ace`
- Role: get/list/watch on configmaps; get/list on pods
- RoleBinding: Binds role to service account in `open-ace` namespace

### NetworkPolicy

**Ingress:**
- Allow from `ingress-nginx` namespace to port 19888
- Allow from `open-ace` namespace (health checks)

**Egress:**
- Allow DNS (UDP 53)
- Allow PostgreSQL (TCP 5432) to database pods
- Allow Redis (TCP 6379) to cache pods
- Allow HTTPS (TCP 443) to external IPs

### PodDisruptionBudget

- `minAvailable: 50%` — Keeps at least 50% of web pods available during voluntary disruptions

**PDB Percentage Behavior** (Issue #1821 F6):
- At 3 replicas: 50% = 2 available (allows 1 disruption) — **same as previous absolute value**
- At 2 replicas: 50% = 1 available (allows 1 disruption) — **more permissive than previous**
- At 1 replica: 50% = 1 available (allows 0 disruptions)
- Review this behavior before scaling down the HPA floor below 3 replicas.

## Configuration

### Required Changes

Before deploying, update:

1. **Ingress host** in `service.yaml` — Replace `open-ace.example.com`
2. **Secret values** in `configmap.yaml` — Generate strong passwords and keys
3. **StorageClass** in `storage.yaml` — Match your cluster's StorageClass
4. **Image** in `kustomization.yaml` — Set your container registry path

### Current support boundary

- The shipped Kubernetes manifest is a **multi-replica reference deployment with sticky routing** because that is still the safest default for live terminal relay WebSockets.
- Ordinary HTTP/API requests can be balanced across pods. Remote session commands, command responses, session output replay, session-machine bindings, machines, sessions, messages, quotas, and audit records are persisted.
- If the pod that owns an active terminal/relay socket restarts, persisted remote-session state remains available, but that live terminal bridge must reconnect.
- Browser SSE reconnects can replay persisted remote session output after a web-pod restart.
- Tenant-aware schema and query boundaries cover users, projects, workspace sessions/messages, usage aggregates, audit logs, remote machines, permissions, and quotas; system administrators retain intentional global visibility.

### TLS with cert-manager

The Ingress is pre-configured for cert-manager with `letsencrypt-prod` ClusterIssuer. Install cert-manager first:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

## Monitoring

The `/health` endpoint is deprecated and delegates to `/readyz` (its response includes `deprecated: true`). Use `/livez` for liveness and `/readyz` for readiness; `/readyz` also reports the service status and git commit hash.

**Prometheus Monitoring**: the `prometheus.io/scrape` annotation scrapes the application `/metrics` endpoint (exported via `prometheus_flask_exporter`).

**Image Registry Dependency** (Issue #1821 F3):
With `imagePullPolicy: Always`, the container registry becomes a critical dependency. If the registry is unavailable:
- New pods cannot start (even with cached images on nodes)
- Pod restarts and rescheduling will fail
- Consider implementing a registry cache (e.g., Harbor proxy cache) for production deployments
- In Phase 2, digest pinning will reduce this dependency by allowing cached images to be reused

## Scaling

The reference manifest starts with three web replicas plus sticky routing. For manual restarts or rescheduling:

```bash
kubectl rollout restart deployment open-ace -n open-ace
```

---

## 中文

## 前提条件

- Kubernetes 集群（1.24+）
- 已配置 `kubectl`
- 可用且支持 `ReadWriteMany` 的 StorageClass（用于共享应用 PVC）
- Ingress 控制器（推荐 nginx-ingress）
- cert-manager（可选，用于 TLS）

## 快速部署

```bash
kubectl apply -k k8s/
```

## 资源清单

所有清单文件在 `k8s/` 目录中，按功能组织：

```
k8s/
├── namespace.yaml      # 命名空间: open-ace
├── configmap.yaml      # ConfigMap + Secret
├── storage.yaml        # PVC + ServiceAccount + RBAC
├── database.yaml       # PostgreSQL + Redis StatefulSets
├── deployment.yaml     # 应用 Deployment + HPA
├── scheduler-deployment.yaml  # 自主开发调度器 Deployment
├── scheduler-service.yaml     # 调度器 Service
├── service.yaml        # Service + Ingress
├── policies.yaml       # PDB + NetworkPolicy
└── kustomization.yaml  # Kustomize 配置
```

### 命名空间

创建带有标准 Kubernetes 标签的 `open-ace` 命名空间。

### Deployment

| 设置 | 值 |
|------|-----|
| 副本数 | 3 |
| 镜像 | `open-ace:latest` |
| 容器端口 | 19888 |
| 更新策略 | RollingUpdate（maxSurge=1, maxUnavailable=0） |
| 安全上下文 | `runAsNonRoot: true`、`runAsUser: 1000`、`allowPrivilegeEscalation: false` |

**资源限制：**

| 资源 | 请求 | 限制 |
|------|------|------|
| CPU | 100m | 500m |
| 内存 | 256Mi | 512Mi |

**健康检查（Issue #2186）：**
- 存活检查：HTTP GET `/livez`，initialDelay=10s，period=10s（仅检查进程存活）
- 就绪检查：HTTP GET `/readyz`，initialDelay=5s，period=5s（检查数据库、配置、依赖）
- 启动探针：HTTP GET `/readyz`，failureThreshold=60，period=5s（最多等待 300s）
- Prometheus 抓取：`/metrics` 端点（Prometheus 格式）

**端点职责：**
- `/livez`：进程存活检查，不检查依赖，避免因短暂抖动触发重启
- `/readyz`：就绪检查，验证数据库连接、Schema 兼容性、配置目录、工作空间
- `/metrics`：Prometheus 指标暴露
- `/health`：已弃用，委托给 `/readyz`，响应包含 `deprecated: true`

**Pod 反亲和性：** 优先分散到不同节点，以便在容量允许时保持可用性。

**HorizontalPodAutoscaler：** 参考清单至少保留 3 个副本，并可根据 CPU 与内存利用率扩展到 10 个副本。

**粘性路由：** Service 使用 `sessionAffinity: ClientIP`，nginx Ingress 使用 cookie affinity。远程会话 HTTP 控制态已经持久化并可跨 Pod，但实时终端 relay WebSocket bridge 仍属于单个 Web 进程；对活跃终端会话而言，粘性路由仍是最稳妥的默认配置。

**HA 支持（Issue #1851）：**

实时终端和 VSCode WebSocket 连接采用"可重连恢复"的 HA 模式：

- Relay 状态注册到 Redis 实现跨 Pod 感知
- 浏览器连接到非 owner Pod 时，会收到重定向 close frame（code 3010）并重连到 owner Pod
- Terminal 历史不持久化，重连后显示 "Connection recovered"
- Redis 故障时自动降级到内存模式（仅限本 Pod）
- `preStop` hook（30s）在滚动更新时提供优雅关闭

建议配置：
- 保持粘性路由以获得最佳体验
- 监控 Redis 健康状态和熔断器状态
- 使用 `preStop` hook 允许活跃连接排空

**多用户工作区说明：** Docker 镜像本身通过 `USER 1000` 指令默认以非 root 用户 `open-ace`（uid 1000）运行，默认 Kubernetes 清单也通过 `runAsNonRoot: true` / `runAsUser: 1000` 予以加强。如果使用 `plain` 隔离 backend（`workspace.isolation`）且需要在容器内动态创建 Linux 用户，请使用专门的 overlay 显式让 Web Pod 以 root 运行（`runAsUser: 0`）**并** 设置 `OPENACE_ALLOW_ROOT_MULTI_USER=1`；入口脚本在缺少两者之一时会直接报错退出，并请在集群变更流程中记录该例外。

### Service 与 Ingress

**ClusterIP Service：** 端口 80 → targetPort 19888

**Ingress：**
- 主机：`open-ace.example.com`（需修改）
- TLS 通过 cert-manager（`letsencrypt-prod`）
- Body 大小：50m
- 超时：300s read/send

### PostgreSQL StatefulSet

| 设置 | 值 |
|------|-----|
| 镜像 | `postgres:15-alpine` |
| 端口 | 5432 |
| 数据库 | `openace` |
| PVC | 10Gi, ReadWriteOnce |
| CPU | 100m-500m |
| 内存 | 256Mi-1Gi |

凭证来自 Secret `open-ace-secrets`（键：`DB_USER`、`DB_PASSWORD`）。

#### 数据库备份责任

**重要：** 参考 Kubernetes 清单**不包含**自动数据库备份。您需要自行负责：

1. **备份策略：** 选择以下方案之一：
   - **托管 PostgreSQL**（生产环境推荐）：云厂商负责备份和 PITR
   - **自管备份：** 使用 `k8s/extras/backup/` 中的可选 CronJob

2. **RPO/RTO：** 由您定义并验证：
   - RPO（恢复点目标）：取决于备份频率
   - RTO（恢复时间目标）：取决于恢复演练频率

3. **恢复演练：** 建议定期进行恢复测试（至少每月一次）

详细备份/恢复步骤请参见 [DATABASE_BACKUP.md](./DATABASE_BACKUP.md)。

#### 可选备份 CronJob

`k8s/extras/backup/` 提供备份 CronJob：

```bash
# 部署备份基础设施
kubectl apply -k k8s/extras/backup/

# 验证 CronJob
kubectl get cronjob -n open-ace
```

**备份 CronJob 特性：**
- 每日 02:00 UTC 执行 PostgreSQL 备份
- 使用 `pg_restore --list` 进行完整性校验
- 上传到 S3 兼容对象存储
- 资源限制：512Mi 内存、500m CPU
- 超时：1 小时（大型数据库可调整）

**NetworkPolicy 兼容性：** 备份 Job 与现有 NetworkPolicy 兼容，无需修改（已允许到 PostgreSQL 和外部 HTTPS 的出站流量）。

**RBAC 要求：** 备份 Job 使用独立的 ServiceAccount（`open-ace-backup`），具有最小权限。

### Redis StatefulSet

| 设置 | 值 |
|------|-----|
| 镜像 | `redis:7-alpine` |
| 端口 | 6379 |
| 最大内存 | 256mb（allkeys-lru） |
| PVC | 5Gi, ReadWriteOnce |
| CPU | 50m-200m |
| 内存 | 128Mi-512Mi |

### ConfigMap

应用配置键：`OPENACE_SECURITY_MODE`（必填，此处为 `"production"` —— 缺失或非法值会导致启动失败）、`FLASK_APP`、`PYTHONUNBUFFERED`、`LOG_LEVEL`、`DB_HOST`、`DB_PORT`、`DB_NAME`、`REDIS_HOST`、`REDIS_PORT`、`ENABLE_SSO`、`ENABLE_MULTI_TENANT`、`ENABLE_AUDIT_LOG`、`ENABLE_CONTENT_FILTER`、`WORKSPACE_BASE_DIR`、`AUDIT_LOG_RETENTION_DAYS`、`DATA_RETENTION_DAYS`

### Secret

**重要：** 部署到生产环境前，请更改所有占位值。建议使用 sealed-secrets 或外部密钥管理工具。

键：`SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`、`UPLOAD_AUTH_KEY`、`DB_USER`、`DB_PASSWORD`、`REDIS_PASSWORD`

### PersistentVolumeClaim

- 名称：`open-ace-data`
- 大小：10Gi
- 访问模式：ReadWriteMany
- 挂载路径：
  - `/workspace`（subPath `workspace`）
  - `/home/open-ace/.open-ace`（subPath `config`）

### RBAC

- ServiceAccount：`open-ace`
- Role：对 configmaps 的 get/list/watch；对 pods 的 get/list
- RoleBinding：将角色绑定到 `open-ace` 命名空间中的服务账户

### NetworkPolicy

**入站规则：**
- 允许来自 `ingress-nginx` 命名空间到端口 19888
- 允许来自 `open-ace` 命名空间（健康检查）

**出站规则：**
- 允许 DNS（UDP 53）
- 允许到数据库 Pod 的 PostgreSQL（TCP 5432）
- 允许到缓存 Pod 的 Redis（TCP 6379）
- 允许到外部 IP 的 HTTPS（TCP 443）

### PodDisruptionBudget

- `minAvailable: 50%` — 自愿驱逐期间至少保留 50% 的 Web Pod 可用

**PDB 百分比行为**（Issue #1821 F6）：
- 3 副本时：50% = 2 个可用（允许 1 次驱逐）—— **与旧的绝对值相同**
- 2 副本时：50% = 1 个可用（允许 1 次驱逐）—— **比之前更宽松**
- 1 副本时：50% = 1 个可用（允许 0 次驱逐）
- 将 HPA 下限缩到 3 副本以下之前，请先评估该行为。

## 配置

### 必须修改项

部署前请更新：

1. **Ingress 主机名** 在 `service.yaml` 中 — 替换 `open-ace.example.com`
2. **Secret 值** 在 `configmap.yaml` 中 — 生成强密码和密钥
3. **StorageClass** 在 `storage.yaml` 中 — 匹配集群的 StorageClass
4. **镜像** 在 `kustomization.yaml` 中 — 设置你的容器镜像仓库路径

### 当前支持边界

- 仓库内提供的 Kubernetes 清单仍是**带粘性路由的多副本参考部署**，因为这对实时终端 relay WebSocket 仍是最稳妥的默认配置。
- 普通 HTTP/API 请求可以在 Pod 间负载均衡。远程会话命令、命令响应、会话输出回放、session-machine 绑定、远程机器、会话、消息、配额和审计记录均已持久化。
- 如果承载某个活跃终端 / relay socket 的 Pod 重启，已持久化的远程会话状态仍可用，但该实时终端 bridge 需要重新连接。
- 浏览器 SSE 重连可以在 Web Pod 重启后回放已持久化的远程会话输出。
- tenant-aware schema / query 边界覆盖用户、项目、工作区会话与消息、用量聚合、审计日志、远程机器、权限和配额；系统管理员保留有意设计的全局可见性。

### 使用 cert-manager 配置 TLS

Ingress 已为 cert-manager 预配置了 `letsencrypt-prod` ClusterIssuer。请先安装 cert-manager：

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

## 监控

`/health` 端点已废弃，委托给 `/readyz`（响应包含 `deprecated: true`）。存活探针请使用 `/livez`，就绪探针请使用 `/readyz`；`/readyz` 同样返回服务状态和 git commit hash。

**Prometheus 监控**：`prometheus.io/scrape` 注解会抓取应用 `/metrics` 端点（通过 `prometheus_flask_exporter` 导出）。

## 扩容

当前参考清单以三个 Web 副本和粘性路由作为起点。如需重启或重新调度：

```bash
kubectl rollout restart deployment open-ace -n open-ace
```

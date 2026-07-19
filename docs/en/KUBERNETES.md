# Kubernetes Deployment

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

**Health Checks:**
- Liveness: HTTP GET `/health`, initialDelay=10s, period=10s
- Readiness: HTTP GET `/health`, initialDelay=5s, period=5s

**Pod Anti-Affinity:** Preferred across nodes to preserve availability when capacity allows.

**HorizontalPodAutoscaler:** The reference manifest keeps at least 3 replicas and can scale to 10 replicas based on CPU and memory utilization.

**Sticky routing:** The Service uses `sessionAffinity: ClientIP`, and the nginx Ingress uses cookie affinity. Remote session HTTP control state is persisted and can cross pods, but live terminal relay WebSocket bridges still belong to one web process; sticky routing remains the safest default for active terminal sessions.

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

**Multi-user workspace note:** The Docker image itself defaults to the non-root `open-ace` user (uid 1000) via a `USER 1000` directive, and the default Kubernetes manifest reinforces this with `runAsNonRoot: true` / `runAsUser: 1000`. If you enable `workspace.multi_user_mode` and need dynamic Linux user creation inside the container, deploy a dedicated overlay that intentionally runs the web pod as root (`runAsUser: 0`) **and** sets `OPENACE_ALLOW_ROOT_MULTI_USER=1`; the entrypoint fail-fasts without both, and you should document that exception in your cluster change process.

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

For detailed backup/restore procedures, see [DATABASE-BACKUP.md](./DATABASE-BACKUP.md).

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

Application configuration keys: `FLASK_APP`, `FLASK_ENV`, `PYTHONUNBUFFERED`, `LOG_LEVEL`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `REDIS_HOST`, `REDIS_PORT`, `ENABLE_SSO`, `ENABLE_MULTI_TENANT`, `ENABLE_AUDIT_LOG`, `ENABLE_CONTENT_FILTER`, `WORKSPACE_BASE_DIR`, `AUDIT_LOG_RETENTION_DAYS`, `DATA_RETENTION_DAYS`

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
- Role: get/list/watch on configmaps, secrets, pods
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

- `minAvailable: 2` — Keeps at least two web pods available during voluntary disruptions

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

The `/health` endpoint returns service status and git commit hash.

## Scaling

The reference manifest starts with three web replicas plus sticky routing. For manual restarts or rescheduling:

```bash
kubectl rollout restart deployment open-ace -n open-ace
```

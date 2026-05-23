# Kubernetes Deployment

## Prerequisites

- Kubernetes cluster (1.24+)
- `kubectl` configured
- StorageClass available (default: `standard`)
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
| Container port | 5001 |
| Strategy | RollingUpdate (maxSurge=1, maxUnavailable=0) |
| Security context | runAsNonRoot, runAsUser=1000 |

**Resource Limits:**

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 100m | 500m |
| Memory | 256Mi | 512Mi |

**Health Checks:**
- Liveness: HTTP GET `/health`, initialDelay=10s, period=10s
- Readiness: HTTP GET `/health`, initialDelay=5s, period=5s

**Pod Anti-Affinity:** Preferred spread across different nodes.

### HorizontalPodAutoscaler

| Setting | Value |
|---------|-------|
| Min replicas | 3 |
| Max replicas | 10 |
| CPU target | 70% |
| Memory target | 80% |
| Scale up | max(100%/15s, 2 pods/15s) |
| Scale down | 10%/60s, stabilization 300s |

### Service & Ingress

**ClusterIP Service:** Port 80 → targetPort 5001

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

Application configuration keys: `FLASK_APP`, `FLASK_ENV`, `PYTHONUNBUFFERED`, `LOG_LEVEL`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `REDIS_HOST`, `REDIS_PORT`, `ENABLE_SSO`, `ENABLE_MULTI_TENANT`, `ENABLE_AUDIT_LOG`, `ENABLE_CONTENT_FILTER`, `AUDIT_LOG_RETENTION_DAYS`, `DATA_RETENTION_DAYS`

### Secret

**IMPORTANT:** Change all placeholder values before deploying to production. Use sealed-secrets or an external secret management tool.

Keys: `SECRET_KEY`, `UPLOAD_AUTH_KEY`, `DB_USER`, `DB_PASSWORD`, `REDIS_PASSWORD`

### PersistentVolumeClaim

- Name: `open-ace-data`
- Size: 10Gi
- Access: ReadWriteOnce
- Mount: `/app/data`

### RBAC

- ServiceAccount: `open-ace`
- Role: get/list/watch on configmaps, secrets, pods
- RoleBinding: Binds role to service account in `open-ace` namespace

### NetworkPolicy

**Ingress:**
- Allow from `ingress-nginx` namespace to port 5001
- Allow from `open-ace` namespace (health checks)

**Egress:**
- Allow DNS (UDP 53)
- Allow PostgreSQL (TCP 5432) to database pods
- Allow Redis (TCP 6379) to cache pods
- Allow HTTPS (TCP 443) to external IPs

### PodDisruptionBudget

- `minAvailable: 2` — Ensures at least 2 replicas during disruptions

## Configuration

### Required Changes

Before deploying, update:

1. **Ingress host** in `service.yaml` — Replace `open-ace.example.com`
2. **Secret values** in `configmap.yaml` — Generate strong passwords and keys
3. **StorageClass** in `storage.yaml` — Match your cluster's StorageClass
4. **Image** in `kustomization.yaml` — Set your container registry path

### TLS with cert-manager

The Ingress is pre-configured for cert-manager with `letsencrypt-prod` ClusterIssuer. Install cert-manager first:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

## Monitoring

Prometheus annotations are set on the Deployment:

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "5001"
prometheus.io/path: "/metrics"
```

The `/health` endpoint returns service status and git commit hash.

## Scaling

The HPA automatically scales between 3-10 replicas based on CPU (70%) and memory (80%) utilization. For manual scaling:

```bash
kubectl scale deployment open-ace -n open-ace --replicas=5
```

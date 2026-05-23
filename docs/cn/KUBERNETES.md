# Kubernetes 部署

## 前提条件

- Kubernetes 集群（1.24+）
- 已配置 `kubectl`
- 可用的 StorageClass（默认：`standard`）
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
| 容器端口 | 5001 |
| 更新策略 | RollingUpdate（maxSurge=1, maxUnavailable=0） |
| 安全上下文 | runAsNonRoot, runAsUser=1000 |

**资源限制：**

| 资源 | 请求 | 限制 |
|------|------|------|
| CPU | 100m | 500m |
| 内存 | 256Mi | 512Mi |

**健康检查：**
- 存活检查：HTTP GET `/health`，initialDelay=10s，period=10s
- 就绪检查：HTTP GET `/health`，initialDelay=5s，period=5s

**Pod 反亲和性：** 优先分布在不同节点上。

### HorizontalPodAutoscaler

| 设置 | 值 |
|------|-----|
| 最小副本数 | 3 |
| 最大副本数 | 10 |
| CPU 目标 | 70% |
| 内存目标 | 80% |
| 扩容 | max(100%/15s, 2 pods/15s) |
| 缩容 | 10%/60s，稳定期 300s |

### Service 与 Ingress

**ClusterIP Service：** 端口 80 → targetPort 5001

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

应用配置键：`FLASK_APP`、`FLASK_ENV`、`PYTHONUNBUFFERED`、`LOG_LEVEL`、`DB_HOST`、`DB_PORT`、`DB_NAME`、`REDIS_HOST`、`REDIS_PORT`、`ENABLE_SSO`、`ENABLE_MULTI_TENANT`、`ENABLE_AUDIT_LOG`、`ENABLE_CONTENT_FILTER`、`AUDIT_LOG_RETENTION_DAYS`、`DATA_RETENTION_DAYS`

### Secret

**重要：** 部署到生产环境前，请更改所有占位值。建议使用 sealed-secrets 或外部密钥管理工具。

键：`SECRET_KEY`、`UPLOAD_AUTH_KEY`、`DB_USER`、`DB_PASSWORD`、`REDIS_PASSWORD`

### PersistentVolumeClaim

- 名称：`open-ace-data`
- 大小：10Gi
- 访问模式：ReadWriteOnce
- 挂载路径：`/app/data`

### RBAC

- ServiceAccount：`open-ace`
- Role：对 configmaps、secrets、pods 的 get/list/watch 权限
- RoleBinding：将角色绑定到 `open-ace` 命名空间中的服务账户

### NetworkPolicy

**入站规则：**
- 允许来自 `ingress-nginx` 命名空间到端口 5001
- 允许来自 `open-ace` 命名空间（健康检查）

**出站规则：**
- 允许 DNS（UDP 53）
- 允许到数据库 Pod 的 PostgreSQL（TCP 5432）
- 允许到缓存 Pod 的 Redis（TCP 6379）
- 允许到外部 IP 的 HTTPS（TCP 443）

### PodDisruptionBudget

- `minAvailable: 2` — 确保中断期间至少有 2 个副本

## 配置

### 必须修改项

部署前请更新：

1. **Ingress 主机名** 在 `service.yaml` 中 — 替换 `open-ace.example.com`
2. **Secret 值** 在 `configmap.yaml` 中 — 生成强密码和密钥
3. **StorageClass** 在 `storage.yaml` 中 — 匹配集群的 StorageClass
4. **镜像** 在 `kustomization.yaml` 中 — 设置你的容器镜像仓库路径

### 使用 cert-manager 配置 TLS

Ingress 已为 cert-manager 预配置了 `letsencrypt-prod` ClusterIssuer。请先安装 cert-manager：

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

## 监控

Deployment 上设置了 Prometheus 注解：

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "5001"
prometheus.io/path: "/metrics"
```

`/health` 端点返回服务状态和 git commit hash。

## 扩容

HPA 基于 CPU（70%）和内存（80%）利用率自动在 3-10 个副本之间扩缩。手动扩缩：

```bash
kubectl scale deployment open-ace -n open-ace --replicas=5
```

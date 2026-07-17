# Kubernetes 部署

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

**健康检查：**
- 存活检查：HTTP GET `/health`，initialDelay=10s，period=10s
- 就绪检查：HTTP GET `/health`，initialDelay=5s，period=5s

**Pod 反亲和性：** 优先分散到不同节点，以便在容量允许时保持可用性。

**HorizontalPodAutoscaler：** 参考清单至少保留 3 个副本，并可根据 CPU 与内存利用率扩展到 10 个副本。

**粘性路由：** Service 使用 `sessionAffinity: ClientIP`，nginx Ingress 使用 cookie affinity。远程工作区终端 / relay 的活跃会话仍有部分运行态保存在单个 Web 进程内，因此同一个浏览器 / Agent 会话的请求必须持续回到同一个 Pod。

**多用户工作区说明：** 默认 Kubernetes 清单以非 root 用户运行 Web 容器。如果启用 `workspace.multi_user_mode` 且需要在容器内动态创建 Linux 用户，请使用专门的 overlay 显式让 Web Pod 以 root 运行，并在集群变更流程中记录该例外。

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

应用配置键：`FLASK_APP`、`FLASK_ENV`、`PYTHONUNBUFFERED`、`LOG_LEVEL`、`DB_HOST`、`DB_PORT`、`DB_NAME`、`REDIS_HOST`、`REDIS_PORT`、`ENABLE_SSO`、`ENABLE_MULTI_TENANT`、`ENABLE_AUDIT_LOG`、`ENABLE_CONTENT_FILTER`、`WORKSPACE_BASE_DIR`、`AUDIT_LOG_RETENTION_DAYS`、`DATA_RETENTION_DAYS`

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
- Role：对 configmaps、secrets、pods 的 get/list/watch 权限
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

- `minAvailable: 2` — 自愿驱逐期间至少保留两个 Web Pod 可用

## 配置

### 必须修改项

部署前请更新：

1. **Ingress 主机名** 在 `service.yaml` 中 — 替换 `open-ace.example.com`
2. **Secret 值** 在 `configmap.yaml` 中 — 生成强密码和密钥
3. **StorageClass** 在 `storage.yaml` 中 — 匹配集群的 StorageClass
4. **镜像** 在 `kustomization.yaml` 中 — 设置你的容器镜像仓库路径

### 当前支持边界

- 仓库内提供的 Kubernetes 清单是**带粘性路由的多副本参考部署**，不是完全无状态的活跃会话 HA 设计。
- 普通 HTTP/API 请求可以在 Pod 间负载均衡。活跃的远程工作区终端 relay、输出缓冲、命令队列和 WebSocket 对象仍保存在单个进程内。
- 如果承载某个活跃终端 / relay 会话的 Pod 重启，数据库中的持久记录仍在，但内存缓冲和实时 relay socket 会中断；用户可能需要重新连接、恢复或重建该活跃终端 / 会话。
- 移除粘性路由需要完成 [#1782](https://github.com/open-ace/open-ace/issues/1782) 中的运行态外置化。
- 更强的 tenant-aware schema / query 隔离在 [#1781](https://github.com/open-ace/open-ace/issues/1781) 中继续推进。

### 使用 cert-manager 配置 TLS

Ingress 已为 cert-manager 预配置了 `letsencrypt-prod` ClusterIssuer。请先安装 cert-manager：

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
```

## 监控

`/health` 端点返回服务状态和 git commit hash。

## 扩容

当前参考清单以三个 Web 副本和粘性路由作为起点。如需重启或重新调度：

```bash
kubectl rollout restart deployment open-ace -n open-ace
```

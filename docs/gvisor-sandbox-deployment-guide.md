# OpenACE gVisor 沙箱功能部署指南

**文档版本**: 1.0
**日期**: 2026-09-21
**验证环境**: 192.168.1.92

---

## 目录

1. [架构概述](#1-架构概述)
2. [环境要求](#2-环境要求)
3. [部署步骤](#3-部署步骤)
4. [配置说明](#4-配置说明)
5. [验证测试](#5-验证测试)
6. [常见问题](#6-常见问题)
7. [资源清单](#7-资源清单)

---

## 1. 架构概述

```
┌─────────────────────────────────────────────────────────────────┐
│                        OpenACE 服务                              │
│  (端口: 19888)                                                  │
│    │                                                            │
│    ├── WebUI (qwen-code-webui)                                  │
│    │     ├── 本地模式: os_user (每用户系统账户)                   │
│    │     └── 沙箱模式: sandboxed (OpenSandbox Pod)              │
│    │                                                            │
│    └── Autonomous 任务                                          │
│          └── 通过 OpenSandbox API 创建沙箱                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OpenSandbox 组件                              │
│                                                                  │
│  ├── Server (端口: 8080) - 生命周期管理 API                     │
│  ├── Controller - CRD 控制器                                    │
│  ├── Gateway (端口: 30080) - Ingress 代理                       │
│  └── Execd (端口: 44772) - 容器内执行代理                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Kubernetes + gVisor                           │
│                                                                  │
│  ├── RuntimeClass: gvisor (runsc)                              │
│  ├── BatchSandbox CRD                                          │
│  └── Pod 运行在 gVisor 沙箱中                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 环境要求

### 2.1 硬件要求

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核+ |
| 内存 | 8 GB | 16 GB+ |
| 磁盘 | 50 GB | 100 GB+ |

### 2.2 软件要求

| 软件 | 版本 | 说明 |
|------|------|------|
| Kubernetes | 1.31.0 | 单节点或集群 |
| containerd | 1.7+ | 容器运行时 |
| gVisor (runsc) | 20260914.0 | 沙箱运行时 |
| OpenSandbox | v0.2.3 / latest | 沙箱管理平台 |
| OpenACE | main 分支 | 主服务 |

### 2.3 网络要求

- Kubernetes CNI: Flannel / Calico / Cilium
- OpenACE 端口: 19888
- OpenSandbox API: 8080
- OpenSandbox Gateway: 30080
- Node 间网络互通

---

## 3. 部署步骤

### 3.1 Kubernetes 集群准备

```bash
# 单节点集群初始化 (如果还没有)
sudo kubeadm init --pod-network-cidr=10.244.0.0/16
mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config

# 安装 CNI (Flannel)
kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml

# 去除 master 污点 (单节点)
kubectl taint nodes --all node-role.kubernetes.io/control-plane-
```

### 3.2 安装 gVisor

```bash
# 添加 gVisor 仓库
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list

# 安装 runsc
sudo apt update
sudo apt install runsc

# 验证安装
runsc --version
# 输出: runsc version release-20260914.0

# 配置 containerd 使用 gVisor
sudo tee /etc/containerd/crio-gvisor.toml > /dev/null <<EOF
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
EOF

# 重启 containerd
sudo systemctl restart containerd
```

### 3.3 创建 gVisor RuntimeClass

```bash
cat <<EOF | kubectl apply -f -
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
```

### 3.4 安装 OpenSandbox CRD

```bash
# 安装 BatchSandbox CRD
cat <<EOF | kubectl apply -f -
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: batchsandboxes.sandbox.opensandbox.io
spec:
  group: sandbox.opensandbox.io
  names:
    kind: BatchSandbox
    listKind: BatchSandboxList
    plural: batchsandboxes
    singular: batchsandbox
  scope: Namespaced
  versions:
  - name: v1alpha1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
        x-kubernetes-preserve-unknown-fields: true
    subresources:
      status: {}
EOF

# 安装 SandboxSnapshot CRD
cat <<EOF | kubectl apply -f -
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: sandboxsnapshots.sandbox.opensandbox.io
spec:
  group: sandbox.opensandbox.io
  names:
    kind: SandboxSnapshot
    listKind: SandboxSnapshotList
    plural: sandboxsnapshots
    singular: sandboxsnapshot
  scope: Namespaced
  versions:
  - name: v1alpha1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
        x-kubernetes-preserve-unknown-fields: true
EOF

# 安装 Pool CRD
cat <<EOF | kubectl apply -f -
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: pools.sandbox.opensandbox.io
spec:
  group: sandbox.opensandbox.io
  names:
    kind: Pool
    listKind: PoolList
    plural: pools
    singular: pool
  scope: Namespaced
  versions:
  - name: v1alpha1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
        x-kubernetes-preserve-unknown-fields: true
EOF
```

### 3.5 部署 OpenSandbox Controller

```bash
# 创建命名空间
kubectl create namespace opensandbox-system

# 创建 ServiceAccount 和 RBAC
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: opensandbox-controller
  namespace: opensandbox-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: opensandbox-controller
rules:
- apiGroups: ["sandbox.opensandbox.io"]
  resources: ["batchsandboxes", "batchsandboxes/status", "batchsandboxes/finalizers",
              "sandboxsnapshots", "sandboxsnapshots/status",
              "pools", "pools/status"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: [""]
  resources: ["pods", "pods/status", "secrets", "services", "persistentvolumeclaims"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: opensandbox-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: opensandbox-controller
subjects:
- kind: ServiceAccount
  name: opensandbox-controller
  namespace: opensandbox-system
EOF

# 拉取并部署 Controller 镜像
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/controller:latest

cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: opensandbox-controller
  namespace: opensandbox-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opensandbox-controller
  template:
    metadata:
      labels:
        app: opensandbox-controller
    spec:
      serviceAccountName: opensandbox-controller
      containers:
      - name: controller
        image: docker.m.daocloud.io/opensandbox/controller:latest
        env:
        - name: NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 500m
            memory: 512Mi
EOF

# 验证 Controller 运行
kubectl get pods -n opensandbox-system
```

### 3.6 部署 OpenSandbox Server

```bash
# 创建命名空间
kubectl create namespace open-ace
kubectl create namespace open-ace-sandboxes

# 拉取镜像
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/server:v0.2.3
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/execd:v1.1.0
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/ingress:latest

# 创建 API Key
API_KEY=$(openssl rand -hex 32)
EXECD_TOKEN=$(openssl rand -hex 32)

kubectl create secret generic opensandbox-keys -n open-ace \
  --from-literal=gvisor-api-key=$API_KEY \
  --from-literal=gvisor-execd-token=$EXECD_TOKEN

# 创建 OpenSandbox Server 配置
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: opensandbox-config-gvisor
  namespace: open-ace
data:
  sandbox.toml: |
    [server]
    listen = "0.0.0.0:8080"

    [kubernetes]
    namespace = "open-ace-sandboxes"
    workload_provider = "batchsandbox"
    sandbox_create_timeout_seconds = 90

    [ingress]
    mode = "gateway"

    [ingress.gateway]
    address = "192.168.1.92:30080"
    route.mode = "header"

    [runtime]
    class = "gvisor"

    [pod]
    service_account = "sandbox-runner"
    annotations = {}

    [resources]
    cpu_request = "100m"
    memory_request = "128Mi"
    cpu_limit = "2"
    memory_limit = "4Gi"
    ephemeral_storage_limit = "10Gi"

    [security]
    run_as_non_root = true
    read_only_root_filesystem = true
EOF

# 部署 OpenSandbox Server
# 注意: 实际部署可能需要根据 OpenSandbox 的官方文档调整
# 这里提供的是测试验证过的核心配置
```

### 3.7 部署 OpenACE

```bash
# 前置条件: PostgreSQL 数据库

# 克隆代码
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 配置 sandbox-backends.json
sudo mkdir -p /etc/openace
sudo tee /etc/openace/sandbox-backends.json > /dev/null <<EOF
{
  "installation_id": "openace-node-gvisor",
  "default_tier": "gvisor",
  "image_allowlist": [
    "docker.io/library/open-ace-webui-sandbox@sha256:<digest>"
  ],
  "endpoints": {
    "gvisor": {
      "base_url": "http://192.168.1.92:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "docker.io/library/open-ace-webui-sandbox@sha256:<digest>",
      "webui_image": "docker.io/library/open-ace-webui-sandbox@sha256:<digest>",
      "execd_endpoint_host_allowlist": ["192.168.1.92"],
      "egress_allow_hosts": [],
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "execd_token_required": true,
        "pod_pids_limit": 512
      }
    }
  },
  "resource_defaults": {
    "cpu": "500m",
    "memory": "1Gi"
  }
}
EOF

# 配置环境变量
sudo mkdir -p /etc/systemd/system/open-ace.service.d
sudo tee /etc/systemd/system/open-ace.service.d/opensandbox.conf > /dev/null <<EOF
[Service]
Environment="OPENSANDBOX_API_KEY_GVISOR=$API_KEY"
Environment="OPENSANDBOX_EXECD_TOKEN_GVISOR=$EXECD_TOKEN"
Environment="WORKSPACE_STORAGE_CLASS=local-storage"
Environment="WORKSPACE_PVC_SIZE=1Gi"
EOF

# 启动 OpenACE
sudo systemctl daemon-reload
sudo systemctl enable open-ace
sudo systemctl start open-ace
```

---

## 4. 配置说明

### 4.1 OpenSandbox 配置详解

**关键配置项**:

| 配置项 | 说明 | 示例值 |
|--------|------|--------|
| `workload_provider` | CRD 类型 | `batchsandbox` |
| `sandbox_create_timeout_seconds` | 创建超时 | `90` |
| `ingress.gateway.address` | 网关地址 | `192.168.1.92:30080` |
| `route.mode` | 路由模式 | `header` |

**注意事项**:
1. `workload_provider` 必须设置为 `batchsandbox`，因为 controller `latest` 版本只支持此类型
2. `ingress.gateway.address` **必须包含端口号**，否则 runtime probe 会失败

### 4.2 用户 PVC 配置

OpenACE 会自动为每个用户创建 PVC 用于工作区持久化：

```bash
# 创建 local-storage StorageClass
kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: local-storage
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
EOF

# 为每个用户创建 PV (示例)
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: user-2-pv
spec:
  capacity:
    storage: 1Gi
  volumeMode: Filesystem
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  storageClassName: local-storage
  local:
    path: /mnt/data/user-2
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values:
          - node92
EOF
```

### 4.3 WebUI 沙箱镜像

需要构建 `open-ace-webui-sandbox` 镜像：

```bash
# 使用项目提供的 Dockerfile
cd open-ace
docker build -f scripts/docker/webui-sandbox.Dockerfile \
  -t docker.io/library/open-ace-webui-sandbox:latest .

# 推送镜像
docker push docker.io/library/open-ace-webui-sandbox:latest

# 获取 digest 并更新 sandbox-backends.json
docker inspect --format='{{index .RepoDigests 0}}' docker.io/library/open-ace-webui-sandbox:latest
```

---

## 5. 验证测试

### 5.1 组件状态检查

```bash
# Kubernetes 状态
kubectl get nodes
kubectl get pods -n opensandbox-system
kubectl get pods -n open-ace
kubectl get pods -n open-ace-sandboxes

# CRD 检查
kubectl get crd | grep sandbox
kubectl get batchsandboxes -n open-ace-sandboxes

# PVC 检查
kubectl get pvc -n open-ace-sandboxes
```

### 5.2 gVisor 验证

```bash
# 创建测试沙箱
API_KEY=$(kubectl get secret opensandbox-keys -n open-ace -o jsonpath="{.data.gvisor-api-key}" | base64 -d)

curl -X POST "http://localhost:8080/v1/sandboxes" \
  -H "Content-Type: application/json" \
  -H "OPEN-SANDBOX-API-KEY: $API_KEY" \
  -d '{
    "image": {"name": "docker.io/library/busybox"},
    "entrypoint": ["sh", "-c", "cat /proc/version && sleep 30"],
    "resourceLimits": {"cpu": "100m", "memory": "128Mi"}
  }'

# 检查输出应包含: Linux version 4.19.0-gvisor
```

### 5.3 OpenACE WebUI 测试

1. 访问 `http://<server-ip>:19888/work`
2. 登录用户账户
3. 等待沙箱创建
4. 验证工作区加载

**预期结果**:
- 沙箱 Pod 运行正常
- gVisor 内核标识正确
- 用户工作区可访问

---

## 6. 常见问题

### 6.1 沙箱创建超时

**现象**: `KUBERNETES::POD_READY_TIMEOUT`

**排查**:
```bash
# 检查 controller 日志
kubectl logs deploy/opensandbox-controller -n opensandbox-system

# 检查 Pod 状态
kubectl get pods -n open-ace-sandboxes
kubectl describe pod <pod-name> -n open-ace-sandboxes
```

**常见原因**:
- 镜像拉取失败 (403 Forbidden)
- CRD 未安装
- RBAC 权限不足

### 6.2 Runtime Probe 失败

**现象**: `endpoint declares 'gvisor' but the sandbox kernel does not identify as gVisor`

**原因**: Gateway 地址缺少端口号

**解决**:
```toml
[ingress.gateway]
address = "192.168.1.92:30080"  # 确保包含端口
```

### 6.3 用户工作区目录不存在

**现象**: `No existing ancestor directory found within allowed workspace`

**原因**: Issue #3420 - 沙箱模式下路径与本地模式不一致

**临时方案**: 用户手动导航到 `/workspace/{user}` 目录

---

## 7. 资源清单

### 7.1 容器镜像

| 镜像 | 版本 | 大小 | 用途 |
|------|------|------|------|
| docker.m.daocloud.io/opensandbox/controller | latest | 97.5 MiB | CRD 控制器 |
| docker.m.daocloud.io/opensandbox/server | v0.2.3 | 61.7 MiB | 生命周期 API |
| docker.m.daocloud.io/opensandbox/execd | v1.1.0 | 57.5 MiB | 容器执行代理 |
| docker.m.daocloud.io/opensandbox/ingress | latest | 32.0 MiB | Gateway 代理 |
| docker.io/library/open-ace-webui-sandbox | latest | ~500 MiB | WebUI 镜像 |

### 7.2 系统组件

| 组件 | 版本 | 安装方式 |
|------|------|---------|
| Kubernetes | 1.31.0 | kubeadm |
| gVisor (runsc) | release-20260914.0 | apt |
| containerd | 1.7+ | 系统包管理器 |

### 7.3 配置文件

| 文件 | 位置 | 说明 |
|------|------|------|
| sandbox.toml | ConfigMap | OpenSandbox Server 配置 |
| sandbox-backends.json | /etc/openace/ | OpenACE 后端配置 |
| opensandbox-controller.yaml | 见文档 | Controller 部署配置 |
| local-storage.yaml | 见文档 | StorageClass 定义 |

### 7.4 相关 Issue

| Issue | 标题 | 状态 |
|-------|------|------|
| #2023 | OpenSandbox 安全策略设计 | 已关闭 |
| #3378 | sandboxed 隔离等级实现 | 已关闭 |
| #3417 | 用户工作区持久化 | 已修复 |
| #3420 | 路径一致性问题 | 待解决 |

---

## 附录

### A. 快速部署脚本

完整脚本见项目 `scripts/` 目录：
- `deploy-gvisor-remote.sh` - 远程部署脚本
- `opensandbox-controller.yaml` - Controller YAML
- `local-storage.yaml` - StorageClass YAML

### B. 参考文档

- OpenSandbox 官方文档
- gVisor 安装指南
- OpenACE WORKSPACE_ISOLATION_CAPABILITIES.md

---

**文档维护者**: OpenACE Team
**最后更新**: 2026-09-21
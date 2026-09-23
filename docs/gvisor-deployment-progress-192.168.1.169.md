# OpenACE gVisor 沙箱部署进度 - 192.168.1.169

**日期**: 2026-09-22
**目标服务器**: 192.168.1.169 (Rocky Linux 9.5)
**参考文档**: docs/gvisor-sandbox-deployment-guide.md

---

## 部署状态

| 步骤 | 状态 | 说明 |
|------|------|------|
| Kubernetes 集群 | ✅ 完成 | v1.31.14, Flannel CNI |
| gVisor 安装 | ✅ 完成 | runsc release-20260914.0 |
| OpenSandbox CRD | ✅ 完成 | BatchSandbox, SandboxSnapshot, Pool |
| OpenSandbox Controller | ✅ 完成 | opensandbox-system namespace |
| OpenSandbox Server | ✅ 完成 | open-ace namespace, NodePort 30080 |
| OpenACE | ⏳ 待部署 | 需要 PostgreSQL |

---

## 在线下载部分（离线部署需预先准备）

### 1. Kubernetes 镜像 (阿里云镜像源)

```bash
# 镜像列表
registry.aliyuncs.com/google_containers/kube-apiserver:v1.31.14
registry.aliyuncs.com/google_containers/kube-controller-manager:v1.31.14
registry.aliyuncs.com/google_containers/kube-scheduler:v1.31.14
registry.aliyuncs.com/google_containers/kube-proxy:v1.31.14
registry.aliyuncs.com/google_containers/coredns:v1.11.3
registry.aliyuncs.com/google_containers/pause:3.10
registry.aliyuncs.com/google_containers/etcd:3.5.24-0

# 下载命令
kubeadm config images pull --image-repository registry.aliyuncs.com/google_containers --kubernetes-version v1.31.14
```

### 2. Flannel CNI

```bash
# YAML 文件
https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml

# 离线准备：预先下载 YAML 文件和镜像
# 镜像：docker.io/flannel/flannel:v0.26.7
```

### 3. containerd 二进制

```bash
# 官方下载
https://github.com/containerd/containerd/releases/download/v1.7.24/containerd-1.7.24-linux-amd64.tar.gz

# 或使用资源服务器已有的
192.168.1.21:/datastore/open-ace/gVisor/packages/binaries/
```

### 4. runc 二进制

```bash
# 官方下载
https://github.com/opencontainers/runc/releases/download/v1.1.12/runc.amd64
```

---

## Rocky Linux 9.5 与文档差异

文档针对 Ubuntu/Debian 系统，Rocky Linux 9.5 需要调整：

### 包管理器
- 文档使用 `apt` → Rocky 使用 `dnf`
- 需要添加 Kubernetes 官方 RPM 仓库

### 内核模块
- 需要手动加载 `br_netfilter` 模块
- 需要配置 `sysctl` 参数

### containerd 安装
- Rocky 默认仓库没有 containerd
- 需要使用官方二进制或 Docker CE 仓库

---

## 关键配置文件

### /etc/sysctl.d/k8s.conf
```ini
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
```

### containerd gVisor 配置
```toml
# 添加到 /etc/containerd/config.toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
```

### OpenSandbox Server 配置 (ConfigMap)
```toml
[server]
listen = "0.0.0.0:8080"

[kubernetes]
namespace = "open-ace-sandboxes"
workload_provider = "batchsandbox"
sandbox_create_timeout_seconds = 90

[ingress]
mode = "gateway"
[ingress.gateway]
address = "192.168.1.169:30080"
route.mode = "header"

[runtime]
type = "kubernetes"
class = "gvisor"
execd_image = "docker.m.daocloud.io/opensandbox/execd:v1.1.0"

[pod]
service_account = "sandbox-runner"

[resources]
cpu_request = "100m"
memory_request = "128Mi"
cpu_limit = "2"
memory_limit = "4Gi"
```

---

## 已解决的问题

### 1. br_netfilter 模块未加载
**错误**: `Failed to check br_netfilter: stat /proc/sys/net/bridge/bridge-nf-call-iptables: no such file or directory`

**解决**:
```bash
modprobe br_netfilter
echo 'br_netfilter' >> /etc/modules-load.d/k8s.conf
```

### 2. Server 无法加载 Kubernetes 配置
**错误**: `Invalid kube-config file. No configuration found.`

**解决**: 创建包含 ServiceAccount token 的 kubeconfig 并挂载到 Pod

### 3. runtime.type 配置错误
**错误**: `Input should be 'docker' or 'kubernetes'`

**解决**: `runtime.type` 应为 `kubernetes`，`runtime.class` 才是 `gvisor`

### 4. API Key 启动检查失败
**错误**: `server.api_key is empty in non-interactive mode`

**解决**: 设置 `OPENSANDBOX_INSECURE_SERVER=YES` 环境变量（生产环境应正确配置 API key）

---

## 下一步

1. 部署 PostgreSQL 数据库
2. 配置 OpenACE sandbox-backends.json
3. 设置环境变量
4. 启动 OpenACE 服务
5. 验证 gVisor 沙箱功能

---

## 端口汇总

| 服务 | 端口 | 说明 |
|------|------|------|
| Kubernetes API | 6443 | 集群 API |
| OpenSandbox Server | 30080 | NodePort |
| OpenSandbox Gateway | 30090 | NodePort |
| OpenACE | 19888 | 待部署 |
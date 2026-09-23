# OpenACE gVisor 沙箱功能离线部署指南

**文档版本**: 8.2
**日期**: 2026-09-22
**验证环境**: 192.168.1.92 (Rocky Linux 9.5) / 192.168.1.169 (离线部署测试)
**目标环境**: Rocky Linux 9.x / RHEL 9.x
**更新说明**: 
- v8.2: **重要补充**：添加 OpenACE 主服务部署说明（三种部署方式：Docker Compose、Kubernetes、打包安装）
- v8.1: **关键修复**：修正 Server 配置中 runtimeClassName 的设置位置（`[secure_runtime]` 部分）
- v8.0: 补充 containerd-shim-runsc-v1 安装、业务镜像、kubelet 重启步骤（基于 192.168.1.169 实际测试）
- v7.0: 补充所有资源的获取方式、官方下载地址、国内镜像源

---

## 目录

1. [离线包说明](#1-离线包说明)
2. [环境要求](#2-环境要求)
3. [部署步骤](#3-部署步骤)
   - 3.1 准备工作
   - 3.2 安装 containerd
   - 3.3 安装 gVisor
   - 3.4 安装 Kubernetes
   - 3.5 初始化 Kubernetes 集群
   - 3.6 安装 CNI 插件
   - 3.7 创建 gVisor RuntimeClass
   - 3.8 安装 OpenSandbox CRD
   - 3.9 部署 OpenSandbox Controller
   - 3.10 部署 OpenSandbox Server
   - 3.11 配置本地存储
   - 3.12 配置 OpenACE
4. [验证测试](#4-验证测试)
5. [常见问题](#5-常见问题)

---

## 1. 离线包说明

### 1.1 目录结构

```
/datastore/open-ace/gVisor/
├── docs/                              # 文档
│   ├── gvisor-sandbox-deployment-guide.md
│   └── gvisor-sandbox-resources.md
└── packages/
    ├── binaries/                      # 二进制文件
    │   ├── runsc                      # gVisor 运行时 (105M)
    │   └── containerd-shim-runsc-v1   # ✅ 新增：gVisor shim (约 20M)
    ├── cni-plugins/                   # CNI 插件
    │   └── bin/                       # 约 190M
    ├── configs/                       # 配置文件
    │   ├── local-storage.yaml
    │   ├── opensandbox-controller.yaml
    │   └── calico.yaml                # Calico 离线 YAML
    ├── gvisor/                        # gVisor 安装脚本
    │   └── install-gvisor.sh          # ✅ 新增
    ├── images/                        # OpenSandbox 镜像 (250M)
    │   ├── opensandbox-controller-latest.tar
    │   ├── opensandbox-server-v0.2.3.tar
    │   ├── opensandbox-execd-v1.1.0.tar
    │   └── opensandbox-ingress-latest.tar
    ├── k8s-images/                    # Kubernetes + Calico 镜像 (412M)
    │   ├── kube-apiserver-v1.31.0.tar
    │   ├── kube-controller-manager-v1.31.0.tar
    │   ├── kube-scheduler-v1.31.0.tar
    │   ├── kube-proxy-v1.31.0.tar
    │   ├── coredns-v1.11.1.tar
    │   ├── etcd-3.5.15-0.tar
    │   ├── pause-3.10.tar
    │   ├── calico-cni-v3.28.0.tar
    │   ├── calico-node-v3.28.0.tar
    │   └── calico-kube-controllers-v3.28.0.tar
    ├── business-images/               # ✅ 新增：业务镜像（沙箱运行必需）
    │   └── busybox-latest.tar         # busybox:latest (约 2M)
    └── scripts/
        ├── export-gvisor-resources.sh # ✅ 更新（含 K8s/Calico）
        └── import-gvisor-resources.sh
```

### 1.2 镜像清单

| 镜像 | 版本 | 大小 | 用途 |
|------|------|------|------|
| **Kubernetes 核心** ||||
| kube-apiserver | v1.31.0 | 28M | API Server |
| kube-controller-manager | v1.31.0 | 26M | Controller Manager |
| kube-scheduler | v1.31.0 | 20M | Scheduler |
| kube-proxy | v1.31.0 | 30M | Kube Proxy |
| coredns | v1.11.1 | 18M | DNS |
| etcd | 3.5.15-0 | 55M | 数据库 |
| pause | 3.10 | 321K | 沙箱容器 |
| **Calico CNI** ||||
| calico/cni | v3.28.0 | 91M | CNI 插件 |
| calico/node | v3.28.0 | 110M | 节点代理 |
| calico/kube-controllers | v3.28.0 | 34M | 控制器 |
| **OpenSandbox** ||||
| opensandbox/controller | latest | 98M | CRD 控制器 |
| opensandbox/server | v0.2.3 | 62M | 生命周期 API |
| opensandbox/execd | v1.1.0 | 58M | 容器执行代理 |
| opensandbox/ingress | latest | 33M | Gateway 代理 |
| **OpenACE** ||||
| open-ace-webui-sandbox | latest | ~500M | WebUI 容器 |
| **业务镜像** ✅ 新增 ||||
| busybox | latest | 2M | 测试/调试镜像 |

### 1.3 二进制文件清单 ✅ 新增

| 文件 | 大小 | 用途 | 说明 |
|------|------|------|------|
| runsc | 105M | gVisor 运行时 | 必需 |
| containerd-shim-runsc-v1 | 20M | gVisor shim | **必需**，离线部署文档之前遗漏 |

**总大小：约 1.3 GB**（含文档、脚本、WebUI 镜像）

### 1.4 WebUI 沙箱镜像构建

WebUI 镜像需要单独构建并加入离线包：

```bash
# 在有网络访问的机器上
cd /path/to/open-ace
docker build -f scripts/docker/webui-sandbox.Dockerfile \
  -t open-ace-webui-sandbox:latest .

# 导出镜像
docker save open-ace-webui-sandbox:latest | gzip > \
  /datastore/open-ace/gVisor/packages/images/open-ace-webui-sandbox-latest.tar.gz

# 获取镜像 digest（用于配置文件）
docker inspect --format='{{index .RepoDigests 0}}' open-ace-webui-sandbox:latest
```

### 1.5 额外需要准备

以下资源需要从网络下载后放入对应目录：

| 资源 | 下载地址 | 存放位置 |
|------|---------|---------|
| containerd RPM | https://download.docker.com/linux/centos/9/x86_64/stable/Packages/ | packages/rpms/ |
| kubeadm/kubectl/kubelet RPM | https://pkgs.k8s.io/core:/stable:/v1.31/rpm/ | packages/rpms/ |
| kubernetes-cni RPM | https://pkgs.k8s.io/core:/stable:/v1.31/rpm/ | packages/rpms/ |
| PostgreSQL RPM | Rocky 9 AppStream 仓库 | 系统自带 |

### 1.6 离线包资源获取方式

**OpenSandbox 镜像**（官方源 + 国内镜像）：

| 镜像 | 官方地址 | 国内镜像地址 |
|------|---------|-------------|
| opensandbox/controller:latest | ghcr.io/opensandbox-group/controller:latest | docker.m.daocloud.io/opensandbox/controller:latest |
| opensandbox/server:v0.2.3 | ghcr.io/opensandbox-group/server:v0.2.3 | docker.m.daocloud.io/opensandbox/server:v0.2.3 |
| opensandbox/execd:v1.1.0 | ghcr.io/opensandbox-group/execd:v1.1.0 | docker.m.daocloud.io/opensandbox/execd:v1.1.0 |
| opensandbox/ingress:latest | ghcr.io/opensandbox-group/ingress:latest | docker.m.daocloud.io/opensandbox/ingress:latest |

```bash
# 从官方源拉取（推荐）
docker pull ghcr.io/opensandbox-group/controller:latest
docker pull ghcr.io/opensandbox-group/server:v0.2.3
docker pull ghcr.io/opensandbox-group/execd:v1.1.0
docker pull ghcr.io/opensandbox-group/ingress:latest

# 或使用国内镜像源（网络受限时）
docker pull docker.m.daocloud.io/opensandbox/controller:latest
docker pull docker.m.daocloud.io/opensandbox/server:v0.2.3
docker pull docker.m.daocloud.io/opensandbox/execd:v1.1.0
docker pull docker.m.daocloud.io/opensandbox/ingress:latest

# 导出镜像
docker save -o opensandbox-controller-latest.tar \
  ghcr.io/opensandbox-group/controller:latest
```

**Kubernetes 镜像**（官方源 + 国内镜像）：

| 镜像 | 官方地址 | 国内镜像地址 |
|------|---------|-------------|
| kube-apiserver:v1.31.0 | registry.k8s.io/kube-apiserver:v1.31.0 | k8s.m.daocloud.io/kube-apiserver:v1.31.0 |
| kube-controller-manager:v1.31.0 | registry.k8s.io/kube-controller-manager:v1.31.0 | k8s.m.daocloud.io/kube-controller-manager:v1.31.0 |
| kube-scheduler:v1.31.0 | registry.k8s.io/kube-scheduler:v1.31.0 | k8s.m.daocloud.io/kube-scheduler:v1.31.0 |
| kube-proxy:v1.31.0 | registry.k8s.io/kube-proxy:v1.31.0 | k8s.m.daocloud.io/kube-proxy:v1.31.0 |
| coredns:v1.11.1 | registry.k8s.io/coredns:v1.11.1 | k8s.m.daocloud.io/coredns:v1.11.1 |
| etcd:3.5.15-0 | registry.k8s.io/etcd:3.5.15-0 | k8s.m.daocloud.io/etcd:3.5.15-0 |
| pause:3.10 | registry.k8s.io/pause:3.10 | k8s.m.daocloud.io/pause:3.10 |

```bash
# 使用 kubeadm 预拉取镜像（推荐）
kubeadm config images pull --image-repository k8s.m.daocloud.io \
  --kubernetes-version v1.31.0

# 或手动拉取
docker pull k8s.m.daocloud.io/kube-apiserver:v1.31.0
# ... 其他镜像
```

**gVisor runsc 二进制**：

| 资源 | 下载地址 | 说明 |
|------|---------|------|
| runsc 二进制 | https://storage.googleapis.com/gvisor/releases/release/20260914.0/x86_64/runsc | gVisor 运行时 |

```bash
# 下载 runsc
wget -O /datastore/open-ace/gVisor/packages/binaries/runsc \
  https://storage.googleapis.com/gvisor/releases/release/20260914.0/x86_64/runsc

chmod +x /datastore/open-ace/gVisor/packages/binaries/runsc

# 验证
./runsc --version
```

**containerd-shim-runsc-v1（✅ 新增，之前遗漏）**：

| 资源 | 获取方式 | 说明 |
|------|---------|------|
| containerd-shim-runsc-v1 | 从已安装 gVisor 的机器复制 | gVisor 与 containerd 集成的 shim 程序 |

```bash
# 方式 1：从已安装 gVisor 的机器复制（推荐）
# 在源机器（如 192.168.1.92）上
scp /usr/local/bin/containerd-shim-runsc-v1 root@<目标机器>:/datastore/open-ace/gVisor/packages/binaries/

# 方式 2：从 gVisor 官方安装（需要网络）
# 参考：https://gvisor.dev/docs/user_guide/containerd/quick_start/
```

**业务镜像（✅ 新增，之前遗漏）**：

| 镜像 | 官方地址 | 国内镜像地址 | 用途 |
|------|---------|-------------|------|
| busybox:latest | docker.io/library/busybox:latest | docker.m.daocloud.io/library/busybox:latest | 测试/调试 |

```bash
# 拉取并导出镜像（在有网络的机器上）
docker pull docker.m.daocloud.io/library/busybox:latest
docker save docker.m.daocloud.io/library/busybox:latest -o \
  /datastore/open-ace/gVisor/packages/business-images/busybox-latest.tar

# 导入镜像时需要创建 docker.io 标签（离线机器上）
ctr -n k8s.io image import busybox-latest.tar
ctr -n k8s.io image tag docker.m.daocloud.io/library/busybox:latest docker.io/library/busybox:latest
```

**CNI 插件**：

| 资源 | 下载地址 | 说明 |
|------|---------|------|
| CNI 插件 | https://github.com/containernetworking/plugins/releases/download/v1.5.1/cni-plugins-linux-amd64-v1.5.1.tgz | CNI 二进制包 |
| Calico YAML | https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml | Calico 安装清单 |

```bash
# 下载 CNI 插件
wget -O cni-plugins-linux-amd64-v1.5.1.tgz \
  https://github.com/containernetworking/plugins/releases/download/v1.5.1/cni-plugins-linux-amd64-v1.5.1.tgz

# 解压到离线包
mkdir -p /datastore/open-ace/gVisor/packages/cni-plugins/bin
tar -xzf cni-plugins-linux-amd64-v1.5.1.tgz \
  -C /datastore/open-ace/gVisor/packages/cni-plugins/bin

# 下载 Calico YAML
wget -O /datastore/open-ace/gVisor/packages/configs/calico.yaml \
  https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml
```

**PostgreSQL 安装**（OpenACE 必需依赖）：
```bash
# Rocky Linux 9 安装 PostgreSQL
dnf install -y postgresql-server postgresql-contrib

# 初始化数据库
postgresql-setup --initdb

# 启动服务
systemctl enable postgresql
systemctl start postgresql

# 创建 OpenACE 数据库和用户
sudo -u postgres psql << 'EOF'
CREATE USER openace WITH PASSWORD 'your_password';
CREATE DATABASE openace OWNER openace;
GRANT ALL PRIVILEGES ON DATABASE openace TO openace;
\c openace
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EOF
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
| 操作系统 | Rocky Linux 9.x / RHEL 9.x | 推荐 Rocky 9.5 |
| containerd | 1.7+ 或 2.x | 容器运行时 |
| gVisor (runsc) | 20260914.0 | 沙箱运行时 |
| Kubernetes | 1.31.x | 容器编排 |

### 2.3 网络要求

- 节点间网络互通
- 禁用 firewalld 或开放必要端口

---

## 3. 部署步骤

### 3.1 准备工作

```bash
# 1. 从离线包服务器复制资源
scp -r root@192.168.1.21:/datastore/open-ace/gVisor /datastore/

# 2. 设置环境变量
export GVISOR_PKG=/datastore/gVisor/packages

# 3. 禁用防火墙和 SELinux
systemctl stop firewalld
systemctl disable firewalld
setenforce 0
sed -i 's/SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config

# 4. 加载内核模块（Kubernetes 网络必需）
modprobe br_netfilter
modprobe overlay

# 持久化内核模块
cat > /etc/modules-load.d/k8s.conf << EOF
br_netfilter
overlay
EOF

# 5. 配置 sysctl（Kubernetes 网络必需）
cat > /etc/sysctl.d/k8s.conf << EOF
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF

sysctl --system

# 6. 禁用 swap
swapoff -a
sed -i '/swap/d' /etc/fstab
```

### 3.2 安装 containerd

```bash
# 安装 containerd（如果有网络）
yum install -y containerd

# 或从离线包安装（如果准备了 RPM）
# rpm -ivh $GVISOR_PKG/rpms/containerd*.rpm

# 配置 containerd
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml

# 启动 containerd
systemctl enable containerd
systemctl start containerd
```

### 3.3 安装 gVisor

**步骤 1：安装 runsc 运行时**

```bash
cd $GVISOR_PKG/gvisor
./install-gvisor.sh

# 验证
runsc --version
# 输出: runsc version release-20260914.0
```

**步骤 2：安装 containerd-shim-runsc-v1（✅ 新增，之前遗漏）**

⚠️ **重要**：gVisor 与 containerd 集成需要 `containerd-shim-runsc-v1` shim 程序，离线部署文档之前遗漏了此步骤。

```bash
# 复制 shim 程序
cp $GVISOR_PKG/binaries/containerd-shim-runsc-v1 /usr/local/bin/
chmod +x /usr/local/bin/containerd-shim-runsc-v1

# 验证 shim
ls -l /usr/local/bin/containerd-shim-runsc-v1
# -rwxr-xr-x 1 root root 20971520 Sep 22 10:00 /usr/local/bin/containerd-shim-runsc-v1
```

**配置 containerd 使用 gVisor runtime**（关键步骤）：

```bash
# 备份原配置
cp /etc/containerd/config.toml /etc/containerd/config.toml.bak

# 添加 gVisor runtime 配置
cat >> /etc/containerd/config.toml <<'EOF'

# gVisor runtime configuration
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc.options]
    TypeUrl = "io.containerd.runsc.v1.options.v1"
    ConfigPath = "/etc/containerd/runsc.toml"

# 创建 runsc 配置文件（可选，用于调试）
cat > /etc/containerd/runsc.toml <<'EOF'
# gVisor runtime options
EOF

# 重启 containerd
systemctl restart containerd

# ✅ 新增：重启 kubelet（让 kubelet 识别新的 runtime）
# 如果不重启 kubelet，创建沙箱时可能会报错：
# runtime "io.containerd.runsc.v1" binary not installed "containerd-shim-runsc-v1"
systemctl restart kubelet

# 验证配置
ctr plugins ls | grep runsc
# 应看到: io.containerd.runsc.v1
```

### 3.4 安装 Kubernetes

```bash
# 安装 Kubernetes 组件（如果有网络）
cat <<EOF | tee /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.31/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.31/rpm/repodata/repomd.xml.key
EOF

yum install -y kubeadm-1.31.0 kubectl-1.31.0 kubelet-1.31.0

# 或从离线包安装
# rpm -ivh $GVISOR_PKG/rpms/kube*.rpm

# 启动 kubelet
systemctl enable kubelet
```

### 3.5 初始化 Kubernetes 集群

```bash
# 导入镜像
cd $GVISOR_PKG/k8s-images
for tar in *.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import $tar
done

# 初始化集群
kubeadm init --pod-network-cidr=10.244.0.0/16 \
    --image-repository k8s.m.daocloud.io \
    --kubernetes-version v1.31.0

# 配置 kubectl
mkdir -p $HOME/.kube
cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
chown $(id -u):$(id -g) $HOME/.kube/config

# 去除 master 污点（单节点）
kubectl taint nodes --all node-role.kubernetes.io/control-plane-
```

### 3.6 安装 CNI 插件

**离线环境准备**：

在**有网络**的机器上执行：
```bash
# 下载 Calico YAML
wget -O /datastore/open-ace/gVisor/packages/configs/calico.yaml \
    https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml
```

**离线环境安装**：

```bash
# 安装 CNI 二进制
cp -r $GVISOR_PKG/cni-plugins/bin/* /opt/cni/bin/

# 导入 Calico 镜像
cd $GVISOR_PKG/k8s-images
for tar in calico-*.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import $tar
done

# 安装 Calico（使用离线 YAML）
kubectl apply -f $GVISOR_PKG/configs/calico.yaml

# 等待 Calico 启动
kubectl wait --for=condition=Ready pods -n kube-system -l k8s-app=calico-node --timeout=180s

# 验证 Calico
kubectl get pods -n kube-system -l k8s-app=calico-node
```

**如果离线包中没有 calico.yaml**，可以从在线环境手动下载或使用以下简化配置：
```bash
# 最小化 Calico 配置（仅测试环境）
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: calico-node
  namespace: kube-system
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: calico-node
  namespace: kube-system
spec:
  selector:
    matchLabels:
      k8s-app: calico-node
  template:
    metadata:
      labels:
        k8s-app: calico-node
    spec:
      serviceAccountName: calico-node
      containers:
      - name: calico-node
        image: docker.m.daocloud.io/calico/node:v3.28.0
        env:
        - name: CALICO_IPV4POOL_CIDR
          value: "10.244.0.0/16"
        - name: FELIX_MTU
          value: "1440"
        securityContext:
          privileged: true
        volumeMounts:
        - name: lib-modules
          mountPath: /lib/modules
          readOnly: true
      volumes:
      - name: lib-modules
        hostPath:
          path: /lib/modules
EOF
```

### 3.7 创建 gVisor RuntimeClass

```bash
cat <<EOF | kubectl apply -f -
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
```

### 3.8 安装 OpenSandbox CRD

```bash
# 创建命名空间
kubectl create namespace opensandbox-system
kubectl create namespace open-ace
kubectl create namespace open-ace-sandboxes

# 安装 BatchSandbox CRD
cat <<'EOF' | kubectl apply -f -
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
---
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
---
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

# 验证 CRD 安装
kubectl get crd | grep sandbox
```

### 3.9 部署 OpenSandbox Controller

```bash
# 导入 OpenSandbox 镜像
cd $GVISOR_PKG/images
for tar in *.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import $tar
done

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

# 部署 Controller
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
kubectl logs -n opensandbox-system -l app=opensandbox-controller --tail=20
```

### 3.10 部署 OpenSandbox Server

OpenSandbox Server 是独立进程，负责沙箱生命周期管理 API。

**前置准备：创建 sandbox-runner ServiceAccount**：

```bash
# 在 open-ace-sandboxes 命名空间创建 ServiceAccount
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: sandbox-runner
  namespace: open-ace-sandboxes
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: sandbox-runner
  namespace: open-ace-sandboxes
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log", "secrets"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/exec"]
  verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: sandbox-runner
  namespace: open-ace-sandboxes
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: sandbox-runner
subjects:
- kind: ServiceAccount
  name: sandbox-runner
  namespace: open-ace-sandboxes
EOF
```

```bash
# 生成 API Key 和 Token
API_KEY=$(openssl rand -hex 32)
EXECD_TOKEN=$(openssl rand -hex 32)

# 保存密钥
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

    [runtime]
    type = "kubernetes"
    execd_image = "opensandbox/execd:v1.1.0"

    [kubernetes]
    namespace = "open-ace-sandboxes"
    workload_provider = "batchsandbox"
    sandbox_create_timeout_seconds = 90
    # ✅ 修正：runtime_class 不在这里，而是在 [secure_runtime] 部分
    # runtime_class = "gvisor"  # ← 错误！不要写在这里

    # ✅ 新增：关键配置！runtimeClassName 必须在这里设置
    [secure_runtime]
    type = "gvisor"
    k8s_runtime_class = "gvisor"  # ← 正确！这个字段控制 Pod 的 runtimeClassName

    [ingress]
    mode = "gateway"

    [ingress.gateway]
    address = "$(hostname -I | awk '{print $1}'):30080"
    route_mode = "header"

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
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: opensandbox-server
  namespace: open-ace
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: opensandbox-server
rules:
- apiGroups: ["sandbox.opensandbox.io"]
  resources: ["batchsandboxes", "batchsandboxes/status"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: [""]
  resources: ["pods", "pods/status", "pods/log", "pods/exec"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: [""]
  resources: ["secrets", "services", "persistentvolumeclaims"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: opensandbox-server
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: opensandbox-server
subjects:
- kind: ServiceAccount
  name: opensandbox-server
  namespace: open-ace
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: opensandbox-gvisor
  namespace: open-ace
spec:
  replicas: 1
  selector:
    matchLabels:
      app: opensandbox-gvisor
  template:
    metadata:
      labels:
        app: opensandbox-gvisor
    spec:
      serviceAccountName: opensandbox-server
      containers:
      - name: server
        image: docker.m.daocloud.io/opensandbox/server:v0.2.3
        ports:
        - containerPort: 8080
        env:
        - name: OPENSANDBOX_CONFIG_PATH
          value: /config/sandbox.toml
        volumeMounts:
        - name: config
          mountPath: /config
          readOnly: true
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
      volumes:
      - name: config
        configMap:
          name: opensandbox-config-gvisor
---
apiVersion: v1
kind: Service
metadata:
  name: opensandbox-gvisor
  namespace: open-ace
spec:
  type: NodePort
  ports:
  - port: 8080
    targetPort: 8080
    nodePort: 30080
  selector:
    app: opensandbox-gvisor
EOF

# 验证 Server 运行
kubectl get pods -n open-ace
kubectl logs -n open-ace -l app=opensandbox-gvisor --tail=20
```

**⚠️ 关键配置说明**（重要）：

1. **workload_provider** 必须设置为 `batchsandbox`
   - Controller `latest` 版本只支持 `sandbox.opensandbox.io` 组的 CRD
   - 不要使用 `agent-sandbox`（会导致 CRD 不匹配）

2. **gateway.address 必须包含端口号**
   - ✅ 正确：`address = "192.168.1.92:30080"`
   - ❌ 错误：`address = "192.168.1.92"`（会导致 runtime probe 失败）

3. **runtimeClassName 必须在 `[secure_runtime]` 部分设置（✅ v8.1 关键修复）**
   - ✅ 正确：
     ```toml
     [secure_runtime]
     type = "gvisor"
     k8s_runtime_class = "gvisor"  # ← 这个字段控制 Pod 的 runtimeClassName
     ```
   - ❌ 错误：
     ```toml
     [kubernetes]
     runtime_class = "gvisor"  # ← 这个字段不起作用！
     ```
   - **如果配置错误，沙箱会创建成功但使用默认 runc，没有真正的 gVisor 隔离！**

### 3.11 配置本地存储

OpenACE 用户工作区需要持久化存储：

```bash
# 创建 StorageClass
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: local-storage
provisioner: kubernetes.io/no-provisioner
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Delete
EOF

# 创建本地存储目录
mkdir -p /mnt/data/opensandbox-gvisor

# 创建 PV（单节点测试）
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolume
metadata:
  name: opensandbox-gvisor-pv
spec:
  capacity:
    storage: 5Gi
  volumeMode: Filesystem
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Delete
  storageClassName: local-storage
  local:
    path: /mnt/data/opensandbox-gvisor
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: Exists
EOF

# 验证存储
kubectl get sc
kubectl get pv
```

### 3.12 部署 OpenACE 主服务

OpenACE 主服务需要以下组件：
- PostgreSQL 数据库
- OpenACE 后端服务
- OpenACE WebUI（可选）

**部署方式选择**：

| 方式 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| Docker Compose | 单机快速测试 | 简单快速，一键部署 | 不适合生产环境 |
| Kubernetes | 生产环境 | 高可用，可扩展 | 配置复杂 |
| 打包安装 | 物理机部署 | 无容器依赖 | 需要手动配置 |

#### 3.12.1 Docker Compose 方式（推荐用于测试）

**前提条件**：
- Docker 和 Docker Compose 已安装
- PostgreSQL 镜像可用（离线环境需预先导入）

**步骤 1：准备镜像**

```bash
# 导入 PostgreSQL 镜像（离线环境）
# 在有网络的机器上：
docker pull docker.m.daocloud.io/library/postgres:15
docker save docker.m.daocloud.io/library/postgres:15 -o postgres-15.tar

# 传输并导入：
docker load -i postgres-15.tar

# 构建 OpenACE 镜像（在有网络的机器上）
cd /path/to/open-ace
docker build -t open-ace:latest .

# 导出并传输（离线环境）
docker save open-ace:latest -o open-ace-latest.tar
# 传输后导入：
docker load -i open-ace-latest.tar
```

**步骤 2：配置环境变量**

```bash
# 创建 .env 文件
cat > .env << EOF
# 数据库配置
POSTGRES_USER=openace
POSTGRES_PASSWORD=openace123
POSTGRES_DB=openace

# OpenACE 配置
DATABASE_URL=postgresql://openace:openace123@postgres:5432/openace
SECRET_KEY=$(openssl rand -hex 32)

# OpenSandbox 配置（使用前面获取的密钥）
OPENSANDBOX_API_KEY_GVISOR=${API_KEY}
OPENSANDBOX_EXECD_TOKEN_GVISOR=${EXECD_TOKEN}
WORKSPACE_STORAGE_CLASS=local-storage
WORKSPACE_PVC_SIZE=1Gi
EOF
```

**步骤 3：启动服务**

```bash
# 使用 docker-compose.yml
docker compose up -d

# 查看服务状态
docker compose ps

# 查看日志
docker compose logs -f open-ace
```

**步骤 4：初始化数据库**

```bash
# 首次启动时，OpenACE 会自动创建表结构
# 如果没有自动创建，手动执行：
docker compose exec open-ace alembic upgrade head
```

#### 3.12.2 Kubernetes 方式（推荐用于生产）

**适用场景**：生产环境，使用 Kubernetes 部署

**前提条件**：
- Kubernetes 集群已就绪
- 已创建 `open-ace` namespace

**步骤 1：准备离线镜像（离线环境必须）**

> **重要**：离线环境必须先准备镜像，否则后续步骤会因为 `ImagePullBackOff` 失败。

```bash
# === 在有网络的机器上 ===
# 拉取 PostgreSQL 和 Redis 镜像
docker pull docker.m.daocloud.io/library/postgres:15-alpine
docker pull docker.m.daocloud.io/library/redis:7-alpine

# 导出镜像
docker save docker.m.daocloud.io/library/postgres:15-alpine -o postgres-15-alpine.tar
docker save docker.m.daocloud.io/library/redis:7-alpine -o redis-7-alpine.tar

# 传输到离线机器
scp postgres-15-alpine.tar redis-7-alpine.tar root@<目标机器>:/tmp/

# === 在离线机器上 ===
# 导入镜像到 Kubernetes 集群
ctr -n k8s.io image import postgres-15-alpine.tar
ctr -n k8s.io image import redis-7-alpine.tar

# 创建标准标签（k8s/database.yaml 使用标准标签）
ctr -n k8s.io image tag docker.m.daocloud.io/library/postgres:15-alpine postgres:15-alpine
ctr -n k8s.io image tag docker.m.daocloud.io/library/redis:7-alpine redis:7-alpine

# 验证镜像
ctr -n k8s.io image list | grep -E 'postgres|redis'
```

**步骤 2：创建 Secret（必须最先执行）**

> **重要**：`k8s/database.yaml` 中的 PostgreSQL 和 Redis 需要 Secret 中的凭证，必须先创建。

```bash
# 创建数据库和 Redis 凭证
kubectl create secret generic open-ace-secrets -n open-ace \
  --from-literal=DB_USER="openace" \
  --from-literal=DB_PASSWORD="openace123" \
  --from-literal=REDIS_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=database-url="postgresql://openace:openace123@postgres:5432/openace" \
  --from-literal=secret-key="$(openssl rand -hex 32)"

# 验证 Secret
kubectl get secret open-ace-secrets -n open-ace
```

**步骤 3：部署 PostgreSQL 和 Redis**

```bash
# 创建 namespace（如果还没有）
kubectl create namespace open-ace

# 部署 PostgreSQL 和 Redis
kubectl apply -f k8s/database.yaml

# 等待 PostgreSQL 就绪
kubectl wait --for=condition=Ready pods -l app.kubernetes.io/component=database -n open-ace --timeout=180s

# 等待 Redis 就绪
kubectl wait --for=condition=Ready pods -l app.kubernetes.io/component=cache -n open-ace --timeout=180s

# 初始化数据库
kubectl exec -n open-ace statefulset/postgres -- psql -U openace -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
```

**步骤 4：构建并推送 OpenACE 镜像**

```bash
# 在有网络的机器上构建镜像
cd /path/to/open-ace
docker build -t your-registry/open-ace:latest .
docker push your-registry/open-ace:latest

# 或使用本地镜像（离线环境）
docker save your-registry/open-ace:latest -o open-ace-latest.tar
# 传输并导入到目标机器
ctr -n k8s.io image import open-ace-latest.tar
```

**步骤 5：部署 OpenACE**

```bash
# 部署 OpenACE
kubectl apply -k k8s/

# 等待服务就绪
kubectl wait --for=condition=Ready pods -l app=open-ace -n open-ace --timeout=180s

# 验证服务
kubectl logs -n open-ace -l app=open-ace --tail=20
```

#### 3.12.3 打包安装方式（物理机部署）

**适用场景**：无容器环境，如 192.168.1.92

**步骤 1：在有网络的机器上打包**

```bash
# 在开发机上
cd /home/qlfan/workspace/open-ace
git pull origin main  # 拉取最新代码

# 打包
cd scripts/install-central/package-method
./package.sh

# 输出：dist/open-ace-{VERSION}.tar.gz
```

**步骤 2：传输到目标机器**

```bash
# 传输打包文件
scp dist/open-ace-*.tar.gz root@<目标机器>:/tmp/

# 传输 OpenSandbox 镜像（如果需要）
scp /path/to/opensandbox-*.tar root@<目标机器>:/tmp/
```

**步骤 3：在目标机器上安装**

```bash
# 解压
cd /tmp && tar -xzf open-ace-*.tar.gz
cd open-ace-*/

# 安装（会自动检测现有安装）
./scripts/install-central/package-method/install.sh

# 安装过程会：
# 1. 检查依赖（Python 3.11, PostgreSQL）
# 2. 安装 Python 包
# 3. 初始化数据库
# 4. 创建 systemd service
# 5. 启动服务
```

**步骤 4：配置 OpenSandbox 后端**

```bash
# 配置 sandbox-backends.json（见下一节）
# 配置完成后重启服务
systemctl restart open-ace
```

### 3.13 配置 OpenACE 连接 OpenSandbox

**获取密钥信息**：

```bash
# 从 Secret 中获取 API Key 和 EXECD Token
API_KEY=$(kubectl get secret opensandbox-keys -n open-ace \
  -o jsonpath="{.data.gvisor-api-key}" | base64 -d)
EXECD_TOKEN=$(kubectl get secret opensandbox-keys -n open-ace \
  -o jsonpath="{.data.gvisor-execd-token}" | base64 -d)

# 显示密钥（记录备用）
echo "API_KEY: $API_KEY"
echo "EXECD_TOKEN: $EXECD_TOKEN"
```

```bash
# 配置 sandbox-backends.json
mkdir -p /etc/openace
cat > /etc/openace/sandbox-backends.json << 'EOF'
{
  "installation_id": "openace-gvisor",
  "default_tier": "gvisor",
  "image_allowlist": [
    "docker.io/library/open-ace-webui-sandbox@sha256:<YOUR_DIGEST>"
  ],
  "endpoints": {
    "gvisor": {
      "base_url": "http://<SERVER_IP>:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "webui_image": "docker.io/library/open-ace-webui-sandbox@sha256:<YOUR_DIGEST>",
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

# 配置环境变量（使用实际密钥值）
mkdir -p /etc/systemd/system/open-ace.service.d
cat > /etc/systemd/system/open-ace.service.d/opensandbox.conf << EOF
[Service]
Environment="OPENSANDBOX_API_KEY_GVISOR=${API_KEY}"
Environment="OPENSANDBOX_EXECD_TOKEN_GVISOR=${EXECD_TOKEN}"
Environment="WORKSPACE_STORAGE_CLASS=local-storage"
Environment="WORKSPACE_PVC_SIZE=1Gi"
EOF

# 重载 systemd
systemctl daemon-reload
```

**⚠️ 密钥使用提醒**：
- 配置文件中的 `<YOUR_API_KEY>` 和 `<YOUR_TOKEN>` 必须替换为实际值
- 使用上面获取的 `$API_KEY` 和 `$EXECD_TOKEN` 变量
- WebUI 镜像 digest 需要从构建结果中获取：
  ```bash
  docker inspect --format='{{index .RepoDigests 0}}' open-ace-webui-sandbox:latest
  ```

---

## 4. 验证测试

### 4.1 组件状态检查

```bash
# Kubernetes 状态
kubectl get nodes
kubectl get pods -A

# gVisor 运行时验证
runsc --version
# 输出: runsc version release-20260914.0

# ✅ 新增：验证 containerd-shim-runsc-v1（关键步骤）
ls -l /usr/local/bin/containerd-shim-runsc-v1
# 应看到 shim 文件

# 验证 containerd 插件
ctr plugins ls | grep runsc
# 应看到: io.containerd.runsc.v1

# OpenSandbox 验证
kubectl get crd | grep sandbox
kubectl get pods -n opensandbox-system
```

### 4.2 验证 gVisor 运行时（✅ 新增）

**使用 ctr 测试 gVisor 运行时**：

```bash
# 导入业务镜像（如果尚未导入）
cd $GVISOR_PKG/business-images
for tar in *.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import "$tar"
done

# 创建 docker.io 标签（重要：sandbox-backends.json 使用 docker.io 镜像地址）
ctr -n k8s.io image tag docker.m.daocloud.io/library/busybox:latest docker.io/library/busybox:latest

# 使用 gVisor 运行测试容器
ctr -n k8s.io run --rm --runtime=io.containerd.runsc.v1 \
  docker.io/library/busybox:latest test-gvisor \
  cat /proc/version

# 预期输出应包含 "gvisor" 字样：
# Linux version 4.19.0-gvisor ...
```

**如果报错**：
```
runtime "io.containerd.runsc.v1" binary not installed "containerd-shim-runsc-v1": file does not exist
```

说明 `containerd-shim-runsc-v1` 未正确安装，请检查：
1. 文件是否存在：`ls -l /usr/local/bin/containerd-shim-runsc-v1`
2. 是否有执行权限：`chmod +x /usr/local/bin/containerd-shim-runsc-v1`
3. 是否重启了 containerd 和 kubelet：`systemctl restart containerd && systemctl restart kubelet`

### 4.3 创建测试沙箱（Kubernetes Pod）

```bash
# 创建测试 Pod
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: gvisor-test
  namespace: default
spec:
  runtimeClassName: gvisor
  containers:
  - name: test
    image: k8s.m.daocloud.io/pause:3.10
    command: ["sh", "-c", "cat /proc/version && sleep 3600"]
  restartPolicy: Never
EOF

# 检查日志，应包含 "gvisor"
kubectl logs gvisor-test
# 输出: Linux version 4.19.0-gvisor
```

### 4.4 测试 OpenSandbox API

```bash
# 获取 API Key（从 Secret 中读取）
API_KEY=$(kubectl get secret opensandbox-keys -n open-ace \
  -o jsonpath="{.data.gvisor-api-key}" | base64 -d)

# 获取 OpenSandbox Service 地址
SERVER_IP=$(hostname -I | awk '{print $1}')
SERVER_PORT=30080

# 测试创建沙箱
curl -X POST "http://${SERVER_IP}:${SERVER_PORT}/v1/sandboxes" \
  -H "OPEN-SANDBOX-API-KEY: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "image": {"name": "docker.m.daocloud.io/library/busybox:latest"},
    "entrypoint": ["sh", "-c", "uname -a && cat /proc/version"],
    "resourceLimits": {"cpu": "100m", "memory": "128Mi"}
  }'

# 预期输出：
# {"id": "...", "status": "Pending", ...}

# 等待沙箱创建（约 30-60 秒）
kubectl get batchsandboxes -n open-ace-sandboxes -w

# 检查沙箱 Pod
kubectl get pods -n open-ace-sandboxes

# 验证 Pod 运行在 gVisor 中
kubectl logs -n open-ace-sandboxes <pod-name>
# 应看到: Linux version 4.19.0-gvisor
```

### 4.5 WebUI 测试

1. 访问 `http://<SERVER_IP>:19888/work`
2. 登录用户账户
3. 等待沙箱创建
4. 验证工作区加载

---

## 5. 常见问题

### 5.1 containerd-shim-runsc-v1 缺失（✅ 新增）

**现象**:
- 沙箱创建后 Pod 一直 Pending
- ctr 测试报错：
  ```
  runtime "io.containerd.runsc.v1" binary not installed "containerd-shim-runsc-v1": file does not exist
  ```

**原因**:
- 离线部署文档之前遗漏了 `containerd-shim-runsc-v1` shim 程序的安装步骤

**解决**:
```bash
# 1. 从已安装 gVisor 的机器复制 shim
scp /usr/local/bin/containerd-shim-runsc-v1 root@<目标机器>:/usr/local/bin/
chmod +x /usr/local/bin/containerd-shim-runsc-v1

# 2. 重启 containerd 和 kubelet
systemctl restart containerd
systemctl restart kubelet

# 3. 验证
ctr plugins ls | grep runsc
```

### 5.2 镜像导入失败

**问题**: `ctr image import` 报错

**解决**:
```bash
# 确保使用正确的 namespace
ctr -n k8s.io image import xxx.tar
```

### 5.3 业务镜像缺失（✅ 新增）

**现象**:
- Pod 状态 ErrImagePull / ImagePullBackOff
- kubelet 日志：`failed to pull and unpack image "docker.io/library/busybox:latest"`

**原因**:
- 离线环境无法访问 Docker Hub
- 沙箱请求的镜像未预先导入

**解决**:
```bash
# 1. 从有网络的机器导出镜像
docker pull docker.m.daocloud.io/library/busybox:latest
docker save docker.m.daocloud.io/library/busybox:latest -o busybox.tar

# 2. 传输到离线机器并导入
ctr -n k8s.io image import busybox.tar
ctr -n k8s.io image tag docker.m.daocloud.io/library/busybox:latest docker.io/library/busybox:latest
```

### 5.4 gVisor RuntimeClass 不生效

**问题**: Pod 使用 runc 而非 runsc

**解决**:
```bash
# 检查 RuntimeClass
kubectl get runtimeclass gvisor -o yaml

# 确保 Pod 指定了 runtimeClassName
spec:
  runtimeClassName: gvisor
```

### 5.5 Runtime Probe 失败

**现象**: `endpoint declares 'gvisor' but the sandbox kernel does not identify as gVisor`

**原因**: Gateway 地址配置缺少端口号

**解决**:
```bash
# 检查当前配置
kubectl get configmap opensandbox-config-gvisor -n open-ace -o yaml | grep address

# 错误示例：缺少端口号
# address = "192.168.1.92"

# 正确示例：必须包含端口号
# address = "192.168.1.92:30080"

# 修正配置后重启 Server
kubectl rollout restart deployment/opensandbox-gvisor -n open-ace
```

### 5.6 沙箱未使用 gVisor 运行时（✅ 新增）

**现象**:
- 沙箱 Pod 可以创建运行
- 但 Pod 的 `runtimeClassName` 为空
- 内核显示宿主机内核，不是 gVisor 内核（`4.19.0-gvisor`）

**验证方法**:
```bash
# 检查沙箱 Pod 的 runtimeClassName
kubectl get pods -n open-ace-sandboxes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.runtimeClassName}{"\n"}{end}'

# 应该输出 "gvisor"，如果为空说明配置错误

# 检查沙箱内核
kubectl exec -n open-ace-sandboxes <pod-name> -- cat /proc/version
# 应该包含 "gvisor" 字样
```

**根本原因**: Server 配置格式错误

**错误配置**:
```toml
[kubernetes]
runtime_class = "gvisor"  # ← 错误！这个字段不起作用
```

**正确配置**:
```toml
# ✅ 关键配置！runtimeClassName 必须在 [secure_runtime] 部分
[secure_runtime]
type = "gvisor"
k8s_runtime_class = "gvisor"  # ← 正确！这个字段控制 Pod 的 runtimeClassName
```

**解决步骤**:
```bash
# 1. 修正 Server 配置
kubectl edit configmap opensandbox-config-gvisor -n open-ace

# 2. 添加 [secure_runtime] 部分（如上所示）

# 3. 重启 Server 使配置生效
kubectl rollout restart deployment/opensandbox-gvisor -n open-ace

# 4. 等待 Server 重启完成
kubectl rollout status deployment/opensandbox-gvisor -n open-ace

# 5. 验证新创建的沙箱是否使用 gVisor
# 创建新的沙箱，检查 runtimeClassName 和内核版本
```

**重要说明**:
- `[secure_runtime]` 部分是 **必需的**
- `k8s_runtime_class` 字段控制 Pod 的 `runtimeClassName`
- 如果配置错误，沙箱会创建成功但使用默认 runc，**没有真正的 gVisor 隔离**

### 5.7 Calico 网络问题
```bash
# 检查 Calico 状态
kubectl get pods -n kube-system -l k8s-app=calico-node

# 查看日志
kubectl logs -n kube-system -l k8s-app=calico-node
```

### 5.6 沙箱创建超时

**现象**: `KUBERNETES::POD_READY_TIMEOUT`

**排查步骤**:
```bash
# 检查 Controller 是否运行
kubectl get pods -n opensandbox-system

# 检查 Controller 日志
kubectl logs deploy/opensandbox-controller -n opensandbox-system

# 检查 BatchSandbox CR
kubectl get batchsandboxes -n open-ace-sandboxes
kubectl describe batchsandbox <name> -n open-ace-sandboxes

# 检查 Pod 状态
kubectl get pods -n open-ace-sandboxes
```

**常见原因**:
- Controller 未运行或 RBAC 权限不足
- CRD 未安装（SandboxSnapshot、Pool）
- 镜像拉取失败

### 5.5 Runtime Probe 失败

**现象**: `endpoint declares 'gvisor' but the sandbox kernel does not identify as gVisor`

**原因**: Gateway 地址配置缺少端口号

**解决**:
```bash
# 检查当前配置
kubectl get configmap opensandbox-config-gvisor -n open-ace -o yaml

# 确保 gateway.address 包含端口
[ingress.gateway]
address = "192.168.1.92:30080"  # 必须包含端口号
```

### 5.6 CRD 缺失

**现象**: `no matches for kind "SandboxSnapshot" in version "sandbox.opensandbox.io/v1alpha1"`

**解决**:
```bash
# 安装缺失的 CRD
kubectl apply -f - <<EOF
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

# 同样安装 Pool CRD
```

### 5.7 Controller 镜像拉取失败

**现象**: `ImagePullBackOff` 或 `403 Forbidden`

**原因**: 镜像源访问受限

**解决**:
```bash
# 使用国内镜像源
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/controller:latest

# 更新 Deployment 镜像
kubectl set image deploy/opensandbox-controller \
  controller=docker.m.daocloud.io/opensandbox/controller:latest \
  -n opensandbox-system
```

---

## 附录

### A. 离线包导出流程

在**有网络访问**的机器上执行：

```bash
# 1. 克隆 OpenACE 仓库
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 2. 执行导出脚本
./scripts/export-gvisor-resources.sh /datastore/open-ace/gVisor/packages

# 3. 构建 WebUI 沙箱镜像
docker build -f scripts/docker/webui-sandbox.Dockerfile \
  -t open-ace-webui-sandbox:latest .

docker save open-ace-webui-sandbox:latest | gzip > \
  /datastore/open-ace/gVisor/packages/images/open-ace-webui-sandbox-latest.tar.gz

# 4. 下载 Calico YAML（如果脚本未成功）
wget -O /datastore/open-ace/gVisor/packages/configs/calico.yaml \
  https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml

# 5. 验证离线包完整性
ls -lh /datastore/open-ace/gVisor/packages/
```

### B. 端口清单

| 服务 | 端口 | 说明 |
|------|------|------|
| OpenACE | 19888 | Web 界面 |
| OpenSandbox API | 8080 | 生命周期管理 |
| OpenSandbox Gateway | 30080 | Ingress 代理 |
| Kubernetes API | 6443 | API Server |

### C. 参考链接

- gVisor 官方文档: https://gvisor.dev/docs/
- Kubernetes 离线安装: https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/
- Calico 安装: https://docs.tigera.io/calico/latest/getting-started/
- OpenSandbox GitHub: https://github.com/opensandbox-group/OpenSandbox

---

**文档维护**: OpenACE Team
**更新日期**: 2026-09-22
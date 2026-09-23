# OpenACE gVisor 沙箱功能离线部署指南

**文档版本**: 2.0 (离线版)
**日期**: 2026-09-22
**验证环境**: 192.168.1.92
**资源位置**: 192.168.1.21:/datastore/open-ace/gVisor/

---

## 目录

1. [离线资源清单](#1-离线资源清单)
2. [环境要求](#2-环境要求)
3. [离线部署步骤](#3-离线部署步骤)
4. [配置说明](#4-配置说明)
5. [验证测试](#5-验证测试)
6. [常见问题](#6-常见问题)

---

## 1. 离线资源清单

### 1.1 目录结构

```
/datastore/open-ace/gVisor/
├── docs/                              # 部署文档
│   ├── gvisor-sandbox-deployment-guide.md
│   └── gvisor-sandbox-resources.md
└── packages/                          # 离线资源包 (~1.4GB)
    ├── binaries/                      # 二进制文件
    │   └── runsc (105MB)             # gVisor 运行时
    ├── cni-plugins/                   # CNI 插件
    │   └── bin/ (190MB)              # Calico, Flannel 等插件
    ├── configs/                       # 配置文件
    │   ├── local-storage.yaml
    │   └── opensandbox-controller.yaml
    ├── images/                        # OpenSandbox 镜像 (249MB)
    │   ├── opensandbox-controller-latest.tar (98MB)
    │   ├── opensandbox-server-v0.2.3.tar (62MB)
    │   ├── opensandbox-execd-v1.1.0.tar (58MB)
    │   └── opensandbox-ingress-latest.tar (33MB)
    ├── k8s-images/                    # Kubernetes 镜像 (850MB)
    │   ├── kube-apiserver_v1.31.14.tar (28MB)
    │   ├── kube-controller-manager_v1.31.14.tar (26MB)
    │   ├── kube-scheduler_v1.31.14.tar (20MB)
    │   ├── kube-proxy_v1.31.14.tar (30MB)
    │   ├── coredns_v1.11.1.tar (18MB)
    │   ├── etcd_3.5.15-0.tar (55MB)
    │   ├── pause_3.10.tar (321KB)
    │   ├── calico_cni_v3.28.0.tar (91MB)
    │   ├── calico_node_v3.28.0.tar (110MB)
    │   └── calico_kube-controllers_v3.28.0.tar (34MB)
    └── scripts/                       # 部署脚本
        ├── export-gvisor-resources.sh
        └── import-gvisor-resources.sh
```

### 1.2 镜像清单

**Kubernetes 核心镜像 (v1.31.14):**
| 镜像 | 大小 | 说明 |
|------|------|------|
| kube-apiserver | 28MB | API 服务器 |
| kube-controller-manager | 26MB | 控制器管理器 |
| kube-scheduler | 20MB | 调度器 |
| kube-proxy | 30MB | 网络代理 |
| coredns | 18MB | DNS 服务 |
| etcd | 55MB | 键值存储 |
| pause | 321KB | 沙箱基础设施容器 |

**Calico CNI 镜像 (v3.28.0):**
| 镜像 | 大小 | 说明 |
|------|------|------|
| calico/cni | 91MB | CNI 插件 |
| calico/node | 110MB | 节点代理 |
| calico/kube-controllers | 34MB | 控制器 |

**OpenSandbox 镜像:**
| 镜像 | 大小 | 说明 |
|------|------|------|
| opensandbox/controller | 98MB | CRD 控制器 |
| opensandbox/server | 62MB | 生命周期 API |
| opensandbox/execd | 58MB | 容器执行代理 |
| opensandbox/ingress | 33MB | Gateway 代理 |

### 1.3 需要额外准备的资源

以下资源需要在**有网络的环境**下载：

**RPM 包 (Rocky Linux 9 / RHEL 9):**
```bash
# 下载命令（在有网络的机器上执行）
mkdir -p rpms
cd rpms
dnf download --resolve kubeadm-1.31.0 kubectl-1.31.0 kubelet-1.31.0 kubernetes-cni containerd.io
```

或从官方镜像下载：
- Kubernetes RPMs: https://pkgs.k8s.io/
- containerd RPMs: https://github.com/containerd/containerd/releases

---

## 2. 环境要求

### 2.1 硬件要求

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核+ |
| 内存 | 8 GB | 16 GB+ |
| 磁盘 | 50 GB | 100 GB+ |

### 2.2 操作系统

- Rocky Linux 9.x / RHEL 9.x
- 其他兼容系统：CentOS Stream 9, AlmaLinux 9

### 2.3 网络要求

- 单节点或集群节点间网络互通
- OpenACE 端口: 19888
- OpenSandbox API: 8080
- OpenSandbox Gateway: 30080

---

## 3. 离线部署步骤

### 3.1 准备离线资源

```bash
# 从 192.168.1.21 复制资源到目标机器
scp -r root@192.168.1.21:/datastore/open-ace/gVisor /datastore/open-ace/

# 设置变量
export GVISOR_DIR=/datastore/open-ace/gVisor
```

### 3.2 安装系统依赖

**如果已有 RPM 包:**
```bash
cd $GVISOR_DIR/packages/rpms
dnf localinstall -y *.rpm
```

**如果没有 RPM 包，手动安装:**
```bash
# 安装 containerd
dnf install -y containerd.io

# 添加 Kubernetes 仓库
cat <<EOF > /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.31/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.31/rpm/repodata/repomd.xml.key
EOF

# 安装 Kubernetes
dnf install -y kubeadm-1.31.0 kubectl-1.31.0 kubelet-1.31.0
```

### 3.3 导入镜像

```bash
# 导入 Kubernetes 核心镜像
cd $GVISOR_DIR/packages/k8s-images
for tar in *.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import $tar
done

# 导入 OpenSandbox 镜像
cd $GVISOR_DIR/packages/images
for tar in *.tar; do
    echo "导入 $tar..."
    ctr -n k8s.io image import $tar
done

# 验证镜像
ctr -n k8s.io images ls | grep -E "k8s|opensandbox|calico"
```

### 3.4 安装 gVisor

```bash
# 复制 runsc 二进制
cp $GVISOR_DIR/packages/binaries/runsc /usr/local/bin/
chmod +x /usr/local/bin/runsc

# 验证安装
runsc --version
# 输出: runsc version release-20260914.0

# 配置 containerd 使用 gVisor
mkdir -p /etc/containerd
cat <<EOF >> /etc/containerd/config.toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"
EOF

# 重启 containerd
systemctl restart containerd
```

### 3.5 安装 CNI 插件

```bash
# 复制 CNI 插件
mkdir -p /opt/cni
cp -r $GVISOR_DIR/packages/cni-plugins/bin /opt/cni/

# 验证
ls /opt/cni/bin/ | head -5
```

### 3.6 初始化 Kubernetes 集群

```bash
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

### 3.7 安装 Calico CNI

```bash
# 下载 Calico YAML（或从离线包获取）
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml

# 或使用已导入的镜像
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: calico-node
  namespace: kube-system
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: calico-kube-controllers
  namespace: kube-system
---
# ... (完整 Calico 配置，参考官方文档)
EOF

# 验证
kubectl get pods -n kube-system | grep calico
```

### 3.8 创建 gVisor RuntimeClass

```bash
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
```

### 3.9 安装 OpenSandbox CRD

```bash
# 安装 CRD
kubectl apply -f - <<EOF
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
```

### 3.10 部署 OpenSandbox Controller

```bash
# 创建命名空间和 RBAC
kubectl create namespace opensandbox-system

kubectl apply -f $GVISOR_DIR/packages/configs/opensandbox-controller.yaml

# 验证
kubectl get pods -n opensandbox-system
```

### 3.11 部署 OpenSandbox Server

参考完整部署文档：`docs/gvisor-sandbox-deployment-guide.md`

### 3.12 部署 OpenACE

参考完整部署文档：`docs/gvisor-sandbox-deployment-guide.md`

---

## 4. 配置说明

### 4.1 关键配置

**OpenSandbox Server 配置:**
```toml
[ingress.gateway]
address = "<SERVER_IP>:30080"  # 必须包含端口号
route.mode = "header"

[kubernetes]
workload_provider = "batchsandbox"  # 必须使用 batchsandbox
```

**OpenACE 配置:**
```json
{
  "endpoints": {
    "gvisor": {
      "base_url": "http://<SERVER_IP>:8080/v1",
      "runtime_class": "gvisor"
    }
  }
}
```

### 4.2 用户 PVC 配置

```bash
# 创建 local-storage StorageClass
kubectl apply -f $GVISOR_DIR/packages/configs/local-storage.yaml
```

---

## 5. 验证测试

### 5.1 组件状态

```bash
# Kubernetes
kubectl get nodes
kubectl get pods -A

# gVisor
runsc --version

# OpenSandbox
kubectl get pods -n opensandbox-system
kubectl get batchsandboxes -A
```

### 5.2 gVisor 验证

```bash
# 创建测试沙箱
API_KEY=<your-api-key>
curl -X POST "http://localhost:8080/v1/sandboxes" \
  -H "OPEN-SANDBOX-API-KEY: $API_KEY" \
  -d '{"image": {"name": "docker.io/library/busybox"}, "entrypoint": ["cat", "/proc/version"]}'

# 输出应包含: Linux version 4.19.0-gvisor
```

---

## 6. 常见问题

### 6.1 镜像导入失败

**问题**: `ctr: image import failed`

**解决**:
```bash
# 检查镜像名称
ctr -n k8s.io images ls | grep <image-name>

# 重新打标签
ctr -n k8s.io images tag <source> <target>
```

### 6.2 gVisor 未识别

**问题**: `endpoint declares 'gvisor' but kernel does not identify`

**解决**: 确保网关地址包含端口号

### 6.3 用户工作区路径问题

**问题**: `No existing ancestor directory found`

**状态**: Issue #3420 待解决

**临时方案**: 用户手动导航到 `/workspace/{user}` 目录

---

## 附录

### A. 快速部署命令汇总

```bash
# 1. 导入镜像
for tar in /datastore/open-ace/gVisor/packages/k8s-images/*.tar; do
    ctr -n k8s.io image import $tar
done
for tar in /datastore/open-ace/gVisor/packages/images/*.tar; do
    ctr -n k8s.io image import $tar
done

# 2. 安装 runsc
cp /datastore/open-ace/gVisor/packages/binaries/runsc /usr/local/bin/

# 3. 安装 CNI
cp -r /datastore/open-ace/gVisor/packages/cni-plugins/bin /opt/cni/

# 4. 初始化集群
kubeadm init --image-repository k8s.m.daocloud.io

# 5. 创建 RuntimeClass
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF
```

### B. 相关 Issue

- #2023 - OpenSandbox 安全策略
- #3378 - sandboxed 隔离等级
- #3417 - 用户工作区持久化
- #3420 - 路径一致性问题

---

**文档维护者**: OpenACE Team
**最后更新**: 2026-09-22
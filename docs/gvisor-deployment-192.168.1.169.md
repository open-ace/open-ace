# OpenACE gVisor 沙箱功能部署报告 - 192.168.1.169

**日期**: 2026-09-22
**目标机器**: 192.168.1.169
**操作系统**: Rocky Linux 9.5 (RHEL系)

---

## 1. 部署进度

### 已完成步骤

| 步骤 | 状态 | 备注 |
|------|------|------|
| SSH 连接 | ✅ 完成 | root/root |
| 安装 containerd | ✅ 完成 | v1.7.24, 官方二进制安装 |
| 安装 Kubernetes | ✅ 完成 | v1.31.14, 阿里云镜像源 |
| 安装 CNI (Flannel) | ✅ 完成 | 需要加载 br_netfilter 模块 |
| 安装 gVisor (runsc) | ✅ 完成 | release-20260914.0 |
| 创建 RuntimeClass | ✅ 完成 | gvisor/runsc |
| 安装 CRD | ✅ 完成 | BatchSandbox, SandboxSnapshot, Pool |
| 部署 Controller | ✅ 完成 | Running |
| 部署 Server | ❌ 失败 | Kubernetes 配置加载问题 |
| 部署 Gateway | ✅ 完成 | Running |

---

## 2. 在线下载资源记录

### 需要离线下载的资源

| 资源 | 来源 | 用途 |
|------|------|------|
| **Kubernetes 镜像** | registry.aliyuncs.com/google_containers | 集群初始化 |
| - kube-apiserver:v1.31.14 | | |
| - kube-controller-manager:v1.31.14 | | |
| - kube-scheduler:v1.31.14 | | |
| - kube-proxy:v1.31.14 | | |
| - coredns:v1.11.3 | | |
| - pause:3.10 | | |
| - etcd:3.5.24-0 | | |
| **Flannel YAML** | https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml | CNI 网络 |
| **containerd 二进制** | https://github.com/containerd/containerd/releases/download/v1.7.24/containerd-1.7.24-linux-amd64.tar.gz | 容器运行时 |
| **runc 二进制** | https://github.com/opencontainers/runc/releases/download/v1.1.12/runc.amd64 | 容器运行时 |

### 已从资源服务器获取

| 资源 | 路径 | 大小 |
|------|------|------|
| runsc | /datastore/open-ace/gVisor/packages/binaries/runsc | 109 MB |
| opensandbox-controller:latest | packages/images/ | 97.5 MB |
| opensandbox-server:v0.2.3 | packages/images/ | 61.7 MB |
| opensandbox-execd:v1.1.0 | packages/images/ | 57.5 MB |
| opensandbox-ingress:latest | packages/images/ | 32.0 MB |

---

## 3. Rocky Linux 9.5 特殊配置

### 3.1 内核模块

```bash
# 加载 br_netfilter (Flannel 需要)
modprobe br_netfilter
echo 'br_netfilter' >> /etc/modules-load.d/k8s.conf

# sysctl 配置
cat <<EOF > /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sysctl --system
```

### 3.2 SELinux

```bash
setenforce 0
sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config
```

### 3.3 swap

```bash
swapoff -a
sed -i '/swap/d' /etc/fstab
```

---

## 4. Kubernetes 集群信息

```bash
# 节点状态
NAME      STATUS   ROLES           AGE   VERSION
node169   Ready    control-plane   41m   v1.31.14

# Pod 状态
opensandbox-system/opensandbox-controller-795fb5b9f5-z7wx9   1/1 Running
open-ace/opensandbox-gateway-7574bdc9b6-4f2x9               1/1 Running
open-ace/opensandbox-server-*                                Error
```

---

## 5. 当前问题

### OpenSandbox Server 启动失败

**错误信息**:
```
HTTPException: 503: {'code': 'KUBERNETES::INITIALIZATION_ERROR', 
'message': 'Failed to initialize Kubernetes client: Failed to load Kubernetes configuration: Invalid kube-config file. No configuration found.'}
```

**尝试的解决方案**:
1. ✅ 添加 ServiceAccount 和 ClusterRoleBinding
2. ✅ 设置 automountServiceAccountToken: true
3. ✅ 添加 KUBERNETES_SERVICE_HOST/PORT 环境变量
4. ✅ 挂载 kubeconfig 文件

**可能的原因**:
1. OpenSandbox Server v0.2.3 对 Kubernetes 配置加载有特定要求
2. 需要查看 OpenSandbox 源代码了解正确的初始化方式
3. 可能需要其他环境变量或配置参数

**待检查**:
- OpenSandbox Server 文档
- `opensandbox_server/services/k8s/kubernetes_service.py` 源代码
- 是否需要其他配置文件

---

## 6. 文档问题记录

### docs/gvisor-sandbox-deployment-guide.md 需要补充的内容

1. **操作系统差异**: 文档基于 Ubuntu/Debian，需要补充 Rocky Linux/RHEL 的步骤
   - 使用 `dnf` 代替 `apt`
   - containerd 需要手动安装 (官方二进制)
   - 内核模块加载方式不同

2. **内核模块**: Flannel 需要 br_netfilter 模块，文档未提及

3. **镜像源**: 建议提供国内镜像源加速配置

4. **OpenSandbox Server 部署**: 文档中 Server 部署部分不完整
   - 缺少完整的 Deployment YAML
   - 缺少 RBAC 配置详情
   - 缺少环境变量说明

5. **在线资源**: 需要补充离线部署清单

---

## 7. 下一步行动

1. 检查 OpenSandbox Server 源代码或官方文档
2. 确认 Server 正确的 Kubernetes 配置加载方式
3. 完善离线部署文档
4. 测试完整部署流程

---

## 附录: 已部署的 Kubernetes 资源

### 命名空间
- opensandbox-system
- open-ace
- open-ace-sandboxes
- sandbox-runner
- kube-flannel

### CRD
- batchsandboxes.sandbox.opensandbox.io
- sandboxsnapshots.sandbox.opensandbox.io
- pools.sandbox.opensandbox.io

### Secrets
- opensandbox-keys (open-ace)
- kubeconfig (open-ace)

### ConfigMaps
- opensandbox-config-gvisor (open-ace)

### ServiceAccounts
- opensandbox-controller (opensandbox-system)
- sandbox-runner (open-ace-sandboxes)
- opensandbox-server (open-ace)

### ClusterRoles/ClusterRoleBindings
- opensandbox-controller
- opensandbox-server
- flannel

### RuntimeClass
- gvisor (handler: runsc)
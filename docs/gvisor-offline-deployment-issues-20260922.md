# gVisor 沙箱离线部署问题记录

**日期**: 2026-09-22
**目标服务器**: 192.168.1.169 (Rocky Linux 9.5)
**资源服务器**: 192.168.1.21:/datastore/open-ace/gVisor/

---

## 问题清单

### 问题 #1: 资源服务器缺少 Kubernetes 离线安装资源

**发现时间**: 部署步骤 3.1 - Kubernetes 集群准备

**描述**:
资源服务器 `192.168.1.21:/datastore/open-ace/gVisor/packages/` 只包含 OpenSandbox 相关资源，缺少 Kubernetes 离线安装所需的资源。

**缺失资源**:

| 资源类型 | 具体内容 | 状态 |
|---------|---------|------|
| RPM 包 | kubeadm, kubelet, kubectl | ❌ 缺失 |
| Kubernetes 镜像 | kube-apiserver, kube-controller-manager, kube-scheduler, kube-proxy, pause, etcd, coredns | ❌ 缺失 |
| CNI 插件镜像 | flannel, install-cni | ❌ 缺失 |
| CNI 二进制 | bridge, flannel, host-local, loopback, portmap | ❌ 缺失 |

**当前已有资源**:

| 资源 | 大小 | 状态 |
|-----|------|------|
| opensandbox-controller-latest.tar | 102 MB | ✅ 已有 |
| opensandbox-server-v0.2.3.tar | 65 MB | ✅ 已有 |
| opensandbox-execd-v1.1.0.tar | 60 MB | ✅ 已有 |
| opensandbox-ingress-latest.tar | 34 MB | ✅ 已有 |
| runsc (gVisor 二进制) | 109 MB | ✅ 已有 |
| local-storage.yaml | - | ✅ 已有 |
| opensandbox-controller.yaml | - | ✅ 已有 |

**影响**: 无法完成 Kubernetes 集群的离线初始化。

**建议方案**:
1. 在有网络的环境中下载 Kubernetes RPM 包和镜像
2. 导出镜像为 tar 文件
3. 将资源上传到资源服务器

**操作步骤**（在联网机器上执行）:
```bash
# 下载 Kubernetes RPM 包
mkdir -p /datastore/open-ace/gVisor/packages/rpms
dnf download kubelet kubeadm kubectl --resolve --destdir=/datastore/open-ace/gVisor/packages/rpms

# 下载 Kubernetes 镜像
mkdir -p /datastore/open-ace/gVisor/packages/k8s-images
for img in kube-apiserver kube-controller-manager kube-scheduler kube-proxy pause etcd coredns; do
    ctr -n k8s.io image pull registry.k8s.io/$img:v1.31.0
    ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/k8s-images/$img.tar registry.k8s.io/$img:v1.31.0
done

# 下载 Flannel 镜像
ctr -n k8s.io image pull docker.io/flannel/flannel:v0.26.1
ctr -n k8s.io image export /datastore/open-ace/gVisor/packages/k8s-images/flannel.tar docker.io/flannel/flannel:v0.26.1
```

---

### 问题 #2: 文档假设 Ubuntu/Debian 系统

**发现时间**: 部署步骤 3.1

**描述**:
部署指南 `gvisor-sandbox-deployment-guide.md` 中的命令主要针对 Ubuntu/Debian 系统（使用 `apt`），而目标服务器是 Rocky Linux 9.5（RHEL 系）。

**文档命令 vs 实际命令**:

| 操作 | 文档命令 (Ubuntu) | 实际命令 (RHEL/Rocky) |
|-----|------------------|---------------------|
| 安装 gVisor | `apt install runsc` | 需要离线安装二进制 |
| 添加仓库 | `apt-add-repository` | `dnf config-manager` |
| 重启服务 | `systemctl restart` | 相同 |

**建议**: 文档应补充 RHEL 系统的离线安装说明。

---

### 问题 #3: containerd 离线安装方式未说明

**发现时间**: 部署步骤 3.1

**描述**:
文档假设 containerd 已通过包管理器安装，未提供离线安装 containerd 的方法。

**解决方案**:
使用官方二进制安装：
```bash
# 需要准备的资源
curl -fsSL https://github.com/containerd/containerd/releases/download/v1.7.24/containerd-1.7.24-linux-amd64.tar.gz
curl -fsSL https://raw.githubusercontent.com/containerd/containerd/main/containerd.service
```

---

### 问题 #4: containerd 默认配置中缺少 ctr 命令

**发现时间**: 部署步骤 3.5

**描述**:
使用官方二进制安装 containerd 后，`ctr` 命令包含在 tar 包中。但如果只安装核心组件，可能缺少 `ctr` 工具。

**影响**: 无法导入镜像。

**解决方案**: 确保下载完整版 containerd tar 包。

---

## 离线部署所需完整资源清单

基于本次部署经验，整理离线部署所需完整资源：

### 1. 容器运行时
| 资源 | 大小 | 来源 |
|-----|------|------|
| containerd-1.7.24-linux-amd64.tar.gz | ~40 MB | https://github.com/containerd/containerd/releases |
| containerd.service | ~1 KB | https://raw.githubusercontent.com/containerd/containerd/main/ |
| runc.amd64 | ~10 MB | https://github.com/opencontainers/runc/releases |

### 2. Kubernetes
| 资源 | 大小 | 来源 |
|-----|------|------|
| kubeadm RPM | ~10 MB | Kubernetes 官方仓库 |
| kubelet RPM | ~20 MB | Kubernetes 官方仓库 |
| kubectl RPM | ~10 MB | Kubernetes 官方仓库 |
| kube-apiserver 镜像 | ~30 MB | registry.k8s.io |
| kube-controller-manager 镜像 | ~30 MB | registry.k8s.io |
| kube-scheduler 镜像 | ~20 MB | registry.k8s.io |
| kube-proxy 镜像 | ~20 MB | registry.k8s.io |
| pause 镜像 | ~1 MB | registry.k8s.io |
| etcd 镜像 | ~50 MB | registry.k8s.io |
| coredns 镜像 | ~15 MB | registry.k8s.io |

### 3. CNI 网络
| 资源 | 大小 | 来源 |
|-----|------|------|
| flannel 镜像 | ~70 MB | docker.io/flannel/flannel |
| CNI 插件二进制 | ~30 MB | https://github.com/containernetworking/plugins/releases |

### 4. gVisor
| 资源 | 大小 | 来源 |
|-----|------|------|
| runsc 二进制 | ~105 MB | https://gvisor.dev |

### 5. OpenSandbox
| 资源 | 大小 | 来源 |
|-----|------|------|
| controller:latest | ~102 MB | ✅ 已有 |
| server:v0.2.3 | ~65 MB | ✅ 已有 |
| execd:v1.1.0 | ~60 MB | ✅ 已有 |
| ingress:latest | ~34 MB | ✅ 已有 |

**总计**: 约 800 MB

---

## 后续行动

1. [ ] 在联网机器上准备 Kubernetes 离线资源
2. [ ] 上传到资源服务器
3. [ ] 更新部署指南补充离线安装说明
4. [ ] 测试完整的离线部署流程
# gVisor 沙箱部署问题记录

**部署环境**: 192.168.1.169 (Rocky Linux 9.5)
**部署日期**: 2026-09-22
**参考文档**: docs/gvisor-sandbox-deployment-guide.md

---

## 1. 操作系统差异问题

### 问题描述
文档中的安装命令使用 Ubuntu/Debian 的 `apt` 包管理器，但目标系统是 Rocky Linux 9.5 (RHEL 系)，需要使用 `dnf`。

### 文档内容 (第 3.2 节)
```bash
# 添加 gVisor 仓库
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list

# 安装 runsc
sudo apt update
sudo apt install runsc
```

### 实际操作
- Rocky Linux 使用 `dnf` 而非 `apt`
- gVisor 官方没有提供 RHEL 系的仓库
- **解决方案**: 从资源服务器复制预下载的 runsc 二进制文件

### 建议
文档应增加 RHEL/Rocky Linux 系统的安装说明，或提供离线安装方法。

---

## 2. containerd 安装问题

### 问题描述
文档未说明 containerd 的安装方式，但 Rocky Linux 9.5 默认仓库中没有 containerd。

### 实际操作
1. Docker CE 仓库连接失败 (SSL 错误)
2. 使用 containerd 官方二进制安装：
   ```bash
   curl -fsSL https://github.com/containerd/containerd/releases/download/v1.7.24/containerd-1.7.24-linux-amd64.tar.gz
   tar -xzf containerd-1.7.24-linux-amd64.tar.gz -C /usr/local
   ```

3. 配置 systemd service 和 SystemdCgroup

### 建议
文档应补充 containerd 的详细安装步骤，包括：
- 下载二进制包
- 创建 systemd service
- 配置 SystemdCgroup

---

## 3. Kubernetes 镜像拉取超时

### 问题描述
文档中使用 `registry.k8s.io` 拉取镜像，但国内网络连接缓慢或超时。

### 文档内容 (第 3.1 节)
```bash
sudo kubeadm init --pod-network-cidr=10.244.0.0/16
```

### 实际操作
- 使用阿里云镜像源：`registry.aliyuncs.com/google_containers`
- 先预拉取镜像，再初始化集群：
  ```bash
  kubeadm config images pull --image-repository registry.aliyuncs.com/google_containers --kubernetes-version v1.31.14
  kubeadm init --image-repository registry.aliyuncs.com/google_containers ...
  ```

### 建议
文档应增加国内镜像源配置说明，或提供离线镜像导入方法。

---

## 4. Flannel CNI 安装问题

### 问题描述
文档中 Flannel 安装失败，缺少必要的前置条件。

### 文档内容 (第 3.1 节)
```bash
kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml
```

### 实际操作
1. **网络下载超时**: 需要预先下载 YAML 文件或使用镜像
2. **内核模块缺失**: Flannel 需要 `br_netfilter` 模块
   ```bash
   modprobe br_netfilter
   echo 'br_netfilter' >> /etc/modules-load.d/k8s.conf
   
   cat > /etc/sysctl.d/k8s.conf << EOF
   net.bridge.bridge-nf-call-iptables = 1
   net.bridge.bridge-nf-call-ip6tables = 1
   net.ipv4.ip_forward = 1
   EOF
   sysctl --system
   ```

### 建议
文档应增加以下内容：
1. br_netfilter 内核模块加载
2. sysctl 配置
3. 离线部署方法

---

## 5. OpenSandbox Server 配置格式错误

### 问题描述
文档中的 `sandbox.toml` 配置格式与 OpenSandbox Server v0.2.3 期望的格式不匹配。

### 文档内容 (第 3.6 节)
```toml
[runtime]
class = "gvisor"
```

### 实际操作
配置格式需要修改为：
```toml
[server]
listen = "0.0.0.0:8080"
api_key = "your-api-key"  # 必需字段

[runtime]
type = "kubernetes"  # 必需，值为 'docker' 或 'kubernetes'

[kubernetes]
namespace = "open-ace-sandboxes"
workload_provider = "batchsandbox"
sandbox_create_timeout_seconds = 90
runtime_class = "gvisor"  # gVisor RuntimeClass 名称
```

### 关键错误
1. `[runtime]` 需要设置 `type = "kubernetes"`，而不是 `class = "gvisor"`
2. `runtime_class` 应在 `[kubernetes]` 部分，而不是 `[runtime]` 部分
3. `[server]` 部分需要 `api_key` 字段（或设置 `OPENSANDBOX_INSECURE_SERVER=YES`）
4. `route.mode` 应为 `route_mode`（下划线而非点）

### 建议
文档需要完全重写 OpenSandbox Server 配置部分，提供正确的配置格式。

---

## 6. OpenSandbox Server RBAC 权限缺失

### 问题描述
文档中的 Server Deployment 缺少 ServiceAccount 和 RBAC 配置，导致 Server 无法访问 Kubernetes API。

### 错误信息
```
Failed to initialize Kubernetes client: Failed to load Kubernetes configuration: Invalid kube-config file. No configuration found.
```

### 实际操作
需要创建：
1. ServiceAccount
2. ClusterRole (包含 BatchSandbox、Pod、PVC、Secret 等资源的权限)
3. ClusterRoleBinding
4. kubeconfig Secret (使用 ServiceAccount token)

### 建议
文档应补充完整的 RBAC 配置 YAML。

---

## 7. OpenSandbox Server 配置文件路径问题

### 问题描述
文档中配置文件挂载路径与 Server 实际读取路径不一致。

### 文档内容
- 挂载路径: `/config/sandbox.toml`
- Server 读取: `/etc/opensandbox/config.toml`

### 实际操作
需要将配置文件挂载到正确路径，或通过环境变量指定路径。

---

## 8. Gateway 地址端口问题

### 问题描述
文档中的 Gateway 地址格式可能有问题。

### 文档内容
```toml
[ingress.gateway]
address = "192.168.1.92:30080"
route.mode = "header"
```

### 实际操作
- `route.mode` 应改为 `route_mode`
- 需要确认 Gateway 服务是否正确配置

---

## 9. 离线部署资源缺失

### 问题描述
文档未提供完整的离线部署资源清单和导入方法。

### 实际操作
需要以下离线资源：
1. **容器镜像** (已准备好):
   - opensandbox/controller:latest
   - opensandbox/server:v0.2.3
   - opensandbox/execd:v1.1.0
   - opensandbox/ingress:latest
   - postgres:15
   - flannel 镜像

2. **二进制文件**:
   - runsc
   - containerd (可选)
   - runc

3. **配置文件**:
   - kube-flannel.yml
   - OpenSandbox CRD YAML
   - OpenSandbox Server 正确的配置模板

### 建议
文档应增加独立的离线部署章节，包含：
1. 资源清单
2. 导入脚本
3. 验证方法

---

## 10. OpenACE 部署说明缺失

### 问题描述
文档中 OpenACE 部署部分较为简略，缺少详细步骤。

### 文档内容 (第 3.7 节)
只提供了基本的克隆代码和配置命令，缺少：
- PostgreSQL 部署方式
- 镜像构建方法
- Kubernetes 部署 YAML
- 完整的环境变量配置

### 实际情况
1. **没有预构建镜像**: Docker Hub 上没有 `openace/open-ace` 镜像
2. **需要从源码构建**: 需要执行 `docker build` 或 `docker compose build`
3. **Kubernetes 部署**: 需要创建额外的 Kubernetes YAML 配置

### 建议
补充完整的 OpenACE 部署流程，包括：
1. 数据库部署
2. 配置文件详解
3. 镜像构建或获取方式
4. Kubernetes Deployment YAML
5. 服务暴露方式

---

## 11. sandbox-backends.json 配置验证

### 问题描述
文档中的 `sandbox-backends.json` 配置格式需要验证是否与当前 OpenACE 代码匹配。

### 文档内容 (第 3.7 节)
```json
{
  "installation_id": "openace-node-gvisor",
  "default_tier": "gvisor",
  "image_allowlist": [...],
  "endpoints": {
    "gvisor": {
      "base_url": "http://192.168.1.92:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      ...
    }
  }
}
```

### 需要验证
1. 配置文件路径是否正确 (`/etc/openace/sandbox-backends.json`)
2. 环境变量名称是否与代码匹配
3. 是否需要其他配置文件
4. WebUI 沙箱镜像的构建方式

---

## 部署状态总结 (2026-09-22)

### 已完成组件
| 组件 | 状态 | 版本/说明 | 验证方式 |
|------|------|----------|---------|
| containerd | ✅ 完成 | v1.7.24 | `containerd --version` |
| runc | ✅ 完成 | v1.1.12 | `runc --version` |
| Kubernetes | ✅ 完成 | v1.31.14 | `kubectl get nodes` |
| Flannel CNI | ✅ 完成 | VXLAN 模式 | `kubectl get pods -n kube-flannel` |
| gVisor runsc | ✅ 完成 | release-20260914.0 | `runsc --version` |
| gVisor RuntimeClass | ✅ 完成 | handler: runsc | `kubectl get runtimeclass gvisor` |
| OpenSandbox CRD | ✅ 完成 | 3 CRDs | `kubectl get crd | grep sandbox` |
| OpenSandbox Controller | ✅ 完成 | Running | `kubectl get pods -n opensandbox-system` |
| OpenSandbox Server | ✅ 完成 | Running | `curl http://localhost:30080/v1/sandboxes` |
| OpenSandbox Gateway | ✅ 完成 | Running | NodePort 30090 |
| PostgreSQL | ✅ 完成 | Running | `postgres.open-ace.svc:5432` |
| StorageClass | ✅ 完成 | local-storage | `kubectl get sc` |
| PV/PVC | ✅ 完成 | 10Gi | `kubectl get pv,pvc -n open-ace` |

### 待完成组件
| 组件 | 状态 | 说明 |
|------|------|------|
| OpenACE 镜像 | ❌ 构建失败 | 网络超时，GitHub CLI/NodeSource 下载失败 |
| OpenACE 部署 | ⏳ 待部署 | 需要先解决镜像构建问题 |
| WebUI 沙箱镜像 | ⏳ 待构建 | `open-ace-webui-sandbox` 镜像 |
| sandbox-backends.json | ⏳ 待配置 | OpenSandbox 后端配置 |

### 后续步骤
1. **解决镜像构建问题**：
   - 修改 Dockerfile 支持离线构建
   - 或使用代理/镜像加速
   - 或预构建镜像推送到镜像仓库

2. **完成 OpenACE 部署**：
   - 构建/获取 OpenACE 镜像
   - 创建 Kubernetes Deployment
   - 配置 sandbox-backends.json
   - 构建或获取 WebUI 沙箱镜像

### 访问信息
- **Kubernetes API**: `https://192.168.1.169:6443`
- **OpenSandbox Server**: `http://192.168.1.169:30080/v1`
- **OpenSandbox Gateway**: `http://192.168.1.169:30090`
- **PostgreSQL**: `postgres.open-ace.svc.cluster.local:5432`
  - 用户: `openace`
  - 密码: `openace123`
  - 数据库: `openace`
- **OpenSandbox API Key**: 已存储在 `opensandbox-keys` Secret

### 在线下载的资源（离线部署需要准备）
1. **Kubernetes 镜像** (阿里云镜像源):
   - registry.aliyuncs.com/google_containers/kube-apiserver:v1.31.14
   - registry.aliyuncs.com/google_containers/kube-controller-manager:v1.31.14
   - registry.aliyuncs.com/google_containers/kube-scheduler:v1.31.14
   - registry.aliyuncs.com/google_containers/kube-proxy:v1.31.14
   - registry.aliyuncs.com/google_containers/pause:3.10
   - registry.aliyuncs.com/google_containers/etcd:3.5.24-0
   - registry.aliyuncs.com/google_containers/coredns:v1.11.3

2. **Flannel 镜像**:
   - 需要从 kube-flannel.yml 中提取镜像名称

3. **PostgreSQL 镜像**:
   - docker.m.daocloud.io/library/postgres:15

---

## 总结

文档 `gvisor-sandbox-deployment-guide.md` 存在以下主要问题：

### 🔴 严重问题（必须修复）
1. **OpenSandbox Server 配置格式错误**: `[runtime]` 需要设置 `type="kubernetes"`，`runtime_class` 应在 `[kubernetes]` 部分
2. **RBAC 权限缺失**: Server 需要 ServiceAccount 和 ClusterRole 才能访问 Kubernetes API
3. **server.api_key 缺失**: Server 启动需要 `api_key` 字段

### 🟡 中等问题（建议修复）
4. **操作系统适用性不足**: 仅针对 Ubuntu/Debian，缺少 RHEL/Rocky Linux 支持
5. **前置条件缺失**: 未说明 br_netfilter、sysctl 等配置
6. **OpenACE 部署不完整**: 缺少镜像构建和 Kubernetes 部署说明
7. **离线部署支持不足**: 缺少完整的离线资源和方法

### 建议优先修复顺序
1. 修正 OpenSandbox Server 配置格式（第 5 节）
2. 补充 RBAC 配置 YAML（第 6 节）
3. 增加 RHEL/Rocky Linux 安装说明（第 1 节）
4. 完善离线部署说明（第 9 节）
5. 补充 OpenACE 部署完整流程（第 10 节）

### 正确的 OpenSandbox Server 配置示例

```toml
[server]
listen = "0.0.0.0:8080"
api_key = "your-api-key-here"  # 必需

[runtime]
type = "kubernetes"  # 必需，值为 'docker' 或 'kubernetes'

[kubernetes]
namespace = "open-ace-sandboxes"
workload_provider = "batchsandbox"
sandbox_create_timeout_seconds = 90
runtime_class = "gvisor"  # gVisor RuntimeClass 名称

[ingress]
mode = "gateway"

[ingress.gateway]
address = "192.168.1.169:30080"  # 必须包含端口号
route_mode = "header"  # 使用下划线，不是点

[pod]
service_account = "sandbox-runner"

[resources]
cpu_request = "100m"
memory_request = "128Mi"
cpu_limit = "2"
memory_limit = "4Gi"

[security]
run_as_non_root = true
read_only_root_filesystem = true
```

### 必需的 RBAC 配置

```yaml
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
  resources: ["batchsandboxes", "batchsandboxes/status", "batchsandboxes/finalizers", 
              "sandboxsnapshots", "sandboxsnapshots/status", "pools", "pools/status"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
- apiGroups: [""]
  resources: ["pods", "pods/status", "pods/exec", "secrets", "services", 
              "persistentvolumeclaims", "events"]
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
```

---

## 12. OpenACE 镜像构建问题

### 问题描述
OpenACE 镜像构建失败，Dockerfile 中包含多个需要从网络下载的组件。

### 构建命令
```bash
docker compose build --build-arg BASE_REGISTRY=docker.m.daocloud.io
```

### 失败原因
1. **GitHub CLI 下载超时** (exit code: 28):
   - URL: `https://github.com/cli/cli/releases/download/v2.42.1/gh_2.42.1_linux_amd64.deb`
   - 错误: 连接超时

2. **其他网络依赖**:
   - NodeSource 仓库 (nodejs)
   - code-server 安装脚本
   - npm 包下载 (qwen-code-webui, qwen-code)

### Dockerfile 中的网络依赖 (第 96-137 行)
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ... \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g qwen-code-webui@0.2.43 @qwen-code/qwen-code@0.23.3 \
    && curl -fsSL --connect-timeout 15 --max-time 300 https://code-server.dev/install.sh | sh -s -- \
    && (curl -fsSL --connect-timeout 30 --max-time 60 -o /tmp/gh.deb https://github.com/cli/cli/releases/download/v2.42.1/gh_2.42.1_linux_amd64.deb ...)
```

### 建议
1. **离线构建方案**: 预先下载所有依赖，修改 Dockerfile 使用本地文件
2. **分层构建**: 将网络下载部分单独处理，支持重试
3. **镜像预构建**: 提供预构建镜像推送到镜像仓库
4. **代理支持**: 支持通过 HTTP_PROXY 环境变量使用代理

### 必需的离线资源
为了支持离线构建，需要预先准备：
1. **Debian 包**:
   - gh_2.42.1_linux_amd64.deb (GitHub CLI)
   - nodejs 22.x (从 NodeSource)

2. **npm 包**:
   - qwen-code-webui@0.2.43
   - @qwen-code/qwen-code@0.23.3

3. **安装脚本**:
   - code-server 安装脚本

4. **Python 依赖**:
   - requirements.txt 中的所有包

### 解决方案（2026-09-22）
创建简化版 Dockerfile，跳过网络下载有问题的组件（GitHub CLI、code-server、Node.js），只保留核心功能：
- 基础镜像: python:3.11-slim
- 只安装必要的系统依赖和 Python 包
- 镜像大小: 204 MB
- 构建命令: `docker build -f Dockerfile.quick -t openace/open-ace:quick .`

---

## 最终部署状态 (2026-09-22 完成)

### 部署环境
- **服务器**: 192.168.1.169
- **系统**: Rocky Linux 9.5
- **Kubernetes**: v1.31.14
- **gVisor**: release-20260914.0

### 已部署组件
| 组件 | 状态 | 版本 | 访问方式 |
|------|------|------|---------|
| containerd | ✅ | v1.7.24 | socket: /run/containerd/containerd.sock |
| Kubernetes | ✅ | v1.31.14 | API: https://192.168.1.169:6443 |
| Flannel CNI | ✅ | VXLAN | - |
| gVisor runsc | ✅ | release-20260914.0 | RuntimeClass: gvisor |
| OpenSandbox Controller | ✅ | latest | namespace: opensandbox-system |
| OpenSandbox Server | ✅ | v0.2.3 | http://192.168.1.169:30080/v1 |
| OpenSandbox Gateway | ✅ | latest | http://192.168.1.169:30090 |
| PostgreSQL | ✅ | 15 | postgres.open-ace.svc:5432 |
| StorageClass | ✅ | local-storage | 10Gi PV |
| OpenACE | ✅ | quick (简化版) | http://192.168.1.169:30888 |

### 访问信息
- **OpenACE WebUI**: http://192.168.1.169:30888
- **默认账号**: admin / admin123
- **OpenSandbox API**: http://192.168.1.169:30080/v1
- **PostgreSQL**: openace / openace123 @ postgres:5432/openace

### 待完成事项
1. **WebUI 沙箱镜像**: 需要构建 `open-ace-webui-sandbox` 镜像
2. **sandbox-backends.json**: 需要配置 OpenSandbox 后端
3. **完整功能测试**: 验证 gVisor 沙箱功能

---

**文档维护者**: 部署过程记录
**最后更新**: 2026-09-22
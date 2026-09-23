# OpenACE gVisor 沙箱隔离测试环境部署报告

**部署日期:** 2026-09-18
**测试环境:** 192.168.1.92
**部署人员:** Qwen Code

## 部署总结

✅ **部分成功** - OpenSandbox server 已部署运行，但缺少关键组件 BatchSandbox CRD

## 已完成的部署步骤

### 1. ✅ 基础设施层
- **Kubernetes 集群**: 正常运行
- **gVisor RuntimeClass**: 已部署
  - handler: `runsc`
  - 内核验证: `4.19.0-gvisor` ✅

### 2. ✅ OpenSandbox Server 部署
- **命名空间**: 
  - `open-ace` ✅
  - `open-ace-sandboxes` ✅
- **Secrets**: 已创建 ✅
  - API Key: 已生成
  - EXECD Token: 已生成
  - Secure Access Keys: 已配置（key_id: `k`）
- **镜像**: 通过国内镜像代理成功拉取 ✅
  - `opensandbox/server:v0.2.3` ✅
  - `opensandbox/execd:v1.1.0` ✅
- **存储**: 本地存储配置完成 ✅
  - StorageClass: `local-storage` ✅
  - PV: `opensandbox-gvisor-pv`, `opensandbox-kata-pv` ✅
  - PVC: 已绑定 ✅
- **部署状态**: Running ✅
  - `opensandbox-gvisor`: 1/1 Running ✅
  - `opensandbox-kata`: 1/1 Running ✅

### 3. ✅ OpenACE 配置
- **配置文件**: `/etc/openace/sandbox-backends.json` ✅
  - default_tier: `gvisor`
  - base_url: `http://10.110.97.152:8080/v1`
  - runtime_class: `gvisor`
  - attestations: 已配置所有必需项
- **环境变量**: 已生成 ✅
  - `OPENSANDBOX_API_KEY_GVISOR`
  - `OPENSANDBOX_EXECD_TOKEN_GVISOR`

### 4. ✅ 网络和安全配置
- **NetworkPolicy**: 已创建 ✅
- **RBAC**: 已配置 ✅
  - ServiceAccount: `opensandbox-server`, `opensandbox-sandbox`
  - ClusterRole/ClusterRoleBinding: 已创建

## ⚠️ 阻塞问题

### BatchSandbox CRD 和 Controller 缺失

**问题描述:**
OpenSandbox 使用 Kubernetes 自定义资源 `BatchSandbox` 来管理沙箱生命周期，但当前集群缺少：
1. **CRD**: `batchsandboxes.sandbox.opensandbox.io`
2. **Controller**: OpenSandbox controller

**影响:**
- 无法通过 OpenSandbox API 创建沙箱
- OpenACE 无法使用 `opensandbox` backend

**原因:**
- GitHub Container Registry (ghcr.io) 访问受限
- Helm 无法从 `ghcr.io/opensandbox-group/charts` 安装 chart
- GitHub raw 文件访问受限

**错误信息:**
```
Error: INSTALLATION FAILED: failed to perform "FetchReference" on source: 
GET "https://ghcr.io/v2/opensandbox-group/charts/opensandbox-controller/manifests/v0.2.3": 
response status code 403: denied
```

## 🔧 解决方案

### 方案 1: 等待网络恢复
等待 GitHub 和 ghcr.io 访问恢复后安装 OpenSandbox controller

### 方案 2: 使用代理
配置 GitHub 和 ghcr.io 的代理访问

### 方案 3: 手动安装（推荐）
从可以访问 GitHub 的机器下载 OpenSandbox controller 的以下文件，然后传输到 192.168.1.92：
1. BatchSandbox CRD 定义
2. Controller deployment manifests
3. Controller 镜像

### 方案 4: 使用其他测试方法
基于已有的 gVisor RuntimeClass，通过以下方式验证隔离功能：
1. 直接创建使用 gVisor 的 Pod
2. 测试内核、文件系统、网络隔离
3. 验证资源限制和安全配置

## 📊 验证清单

| 组件 | 状态 | 说明 |
|-----|------|------|
| gVisor RuntimeClass | ✅ | runsc 正常工作 |
| OpenSandbox Server | ✅ | 健康检查通过 |
| BatchSandbox CRD | ❌ | 缺失 - 阻塞项 |
| Controller | ❌ | 缺失 - 阻塞项 |
| OpenACE 配置 | ✅ | 配置文件已生成 |
| 沙箱创建测试 | ⏸️ | 待 CRD 安装后测试 |

## 🚀 后续步骤

### 立即可行:
1. **测试基础 gVisor 隔离功能**
   ```bash
   kubectl apply -f - <<EOF
   apiVersion: v1
   kind: Pod
   metadata:
     name: test-gvisor-isolation
   spec:
     runtimeClassName: gvisor
     containers:
     - name: test
       image: docker.m.daocloud.io/library/busybox:latest
       command: ["sh", "-c", "uname -a && sleep 3600"]
   EOF
   ```

2. **验证隔离能力**
   - 内核隔离: `uname -a` 应显示 gVisor 内核
   - 文件系统隔离: 无法访问宿主文件
   - 网络隔离: 无法访问 metadata service
   - 进程隔离: 只能看到容器内进程

### 完成 OpenSandbox 部署:
1. 从有 GitHub 访问的机器下载 OpenSandbox controller manifests
2. 传输到 192.168.1.92 并应用
3. 验证 BatchSandbox CRD 安装成功
4. 通过 OpenSandbox API 创建沙箱

### OpenACE 端到端测试:
1. 启动 OpenACE 服务（使用已生成的配置）
2. 创建 autonomous 任务使用 `opensandbox` backend
3. 验证沙箱隔离契约
4. 检查能力声明是否符合预期

## 📝 配置文件位置

- **OpenACE 配置**: `/etc/openace/sandbox-backends.json`
- **环境变量**: `/tmp/openace-env.sh`
- **OpenSandbox Service**: `http://10.110.97.152:8080/v1`

## 🔍 故障排查

如果遇到问题，检查以下日志：
```bash
# OpenSandbox server 日志
kubectl logs -n open-ace -l app.kubernetes.io/name=opensandbox

# Pod 创建日志
kubectl describe pods -n open-ace-sandboxes

# Kubernetes events
kubectl get events -n open-ace-sandboxes --sort-by='.lastTimestamp'
```

---

**状态**: ⏸️ 部分完成 - 等待 BatchSandbox CRD 安装
**阻塞项**: GitHub/ghcr.io 访问受限
**建议**: 使用代理或从其他机器传输文件

**生成时间**: 2026-09-18 11:30:00 CST
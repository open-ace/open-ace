# OpenACE gVisor 沙箱功能排查记录

**日期**: 2026-09-21
**环境**: 192.168.1.92
**测试地址**: http://192.168.1.92:19888/work
**登录账号**: qlfan/Admin123

---

## 问题现象

访问 `/work` 页面登录后，工作区创建不出来。

---

## 排查过程

### 1. 配置检查

#### 1.1 resource_defaults 配置 ✅ 已生效

**配置文件**: `/etc/openace/sandbox-backends.json`

```json
{
  "installation_id": "openace-node92-gvisor",
  "default_tier": "gvisor",
  "resource_defaults": {
    "cpu": "500m",
    "memory": "1Gi"
  }
}
```

**验证结果**:
- 配置正确加载
- `build_resource_limits(policy=None, cfg, endpoint)` 返回 `{'cpu': '500m', 'memory': '1Gi'}`
- 代码路径: `/root/app/modules/workspace/autonomous/sandbox/opensandbox/policy.py`

---

### 2. 根本原因分析

#### 2.1 沙箱创建失败原因

**OpenSandbox Server 日志**:
```
ERROR: Creation failed, cleaning up sandbox xxx: 504: 
{'code': 'KUBERNETES::POD_READY_TIMEOUT', 'message': 'Timeout waiting for sandbox to be Running with IP. Elapsed: 60.0s, Last state: Pending'}
```

**问题链**:
1. OpenSandbox Server 创建 Sandbox CRD
2. 需要 opensandbox-controller 处理 CRD 并创建 Pod
3. **opensandbox-controller 镜像拉取失败** → Pod 无法创建 → 60秒后超时

#### 2.2 opensandbox-controller 镜像问题

**原始镜像**: `docker.m.daocloud.io/opensandbox/controller:v0.2.3`
**错误**: `403 Forbidden`

**解决**: 改用 `latest` 版本
```bash
ctr -n k8s.io image pull docker.m.daocloud.io/opensandbox/controller:latest
kubectl set image deploy/opensandbox-controller controller=docker.m.daocloud.io/opensandbox/controller:latest -n opensandbox-system
```

---

### 3. Provider 配置问题

#### 3.1 agent-sandbox vs batchsandbox

**问题**: 
- OpenSandbox Server 默认使用 `agent-sandbox` provider
- 创建 `sandboxes.agents.x-k8s.io` CRD
- Controller `latest` 版本只支持 `sandbox.opensandbox.io` 组的 CRD

**解决**: 修改 OpenSandbox Server 配置使用 `batchsandbox` provider

**配置文件**: `opensandbox-config-gvisor` ConfigMap
```toml
[kubernetes]
namespace = "open-ace-sandboxes"
workload_provider = "batchsandbox"  # 从 "agent-sandbox" 改为 "batchsandbox"
```

**更新命令**:
```bash
kubectl get configmap opensandbox-config-gvisor -n open-ace -o json | jq -r ".data[\"sandbox.toml\"]" > /tmp/sandbox.toml
sed -i 's/workload_provider = "agent-sandbox"/workload_provider = "batchsandbox"/' /tmp/sandbox.toml
kubectl create configmap opensandbox-config-gvisor --from-file=sandbox.toml=/tmp/sandbox.toml -n open-ace --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deploy/opensandbox-gvisor -n open-ace
```

---

### 4. 当前遗留问题

#### 4.1 缺失的 CRD

Controller `latest` 版本需要以下 CRD，但当前环境未安装：
- `SandboxSnapshot.sandbox.opensandbox.io` ❌
- `Pool.sandbox.opensandbox.io` ❌

**Controller 日志**:
```
error: no matches for kind "SandboxSnapshot" in version "sandbox.opensandbox.io/v1alpha1"
error: no matches for kind "Pool" in version "sandbox.opensandbox.io/v1alpha1"
```

**已安装的 CRD**:
- `batchsandboxes.sandbox.opensandbox.io` ✅
- `sandboxes.agents.x-k8s.io` ✅

#### 4.2 解决方案选项

1. **安装缺失的 CRD** - 需要 OpenSandbox 完整安装包
2. **降级 controller 版本** - 找到不需要额外 CRD 的版本
3. **切回 agent-sandbox provider** - 需要兼容的 controller 版本

---

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| OpenSandbox Server | ✅ Running | 使用 batchsandbox provider |
| opensandbox-controller | ⚠️ Running | latest 版本，但缺少 CRD |
| BatchSandbox CRD | ✅ | 已创建但无法处理 |
| SandboxSnapshot CRD | ❌ 缺失 | Controller 需要 |
| Pool CRD | ❌ 缺失 | Controller 需要 |
| resource_defaults | ✅ 已生效 | 500m CPU / 1Gi 内存 |

---

## 关键文件位置

### OpenACE (192.168.1.92)
- 配置: `/etc/openace/sandbox-backends.json`
- 代码: `/root/app/`
- Policy: `/root/app/modules/workspace/autonomous/sandbox/opensandbox/policy.py`

### Kubernetes
- OpenSandbox Server: `kubectl get all -n open-ace`
- Controller: `kubectl get all -n opensandbox-system`
- CRD: `kubectl get crd | grep -E "sandbox|opensandbox"`
- BatchSandbox: `kubectl get batchsandboxes -n open-ace-sandboxes`

---

### 5. Gateway 地址配置问题 ✅ 已解决

#### 5.1 Runtime Probe 失败

**错误日志**:
```
runtime probe: endpoint declares 'gvisor' but the sandbox kernel does not identify as gVisor ('')
```

**原因**: Gateway 地址配置缺少端口号，execd 连接失败

**修复前**:
```toml
[ingress.gateway]
address = "192.168.1.92"  # 缺少端口号
```

**修复后**:
```toml
[ingress.gateway]
address = "192.168.1.92:30080"  # 添加端口号
```

**验证**: Pod 内 `/proc/version` 显示 `Linux version 4.19.0-gvisor`

---

### 6. CRD 和 RBAC 权限问题 ✅ 已解决

#### 6.1 缺失的 CRD

**错误**: `no matches for kind "SandboxSnapshot" in version "sandbox.opensandbox.io/v1alpha1"`

**解决**: 手动创建 `SandboxSnapshot` 和 `Pool` CRD

```bash
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
```

#### 6.2 RBAC 权限不足

**错误**: controller 无权限操作 `sandboxsnapshots` 资源

**解决**: 更新 ClusterRole 添加缺失权限

```yaml
# scripts/opensandbox-controller.yaml
- apiGroups: ["sandbox.opensandbox.io"]
  resources: ["sandboxsnapshots", "sandboxsnapshots/status", "pools", "pools/status"]
  verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
```

---

### 7. 用户工作区持久化问题 ⚠️ 待解决

#### 7.1 问题现象

用户登录后沙箱创建成功，但无法在工作区创建项目：

```
No existing ancestor directory found within allowed workspace
```

#### 7.2 根本原因

WebUI 创建沙箱时没有传递用户 volumes：
- `app/services/webui_sandbox.py` 创建 `SandboxSpec` 时没有设置 `volumes` 字段
- 沙箱容器中不存在用户家目录（如 `/home/qlfan`）

#### 7.3 验证解决方案

**测试 OpenSandbox volumes API**:
```bash
curl -X POST "http://localhost:8080/v1/sandboxes" \
  -H "OPEN-SANDBOX-API-KEY: $API_KEY" \
  -d '{
    "image": {"name": "docker.io/library/open-ace-webui-sandbox"},
    "volumes": [{
      "name": "user-workspace",
      "mountPath": "/home/qlfan",
      "pvc": {"claimName": "user-qlfan-workspace"}
    }]
  }'
```

**结果**: ✅ 成功
- gVisor 运行正常（4.19.0-gvisor）
- `/home/qlfan` 目录存在且可写
- PVC 自动创建并绑定到宿主机存储

#### 7.4 实现方案（待开发）

1. WebUI 创建沙箱时，根据 `user_id` 生成 PVC 名称
2. 在 `SandboxSpec.volumes` 中添加用户 PVC 挂载
3. 确保 PVC 使用正确的 StorageClass

**Issue**: #3417

---

### 8. 错误修改模板导致 Pod 启动失败 ❌ 已回滚

#### 8.1 错误操作

将 BatchSandbox 模板中的 `/home/agent` 改为 `/home/qlfan`：
```yaml
# 错误的修改
volumeMounts:
  - name: home
    mountPath: /home/qlfan  # ❌ 错误：写死了用户路径
```

**后果**: Pod 无法启动，用户反馈"你修改后pod创建后都启动不了了"

#### 8.2 正确理解

- 模板中的 `/home/agent` 是 AI agent 的家目录，**不应该修改**
- 用户家目录应该通过 **volumes API 动态挂载**，而不是硬编码在模板中

#### 8.3 回滚

已将模板恢复为原始配置。

---

## 当前状态（更新）

| 组件 | 状态 | 说明 |
|------|------|------|
| OpenSandbox Server | ✅ Running | batchsandbox provider |
| opensandbox-controller | ✅ Running | latest 版本，CRD 已安装 |
| Gateway 配置 | ✅ 已修复 | 地址包含端口号 |
| gVisor 运行时 | ✅ 验证通过 | 4.19.0-gvisor |
| 用户工作区持久化 | ❌ 待实现 | Issue #3417 |

---

## 下一步行动

1. **实现用户工作区持久化** - 修改 `webui_sandbox.py` 传递用户 volumes
2. **扩展 VolumeSpec** - 支持 PVC 类型的 volume
3. **测试完整流程** - 用户登录 → 沙箱创建 → 项目创建 → 数据持久化

---

## 常用命令

```bash
# 检查沙箱状态
kubectl get batchsandboxes -n open-ace-sandboxes
kubectl get pods -n open-ace-sandboxes

# 检查 controller 日志
kubectl logs deploy/opensandbox-controller -n opensandbox-system --tail=50

# 检查 OpenSandbox Server 日志
kubectl logs deploy/opensandbox-gvisor -n open-ace --tail=50

# 重启服务
kubectl rollout restart deploy/opensandbox-controller -n opensandbox-system
kubectl rollout restart deploy/opensandbox-gvisor -n open-ace
```
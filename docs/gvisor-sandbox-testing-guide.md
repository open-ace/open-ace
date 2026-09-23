# OpenACE gVisor 沙箱隔离测试指南

**测试环境:** 192.168.1.92 (已部署 gVisor)
**测试日期:** 2026-09-18

## 测试架构

OpenACE 有两套独立的沙箱隔离体系：

### 1. Autonomous 任务的 Sandbox Backend
- **用途:** AI 自主开发任务（Issue 驱动的代码自动生成）
- **backend 类型:** `opensandbox`（容器 + gVisor/Kata）
- **配置文件:** `/etc/openace/sandbox-backends.json`

### 2. Interactive WebUI 的隔离级别
- **用途:** 浏览器交互式工作区
- **隔离级别:** `sandboxed`（每用户独立 Pod + gVisor/Kata）
- **验证机制:** 通过 API 查询能力快照

## 快速开始

### 步骤 1: 环境检查

```bash
# 在 192.168.1.92 上执行
cd /home/qlfan/workspace/open-ace

# 检查当前环境
./scripts/check-gvisor-env.sh
```

### 步骤 2: 部署 OpenSandbox 组件

```bash
# 执行自动部署脚本
./scripts/test-gvisor-setup.sh
```

这个脚本会：
1. ✅ 检查 gVisor RuntimeClass
2. ✅ 创建必要的命名空间
3. ✅ 生成并创建 OpenSandbox 密钥
4. ✅ 部署 OpenSandbox server（gVisor tier）
5. ✅ 配置 OpenACE backend
6. ✅ 输出后续测试命令

### 步骤 3: 配置 OpenACE

部署脚本会生成配置文件，你需要：

```bash
# 复制配置文件到 OpenACE 配置目录
mkdir -p /etc/openace
cp /tmp/sandbox-backends.json /etc/openace/

# 设置环境变量（脚本会输出具体的密钥值）
export OPENSANDBOX_API_KEY_GVISOR=<生成的 API key>
export OPENSANDBOX_EXECD_TOKEN_GVISOR=<生成的 EXECD token>
```

### 步骤 4: 运行测试

#### 4.1 单元测试

```bash
# 测试 gVisor 配置解析
pytest tests/unit/test_opensandbox_config.py::test_a_gvisor_tier_cannot_attest_the_egress_sidecar -v

# 测试 gVisor 运行时探测
pytest tests/unit/test_opensandbox_provider.py::test_runtime_probe_rejects_a_gvisor_kernel_on_a_kata_endpoint -v

# 测试 gVisor 隔离能力声明
pytest tests/unit/test_opensandbox_manifests.py::test_the_gvisor_tier_configures_no_egress_sidecar -v
```

#### 4.2 集成测试

```bash
# 运行完整集成测试
python3 scripts/test-gvisor-integration.py
```

集成测试包含：
- ✅ gVisor Pod 创建和销毁
- ✅ gVisor 内核隔离验证
- ✅ 文件系统隔离验证
- ✅ 网络隔离验证
- ✅ 进程隔离验证
- ✅ 安全上下文验证

## 验证检查清单

### ✅ 基础设施层
- [ ] RuntimeClass `gvisor` 存在且 handler 为 `runsc`
- [ ] 测试 Pod 能成功运行并识别 gVisor 内核
- [ ] OpenSandbox server 健康检查通过
- [ ] NetworkPolicy 正确应用（deny-default）

### ✅ 配置层
- [ ] `sandbox-backends.json` 配置正确
- [ ] `runtime_class` 声明为 `gvisor`
- [ ] `egress_cni_default_deny` 已设置
- [ ] API key 和 execd token 已配置

### ✅ 功能层
- [ ] 沙箱能成功创建和销毁
- [ ] `/proc/version` 探测识别到 gVisor
- [ ] 文件系统隔离生效（无法访问宿主文件）
- [ ] 网络隔离生效（metadata service 不可达）
- [ ] Autonomous 任务能在沙箱内运行
- [ ] WebUI 能在独立 Pod 中启动

### ✅ 安全验证
- [ ] 无法从沙箱访问宿主机文件系统
- [ ] 无法访问其他租户的沙箱
- [ ] 无法访问 Kubernetes API server
- [ ] 无法访问 metadata service
- [ ] 容器以非 root 用户运行

## 手动部署步骤（如果自动脚本失败）

### 1. 创建命名空间

```bash
kubectl create namespace open-ace
kubectl create namespace open-ace-sandboxes
```

### 2. 创建密钥

```bash
# 生成随机密钥
API_KEY=$(openssl rand -hex 32)
EXECD_TOKEN=$(openssl rand -hex 32)
SECURE_KEY=$(openssl rand -base64 32)

kubectl create secret generic opensandbox-keys -n open-ace \
  --from-literal=gvisor-api-key=$API_KEY \
  --from-literal=gvisor-execd-token=$EXECD_TOKEN \
  --from-literal=gvisor-secure-access-keys="k1=$SECURE_KEY" \
  --from-literal=gvisor-secure-access-active-key=k1
```

### 3. 部署 OpenSandbox

```bash
cd k8s/extras/opensandbox
kubectl apply -k .

# 等待部署完成
kubectl rollout status deployment/opensandbox-gvisor -n open-ace
```

### 4. 配置 OpenACE

创建 `/etc/openace/sandbox-backends.json`：

```json
{
  "installation_id": "openace-gvisor-test",
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://opensandbox.open-ace.svc.cluster.local:8080/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "docker.m.daocloud.io/library/busybox:latest",
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true
      }
    }
  },
  "rollout": {"mode": "all"}
}
```

## 常见问题排查

| 问题 | 可能原因 | 排查步骤 |
|-----|---------|---------|
| RuntimeClass 不存在 | runsc 未安装 | 检查 `/usr/local/bin/runsc` 是否存在 |
| Pod 一直 Pending | 节点不支持 | 检查 kubelet 日志，确认 RuntimeClass 可用 |
| 内核未识别为 gVisor | probe 路径错误 | 检查 `/proc/version`、`/proc/cmdline`、`dmesg` |
| NetworkPolicy 不生效 | CNI 不支持 | 使用 Calico/Cilium，检查 `kubectl get networkpolicy` |
| 沙箱创建超时 | OpenSandbox server 未就绪 | 检查 server pod 状态和日志 |

## 测试结果记录

测试完成后，请记录：

```bash
# 保存测试输出
python3 scripts/test-gvisor-integration.py 2>&1 | tee /tmp/gvisor-test-result-$(date +%Y%m%d).log

# 保存环境信息
kubectl get all -n open-ace -o yaml > /tmp/gvisor-env-$(date +%Y%m%d).yaml
```

## 后续工作

1. **性能测试**: 对比 gVisor vs Kata 的启动时间和资源开销
2. **安全测试**: 尝试各种逃逸手段验证隔离边界
3. **压力测试**: 创建大量沙箱验证资源限制
4. **监控集成**: 接入 Prometheus 监控沙箱生命周期
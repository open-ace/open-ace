#!/bin/bash
# OpenACE gVisor 沙箱隔离测试环境部署脚本
# 基于已有的 gVisor 部署（192.168.1.92）

set -e

export KUBECONFIG=/root/.kube/config
NAMESPACE="open-ace"
TEST_NAMESPACE="open-ace-sandboxes"

echo "=== OpenACE gVisor 沙箱隔离测试环境部署 ==="
echo "日期: $(date)"
echo ""

# ── 1. 环境检查 ──
echo ">>> 步骤 1: 检查基础环境..."

# 检查 gVisor runtime
echo "检查 gVisor RuntimeClass..."
if kubectl get runtimeclass gvisor -o jsonpath='{.handler}' 2>/dev/null | grep -q "runsc"; then
    echo "✅ gVisor RuntimeClass 存在且配置正确"
else
    echo "❌ gVisor RuntimeClass 不存在或配置错误"
    echo "请先部署 gVisor runtime: https://gvisor.dev/docs/user_guide/install/"
    exit 1
fi

# 测试 gVisor Pod
echo "测试 gVisor Pod 创建..."
kubectl delete pod test-gvisor-runtime --ignore-not-found -n default
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: test-gvisor-runtime
  namespace: default
spec:
  runtimeClassName: gvisor
  containers:
  - name: test
    image: docker.m.daocloud.io/library/busybox:latest
    command: ["sh", "-c", "uname -a && sleep 10"]
EOF

sleep 5
if kubectl wait pod test-gvisor-runtime --for=condition=Ready --timeout=30s -n default 2>/dev/null; then
    OUTPUT=$(kubectl exec test-gvisor-runtime -- uname -a 2>/dev/null || echo "")
    if [[ "$OUTPUT" == *"gvisor"* ]]; then
        echo "✅ gVisor 内核验证成功: $OUTPUT"
    else
        echo "❌ 未检测到 gVisor 内核: $OUTPUT"
        kubectl delete pod test-gvisor-runtime --ignore-not-found -n default
        exit 1
    fi
else
    echo "❌ gVisor Pod 创建失败"
    kubectl logs test-gvisor-runtime -n default || true
    kubectl delete pod test-gvisor-runtime --ignore-not-found -n default
    exit 1
fi
kubectl delete pod test-gvisor-runtime --ignore-not-found -n default

echo ""

# ── 2. 创建命名空间 ──
echo ">>> 步骤 2: 创建命名空间..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace $TEST_NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
echo "✅ 命名空间创建完成"
echo ""

# ── 3. 创建密钥 ──
echo ">>> 步骤 3: 创建 OpenSandbox 密钥..."

# 生成随机密钥
API_KEY=$(openssl rand -hex 32)
EXECD_TOKEN=$(openssl rand -hex 32)
SECURE_KEY=$(openssl rand -base64 32)
SECURE_KEY_ID="k1"

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: opensandbox-keys
  namespace: $NAMESPACE
type: Opaque
stringData:
  gvisor-api-key: "$API_KEY"
  gvisor-execd-token: "$EXECD_TOKEN"
  gvisor-secure-access-keys: "${SECURE_KEY_ID}=${SECURE_KEY}"
  gvisor-secure-access-active-key: "$SECURE_KEY_ID"
EOF

echo "✅ OpenSandbox 密钥创建完成"
echo "  API Key: ${API_KEY:0:16}..."
echo "  EXECD Token: ${EXECD_TOKEN:0:16}..."
echo ""

# ── 4. 部署 OpenSandbox 组件 ──
echo ">>> 步骤 4: 部署 OpenSandbox 组件..."

cd /home/qlfan/workspace/open-ace/k8s/extras/opensandbox

# 应用所有 manifests
kubectl apply -k . 2>&1 | grep -v "unchanged" || true

# 等待部署完成
echo "等待 OpenSandbox server 启动..."
kubectl rollout status deployment/opensandbox-gvisor -n $NAMESPACE --timeout=120s

if kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=opensandbox -o jsonpath='{.items[0].status.phase}' | grep -q "Running"; then
    echo "✅ OpenSandbox server 部署成功"
else
    echo "❌ OpenSandbox server 部署失败"
    kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=opensandbox --tail=50
    exit 1
fi
echo ""

# ── 5. 验证网络策略 ──
echo ">>> 步骤 5: 验证网络策略..."
kubectl get networkpolicy -n $TEST_NAMESPACE || echo "⚠️ NetworkPolicy 不存在，将创建默认策略"
echo ""

# ── 6. 配置 OpenACE backend ──
echo ">>> 步骤 6: 配置 OpenACE sandbox-backends.json..."

# 获取 OpenSandbox Service ClusterIP
SERVICE_IP=$(kubectl get svc opensandbox -n $NAMESPACE -o jsonpath='{.spec.clusterIP}')
SERVICE_PORT="8080"

# 创建配置文件
cat > /tmp/sandbox-backends.json <<EOF
{
  "installation_id": "openace-gvisor-test-$(date +%Y%m%d)",
  "default_tier": "gvisor",
  "endpoints": {
    "gvisor": {
      "base_url": "http://${SERVICE_IP}:${SERVICE_PORT}/v1",
      "api_key_env": "OPENSANDBOX_API_KEY_GVISOR",
      "execd_token_env": "OPENSANDBOX_EXECD_TOKEN_GVISOR",
      "runtime_class": "gvisor",
      "default_image": "docker.m.daocloud.io/library/busybox:latest",
      "webui_image": "docker.m.daocloud.io/library/busybox:latest",
      "execd_endpoint_host_allowlist": ["opensandbox-gateway.open-ace.example"],
      "egress_allow_hosts": [],
      "attestations": {
        "egress_cni_default_deny": true,
        "metadata_cidr_blocked": true,
        "execd_token_required": true,
        "execd_runs_as_exec_identity": true,
        "secure_access_required": true,
        "nonroot_enforced": true,
        "readonly_rootfs": true,
        "seccomp_runtime_default": true,
        "dedicated_service_account": true,
        "pod_pids_limit": 512,
        "ephemeral_storage_enforced": true,
        "inode_quota_enforced": false
      }
    }
  },
  "rollout": {"mode": "all"},
  "image_allowlist": ["docker.m.daocloud.io/library/busybox:latest"],
  "resource_defaults": {"cpu": "500m", "memory": "512Mi", "ephemeral-storage": "1Gi"},
  "sandbox_ttl_seconds": 3600
}
EOF

echo "✅ OpenACE backend 配置已生成: /tmp/sandbox-backends.json"
echo "Service URL: http://${SERVICE_IP}:${SERVICE_PORT}/v1"
echo ""

# ── 7. 输出测试命令 ──
echo ">>> 步骤 7: 后续测试步骤..."
echo ""
echo "1. 将配置文件复制到 OpenACE 配置目录："
echo "   mkdir -p /etc/openace"
echo "   cp /tmp/sandbox-backends.json /etc/openace/sandbox-backends.json"
echo ""
echo "2. 设置环境变量："
echo "   export OPENSANDBOX_API_KEY_GVISOR=$API_KEY"
echo "   export OPENSANDBOX_EXECD_TOKEN_GVISOR=$EXECD_TOKEN"
echo ""
echo "3. 运行单元测试："
echo "   cd /home/qlfan/workspace/open-ace"
echo "   pytest tests/unit/test_opensandbox_provider.py -v -k gvisor"
echo ""
echo "4. 运行集成测试（需要 OpenACE 服务运行）："
echo "   python3 scripts/test-gvisor-integration.py"
echo ""
echo "=== 部署完成 ==="
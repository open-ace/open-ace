#!/bin/bash
# OpenACE gVisor 沙箱隔离测试环境部署脚本（远程服务器版本）

set -e

export KUBECONFIG=/root/.kube/config
SCRIPT_DIR="/root/open-ace-scripts"
NAMESPACE="open-ace"
TEST_NAMESPACE="open-ace-sandboxes"

echo "=== OpenACE gVisor 沙箱隔离测试环境部署 ==="
echo "日期: $(date)"
echo ""

# ── 1. 创建命名空间 ──
echo ">>> 步骤 1: 创建命名空间..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace $TEST_NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
echo "✅ 命名空间创建完成"
echo ""

# ── 2. 创建密钥 ──
echo ">>> 步骤 2: 创建 OpenSandbox 密钥..."

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

# ── 3. 部署 OpenSandbox 组件 ──
echo ">>> 步骤 3: 部署 OpenSandbox 组件..."

cd $SCRIPT_DIR/k8s/extras/opensandbox

# 应用所有 manifests
kubectl apply -k . 2>&1 | grep -E "created|configured" || echo "Manifests applied"

# 等待部署完成
echo "等待 OpenSandbox server 启动..."
sleep 5

kubectl rollout status deployment/opensandbox-gvisor -n $NAMESPACE --timeout=180s

if kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=opensandbox -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q "Running"; then
    echo "✅ OpenSandbox server 部署成功"
    kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=opensandbox
else
    echo "❌ OpenSandbox server 部署失败"
    kubectl describe pods -n $NAMESPACE -l app.kubernetes.io/name=opensandbox | tail -30
    kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=opensandbox --tail=50 || true
    exit 1
fi
echo ""

# ── 4. 验证网络策略 ──
echo ">>> 步骤 4: 验证网络策略..."
kubectl get networkpolicy -n $TEST_NAMESPACE || echo "⚠️ NetworkPolicy 不存在"
echo ""

# ── 5. 配置 OpenACE backend ──
echo ">>> 步骤 5: 配置 OpenACE sandbox-backends.json..."

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

# ── 6. 保存密钥信息 ──
echo ">>> 步骤 6: 保存密钥信息..."
cat > /tmp/gvisor-credentials.txt <<EOF
# OpenACE gVisor 沙箱隔离测试密钥
# 生成时间: $(date)

# 环境变量
export OPENSANDBOX_API_KEY_GVISOR=$API_KEY
export OPENSANDBOX_EXECD_TOKEN_GVISOR=$EXECD_TOKEN

# OpenSandbox Service URL
# http://${SERVICE_IP}:${SERVICE_PORT}/v1

# 配置文件位置
# /tmp/sandbox-backends.json
EOF

echo "✅ 密钥信息已保存到: /tmp/gvisor-credentials.txt"
echo ""

# ── 7. 输出后续步骤 ──
echo ">>> 步骤 7: 后续测试步骤..."
echo ""
echo "1. 查看密钥信息："
echo "   cat /tmp/gvisor-credentials.txt"
echo ""
echo "2. 运行集成测试："
echo "   cd /root/open-ace-scripts"
echo "   python3 test-gvisor-integration.py"
echo ""
echo "=== 部署完成 ==="
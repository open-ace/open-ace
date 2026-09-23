#!/bin/bash
# OpenACE gVisor 环境检查脚本

export KUBECONFIG=/root/.kube/config

echo "=== OpenACE gVisor 环境检查 ==="
echo "检查时间: $(date)"
echo ""

# 1. 检查 Kubernetes 集群
echo ">>> 1. Kubernetes 集群状态"
kubectl cluster-info
echo ""

# 2. 检查 gVisor RuntimeClass
echo ">>> 2. gVisor RuntimeClass"
kubectl get runtimeclass gvisor -o yaml 2>/dev/null && echo "✅ gVisor RuntimeClass 存在" || echo "❌ gVisor RuntimeClass 不存在"
echo ""

# 3. 检查命名空间
echo ">>> 3. 命名空间"
kubectl get namespace open-ace 2>/dev/null && echo "✅ open-ace 命名空间存在" || echo "⚠️  open-ace 命名空间不存在"
kubectl get namespace open-ace-sandboxes 2>/dev/null && echo "✅ open-ace-sandboxes 命名空间存在" || echo "⚠️  open-ace-sandboxes 命名空间不存在"
echo ""

# 4. 检查 OpenSandbox 组件
echo ">>> 4. OpenSandbox 组件"
echo "Deployments:"
kubectl get deployments -n open-ace 2>/dev/null | grep opensandbox || echo "⚠️  未找到 OpenSandbox deployments"
echo ""
echo "Pods:"
kubectl get pods -n open-ace 2>/dev/null | grep opensandbox || echo "⚠️  未找到 OpenSandbox pods"
echo ""

# 5. 检查网络策略
echo ">>> 5. 网络策略"
kubectl get networkpolicy -n open-ace-sandboxes 2>/dev/null || echo "⚠️  未找到 NetworkPolicy"
echo ""

# 6. 检查 Secrets
echo ">>> 6. OpenSandbox Secrets"
kubectl get secret opensandbox-keys -n open-ace 2>/dev/null && echo "✅ OpenSandbox secrets 存在" || echo "⚠️  OpenSandbox secrets 不存在"
echo ""

# 7. 测试 gVisor Pod
echo ">>> 7. 测试 gVisor Pod 创建"
cat <<'EOF' | kubectl apply -f - 2>/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: test-gvisor-check
spec:
  runtimeClassName: gvisor
  containers:
  - name: test
    image: docker.m.daocloud.io/library/busybox:latest
    command: ["sh", "-c", "uname -a"]
EOF

sleep 3
if kubectl get pod test-gvisor-check -o jsonpath='{.status.phase}' 2>/dev/null | grep -q "Succeeded\|Running"; then
    OUTPUT=$(kubectl logs test-gvisor-check 2>/dev/null || kubectl exec test-gvisor-check -- uname -a 2>/dev/null || echo "")
    if [[ "$OUTPUT" == *"gvisor"* ]]; then
        echo "✅ gVisor 内核验证成功: $OUTPUT"
    else
        echo "⚠️  内核输出: $OUTPUT"
    fi
else
    echo "❌ gVisor Pod 创建失败"
    kubectl describe pod test-gvisor-check 2>/dev/null | tail -20
fi

kubectl delete pod test-gvisor-check --ignore-not-found 2>/dev/null
echo ""

echo "=== 检查完成 ==="
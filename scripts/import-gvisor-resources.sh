#!/bin/bash
# OpenSandbox 镜像导入脚本
# 用途: 在目标机器上导入镜像和安装 gVisor

set -e

SOURCE_DIR="${1:-/datastore/open-ace/gVisor/packages}"

echo "=== 导入 OpenSandbox 镜像 ==="

if [ -f "$SOURCE_DIR/images/opensandbox-controller-latest.tar" ]; then
    echo "导入 controller:latest..."
    ctr -n k8s.io image import "$SOURCE_DIR/images/opensandbox-controller-latest.tar"
    echo "✅ controller:latest 导入完成"
else
    echo "❌ controller 镜像文件不存在"
fi

if [ -f "$SOURCE_DIR/images/opensandbox-server-v0.2.3.tar" ]; then
    echo "导入 server:v0.2.3..."
    ctr -n k8s.io image import "$SOURCE_DIR/images/opensandbox-server-v0.2.3.tar"
    echo "✅ server:v0.2.3 导入完成"
else
    echo "❌ server 镜像文件不存在"
fi

if [ -f "$SOURCE_DIR/images/opensandbox-execd-v1.1.0.tar" ]; then
    echo "导入 execd:v1.1.0..."
    ctr -n k8s.io image import "$SOURCE_DIR/images/opensandbox-execd-v1.1.0.tar"
    echo "✅ execd:v1.1.0 导入完成"
else
    echo "❌ execd 镜像文件不存在"
fi

if [ -f "$SOURCE_DIR/images/opensandbox-ingress-latest.tar" ]; then
    echo "导入 ingress:latest..."
    ctr -n k8s.io image import "$SOURCE_DIR/images/opensandbox-ingress-latest.tar"
    echo "✅ ingress:latest 导入完成"
else
    echo "❌ ingress 镜像文件不存在"
fi

echo "=== 安装 gVisor ==="
if [ -f "$SOURCE_DIR/binaries/runsc" ]; then
    cp "$SOURCE_DIR/binaries/runsc" /usr/local/bin/
    chmod +x /usr/local/bin/runsc
    echo "✅ runsc 安装完成: $(runsc --version 2>/dev/null | head -1 || echo 'version unknown')"
else
    echo "❌ runsc 文件不存在，需要手动安装 gVisor"
    echo "   安装命令:"
    echo "   curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg"
    echo "   echo 'deb [arch=\$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main' | sudo tee /etc/apt/sources.list.d/gvisor.list"
    echo "   sudo apt update && sudo apt install runsc"
fi

echo ""
echo "=== 验证镜像 ==="
echo "已导入的 OpenSandbox 镜像:"
ctr -n k8s.io images ls 2>/dev/null | grep opensandbox || echo "  无"

echo ""
echo "=== 下一步 ==="
echo "1. 创建 gVisor RuntimeClass:"
echo "   kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF"
echo ""
echo "2. 部署 OpenSandbox CRD 和 Controller"
echo "   参考: docs/gvisor-sandbox-deployment-guide.md"
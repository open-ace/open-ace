#!/bin/bash
# gVisor 安装脚本（离线环境）
# 用途: 从离线包安装 gVisor runsc 二进制

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${1:-$SCRIPT_DIR/../binaries}"

echo "=== 安装 gVisor (离线模式) ==="
echo "源目录: $SOURCE_DIR"
echo ""

# 检查 runsc 文件
if [ ! -f "$SOURCE_DIR/runsc" ]; then
    echo "❌ 错误: runsc 文件不存在"
    echo "   期望路径: $SOURCE_DIR/runsc"
    echo ""
    echo "请确保离线包中包含 binaries/runsc 文件"
    exit 1
fi

# 复制 runsc 到系统目录
echo "复制 runsc 到 /usr/local/bin/..."
cp "$SOURCE_DIR/runsc" /usr/local/bin/
chmod +x /usr/local/bin/runsc

echo "✅ runsc 安装完成"
echo ""

# 验证安装
echo "=== 验证安装 ==="
if runsc --version &>/dev/null; then
    echo "版本信息:"
    runsc --version
    echo ""
    echo "✅ gVisor 安装成功"
else
    echo "❌ runsc 验证失败"
    exit 1
fi

echo ""
echo "=== 下一步 ==="
echo "1. 配置 containerd 使用 gVisor runtime:"
echo "   cat >> /etc/containerd/config.toml <<'EOF'"
echo ""
echo "   [plugins.\"io.containerd.grpc.v1.cri\".containerd.runtimes.runsc]"
echo "     runtime_type = \"io.containerd.runsc.v1\""
echo "   EOF"
echo ""
echo "2. 重启 containerd:"
echo "   systemctl restart containerd"
echo ""
echo "3. 创建 RuntimeClass:"
echo "   kubectl apply -f - <<EOF"
echo "   apiVersion: node.k8s.io/v1"
echo "   kind: RuntimeClass"
echo "   metadata:"
echo "     name: gvisor"
echo "   handler: runsc"
echo "   EOF"
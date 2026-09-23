#!/bin/bash
# OpenSandbox 镜像导出脚本
# 用途: 将测试环境中的镜像导出，供离线部署使用

set -e

TARGET_DIR="${1:-/datastore/open-ace/gVisor/packages}"

echo "=== 创建目录结构 ==="
mkdir -p "$TARGET_DIR"/{images,binaries,configs,scripts}

echo "=== 导出 OpenSandbox 镜像 ==="

# Controller
if ctr -n k8s.io images ls | grep -q "opensandbox/controller:latest"; then
    echo "导出 controller:latest..."
    ctr -n k8s.io image export "$TARGET_DIR/images/opensandbox-controller-latest.tar" \
        docker.m.daocloud.io/opensandbox/controller:latest
    echo "✅ controller:latest 导出完成"
else
    echo "❌ controller:latest 镜像不存在"
fi

# Server
if ctr -n k8s.io images ls | grep -q "opensandbox/server:v0.2.3"; then
    echo "导出 server:v0.2.3..."
    ctr -n k8s.io image export "$TARGET_DIR/images/opensandbox-server-v0.2.3.tar" \
        docker.m.daocloud.io/opensandbox/server:v0.2.3
    echo "✅ server:v0.2.3 导出完成"
else
    echo "❌ server:v0.2.3 镜像不存在"
fi

# Execd
if ctr -n k8s.io images ls | grep -q "opensandbox/execd:v1.1.0"; then
    echo "导出 execd:v1.1.0..."
    ctr -n k8s.io image export "$TARGET_DIR/images/opensandbox-execd-v1.1.0.tar" \
        docker.m.daocloud.io/opensandbox/execd:v1.1.0
    echo "✅ execd:v1.1.0 导出完成"
else
    echo "❌ execd:v1.1.0 镜像不存在"
fi

# Ingress
if ctr -n k8s.io images ls | grep -q "opensandbox/ingress:latest"; then
    echo "导出 ingress:latest..."
    ctr -n k8s.io image export "$TARGET_DIR/images/opensandbox-ingress-latest.tar" \
        docker.m.daocloud.io/opensandbox/ingress:latest
    echo "✅ ingress:latest 导出完成"
else
    echo "❌ ingress:latest 镜像不存在"
fi

echo "=== 复制 gVisor 二进制 ==="
if [ -f /usr/local/bin/runsc ]; then
    cp /usr/local/bin/runsc "$TARGET_DIR/binaries/"
    chmod +x "$TARGET_DIR/binaries/runsc"
    echo "✅ runsc 复制完成"
else
    echo "❌ runsc 不存在于 /usr/local/bin/"
fi

echo ""
echo "=== 导出 Kubernetes 核心镜像 ==="
mkdir -p "$TARGET_DIR/k8s-images"

K8S_IMAGES=(
    "k8s.m.daocloud.io/kube-apiserver:v1.31.0"
    "k8s.m.daocloud.io/kube-controller-manager:v1.31.0"
    "k8s.m.daocloud.io/kube-scheduler:v1.31.0"
    "k8s.m.daocloud.io/kube-proxy:v1.31.0"
    "k8s.m.daocloud.io/coredns:v1.11.1"
    "k8s.m.daocloud.io/etcd:3.5.15-0"
    "k8s.m.daocloud.io/pause:3.10"
)

for img in "${K8S_IMAGES[@]}"; do
    name=$(echo "$img" | awk -F'/' '{print $NF}' | tr ':' '-')
    echo "导出 $img..."
    if ctr -n k8s.io images ls | grep -q "$img"; then
        ctr -n k8s.io image export "$TARGET_DIR/k8s-images/${name}.tar" "$img"
        echo "✅ $img 导出完成"
    else
        echo "⚠️ $img 不存在，跳过"
    fi
done

echo ""
echo "=== 导出 Calico 镜像 ==="
CALICO_IMAGES=(
    "docker.m.daocloud.io/calico/cni:v3.28.0"
    "docker.m.daocloud.io/calico/node:v3.28.0"
    "docker.m.daocloud.io/calico/kube-controllers:v3.28.0"
)

for img in "${CALICO_IMAGES[@]}"; do
    name=$(echo "$img" | awk -F'/' '{print $NF}' | tr ':' '-')
    echo "导出 $img..."
    if ctr -n k8s.io images ls | grep -q "$img"; then
        ctr -n k8s.io image export "$TARGET_DIR/k8s-images/calico-${name}.tar" "$img"
        echo "✅ $img 导出完成"
    else
        echo "⚠️ $img 不存在，跳过"
    fi
done

echo ""
echo "=== 下载 Calico YAML ==="
if command -v wget &>/dev/null; then
    wget -O "$TARGET_DIR/configs/calico.yaml" \
        https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml \
        2>/dev/null || echo "⚠️ Calico YAML 下载失败（网络问题）"
    echo "✅ Calico YAML 下载完成"
else
    echo "⚠️ wget 未安装，跳过 Calico YAML 下载"
    echo "   手动下载地址:"
    echo "   https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml"
fi

echo ""
echo "=== 复制配置文件 ==="
# 如果本地有配置文件，复制到 configs 目录
if [ -d "/scripts" ]; then
    cp /scripts/opensandbox-controller.yaml "$TARGET_DIR/configs/" 2>/dev/null || true
    cp /scripts/local-storage.yaml "$TARGET_DIR/configs/" 2>/dev/null || true
fi

# 复制安装脚本
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/install-gvisor.sh" ]; then
    mkdir -p "$TARGET_DIR/gvisor"
    cp "$SCRIPT_DIR/install-gvisor.sh" "$TARGET_DIR/gvisor/"
fi

echo ""
echo "=== 导出完成 ==="
echo "资源目录: $TARGET_DIR"
echo ""
echo "镜像文件:"
echo "  OpenSandbox: $(ls -1 "$TARGET_DIR/images/" 2>/dev/null | wc -l) 个文件"
echo "  Kubernetes:  $(ls -1 "$TARGET_DIR/k8s-images/" 2>/dev/null | wc -l) 个文件"
echo ""
echo "二进制文件:"
ls -lh "$TARGET_DIR/binaries/" 2>/dev/null || echo "  无"
echo ""
echo "配置文件:"
ls -lh "$TARGET_DIR/configs/" 2>/dev/null || echo "  无"
echo ""
echo "总大小: $(du -sh "$TARGET_DIR" 2>/dev/null | awk '{print $1}')"
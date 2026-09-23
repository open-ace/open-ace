#!/bin/bash
# Kubernetes 集群初始化脚本
# 用途: 初始化 Kubernetes 集群并安装网络插件

set -e

echo "=== 初始化 Kubernetes Master ==="
# 获取虚拟机 IP
NODE_IP=$(hostname -I | awk '{print $2}')
echo "Node IP: $NODE_IP"

# 初始化 Kubernetes
kubeadm init \
  --apiserver-advertise-address=$NODE_IP \
  --pod-network-cidr=10.244.0.0/16 \
  --service-cidr=10.96.0.0/12 \
  --kubernetes-version=v1.31.0

# 配置 kubectl
mkdir -p $HOME/.kube
cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
chown $(id -u):$(id -g) $HOME/.kube/config

# 去除 master 污点 (单节点部署)
kubectl taint nodes --all node-role.kubernetes.io/control-plane- || true

echo "=== 安装 Flannel CNI ==="
kubectl apply -f https://raw.githubusercontent.com/flannel-io/flannel/master/Documentation/kube-flannel.yml

echo "=== 创建 gVisor RuntimeClass ==="
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runsc
EOF

echo "=== 等待节点 Ready ==="
kubectl wait --for=condition=Ready node --all --timeout=300s

echo "=== 验证集群状态 ==="
kubectl get nodes
kubectl get pods -n kube-system

echo "=== 安装 OpenSandbox CRD ==="
kubectl apply -f /vagrant/resources/crds.yaml

echo "=== 下一步 ==="
echo "1. 安装 OpenSandbox Controller: kubectl apply -f /vagrant/resources/opensandbox-controller.yaml"
echo "2. 配置 OpenACE: 参考 /vagrant/resources/gvisor-sandbox-deployment-guide.md"
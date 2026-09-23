# OpenACE gVisor 沙箱测试环境 - Vagrant 部署

## 快速开始

```bash
# 1. 进入 vagrant 目录
cd vagrant

# 2. 启动虚拟机
vagrant up

# 3. 进入虚拟机
vagrant ssh

# 4. 初始化 Kubernetes
sudo /vagrant/scripts/init-k8s.sh

# 5. 部署 OpenSandbox
kubectl apply -f /vagrant/resources/opensandbox-controller.yaml

# 6. 部署 OpenACE
# 参考 docs/gvisor-sandbox-deployment-guide.md
```

## 访问地址

启动完成后：

- OpenACE Web UI: http://localhost:19888/work
- Kubernetes API: https://localhost:6443
- OpenSandbox Gateway: http://localhost:30080

## 系统要求

- Vagrant 2.2+
- VirtualBox 或 libvirt
- 至少 16GB 内存
- 至少 8 核 CPU
- 至少 100GB 磁盘空间

## 目录结构

```
vagrant/
├── Vagrantfile          # 虚拟机配置
├── scripts/
│   └── init-k8s.sh      # Kubernetes 初始化脚本
└── resources/
    ├── crds.yaml        # OpenSandbox CRD 定义
    └── opensandbox-controller.yaml
```

## 常用命令

```bash
# 查看虚拟机状态
vagrant status

# SSH 进入虚拟机
vagrant ssh

# 停止虚拟机
vagrant halt

# 销毁虚拟机
vagrant destroy

# 重新加载配置
vagrant reload
```

## 导入镜像

如果已有镜像文件，复制到 `resources/images/` 目录：

```bash
# 在虚拟机内导入镜像
sudo ctr -n k8s.io image import /vagrant/resources/images/opensandbox-controller-latest.tar
sudo ctr -n k8s.io image import /vagrant/resources/images/opensandbox-server-v0.2.3.tar
sudo ctr -n k8s.io image import /vagrant/resources/images/opensandbox-execd-v1.1.0.tar
sudo ctr -n k8s.io image import /vagrant/resources/images/opensandbox-ingress-latest.tar
```

## 故障排查

### 虚拟机无法启动

```bash
# 检查 VirtualBox 或 libvirt 状态
vagrant status

# 查看日志
vagrant up --debug
```

### Kubernetes 节点 NotReady

```bash
# 检查容器运行时
sudo systemctl status containerd

# 检查 kubelet
sudo systemctl status kubelet

# 检查 CNI
kubectl get pods -n kube-system
```

### gVisor 未正确安装

```bash
# 检查 runsc
runsc --version

# 检查 RuntimeClass
kubectl get runtimeclass gvisor -o yaml
```
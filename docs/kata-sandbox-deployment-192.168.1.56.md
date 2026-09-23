# Open-ACE Kata 沙箱隔离测试环境部署文档

**部署日期**: 2026-09-18
**目标机器**: 192.168.1.56 (物理机, root/ivyent)
**系统**: Rocky Linux 8.10
**内核**: 4.18.0-553.el8_10.x86_64

---

## ✅ 部署完成状态

### 服务访问地址

| 服务 | 地址 | 说明 |
|------|------|------|
| **Open-ACE WebUI** | http://192.168.1.56:19888 | AI Coding Agent 工作台 |
| **OpenSandbox API** | http://192.168.1.56:30080 | Kata 沙箱服务 |
| **Kubernetes API** | https://192.168.1.56:6443 | K8s 集群 |

### 登录信息

- **用户名**: admin
- **密码**: admin123

### 组件状态

| 组件 | 状态 | 说明 |
|------|------|------|
| Kubernetes 集群 | ✅ Running | 单节点集群，v1.31.14 |
| Kata Containers | ✅ Configured | QEMU 运行时 |
| Open-ACE 服务 | ✅ Running | 端口 19888 |
| OpenSandbox Server | ✅ Running | Kubernetes Pod |
| PostgreSQL 15 | ✅ Running | Docker 容器 |

---

## 一、架构概述

```
┌─────────────────────────────────────────────────────────────────┐
│                     192.168.1.56 (物理机)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐     ┌─────────────────────────────────┐   │
│  │   Open-ACE      │────▶│     OpenSandbox Server          │   │
│  │   :19888        │     │     :30080 (NodePort)           │   │
│  └─────────────────┘     └─────────────────────────────────┘   │
│         │                          │                            │
│         ▼                          ▼                            │
│  ┌─────────────────┐     ┌─────────────────────────────────┐   │
│  │   PostgreSQL 15 │     │     Kubernetes Cluster          │   │
│  │   :5432         │     │     ┌───────────────────────┐   │   │
│  └─────────────────┘     │     │  Kata Pod (VM)       │   │   │
│                          │     │  RuntimeClass: kata   │   │   │
│                          │     └───────────────────────┘   │   │
│                          └─────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、关键配置

### 2.1 Kata Containers 配置

**配置文件**: `/opt/kata/share/defaults/kata-containers/configuration-qemu.toml`

```bash
# virtiofsd 沙箱（解决内核 4.18 兼容性问题）
virtio_fs_extra_args = ["--thread-pool-size=1", "--announce-submounts", "--sandbox=none"]

# 内核参数（解决 ext4 只读挂载问题）
kernel_params = "cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1 rootflags=noload"
```

### 2.2 OpenSandbox 配置

**ConfigMap**: `opensandbox-config-kata`

关键配置项：
- `runtime_class`: kata-qemu
- `secure_runtime`: enabled

### 2.3 Open-ACE Sandbox Backend 配置

**配置文件**: `/opt/open-ace/config/sandbox-backends.json`

```json
{
  "installation_id": "kata-test-192.168.1.56",
  "default_tier": "kata",
  "endpoints": {
    "kata": {
      "base_url": "http://192.168.1.56:30080/v1",
      "runtime_class": "kata-qemu",
      "default_image": "docker.m.daocloud.io/library/busybox:latest"
    }
  }
}
```

---

## 三、环境变量

Open-ACE 服务启动需要以下环境变量：

```bash
DATABASE_URL="postgresql://openace:openace123@127.0.0.1:5432/openace"
SECRET_KEY="kata-test-secret-key-2026"
OPENACE_ENCRYPTION_KEY="kata-encryption-key-2026"
OPENACE_SECURITY_MODE="pilot"
OPENACE_SANDBOX_BACKENDS=/opt/open-ace/config/sandbox-backends.json
OPENSANDBOX_API_KEY_KATA=kata-test-key-2026
OPENSANDBOX_EXECD_TOKEN_KATA=kata-execd-token-2026
```

---

## 四、管理命令

### 4.1 Open-ACE 服务

```bash
# 查看状态
ps aux | grep "python3 server.py"

# 查看日志
tail -f /var/log/open-ace.log

# 重启服务
pkill -f "python3 server.py"
cd /opt/open-ace && # ... 启动命令
```

### 4.2 OpenSandbox Server

```bash
# 查看状态
kubectl get pods -n open-ace

# 查看日志
kubectl logs -n open-ace -l app.kubernetes.io/name=opensandbox

# 健康检查
curl http://192.168.1.56:30080/health
```

### 4.3 Kubernetes 集群

```bash
# 查看节点
kubectl get nodes

# 查看 RuntimeClass
kubectl get runtimeclass

# 查看 Pod
kubectl get pods -A
```

### 4.4 PostgreSQL

```bash
# 进入容器
docker exec -it postgres15 psql -U openace -d openace

# 备份数据库
docker exec postgres15 pg_dump -U openace openace > backup.sql
```

---

## 五、验证测试

### 5.1 访问 Open-ACE WebUI

1. 打开浏览器访问: http://192.168.1.56:19888
2. 登录: admin / admin123
3. 检查沙箱配置是否正确加载

### 5.2 测试 OpenSandbox API

```bash
# 健康检查
curl http://192.168.1.56:30080/health

# 查看 Sandbox CRD
kubectl get sandboxes -A
```

### 5.3 测试 Kata 隔离

```bash
# 创建测试 Pod
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: test-kata-qemu
spec:
  runtimeClassName: kata-qemu
  containers:
  - name: test
    image: docker.m.daocloud.io/library/busybox:latest
    command: ["sh", "-c", "echo 'Hello from Kata VM!' && uname -a"]
EOF

# 验证 VM 隔离
kubectl exec test-kata-qemu -- uname -a
# 应显示 Kata VM 内核版本（6.18.35）而非宿主机内核（4.18）
```

---

## 六、已知问题和解决方案

### 6.1 virtiofsd 兼容性问题

**问题**: 内核 4.18 不支持 virtiofsd 默认的沙箱模式

**解决**: 添加 `--sandbox=none` 参数

```bash
# 修改 Kata 配置
virtio_fs_extra_args = ["--thread-pool-size=1", "--announce-submounts", "--sandbox=none"]
```

### 6.2 ext4 日志恢复问题

**问题**: nvdimm rootfs 挂载失败

**解决**: 添加 `rootflags=noload` 内核参数

```bash
kernel_params = "cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1 rootflags=noload"
```

### 6.3 镜像拉取问题

**问题**: 目标机器无法访问外网，无法拉取镜像

**解决**: 使用本地镜像或国内镜像源

```bash
# Docker 镜像源
docker.m.daocloud.io/library/...

# 导入到 containerd
docker save <image> -o image.tar
ctr -n k8s.io images import image.tar
```

---

## 七、下一步

1. **配置沙箱使用**: 在 Open-ACE 中配置项目使用 OpenSandbox backend
2. **创建自主开发任务**: 测试 AI Agent 在 Kata VM 中执行
3. **性能测试**: 测试沙箱启动时间和资源占用
4. **安全验证**: 验证 VM 隔离的安全性

---

**文档更新时间**: 2026-09-18 15:10
**部署人员**: Qwen Code
**状态**: ✅ 部署完成，可进行测试
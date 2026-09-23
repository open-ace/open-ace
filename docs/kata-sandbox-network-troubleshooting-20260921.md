# Kata 沙箱网络问题排查报告

**日期**: 2026-09-21
**目标**: 启用 Kata 沙箱工作区，让登录后的工作区在 Kata/QEMU VM 中运行
**测试链路**: Open-ACE → OpenSandbox → Kata Containers (QEMU VM)
**部署目标**: 192.168.1.56 (物理机, Rocky Linux 8.10)

---

## 1. 背景

### 1.1 设计意图

根据 `docs/WORKSPACE_ISOLATION_CAPABILITIES.md`：

- `sandboxed` 等级：每用户 WebUI 进程运行于 OpenSandbox pod
- egress probe 验证沙箱网络隔离是否生效
- DNS 解析失败 → 沙箱没有工作的 DNS → Agent 无法访问 LLM proxy → 拒绝启动

### 1.2 设计文档的关键说明

**§6.1 维度表**：

| 维度 | sandboxed 首 pod probe 后 |
|------|-------------------------|
| **network_egress** | **仅 sidecar attestation tier: enforced**<br>gVisor/CNI tier: unsupported |

**§6.3 诚实声明**：

> **gVisor/CNI tier 的出口验证仅负向**（集群级 deny-default 对照：证明拒绝路径存在，不能证明放行真的生效）
> 
> **仅 sidecar attestation tier 的 `/policy` 实读才升级 enforced(T-M)**

**这意味着**：
- CNI tier（包括 Calico）的出口验证是"负向"的
- 只能证明"拒绝路径存在"，不能证明放行真的生效
- 对于需要网络访问的场景，应该使用 sidecar attestation tier

---

## 2. 已完成的工作

### 2.1 部署网关

**目标**: 解决 OpenSandbox 网关访问问题

**步骤**:
1. 创建网关应用 `/tmp/gateway_app.py`
2. 使用 Gunicorn 部署，HostNetwork 模式
3. 监听 `0.0.0.0:30090`

**结果**: ✅ 网关启动成功，返回 200

### 2.2 解决 NodePort 阻塞问题

**问题**: Kubernetes NodePort 服务创建了 iptables 规则，阻塞了端口 30090

**诊断**:
```bash
iptables -t nat -L PREROUTING -n -v | grep 30090
# KUBE-EXT-XDNJ3SJ7K4F7VNYD  all  --  *      *       0.0.0.0/0            0.0.0.0/0            /* opensandbox-gateway NodePort */ tcp dpt:30090
```

**解决**: 删除 NodePort 服务
```bash
kubectl delete service opensandbox-gateway-nodeport
kubectl delete service opensandbox-gateway
```

**结果**: ✅ iptables 规则清理，端口 30090 可访问

### 2.3 配置 OpenSandbox Gateway Mode

**目标**: 让 OpenSandbox 使用网关模式而不是直接 Pod IP

**配置** (`opensandbox-config-kata` ConfigMap):
```toml
[ingress]
mode = "gateway"
[ingress.gateway]
address = "192.168.1.56:30090"
route.mode = "header"
```

**结果**: ✅ OpenSandbox 开始返回正确的 headers

---

## 3. 遇到的问题

### 3.1 ❌ DNS 解析失败（未解决）

**现象**:
```bash
kubectl exec kata-isolation-test -- nslookup kubernetes.default.svc.cluster.local
# ;; connection timed out; no servers could be reached
```

**诊断**:
- Kata VM 内部 DNS 配置正确：`nameserver 10.96.0.10`
- 但无法访问 ClusterIP DNS 服务（10.96.0.10）

### 3.2 ❌ Kata VM 无法访问默认网关（未解决）

**现象**:
```bash
kubectl exec kata-isolation-test -- ping -c 2 169.254.1.1
# PING 169.254.1.1 (169.254.1.1): 56 data bytes
# --- 169.254.1.1 ping statistics ---
# 2 packets transmitted, 0 packets received, 100% packet loss
```

**诊断**:
- Calico 使用代理 ARP 地址 `169.254.1.1` 作为 Pod 默认网关
- Kata VM 无法 ping 通这个地址
- ARP 表显示 `169.254.1.1 lladdr ee:ee:ee:ee:ee:ee REACHABLE`（代理 ARP 响应正常）
- 但 ping 仍然失败

### 3.3 ❌ Kata VM 无任何网络连接（未解决）

**现象**:
```bash
# 无法访问 Kubernetes API
kubectl exec kata-isolation-test -- wget -O- --timeout=5 https://kubernetes.default.svc.cluster.local/api
# wget: bad address 'kubernetes.default.svc.cluster.local'

# 无法访问外部网络
# 镜像拉取失败: dial tcp 162.125.32.6:443: i/o timeout
```

---

## 4. 根本原因分析

### 4.1 Calico + Kata Containers 网络兼容性问题

**技术原理**:

1. **Calico 的网络模型**:
   - 使用 veth pair 连接宿主机和 Pod
   - 使用代理 ARP (169.254.1.1) 作为 Pod 的默认网关
   - 代理 ARP 让 Pod 认为网关在同一个 L2 网络

2. **Kata Containers 的网络模型**:
   - 使用 QEMU VM 运行容器
   - VM 有自己的网络栈
   - 通过 virtio-net 连接到宿主机的 tap 设备

3. **兼容性问题**:
   - Kata VM 的虚拟网络接口无法正确与 Calico 的 veth pair 通信
   - 代理 ARP 响应可以到达 VM（ARP 表显示 REACHABLE）
   - 但数据包无法正确路由

**这是 Calico 与 Kata Containers 的已知兼容性问题。**

### 4.2 尝试过的解决方案

#### 4.2.1 切换 Calico 从 IPIP 到 VXLAN 模式

**操作**:
```bash
kubectl patch ippool default-ipv4-ippool --type merge -p '{"spec":{"ipipMode":"Never","vxlanMode":"Always"}}'
```

**结果**: ❌ 问题仍然存在

**原因**: VXLAN 模式仍然使用代理 ARP，没有解决根本问题

#### 4.2.2 修改 Kata 网络模型为 macvtap

**操作**:
```bash
# 加载 macvtap 模块
modprobe macvtap

# 修改 Kata 配置
sed -i 's/internetworking_model = "tcfilter"/internetworking_model = "macvtap"/' \
  /opt/kata/share/defaults/kata-containers/configuration-qemu.toml

# 重启 containerd
systemctl restart containerd
```

**结果**: ❌ 问题仍然存在

**原因**: 
- QEMU 仍然使用普通的 tap 设备
- macvtap 配置似乎没有改变底层行为
- 可能需要更深层的配置或 macvtap 本身不适合这个场景

---

## 5. 设计文档的指导

根据 `docs/WORKSPACE_ISOLATION_CAPABILITIES.md`：

**设计文档明确说明了**：

1. **CNI tier 的网络出口验证是"负向"的**
   - 只能证明"拒绝路径存在"
   - **不能证明放行真的生效**
   - `network_egress` 维度保持 `unsupported`

2. **需要网络访问的场景应该使用 sidecar attestation tier**
   - 通过 egress sidecar 的 `/policy` 实读来验证
   - 这是唯一能将 `network_egress` 升级为 `enforced` 的方式

3. **当前问题属于基础设施层面**
   - 不是 Open-ACE 功能的设计缺陷
   - 是 Calico + Kata 的基础设施兼容性问题

---

## 6. 推荐的解决方案

### 方案 A：切换到 gVisor 而不是 Kata（推荐）

| 项目 | 说明 |
|------|------|
| **优点** | gVisor 与 Calico 兼容性更好；不需要修改 CNI 配置；设计文档中已有验证路径 |
| **缺点** | kernel 隔离层级比 Kata 低（用户态内核 vs QEMU VM）；需要重新配置 sandbox tier |
| **难度** | 中等 |
| **步骤** | 1. 部署 gVisor runtime<br>2. 配置 sandbox tier 为 gVisor<br>3. 测试网络连通性 |

### 方案 B：配置 sidecar attestation tier

| 项目 | 说明 |
|------|------|
| **优点** | 设计文档明确支持的路径；可以将 `network_egress` 升级为 `enforced` |
| **缺点** | 需要部署 egress sidecar；配置复杂度更高 |
| **难度** | 高 |
| **步骤** | 参考 `docs/WORKSPACE_ISOLATION_CAPABILITIES.md` §6.1 |

### 方案 C：修复 Calico + Kata 兼容性

| 项目 | 说明 |
|------|------|
| **优点** | 解决根本问题；可能让 Kata tier 完全工作 |
| **缺点** | 技术难度高；可能需要 Calico 社区或 Kata 社区支持；可能需要更换 CNI（如 Flannel）；影响现有集群网络 |
| **难度** | 很高 |
| **步骤** | 1. 深入研究 Calico + Kata 网络集成<br>2. 尝试修改 Calico 链路层配置<br>3. 或更换为 Flannel CNI |

---

## 7. 当前状态

### 7.1 已验证的组件

- ✅ 网关部署（Gunicorn + HostNetwork）
- ✅ OpenSandbox gateway mode 配置
- ✅ Kata Containers runtime 部署
- ✅ Kata pod 可以启动（使用本地镜像）
- ✅ Calico VXLAN 模式配置

### 7.2 未解决的问题

- ❌ Kata VM 内部网络连通性
- ❌ Kata VM 访问 Kubernetes DNS
- ❌ Kata VM 访问 Kubernetes API
- ❌ Kata VM 访问外部网络

### 7.3 关键诊断信息

**Kata VM 网络配置**:
```
# 网络接口
eth0: inet 10.244.133.141/32 scope global eth0

# 路由表
default via 169.254.1.1 dev eth0 
169.254.1.1 dev eth0 scope link 

# DNS 配置
nameserver 10.96.0.10

# ARP 表
169.254.1.1 dev eth0 lladdr ee:ee:ee:ee:ee:ee REACHABLE
```

**宿主机网络**:
```
# Calico VXLAN 接口
vxlan.calico: <BROADCAST,MULTICAST,UP,LOWER_UP>

# veth 接口
cali3d60b0e2d6a@if3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1450
```

**Calico 配置**:
```yaml
ipipMode: Never
vxlanMode: Always
natOutgoing: true
blockSize: 26
```

---

## 8. 后续步骤建议

### 8.1 如果选择方案 A（切换到 gVisor）

1. 检查集群是否已部署 gVisor runtime
2. 修改 Open-ACE 配置，使用 gViso sandbox tier
3. 测试 gVisor pod 的网络连通性
4. 验证 egress probe

### 8.2 如果选择方案 B（sidecar attestation tier）

1. 阅读设计文档 §6.1 中关于 sidecar tier 的说明
2. 部署 egress sidecar
3. 配置 sidecar attestation
4. 测试 `/policy` 实读验证

### 8.3 如果选择方案 C（修复 Calico + Kata 兼容性）

1. 研究 Calico 的链路层配置选项
2. 尝试禁用代理 ARP，使用其他路由方式
3. 或研究 Flannel CNI 对 Kata 的支持
4. 可能需要咨询 Calico 或 Kata 社区

---

## 9. 参考文档

- `/home/qlfan/workspace/open-ace/docs/WORKSPACE_ISOLATION_CAPABILITIES.md` - 工作区隔离能力设计文档
- `/home/qlfan/workspace/open-ace/app/modules/workspace/autonomous/sandbox/opensandbox/provider.py` - egress probe 实现
- Calico 官方文档: https://docs.tigera.io/calico/latest/networking/
- Kata Containers 官方文档: https://katacontainers.io/docs/

---

## 10. 附录：诊断命令

### 10.1 检查 Kata pod 网络连通性

```bash
# DNS 解析测试
kubectl exec <pod> -- nslookup kubernetes.default.svc.cluster.local

# Ping 网关测试
kubectl exec <pod> -- ping -c 2 169.254.1.1

# 访问 Kubernetes API
kubectl exec <pod> -- wget -O- --timeout=5 https://kubernetes.default.svc.cluster.local/api
```

### 10.2 检查 Kata VM 内部网络配置

```bash
# 网络接口
kubectl exec <pod> -- ip addr show

# 路由表
kubectl exec <pod> -- ip route show

# DNS 配置
kubectl exec <pod> -- cat /etc/resolv.conf

# ARP 表
kubectl exec <pod> -- ip neigh show
```

### 10.3 检查宿主机网络

```bash
# Calico 接口
ip -o link | grep cali

# VXLAN 接口
ip -o link | grep vxlan

# ARP 表
ip neigh show | grep -E "169.254|10.244"
```

### 10.4 检查 Calico 配置

```bash
# IPPool 配置
kubectl get ippool default-ipv4-ippool -o yaml

# Felix 配置
kubectl get felixconfiguration default -o yaml
```

### 10.5 检查 Kata 配置

```bash
# Kata 配置文件
cat /opt/kata/share/defaults/kata-containers/configuration-qemu.toml | grep internetworking_model

# QEMU 进程
ps aux | grep qemu | grep -oE "netdev [^ ]+"
```
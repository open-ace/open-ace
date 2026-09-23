# gVisor 沙箱离线部署遗漏问题记录

**日期**: 2026-09-22
**目标机器**: 192.168.1.169
**参考文档**: docs/gvisor-sandbox-offline-deployment.md
**状态**: ✅ 文档已更新，等待 169 验证部署

---

## 问题解决情况

| 问题 | 严重程度 | 文档状态 | 实际状态 |
|------|---------|---------|---------|
| 1.1 `containerd-shim-runsc-v1` 缺失 | 严重 | ✅ 已补充 | ✅ 已解决 |
| 1.2 业务镜像缺失（busybox 等） | 严重 | ✅ 已补充 | ✅ 已解决 |
| 1.3 kubelet 需要重启才能识别新 runtime | 中等 | ✅ 已补充 | ✅ 已解决 |
| 1.4 runtimeClassName 未传递 | 严重 | ✅ 已修正 | ✅ 已解决 |
| 1.5 OpenACE 主服务部署说明缺失 | 严重 | ✅ 已补充 | ✅ 已解决 |
| 1.6 数据库凭证 Secret 配置不完整 | 严重 | ✅ 已补充 | ✅ 已解决 |
| 1.7 OpenACE 部署步骤顺序错误 | 严重 | ✅ 已修正 | ⏳ 等待验证 |
| 1.8 文档缺少清理已有部署的说明 | 中等 | ✅ 已说明 | ✅ 已清理完成 |
| 1.9 Kubernetes 方式缺少离线镜像导入说明 | 严重 | ✅ 已补充 | ⏳ 等待验证 |

---

## 1. 发现的问题

### 1.1 containerd-shim-runsc-v1 缺失 [严重]

**现象**:
- 沙箱创建后 Pod 一直 Pending
- ctr 测试报错：`runtime "io.containerd.runsc.v1" binary not installed "containerd-shim-runsc-v1": file does not exist`

**原因**:
- 离线部署文档只提到安装 `runsc` 二进制
- 但 gVisor 与 containerd 集成还需要 `containerd-shim-runsc-v1` shim 程序
- shim 程序没有包含在资源服务器中

**解决方案**:
1. 从已安装 gVisor 的机器（如 192.168.1.92）复制 shim：
   ```bash
   # 在源机器上
   scp /usr/local/bin/containerd-shim-runsc-v1 root@目标机器:/usr/local/bin/
   chmod +x /usr/local/bin/containerd-shim-runsc-v1
   ```
2. 重启 containerd 和 kubelet：
   ```bash
   systemctl restart containerd
   systemctl restart kubelet
   ```

**文档需补充**:
- 离线资源包中应包含 `containerd-shim-runsc-v1`
- 或提供 shim 的下载地址

---

### 1.2 镜像缺失导致沙箱无法启动 [严重]

**现象**:
- Pod 状态 ErrImagePull / ImagePullBackOff
- kubelet 日志：`failed to pull and unpack image "docker.io/library/busybox:latest"`

**原因**:
- 离线环境无法访问 Docker Hub
- sandbox-backends.json 中配置的 `default_image` 或测试请求中的镜像未预先导入

**解决方案**:
1. 从有网络的机器导出镜像：
   ```bash
   # 在有网络的机器上
   docker save docker.m.daocloud.io/library/busybox:latest -o busybox.tar
   ```
2. 传输并导入：
   ```bash
   # 在离线机器上
   ctr -n k8s.io image import busybox.tar
   ctr -n k8s.io image tag docker.m.daocloud.io/library/busybox:latest docker.io/library/busybox:latest
   ```

**文档需补充**:
- 离线资源包中应包含 busybox 镜像或其他业务镜像
- 提供镜像导入命令

---

### 1.3 kubelet 需要重启才能识别新的 runtime [中等]

**现象**:
- 安装 `containerd-shim-runsc-v1` 并重启 containerd 后，沙箱仍然失败
- 需要重启 kubelet 才能让 gVisor runtime 正常工作

**解决方案**:
```bash
systemctl restart kubelet
```

**文档需补充**:
- 安装 gVisor 后需要重启 containerd 和 kubelet

---

### 1.4 OpenSandbox Controller 未传递 runtimeClassName [严重] ✅ 已解决

**现象**:
- Server ConfigMap 配置了 `runtime_class = "gvisor"`
- 但创建的 Pod spec 中缺少 `runtimeClassName` 字段
- 沙箱使用默认 runc 运行时，内核显示宿主机内核而非 gVisor 内核

**根本原因**:
- 配置格式错误：`runtime_class` 写在了 `[kubernetes]` 部分
- 正确位置：`[secure_runtime]` 部分的 `k8s_runtime_class` 字段

**解决方案**:
修正 Server 配置，将 runtimeClassName 设置在正确的部分：

```toml
# ✅ 正确配置
[secure_runtime]
type = "gvisor"
k8s_runtime_class = "gvisor"  # ← 这个字段控制 Pod 的 runtimeClassName

# ❌ 错误配置（不起作用）
[kubernetes]
runtime_class = "gvisor"  # ← 不要写在这里！
```

**验证方法**:
```bash
# 检查沙箱 Pod 的 runtimeClassName
kubectl get pods -n open-ace-sandboxes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.runtimeClassName}{"\n"}{end}'
# 应该输出 "gvisor"

# 检查沙箱内核
kubectl exec -n open-ace-sandboxes <pod-name> -- cat /proc/version
# 应该包含 "gvisor" 字样
```

**文档已更新**:
- v8.1 已修正 Server 配置格式
- 添加了详细的说明和验证步骤

---

### 1.5 OpenACE 主服务部署说明缺失 [严重] ✅ 已解决

**现象**:
- 按照 `gvisor-sandbox-offline-deployment.md` 文档部署后，OpenACE 无法登录
- 数据库中 `users` 表为空，没有默认 admin 用户
- `alembic_version` 表为空，数据库迁移未执行

**根本原因**:
- 文档第 3.12 节只说明了如何配置 `sandbox-backends.json`
- **文档完全没有说明 OpenACE 主服务如何部署**
- 缺失内容：
  1. OpenACE 镜像从哪里获取？
  2. OpenACE 镜像如何构建？
  3. OpenACE 是用 systemd 运行还是 Kubernetes Deployment 运行？
  4. 数据库初始化步骤（`alembic upgrade head` 和 `init_db.py`）如何执行？

**解决方案**:
v8.2 已补充 OpenACE 主服务部署说明，包含三种部署方式：

1. **Docker Compose 方式**（推荐用于测试）
   - 使用 `docker-compose.yml`
   - 包含 PostgreSQL + OpenACE
   - 适合单机快速测试

2. **Kubernetes 方式**（推荐用于生产）
   - 使用 `k8s/` 目录下的 manifests
   - 包含 PostgreSQL Deployment
   - 适合生产环境

3. **打包安装方式**（物理机部署）
   - 使用 `package.sh` 打包
   - 使用 `install.sh` 安装
   - 适合无容器环境

每种方式都包含：
- 镜像获取/构建方法
- 数据库初始化步骤
- 配置说明

**文档已更新**:
- v8.2 已补充第 3.12 节 OpenACE 主服务部署
- 添加了镜像构建、数据库初始化、部署方式选择
- 包含离线环境的具体操作步骤

---

### 1.6 数据库凭证 Secret 配置不完整 [严重] ✅ 已补充

**现象**:
- 文档 3.12.2 步骤 3 只说明创建 `database-url` 和 `secret-key`
- 但 `k8s/database.yaml` 中的 PostgreSQL 和 Redis 需要更多凭证

**根本原因**:
- 文档遗漏了 `k8s/database.yaml` 需要的 Secret 字段
- PostgreSQL 需要 `DB_USER` 和 `DB_PASSWORD`
- Redis 需要 `REDIS_PASSWORD`

**解决方案**:
在创建 Secret 时包含所有必需字段：
```bash
kubectl create secret generic open-ace-secrets -n open-ace \
  --from-literal=DB_USER="openace" \
  --from-literal=DB_PASSWORD="openace123" \
  --from-literal=REDIS_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=database-url="postgresql://openace:openace123@postgres:5432/openace" \
  --from-literal=secret-key="$(openssl rand -hex 32)"
```

**文档已更新**:
- 主部署文档 `gvisor-sandbox-offline-deployment.md` 3.12.2 步骤 3 已补充完整的 Secret 创建命令

---

### 1.7 OpenACE 部署步骤顺序错误 [严重] ✅ 已修正

**现象**:
- 文档 3.12.2 Kubernetes 方式的步骤顺序不合理
- 按照文档顺序执行会导致 PostgreSQL 部署失败

**文档现状**:
- 步骤 1：部署 PostgreSQL (`kubectl apply -f k8s/database.yaml`)
- 步骤 2：构建镜像
- 步骤 3：创建 Secret

**问题**:
- `k8s/database.yaml` 中的 PostgreSQL 和 Redis 需要 `open-ace-secrets` Secret
- 但 Secret 在步骤 3 才创建
- 导致步骤 1 执行时 PostgreSQL 和 Redis 无法启动（缺少凭证）

**解决方案**:
v9.1 已调整步骤顺序：
- 步骤 1：准备离线镜像（新增）
- 步骤 2：创建 Secret（从步骤 3 移来）
- 步骤 3：部署 PostgreSQL 和 Redis
- 步骤 4：构建并推送 OpenACE 镜像
- 步骤 5：部署 OpenACE

**文档已更新**:
- 主部署文档 `gvisor-sandbox-offline-deployment.md` 3.12.2 已修正步骤顺序

---

### 1.8 文档缺少清理已有部署的说明 [中等] ❌ 待补充

**现象**:
- 192.168.1.169 上已有之前错误部署的 OpenACE 资源
- 需要清理后才能按照文档重新部署
- 文档没有说明如何处理这种情况

**文档现状**:
- 文档 3.12.2 Kubernetes 方式假设环境是全新的
- 没有提到如何清理已有的 Deployment、Pod、Secret、PVC 等

**当前 169 上的已有资源**:
```
NAME                                   READY   STATUS    AGE
open-ace-7df9fbdb4c-2dt9s              1/1     Running   6h16m
opensandbox-gateway-7574bdc9b6-4f2x9   1/1     Running   7h57m
opensandbox-server-7d74768c94-nsbc5    1/1     Running   67m
postgres-65cf4cc77f-mms97              1/1     Running   7h5m
```

**问题**:
- 无法直接按照文档重新部署，因为资源已存在
- 需要手动清理或更新策略

**文档需补充**:
- 添加"清理已有部署"章节，或
- 在每个步骤中说明如何处理已存在的资源（如 `kubectl apply` 会更新，`kubectl create` 会失败）
- 建议在文档开头添加前提条件检查

---

### 1.9 Kubernetes 方式缺少离线镜像导入说明 [严重] ✅ 已补充

**现象**:
- 文档 3.12.2 Kubernetes 方式步骤 1 部署 PostgreSQL 失败
- 错误：`ImagePullBackOff` - 无法拉取 `postgres:15-alpine` 镜像
- 原因：192.168.1.169 是离线环境，无法访问 Docker Hub

**文档现状**:
- Docker Compose 方式（3.12.1）有详细的离线镜像导入说明：
  ```bash
  # 导入 PostgreSQL 镜像（离线环境）
  docker pull docker.m.daocloud.io/library/postgres:15
  docker save docker.m.daocloud.io/library/postgres:15 -o postgres-15.tar
  docker load -i postgres-15.tar
  ```
- Kubernetes 方式（3.12.2）**完全缺少**离线镜像导入说明

**缺失内容**:
1. PostgreSQL 镜像如何获取和导入？
2. Redis 镜像如何获取和导入？
3. OpenACE 镜像如何构建和导入？
4. 应该使用哪个镜像源（`postgres:15-alpine` vs `docker.m.daocloud.io/library/postgres:15`）？
5. 如何使用 `ctr -n k8s.io image import` 导入到 Kubernetes 集群？

**文档需补充**:
在步骤 1 之前添加"准备离线镜像"步骤：
```bash
# 在有网络的机器上
docker pull docker.m.daocloud.io/library/postgres:15-alpine
docker pull docker.m.daocloud.io/library/redis:7-alpine
docker save docker.m.daocloud.io/library/postgres:15-alpine -o postgres-15-alpine.tar
docker save docker.m.daocloud.io/library/redis:7-alpine -o redis-7-alpine.tar

# 传输到离线机器
scp postgres-15-alpine.tar redis-7-alpine.tar root@192.168.1.169:/tmp/

# 在离线机器上导入
ctr -n k8s.io image import postgres-15-alpine.tar
ctr -n k8s.io image import redis-7-alpine.tar
ctr -n k8s.io image tag docker.m.daocloud.io/library/postgres:15-alpine postgres:15-alpine
ctr -n k8s.io image tag docker.m.daocloud.io/library/redis:7-alpine redis:7-alpine
```

**文档已更新**:
- 主部署文档 `gvisor-sandbox-offline-deployment.md` 3.12.2 已添加步骤 1（准备离线镜像）
- 包含完整的镜像获取、导出、传输、导入流程

---

## 2. 离线资源包缺失清单

根据实际部署发现，离线资源包需要补充：

| 资源 | 当前状态 | 需要添加 |
|------|---------|---------|
| runsc | ✅ 已有 | - |
| containerd-shim-runsc-v1 | ❌ 缺失 | 需要添加 |
| busybox 镜像 | ❌ 缺失 | 需要添加 |
| open-ace-webui-sandbox 镜像 | ❌ 缺失 | 业务镜像，需要构建 |

---

## 3. 完整的离线部署步骤补充

### 3.1 安装 gVisor 完整组件

```bash
# 1. 复制 runsc
cp /datastore/open-ace/gVisor/packages/binaries/runsc /usr/local/bin/
chmod +x /usr/local/bin/runsc

# 2. 复制 containerd-shim-runsc-v1（新增）
cp /datastore/open-ace/gVisor/packages/binaries/containerd-shim-runsc-v1 /usr/local/bin/
chmod +x /usr/local/bin/containerd-shim-runsc-v1

# 3. 配置 containerd
# 已在 /etc/containerd/config.toml 中添加 gVisor runtime 配置

# 4. 重启 containerd 和 kubelet（新增 kubelet 重启）
systemctl restart containerd
systemctl restart kubelet
```

### 3.2 导入业务镜像

```bash
# 导入镜像到 k8s.io 命名空间
for tar in /datastore/open-ace/gVisor/packages/images/*.tar; do
    ctr -n k8s.io image import "$tar"
done

# 创建 docker.io 标签（用于 sandbox-backends.json 配置）
ctr -n k8s.io image tag docker.m.daocloud.io/library/busybox:latest docker.io/library/busybox:latest
```

---

## 4. 验证测试

```bash
# 验证 gVisor shim
ctr plugins ls | grep runsc

# 测试 gVisor 运行容器
ctr -n k8s.io run --rm --runtime=io.containerd.runsc.v1 docker.io/library/busybox:latest test-gvisor cat /proc/version
# 输出应包含 "gvisor" 字样

# 测试创建沙箱
API_KEY=$(kubectl get secret opensandbox-keys -n open-ace -o jsonpath='{.data.gvisor-api-key}' | base64 -d)
curl -X POST "http://localhost:30080/v1/sandboxes" \
  -H "OPEN-SANDBOX-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"image": {"uri": "docker.io/library/busybox:latest"}, "entrypoint": ["sleep", "300"], "resourceLimits": {"cpu": "500m", "memory": "128Mi"}}'
```

---

## 5. 建议更新文档

`docs/gvisor-sandbox-offline-deployment.md` 需要补充：

1. **3.3 安装 gVisor 章节**：补充 `containerd-shim-runsc-v1` 安装步骤
2. **离线资源清单**：添加 shim 程序和业务镜像
3. **验证步骤**：添加 shim 验证和 gVisor 内核验证
4. **重启服务**：明确需要重启 kubelet

---

---

### 1.10 离线资源包缺少 PostgreSQL 和 Redis 镜像 [严重] ❌ 待补充

**发现时间**: 2026-09-23
**环境**: 192.168.1.169 离线环境

**现象**:
- 按照文档 3.12.2 步骤 1 执行镜像导入失败
- 在资源服务器 192.168.1.21 的 `/datastore/open-ace/gVisor/packages/k8s-images/` 目录下找不到 PostgreSQL 和 Redis 镜像文件
- 文档要求导入 `postgres:15-alpine` 和 `redis:7-alpine`

**文档现状**:
- 文档 1.2 镜像清单中没有列出 PostgreSQL 和 Redis 镜像
- 文档 1.1 目录结构中没有 `postgres-15-alpine.tar` 和 `redis-7-alpine.tar`
- 文档 3.12.2 步骤 1 假设用户已经准备好了这些镜像，但实际上资源包中没有

**问题**:
- 文档描述了如何**获取**镜像（在有网络的机器上 docker pull + docker save），但没有提供现成的镜像文件
- 离线部署用户如果按照"复制离线包"的步骤，会发现缺少这些关键镜像
- 与文档声称的"总大小：约 1.3 GB（含文档、脚本、WebUI 镜像）"不符，实际缺失了 OpenACE 依赖的数据库镜像

**临时解决方案**:
在有网络的机器（192.168.1.92）上执行：
```bash
# 拉取镜像
docker pull docker.m.daocloud.io/library/postgres:15-alpine
docker pull docker.m.daocloud.io/library/redis:7-alpine

# 导出镜像
docker save docker.m.daocloud.io/library/postgres:15-alpine -o postgres-15-alpine.tar
docker save docker.m.daocloud.io/library/redis:7-alpine -o redis-7-alpine.tar

# 传输到 169
scp postgres-15-alpine.tar redis-7-alpine.tar root@192.168.1.169:/tmp/
```

在 169 上执行：
```bash
# 导入镜像
ctr -n k8s.io image import /tmp/postgres-15-alpine.tar
ctr -n k8s.io image import /tmp/redis-7-alpine.tar

# 创建标签
ctr -n k8s.io image tag docker.m.daocloud.io/library/postgres:15-alpine postgres:15-alpine
ctr -n k8s.io image tag docker.m.daocloud.io/library/redis:7-alpine redis:7-alpine
```

**文档需补充**:
1. 在离线资源包清单（1.1 目录结构）中添加：
   - `k8s-images/postgres-15-alpine.tar` (约 80M)
   - `k8s-images/redis-7-alpine.tar` (约 30M)
2. 在镜像清单（1.2）中添加 PostgreSQL 和 Redis 镜像
3. 或者在文档开头明确说明：OpenACE 数据库镜像需要额外准备，不在基础离线包中

**状态**: ⏳ 等待文档更新

---

**记录者**: AI 助手
**最后更新**: 2026-09-23
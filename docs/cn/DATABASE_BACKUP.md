# 数据库备份与恢复指南

本文档描述 Kubernetes 部署的 Open ACE 数据库备份和恢复策略。

> **Issue #1853**: Kubernetes 数据库备份参考实现

## 概述

Open ACE 为 Kubernetes 部署提供可选的数据库备份基础设施。备份方案包括：

- 通过 CronJob 定时执行 PostgreSQL 备份
- 使用 `pg_restore --list` 进行完整性校验
- 支持多种对象存储后端（AWS S3、MinIO 等）
- 提供恢复 Job 用于灾难恢复

## 部署方案

### 方案 A：托管 PostgreSQL（生产环境推荐）

对于有 SLA 要求的生产环境，使用托管 PostgreSQL 服务：

| 云厂商 | 服务 | 特性 |
|--------|------|------|
| AWS | RDS/Aurora | 自动备份、PITR、多可用区 |
| Google Cloud | Cloud SQL | 自动备份、PITR、高可用 |
| Azure | Database for PostgreSQL | 自动备份、PITR、异地复制 |
| 阿里云 | RDS for PostgreSQL | 自动备份、PITR |

**优势：**
- 备份责任由云厂商承担
- 支持时间点恢复（PITR）
- 典型 RPO：< 5 分钟
- 典型 RTO：< 1 小时

### 方案 B：自管备份（开发/测试环境）

对于成本敏感或开发环境，使用提供的 CronJob：

| 方面 | 设置 |
|------|------|
| 备份工具 | `pg_dump -Fc`（自定义格式） |
| 调度 | 每日 02:00 UTC |
| 存储 | S3 兼容对象存储 |
| 保留期 | 30 天（可通过存储桶策略配置） |
| RPO | 6-24 小时（取决于 CronJob 频率） |
| RTO | 取决于数据库大小和恢复演练频率 |

## 快速开始

### 1. 配置对象存储凭证

```bash
cd k8s/extras/backup/

# 复制并编辑凭证
cp secret-s3.yaml.example secret-s3.yaml
# 使用您的 S3 凭证编辑 secret-s3.yaml
```

### 2. 部署备份基础设施

```bash
# 应用所有备份清单
kubectl apply -k .

# 验证 CronJob
kubectl get cronjob -n open-ace
kubectl describe cronjob postgres-backup -n open-ace
```

### 3. 触发手动备份（测试）

```bash
# 从 CronJob 创建一次性 Job
kubectl create job --from=cronjob/postgres-backup manual-backup-$(date +%Y%m%d) -n open-ace

# 查看日志
kubectl logs -f job/manual-backup-$(date +%Y%m%d) -n open-ace
```

## 备份内容

| 组件 | 方法 | 频率 | 保留期 |
|------|------|------|--------|
| PostgreSQL 数据库 | `pg_dump -Fc` | 每日 | 30 天 |
| PostgreSQL 全局对象 | `pg_dumpall --globals-only` | 每日 | 30 天 |
| Redis（可选） | RDB 快照 | 每日 | 7 天 |
| ConfigMap/Secret（可选） | kubectl get | 每日 | 30 天 |

## 资源配置

### 默认设置

| 参数 | 值 | 说明 |
|------|-----|------|
| 调度 | `0 2 * * *` | 每日 02:00 UTC |
| CPU 请求 | 200m | |
| CPU 限制 | 500m | |
| 内存请求 | 256Mi | |
| 内存限制 | 512Mi | 大数据库需增加 |
| 超时 | 3600s | 1 小时 |

### 大数据库调整

对于 > 1GB 的数据库：

```yaml
# 编辑 cronjob.yaml
resources:
  limits:
    memory: 1Gi
activeDeadlineSeconds: 7200  # 2 小时
```

## 备份完整性校验

每次备份包含自动完整性校验：

1. **备份创建后：** `pg_restore --list db.dump` 验证文件可解析
2. **校验失败：** Job 退出码为 1，触发 CronJob 重试
3. **成功标志：** Job 退出码为 0，备份上传到对象存储

```bash
# 手动校验
pg_restore --list backup.dump > /dev/null && echo "有效" || echo "损坏"
```

## 对象存储后端

### AWS S3

```yaml
# secret-s3.yaml
apiVersion: v1
kind: Secret
metadata:
  name: backup-storage-credentials
  namespace: open-ace
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "AKIAIOSFODNN7EXAMPLE"
  AWS_SECRET_ACCESS_KEY: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "my-open-ace-backups"
  S3_ENDPOINT: ""  # AWS S3 留空
```

### MinIO

```yaml
# secret-minio.yaml
apiVersion: v1
kind: Secret
metadata:
  name: backup-storage-credentials
  namespace: open-ace
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "minioadmin"
  AWS_SECRET_ACCESS_KEY: "minioadmin"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "open-ace-backups"
  S3_ENDPOINT: "http://minio.example.com:9000"
```

## 恢复步骤

### 步骤 1：确认备份

列出对象存储中的可用备份：

```bash
aws s3 ls s3://your-bucket/open-ace/
```

### 步骤 2：下载备份

```bash
aws s3 cp s3://your-bucket/open-ace/20240115/backup.tar.gz .
tar -xzf backup.tar.gz
```

### 步骤 3：校验完整性

```bash
pg_restore --list db.dump
```

### 步骤 4：恢复数据库

```bash
# 设置恢复时间戳
export RESTORE_TIMESTAMP=20240115

# 应用恢复 Job
kubectl apply -f restore-job.yaml

# 监控进度
kubectl logs -f job/postgres-restore -n open-ace
```

### 步骤 5：验证应用

```bash
# 检查数据库连接
kubectl exec -it deployment/open-ace -n open-ace -- \
  curl -s http://localhost:19888/health

# 检查应用日志
kubectl logs -f deployment/open-ace -n open-ace
```

## 多租户注意事项

启用 `ENABLE_MULTI_TENANT` 时：

### 备份

- `pg_dump` 自动捕获所有租户 schema
- 无需特殊备份配置

### 恢复

恢复后需验证：

1. 所有租户 schema 存在
2. 租户用户权限正确
3. 运行应用层权限校验

```sql
-- 检查租户 schema
SELECT schema_name FROM information_schema.schemata
WHERE schema_name LIKE 'tenant_%';

-- 检查权限
SELECT grantee, table_schema, privilege_type
FROM information_schema.table_privileges
WHERE table_schema LIKE 'tenant_%';
```

## NetworkPolicy 兼容性

备份 Job 与现有 NetworkPolicy 兼容：

| 流量 | 状态 |
|------|------|
| PostgreSQL (5432) | ✅ 现有出站规则已允许 |
| Redis (6379) | ✅ 现有出站规则已允许 |
| 外部 HTTPS (443) | ✅ 对象存储已允许 |

**无需修改 NetworkPolicy。**

## RBAC 要求

备份 Job 使用专用 ServiceAccount：

```yaml
# 最小权限
rules:
  - apiGroups: [""]
    resources: ["configmaps", "secrets"]
    verbs: ["get", "list"]  # 可选，用于 K8s 资源备份
```

## 加密策略

| 层级 | 选项 |
|------|------|
| 传输层 | HTTPS（TLS 1.2+） |
| 存储层 | SSE-S3 或 SSE-KMS |
| 客户端（可选） | AES-256 加密 |

## 监控和告警

### 检查备份状态

```bash
# 列出最近的 Job
kubectl get jobs -n open-ace -l app.kubernetes.io/component=backup

# 检查最后一个 Job 状态
kubectl describe job -n open-ace -l app.kubernetes.io/component=backup | \
  grep -A5 "Status:"

# 查看 Job 日志
kubectl logs job/postgres-backup-$(date +%Y%m%d) -n open-ace
```

### 建议告警

配置以下告警：
- CronJob 执行失败（连续 3 次以上）
- 备份 Job 超时
- 对象存储上传失败

## 恢复演练

### 月度演练清单

1. ✅ 从对象存储下载最新备份
2. ✅ 验证备份完整性（`pg_restore --list`）
3. ✅ 恢复到测试数据库
4. ✅ 验证数据完整性（行数、schema 校验）
5. ✅ 对恢复的数据库启动应用
6. ✅ 运行冒烟测试
7. ✅ 记录实际 RTO

## 故障排查

### CronJob 未运行

```bash
# 检查 CronJob 状态
kubectl describe cronjob postgres-backup -n open-ace

# 检查是否暂停
kubectl get cronjob postgres-backup -n open-ace -o jsonpath='{.spec.suspend}'
```

### 备份 OOM 失败

```bash
# 增加内存限制
kubectl patch cronjob postgres-backup -n open-ace --type=json -p \
  '[{"op": "replace", "path": "/spec/jobTemplate/spec/template/spec/containers/0/resources/limits/memory", "value": "1Gi"}]'
```

### 网络超时

```bash
# 验证 NetworkPolicy 允许出站
kubectl get networkpolicy -n open-ace -o yaml

# 从备份 Pod 测试连接
kubectl run tmp-shell --rm -i --tty --image postgres:15-alpine -- \
  psql -h postgres -U openace -d openace -c "SELECT 1"
```

### 恢复失败

```bash
# 检查数据库连接
kubectl exec -it postgres-0 -n open-ace -- \
  psql -U openace -d openace -c "SELECT count(*) FROM pg_stat_activity WHERE datname='openace';"

# 恢复前停止应用
kubectl scale deployment open-ace --replicas=0 -n open-ace

# 恢复后启动应用
kubectl scale deployment open-ace --replicas=3 -n open-ace
```

## 相关文档

- [KUBERNETES.md](./KUBERNETES.md) - Kubernetes 部署指南
- [备份清单详情](https://github.com/open-ace/open-ace/blob/main/k8s/extras/backup/README.md) - Kubernetes 备份清单参考

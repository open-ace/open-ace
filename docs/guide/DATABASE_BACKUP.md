# Database Backup and Recovery — 数据库备份与恢复

[English](#english) | [中文](#中文)

---

## English

This document describes the database backup and recovery strategy for Open ACE, covering both Kubernetes deployments (`k8s/extras/backup/`) and Docker Compose deployments (`docker-compose.yml`).

> **Issue #1853**: Kubernetes database backup reference implementation

## Overview

Open ACE provides optional database backup infrastructure:

- Scheduled PostgreSQL backups via CronJob (Kubernetes)
- Manual `pg_dump`/`pg_restore` procedures (Docker Compose)
- Integrity verification using `pg_restore --list`
- Multiple object storage backend support (AWS S3, MinIO, etc.)
- Restore Job for disaster recovery (Kubernetes)

### Deployment options

| Option | Environment | Backup owner | Typical RPO |
|--------|-------------|--------------|-------------|
| Managed PostgreSQL (RDS/Aurora, Cloud SQL, Azure DB, Alibaba RDS) | Production with SLA | Cloud provider (automated backups, PITR) | < 5 minutes |
| Self-managed CronJob (`k8s/extras/backup/`) | Kubernetes, cost-sensitive | This repo's manifests | 6–24 hours (CronJob frequency) |
| Manual `pg_dump` on the `postgres-data` volume | Docker Compose single host | You, via this guide | Whatever you schedule |

## Kubernetes backup (self-managed)

### Read this first: the object storage Secret is NOT part of the kustomization

`k8s/extras/backup/kustomization.yaml` lists exactly these resources:

- `serviceaccount.yaml`
- `rbac.yaml`
- `backup-script-configmap.yaml`
- `cronjob.yaml`
- `restore-job.yaml`

The credentials file (`secret-s3.yaml`, created from `secret-s3.yaml.example`, or `secret-minio.yaml` from `secret-minio.yaml.example`) is **not** among them. If you follow the classic "copy the example, then `kubectl apply -k .`" recipe, the apply succeeds, the CronJob is created, and every backup job then fails at the upload step because the `backup-storage-credentials` Secret referenced by the CronJob does not exist — the failure is silent unless you check job logs.

**Apply the Secret explicitly, then the kustomization:**

```bash
cd k8s/extras/backup/

# 1. Create and edit credentials
cp secret-s3.yaml.example secret-s3.yaml
# Edit secret-s3.yaml with your S3 credentials

# 2. Apply the Secret explicitly (it is NOT included by kustomization.yaml)
kubectl apply -f secret-s3.yaml

# 3. Apply the rest of the backup manifests
kubectl apply -k .

# 4. Verify CronJob AND Secret
kubectl get cronjob -n open-ace
kubectl get secret backup-storage-credentials -n open-ace
```

### Trigger a manual backup (testing)

```bash
# Create a one-time job from the CronJob
kubectl create job --from=cronjob/postgres-backup manual-backup-$(date +%Y%m%d) -n open-ace

# Watch logs
kubectl logs -f job/manual-backup-$(date +%Y%m%d) -n open-ace
```

### CronJob parameters

Values below are what `k8s/extras/backup/cronjob.yaml` actually ships:

| Parameter | Value | Notes |
|-----------|-------|-------|
| Schedule | `0 2 * * *` | Daily at 02:00 UTC (cronjob.yaml:18) |
| activeDeadlineSeconds | 3600 | 1 hour timeout; increase to 7200 for large databases (cronjob.yaml:33) |
| CPU request / limit | 200m / 500m | (cronjob.yaml:132-137) |
| Memory request / limit | 256Mi / 512Mi | Increase to 1Gi for databases > 1GB (cronjob.yaml:132-137) |
| concurrencyPolicy | Forbid | No overlapping runs |
| backoffLimit | 2 | Retry up to 2 times on failure |
| successfulJobsHistoryLimit / failedJobsHistoryLimit | 3 / 3 | Kept for debugging |
| Images | `alpine:3.19` (init: installs AWS CLI into an emptyDir) + `postgres:15-alpine` (main) | postgres:15-alpine does not include AWS CLI |

### Backup content

| Component | Method | Frequency | Retention |
|-----------|--------|-----------|-----------|
| PostgreSQL database | `pg_dump -Fc` (custom format) | Daily | 30 days |
| PostgreSQL globals | `pg_dumpall --globals-only` | Daily | 30 days |
| Redis (optional, `BACKUP_REDIS`) | RDB snapshot | Daily | 7 days |

### Backup integrity verification

Each backup includes automatic integrity verification:

1. **After backup creation:** `pg_restore --list db.dump` validates the file is parseable
2. **If verification fails:** Job exits with code 1, triggering CronJob retry
3. **Success indicator:** Job exits with code 0, backup uploaded to object storage

```bash
# Manual verification
pg_restore --list backup.dump > /dev/null && echo "Valid" || echo "Corrupted"
```

### Object storage credentials

AWS S3 (`secret-s3.yaml`):

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
  S3_ENDPOINT: ""  # Leave empty for AWS S3
```

MinIO (`secret-minio.yaml`, from `secret-minio.yaml.example`):

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

### Recovery procedure (Kubernetes)

```bash
# Step 1: Identify the backup in your object storage
aws s3 ls s3://your-bucket/open-ace/

# Step 2: Download it
aws s3 cp s3://your-bucket/open-ace/20240115/backup.tar.gz .
tar -xzf backup.tar.gz

# Step 3: Verify integrity
pg_restore --list db.dump

# Step 4: Stop application writers, then apply the restore Job
kubectl scale deployment open-ace --replicas=0 -n open-ace
export RESTORE_TIMESTAMP=20240115
kubectl apply -f restore-job.yaml
kubectl logs -f job/postgres-restore -n open-ace

# Step 5: Restart and verify the application
kubectl scale deployment open-ace --replicas=3 -n open-ace
kubectl exec -it deployment/open-ace -n open-ace -- \
  curl -s http://localhost:19888/health
```

The backup Job is compatible with the existing NetworkPolicy (PostgreSQL 5432, Redis 6379, and external HTTPS 443 egress are already allowed); no NetworkPolicy modification is required.

### Monitoring and troubleshooting

```bash
# List recent backup jobs
kubectl get jobs -n open-ace -l app.kubernetes.io/component=backup

# Check last job status
kubectl describe job -n open-ace -l app.kubernetes.io/component=backup

# View job logs
kubectl logs job/postgres-backup-$(date +%Y%m%d) -n open-ace
```

Common failures:

| Symptom | Cause | Fix |
|---------|-------|-----|
| Job runs, dump succeeds, upload fails with missing credentials | `secret-s3.yaml` was never applied (it is not in the kustomization) | `kubectl apply -f secret-s3.yaml` |
| Job OOM-killed on large databases | 512Mi memory limit too low | Patch the CronJob memory limit to 1Gi |
| CronJob never starts | Suspended, or previous job still within the 1h deadline | Check `suspend` flag and `activeDeadlineSeconds` |

## Docker Compose backup and restore

The Compose stack runs PostgreSQL 15 (`postgres:15-alpine`) with all data on the named volume `postgres-data`, mounted at `/var/lib/postgresql/data`. Defaults come from the environment: user `${DB_USER:-ace}`, database `${DB_NAME:-ace}`. Back up with `pg_dump`; never copy the volume directory while PostgreSQL is running.

### Backup

```bash
# Custom-format dump streamed out of the container (-T disables TTY for redirection)
mkdir -p ./backups
docker compose exec -T postgres pg_dump -U ${DB_USER:-ace} -d ${DB_NAME:-ace} -Fc \
  > ./backups/ace-$(date +%Y%m%d-%H%M).dump

# Verify it is parseable
pg_restore --list ./backups/ace-$(date +%Y%m%d-%H%M).dump > /dev/null && echo "Valid"
```

Plain SQL dump (greppable, larger):

```bash
docker compose exec -T postgres pg_dump -U ${DB_USER:-ace} -d ${DB_NAME:-ace} \
  > ./backups/ace-$(date +%Y%m%d-%H%M).sql
```

### Restore

```bash
# 1. Stop the writers (app and, if enabled, scheduler)
docker compose stop app scheduler

# 2. Restore over the existing database (custom format only)
cat ./backups/ace-20260929-0200.dump \
  | docker compose exec -T postgres pg_restore -U ${DB_USER:-ace} -d ${DB_NAME:-ace} --clean --if-exists

# 3. Restart and verify
docker compose start app scheduler
docker compose exec -T postgres psql -U ${DB_USER:-ace} -d ${DB_NAME:-ace} -c "SELECT version();"
curl http://localhost:19888/health
```

### Restoring to a fresh volume

If the `postgres-data` volume was lost entirely:

```bash
docker compose down
docker volume rm open-ace_postgres-data   # adjust the compose project prefix if needed
docker compose up -d postgres             # empty database re-created from env vars
cat ./backups/ace-20260929-0200.dump \
  | docker compose exec -T postgres pg_restore -U ${DB_USER:-ace} -d ${DB_NAME:-ace}
docker compose up -d
```

After any restore, run `alembic upgrade head` if the backup predates the current schema (see [../dev/SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md)).

### Scheduling (optional)

Cron on the Docker host:

```cron
0 2 * * * cd /path/to/open-ace && docker compose exec -T postgres pg_dump -U ace -d ace -Fc > backups/ace-$(date +\%Y\%m\%d).dump
```

## Multi-tenant note

`pg_dump` captures all tenants automatically — no special backup configuration is needed. After a restore, verify that all tenants are present and run the application-level permission validation.

## Recovery drill checklist

1. Download the latest backup from object storage (or `./backups/`)
2. Verify integrity (`pg_restore --list`)
3. Restore to a test database
4. Verify row counts and schema validation
5. Start the application against the restored database and run smoke tests
6. Document the RTO achieved

## Related documentation

- [KUBERNETES.md](KUBERNETES.md) — Kubernetes deployment guide
- [`k8s/extras/backup/README.md`](../../k8s/extras/backup/README.md) — backup manifest reference
- [../dev/SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md) — schema migration (run after restoring older backups)

---

## 中文

本文档描述 Open ACE 的数据库备份与恢复策略，覆盖 Kubernetes 部署（`k8s/extras/backup/`）与 Docker Compose 部署（`docker-compose.yml`）。

> **Issue #1853**：Kubernetes 数据库备份参考实现

## 概览

Open ACE 提供可选的数据库备份设施：

- 通过 CronJob 定时备份 PostgreSQL（Kubernetes）
- 手动 `pg_dump`/`pg_restore` 流程（Docker Compose）
- 使用 `pg_restore --list` 校验完整性
- 支持多种对象存储后端（AWS S3、MinIO 等）
- 用于灾难恢复的恢复 Job（Kubernetes）

### 部署选项

| 选项 | 环境 | 备份责任方 | 典型 RPO |
|------|------|-----------|---------|
| 托管 PostgreSQL（RDS/Aurora、Cloud SQL、Azure DB、阿里云 RDS） | 有 SLA 的生产环境 | 云厂商（自动备份、PITR） | < 5 分钟 |
| 自管 CronJob（`k8s/extras/backup/`） | Kubernetes、成本敏感 | 本仓库清单 | 6–24 小时（取决于 CronJob 频率） |
| 在 `postgres-data` 卷上手动 `pg_dump` | Docker Compose 单机 | 你自己，按本指南操作 | 取决于你的排程 |

## Kubernetes 备份（自管）

### 必读：对象存储 Secret 不在 kustomization 内

`k8s/extras/backup/kustomization.yaml` 的 resources 只包含：

- `serviceaccount.yaml`
- `rbac.yaml`
- `backup-script-configmap.yaml`
- `cronjob.yaml`
- `restore-job.yaml`

凭据文件（从 `secret-s3.yaml.example` 复制的 `secret-s3.yaml`，或从 `secret-minio.yaml.example` 复制的 `secret-minio.yaml`）**不在其中**。如果照搬"复制示例后 `kubectl apply -k .`"的老步骤，apply 会成功、CronJob 会创建，但之后每个备份任务都会在上传一步失败——因为 CronJob 引用的 `backup-storage-credentials` Secret 根本不存在；除非查看任务日志，否则该失败是静默的。

**先显式 apply Secret，再 apply kustomization：**

```bash
cd k8s/extras/backup/

# 1. 创建并编辑凭据
cp secret-s3.yaml.example secret-s3.yaml
# 编辑 secret-s3.yaml，填入你的 S3 凭据

# 2. 显式 apply Secret（kustomization.yaml 不包含它）
kubectl apply -f secret-s3.yaml

# 3. apply 其余备份清单
kubectl apply -k .

# 4. 同时核对 CronJob 与 Secret
kubectl get cronjob -n open-ace
kubectl get secret backup-storage-credentials -n open-ace
```

### 手动触发备份（测试）

```bash
# 从 CronJob 创建一次性 Job
kubectl create job --from=cronjob/postgres-backup manual-backup-$(date +%Y%m%d) -n open-ace

# 查看日志
kubectl logs -f job/manual-backup-$(date +%Y%m%d) -n open-ace
```

### CronJob 参数

以下数值是 `k8s/extras/backup/cronjob.yaml` 实际发布的内容：

| 参数 | 值 | 说明 |
|------|----|------|
| Schedule | `0 2 * * *` | 每天 02:00 UTC（cronjob.yaml:18） |
| activeDeadlineSeconds | 3600 | 1 小时超时；大库可提高到 7200（cronjob.yaml:33） |
| CPU 请求/上限 | 200m / 500m | （cronjob.yaml:132-137） |
| 内存 请求/上限 | 256Mi / 512Mi | 超过 1GB 的库建议提到 1Gi（cronjob.yaml:132-137） |
| concurrencyPolicy | Forbid | 禁止重叠运行 |
| backoffLimit | 2 | 失败最多重试 2 次 |
| successfulJobsHistoryLimit / failedJobsHistoryLimit | 3 / 3 | 保留以便排查 |
| 镜像 | `alpine:3.19`（init 容器：把 AWS CLI 装进 emptyDir）+ `postgres:15-alpine`（主容器） | postgres:15-alpine 不含 AWS CLI |

### 备份内容

| 组件 | 方法 | 频率 | 保留 |
|------|------|------|------|
| PostgreSQL 数据库 | `pg_dump -Fc`（custom 格式） | 每天 | 30 天 |
| PostgreSQL 全局对象 | `pg_dumpall --globals-only` | 每天 | 30 天 |
| Redis（可选，`BACKUP_REDIS`） | RDB 快照 | 每天 | 7 天 |

### 备份完整性校验

每次备份都自动做完整性校验：

1. **备份创建后：** `pg_restore --list db.dump` 验证文件可解析
2. **校验失败：** Job 以退出码 1 结束，触发 CronJob 重试
3. **成功标志：** Job 以退出码 0 结束，备份上传到对象存储

```bash
# 手动校验
pg_restore --list backup.dump > /dev/null && echo "Valid" || echo "Corrupted"
```

### 对象存储凭据

AWS S3（`secret-s3.yaml`）：

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
  S3_ENDPOINT: ""  # Leave empty for AWS S3
```

MinIO（`secret-minio.yaml`，来自 `secret-minio.yaml.example`）：

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

### 恢复流程（Kubernetes）

```bash
# 第 1 步：在对象存储中定位备份
aws s3 ls s3://your-bucket/open-ace/

# 第 2 步：下载
aws s3 cp s3://your-bucket/open-ace/20240115/backup.tar.gz .
tar -xzf backup.tar.gz

# 第 3 步：校验完整性
pg_restore --list db.dump

# 第 4 步：停掉应用写入，然后 apply 恢复 Job
kubectl scale deployment open-ace --replicas=0 -n open-ace
export RESTORE_TIMESTAMP=20240115
kubectl apply -f restore-job.yaml
kubectl logs -f job/postgres-restore -n open-ace

# 第 5 步：重启并验证应用
kubectl scale deployment open-ace --replicas=3 -n open-ace
kubectl exec -it deployment/open-ace -n open-ace -- \
  curl -s http://localhost:19888/health
```

备份 Job 与现有 NetworkPolicy 兼容（PostgreSQL 5432、Redis 6379、外部 HTTPS 443 出站均已放行）；无需修改 NetworkPolicy。

### 监控与排障

```bash
# 列出最近的备份 Job
kubectl get jobs -n open-ace -l app.kubernetes.io/component=backup

# 查看最近 Job 状态
kubectl describe job -n open-ace -l app.kubernetes.io/component=backup

# 查看 Job 日志
kubectl logs job/postgres-backup-$(date +%Y%m%d) -n open-ace
```

常见故障：

| 症状 | 原因 | 修复 |
|------|------|------|
| Job 运行、dump 成功、上传报凭据缺失 | `secret-s3.yaml` 从未 apply（它不在 kustomization 内） | `kubectl apply -f secret-s3.yaml` |
| 大库上 Job 被 OOM 杀死 | 512Mi 内存上限过低 | 把 CronJob 内存上限补丁到 1Gi |
| CronJob 从不启动 | 被挂起，或上一个 Job 仍在 1 小时死线内 | 检查 `suspend` 标志与 `activeDeadlineSeconds` |

## Docker Compose 备份与恢复

Compose 栈运行 PostgreSQL 15（`postgres:15-alpine`），全部数据落在名为 `postgres-data` 的卷上，挂载于 `/var/lib/postgresql/data`。默认值来自环境变量：用户 `${DB_USER:-ace}`、数据库 `${DB_NAME:-ace}`。用 `pg_dump` 备份；PostgreSQL 运行期间切勿直接拷贝卷目录。

### 备份

```bash
# custom 格式 dump，从容器流式导出（-T 关闭 TTY 以便重定向）
mkdir -p ./backups
docker compose exec -T postgres pg_dump -U ${DB_USER:-ace} -d ${DB_NAME:-ace} -Fc \
  > ./backups/ace-$(date +%Y%m%d-%H%M).dump

# 验证可解析
pg_restore --list ./backups/ace-$(date +%Y%m%d-%H%M).dump > /dev/null && echo "Valid"
```

纯 SQL dump（可 grep、体积更大）：

```bash
docker compose exec -T postgres pg_dump -U ${DB_USER:-ace} -d ${DB_NAME:-ace} \
  > ./backups/ace-$(date +%Y%m%d-%H%M).sql
```

### 恢复

```bash
# 1. 停掉写入方（app 与 scheduler，若启用）
docker compose stop app scheduler

# 2. 覆盖恢复到现有数据库（仅 custom 格式）
cat ./backups/ace-20260929-0200.dump \
  | docker compose exec -T postgres pg_restore -U ${DB_USER:-ace} -d ${DB_NAME:-ace} --clean --if-exists

# 3. 重启并验证
docker compose start app scheduler
docker compose exec -T postgres psql -U ${DB_USER:-ace} -d ${DB_NAME:-ace} -c "SELECT version();"
curl http://localhost:19888/health
```

### 恢复到全新卷

如果 `postgres-data` 卷整体丢失：

```bash
docker compose down
docker volume rm open-ace_postgres-data   # 如 compose 项目前缀不同请相应调整
docker compose up -d postgres             # 按环境变量重建空数据库
cat ./backups/ace-20260929-0200.dump \
  | docker compose exec -T postgres pg_restore -U ${DB_USER:-ace} -d ${DB_NAME:-ace}
docker compose up -d
```

任何恢复之后，若备份早于当前模式，请运行 `alembic upgrade head`（见 [../dev/SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md)）。

### 定时备份（可选）

在 Docker 宿主机上配置 cron：

```cron
0 2 * * * cd /path/to/open-ace && docker compose exec -T postgres pg_dump -U ace -d ace -Fc > backups/ace-$(date +\%Y\%m\%d).dump
```

## 多租户说明

`pg_dump` 会自动捕获所有租户——无需特殊备份配置。恢复之后，验证所有租户存在，并运行应用层权限校验。

## 恢复演练清单

1. 从对象存储（或 `./backups/`）下载最新备份
2. 校验完整性（`pg_restore --list`）
3. 恢复到测试数据库
4. 验证行数与模式校验
5. 用恢复出的数据库启动应用并跑冒烟测试
6. 记录实际达成的 RTO

## 相关文档

- [KUBERNETES.md](KUBERNETES.md) — Kubernetes 部署指南
- [`k8s/extras/backup/README.md`](../../k8s/extras/backup/README.md) — 备份清单参考
- [../dev/SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md) — 模式迁移（恢复较旧备份后需运行）

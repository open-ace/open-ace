# Open ACE - Database Backup for Kubernetes

This directory contains optional Kubernetes manifests for database backup and restore operations.

## Overview

The backup solution provides:
- Scheduled PostgreSQL backups via CronJob
- Integrity verification using `pg_restore --list`
- Multiple object storage backend support (S3, MinIO)
- Optional Redis backup
- Restore Job for disaster recovery

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Backup Pod                          │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ InitContainer: install-awscli (alpine:3.19)      │  │
│  │   - apk add --no-cache aws-cli                   │  │
│  │   - Copy /usr/bin/aws to shared volume           │  │
│  └──────────────────────────────────────────────────┘  │
│                         ↓                                │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Main Container: backup (postgres:15-alpine)      │  │
│  │   - Mount aws binary from shared volume          │  │
│  │   - Run pg_dump, integrity check, upload         │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**Why initContainer?**
- The `postgres:15-alpine` image does NOT include AWS CLI
- Using initContainer ensures AWS CLI is installed before the main container starts
- This approach is more reliable than runtime installation

## Quick Start

```bash
# 1. Create object storage credentials
cp secret-s3.yaml.example secret-s3.yaml
# Edit secret-s3.yaml with your credentials

# 2. Apply backup manifests
kubectl apply -k .

# 3. Verify CronJob is created
kubectl get cronjob -n open-ace
```

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Kustomize entry point |
| `cronjob.yaml` | Scheduled backup CronJob (with initContainer) |
| `backup-script-configmap.yaml` | Backup script with integrity check |
| `serviceaccount.yaml` | ServiceAccount for backup jobs |
| `rbac.yaml` | Role and RoleBinding (optional) |
| `restore-job.yaml` | One-time restore Job (with initContainer) |
| `secret-s3.yaml.example` | AWS S3 credentials template |
| `secret-minio.yaml.example` | MinIO credentials template |

## Resource Configuration

| Parameter | Value | Notes |
|-----------|-------|-------|
| Schedule | `0 2 * * *` | Daily at 02:00 UTC |
| CPU Request | 200m | |
| CPU Limit | 500m | |
| Memory Request | 256Mi | |
| Memory Limit | 512Mi | Increase to 1Gi for DB > 1GB |
| Timeout | 3600s | 1 hour; increase to 7200s for large DBs |
| History Limit | 3 | Success/failed jobs retained |

## NetworkPolicy Compatibility

The backup Job is compatible with the existing NetworkPolicy in `k8s/policies.yaml`:
- Egress to PostgreSQL (TCP 5432): Already allowed
- Egress to Redis (TCP 6379): Already allowed
- Egress to external HTTPS (TCP 443): Already allowed for object storage and package download

**No NetworkPolicy modification required.**

## Object Storage Backends

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
  AWS_ACCESS_KEY_ID: "your-access-key"
  AWS_SECRET_ACCESS_KEY: "your-secret-key"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "your-bucket-name"
  S3_ENDPOINT: ""  # Leave empty for AWS S3
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
  AWS_ACCESS_KEY_ID: "minio-access-key"
  AWS_SECRET_ACCESS_KEY: "minio-secret-key"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "open-ace-backups"
  S3_ENDPOINT: "http://minio.example.com:9000"
```

## Restore Procedure

1. Download backup from object storage:
   ```bash
   aws s3 cp s3://bucket/open-ace/2024-01-15/backup.tar.gz .
   ```

2. Extract and verify:
   ```bash
   tar -xzf backup.tar.gz
   pg_restore --list db.dump
   ```

3. Apply restore Job:
   ```bash
   kubectl apply -f restore-job.yaml
   ```

4. Monitor restore progress:
   ```bash
   kubectl logs -f job/postgres-restore -n open-ace
   ```

## Multi-Tenant Considerations

When `ENABLE_MULTI_TENANT` is enabled:
- Backup: `pg_dump` captures all schemas automatically
- Restore: Verify tenant schema permissions after restore
- Run application-level permission validation script

## PostgreSQL Version Compatibility

The backup includes PostgreSQL version metadata. During restore:
- Version numbers are compared
- Major version mismatch triggers a warning (does not block restore)
- Consider restoring to matching version first for cross-version migration

## Troubleshooting

### CronJob not running
```bash
kubectl describe cronjob postgres-backup -n open-ace
kubectl get jobs -n open-ace
```

### Backup fails with OOM
Increase memory limit in `cronjob.yaml`:
```yaml
resources:
  limits:
    memory: 1Gi
```

### AWS CLI installation fails (initContainer)
Check initContainer logs:
```bash
kubectl logs job/postgres-backup-xxx -n open-ace -c install-awscli
```

### Network timeout
Verify NetworkPolicy allows egress:
```bash
kubectl get networkpolicy -n open-ace -o yaml
```

## Related Documentation

- [DATABASE-BACKUP.md](../../docs/en/DATABASE-BACKUP.md) - Detailed backup strategy
- [KUBERNETES.md](../../docs/en/KUBERNETES.md) - Kubernetes deployment guide

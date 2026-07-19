# Database Backup and Recovery Guide

This document describes the database backup and recovery strategy for Open ACE deployed on Kubernetes.

> **Issue #1853**: Kubernetes database backup reference implementation

## Overview

Open ACE provides optional database backup infrastructure for Kubernetes deployments. The backup solution includes:

- Scheduled PostgreSQL backups via CronJob
- Integrity verification using `pg_restore --list`
- Multiple object storage backend support (AWS S3, MinIO, etc.)
- Restore Job for disaster recovery

## Deployment Options

### Option A: Managed PostgreSQL (Recommended for Production)

For production environments with SLA requirements, use a managed PostgreSQL service:

| Provider | Service | Features |
|----------|---------|----------|
| AWS | RDS/Aurora | Automated backups, PITR, Multi-AZ |
| Google Cloud | Cloud SQL | Automated backups, PITR, HA |
| Azure | Database for PostgreSQL | Automated backups, PITR, Geo-replication |
| Alibaba Cloud | RDS for PostgreSQL | Automated backups, PITR |

**Benefits:**
- Backup responsibility handled by cloud provider
- Point-in-Time Recovery (PITR) available
- Typical RPO: < 5 minutes
- Typical RTO: < 1 hour

### Option B: Self-Managed Backup (Development/Testing)

For cost-sensitive or development environments, use the provided CronJob:

| Aspect | Setting |
|--------|---------|
| Backup Tool | `pg_dump -Fc` (custom format) |
| Schedule | Daily at 02:00 UTC |
| Storage | S3-compatible object storage |
| Retention | 30 days (configurable via bucket policy) |
| RPO | 6-24 hours (depends on CronJob frequency) |
| RTO | Depends on database size and recovery testing |

## Quick Start

### 1. Configure Object Storage Credentials

```bash
cd k8s/extras/backup/

# Copy and edit credentials
cp secret-s3.yaml.example secret-s3.yaml
# Edit secret-s3.yaml with your S3 credentials
```

### 2. Deploy Backup Infrastructure

```bash
# Apply all backup manifests
kubectl apply -k .

# Verify CronJob
kubectl get cronjob -n open-ace
kubectl describe cronjob postgres-backup -n open-ace
```

### 3. Trigger Manual Backup (Testing)

```bash
# Create a one-time job from the CronJob
kubectl create job --from=cronjob/postgres-backup manual-backup-$(date +%Y%m%d) -n open-ace

# Watch logs
kubectl logs -f job/manual-backup-$(date +%Y%m%d) -n open-ace
```

## Backup Content

| Component | Method | Frequency | Retention |
|-----------|--------|-----------|-----------|
| PostgreSQL database | `pg_dump -Fc` | Daily | 30 days |
| PostgreSQL globals | `pg_dumpall --globals-only` | Daily | 30 days |
| Redis (optional) | RDB snapshot | Daily | 7 days |
| ConfigMap/Secret (optional) | kubectl get | Daily | 30 days |

## Resource Configuration

### Default Settings

| Parameter | Value | Notes |
|-----------|-------|-------|
| Schedule | `0 2 * * *` | Daily at 02:00 UTC |
| CPU Request | 200m | |
| CPU Limit | 500m | |
| Memory Request | 256Mi | |
| Memory Limit | 512Mi | Increase for large databases |
| Timeout | 3600s | 1 hour |

### Large Database Adjustments

For databases > 1GB:

```yaml
# Edit cronjob.yaml
resources:
  limits:
    memory: 1Gi
activeDeadlineSeconds: 7200  # 2 hours
```

## Backup Integrity Verification

Each backup includes automatic integrity verification:

1. **After backup creation:** `pg_restore --list db.dump` validates the file is parseable
2. **If verification fails:** Job exits with code 1, triggering CronJob retry
3. **Success indicator:** Job exits with code 0, backup uploaded to object storage

```bash
# Manual verification
pg_restore --list backup.dump > /dev/null && echo "Valid" || echo "Corrupted"
```

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
  AWS_ACCESS_KEY_ID: "AKIAIOSFODNN7EXAMPLE"
  AWS_SECRET_ACCESS_KEY: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "my-open-ace-backups"
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
  AWS_ACCESS_KEY_ID: "minioadmin"
  AWS_SECRET_ACCESS_KEY: "minioadmin"
  AWS_REGION: "us-east-1"
  S3_BUCKET: "open-ace-backups"
  S3_ENDPOINT: "http://minio.example.com:9000"
```

## Recovery Procedure

### Step 1: Identify Backup

List available backups in your object storage:

```bash
aws s3 ls s3://your-bucket/open-ace/
```

### Step 2: Download Backup

```bash
aws s3 cp s3://your-bucket/open-ace/20240115/backup.tar.gz .
tar -xzf backup.tar.gz
```

### Step 3: Verify Integrity

```bash
pg_restore --list db.dump
```

### Step 4: Restore Database

```bash
# Set restore timestamp
export RESTORE_TIMESTAMP=20240115

# Apply restore Job
kubectl apply -f restore-job.yaml

# Monitor progress
kubectl logs -f job/postgres-restore -n open-ace
```

### Step 5: Verify Application

```bash
# Check database connectivity
kubectl exec -it deployment/open-ace -n open-ace -- \
  curl -s http://localhost:19888/health

# Check application logs
kubectl logs -f deployment/open-ace -n open-ace
```

## Multi-Tenant Considerations

When `ENABLE_MULTI_TENANT` is enabled:

### Backup

- `pg_dump` automatically captures all tenant schemas
- No special backup configuration needed

### Restore

After restore, verify:

1. All tenant schemas exist
2. Tenant user permissions are correct
3. Run application-level permission validation

```sql
-- Check tenant schemas
SELECT schema_name FROM information_schema.schemata 
WHERE schema_name LIKE 'tenant_%';

-- Check permissions
SELECT grantee, table_schema, privilege_type 
FROM information_schema.table_privileges 
WHERE table_schema LIKE 'tenant_%';
```

## NetworkPolicy Compatibility

The backup Job is compatible with existing NetworkPolicy:

| Traffic | Status |
|---------|--------|
| PostgreSQL (5432) | ✅ Allowed by existing egress rule |
| Redis (6379) | ✅ Allowed by existing egress rule |
| External HTTPS (443) | ✅ Allowed for object storage |

**No NetworkPolicy modification required.**

## RBAC Requirements

The backup Job uses a dedicated ServiceAccount:

```yaml
# Minimal permissions
rules:
  - apiGroups: [""]
    resources: ["configmaps", "secrets"]
    verbs: ["get", "list"]  # Optional, for K8s resource backup
```

## Encryption

| Layer | Option |
|-------|--------|
| Transport | HTTPS (TLS 1.2+) |
| Storage | SSE-S3 or SSE-KMS |
| Client-side (optional) | AES-256 encryption |

## Monitoring and Alerts

### Check Backup Status

```bash
# List recent jobs
kubectl get jobs -n open-ace -l app.kubernetes.io/component=backup

# Check last job status
kubectl describe job -n open-ace -l app.kubernetes.io/component=backup | \
  grep -A5 "Status:"

# View job logs
kubectl logs job/postgres-backup-$(date +%Y%m%d) -n open-ace
```

### Recommended Alerts

Configure alerts for:
- CronJob execution failures (3+ consecutive failures)
- Backup job timeout
- Object storage upload failures

## Recovery Testing

### Monthly Drill Checklist

1. ✅ Download latest backup from object storage
2. ✅ Verify backup integrity (`pg_restore --list`)
3. ✅ Restore to a test database
4. ✅ Verify data integrity (row counts, schema validation)
5. ✅ Start application against restored database
6. ✅ Run smoke tests
7. ✅ Document RTO achieved

## Troubleshooting

### CronJob Not Running

```bash
# Check CronJob status
kubectl describe cronjob postgres-backup -n open-ace

# Check for suspended CronJob
kubectl get cronjob postgres-backup -n open-ace -o jsonpath='{.spec.suspend}'
```

### Backup Fails with OOM

```bash
# Increase memory limit
kubectl patch cronjob postgres-backup -n open-ace --type=json -p \
  '[{"op": "replace", "path": "/spec/jobTemplate/spec/template/spec/containers/0/resources/limits/memory", "value": "1Gi"}]'
```

### Network Timeout

```bash
# Verify NetworkPolicy allows egress
kubectl get networkpolicy -n open-ace -o yaml

# Test connectivity from backup pod
kubectl run tmp-shell --rm -i --tty --image postgres:15-alpine -- \
  psql -h postgres -U openace -d openace -c "SELECT 1"
```

### Restore Fails

```bash
# Check database connections
kubectl exec -it postgres-0 -n open-ace -- \
  psql -U openace -d openace -c "SELECT count(*) FROM pg_stat_activity WHERE datname='openace';"

# Stop application before restore
kubectl scale deployment open-ace --replicas=0 -n open-ace

# After restore
kubectl scale deployment open-ace --replicas=3 -n open-ace
```

## Related Documentation

- [KUBERNETES.md](./KUBERNETES.md) - Kubernetes deployment guide
- [k8s/extras/backup/README.md](../../k8s/extras/backup/README.md) - Backup manifest details
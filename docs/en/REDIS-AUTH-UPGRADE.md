# Redis Authentication Upgrade Guide

This document describes how to enable Redis authentication for existing Open ACE deployments.

## Issue #1895: K8s RBAC Permission Minimization and Redis Authentication Hardening

### Background

Starting from version X.X.X, Open ACE requires Redis authentication in production environments. This guide helps you upgrade existing deployments.

### Prerequisites

- Open ACE deployed on Kubernetes
- `kubectl` access to the cluster
- Redis is currently running without authentication

### Upgrade Steps

#### Step 1: Generate a Strong Password

Generate a strong password for Redis:

```bash
REDIS_PASSWORD=$(openssl rand -base64 32)
echo "Generated password: $REDIS_PASSWORD"
```

**Important**: Save this password securely. You will need it for application configuration.

#### Step 2: Update the Secret

Add the Redis password to the Kubernetes secret:

```bash
kubectl patch secret open-ace-secrets -n open-ace \
  --type=json -p='[{"op":"add","path":"/data/REDIS_PASSWORD","value":"'$REDIS_PASSWORD'"}]'
```

Or, if you prefer to update the entire secret:

```bash
kubectl create secret generic open-ace-secrets \
  --from-literal=REDIS_PASSWORD="$REDIS_PASSWORD" \
  --namespace=open-ace \
  --dry-run=client -o yaml | kubectl apply -f -
```

#### Step 3: Restart Redis StatefulSet

Rolling restart the Redis StatefulSet to pick up the new configuration:

```bash
kubectl rollout restart statefulset/redis -n open-ace
kubectl rollout status statefulset/redis -n open-ace
```

#### Step 4: Verify Redis Authentication

Test that Redis now requires authentication:

```bash
# This should return NOAUTH error
kubectl exec -it redis-0 -n open-ace -- redis-cli ping
# Expected: (error) NOAUTH Authentication required

# This should return PONG
kubectl exec -it redis-0 -n open-ace -- \
  env REDISCLI_AUTH=$REDIS_PASSWORD redis-cli ping
# Expected: PONG
```

#### Step 5: Deploy New Application Version

Deploy the new version of Open ACE with Redis authentication support:

```bash
kubectl apply -f k8s/
kubectl rollout status deployment/open-ace -n open-ace
```

#### Step 6: Verify Application

Check that the application starts correctly:

```bash
# Check pod logs
kubectl logs -l app.kubernetes.io/name=open-ace -n open-ace --tail=100

# Check for Redis connection
kubectl exec -it deployment/open-ace -n open-ace -- \
  python -c "from app.utils.cache import CacheManager; c = CacheManager(); print(c.stats())"
```

### Rollback Procedure

If you encounter issues after enabling Redis authentication:

#### Step 1: Rollback Application

```bash
kubectl rollout undo deployment/open-ace -n open-ace
kubectl rollout status deployment/open-ace -n open-ace
```

#### Step 2: Rollback Redis

Remove the authentication requirement:

```bash
# Deploy the old Redis manifest (without --requirepass)
kubectl apply -f k8s/database.yaml --force
```

#### Step 3: Remove Password from Secret

```bash
kubectl patch secret open-ace-secrets -n open-ace \
  --type=json -p='[{"op":"remove","path":"/data/REDIS_PASSWORD"}]'
```

### Development Environment

In development environments, you can leave `REDIS_PASSWORD` empty or unset. The application will allow Redis connections without authentication.

To run locally without Redis authentication:

```bash
# Option 1: No password
unset REDIS_PASSWORD

# Option 2: Empty password
export REDIS_PASSWORD=""

# Start the application
FLASK_ENV=development python server.py
```

### Security Considerations

1. **Password Strength**: Use a strong, randomly generated password (at least 32 characters)
2. **Password Rotation**: Rotate the Redis password periodically (recommended: every 90 days)
3. **Network Policy**: Redis NetworkPolicy is already configured, but authentication adds an additional layer of defense
4. **Backup**: Ensure `BACKUP_REDIS=true` cronjobs have the `REDIS_PASSWORD` environment variable injected

### Troubleshooting

#### Application Fails to Start

**Symptom**: Application pod crashes with error:
```
RuntimeError: REDIS_PASSWORD must be set in production
```

**Solution**: Ensure the secret contains a valid `REDIS_PASSWORD` value:
```bash
kubectl get secret open-ace-secrets -n open-ace -o jsonpath='{.data.REDIS_PASSWORD}' | base64 -d
```

#### Redis Health Check Fails

**Symptom**: Redis pod keeps restarting with liveness probe failures.

**Solution**: Check that `REDISCLI_AUTH` environment variable is injected:
```bash
kubectl exec -it redis-0 -n open-ace -- env | grep REDISCLI_AUTH
```

#### Backup Fails with Redis Auth Error

**Symptom**: Backup cronjob logs show:
```
WARNING: Redis BGSAVE failed (Redis may not be available or auth required)
```

**Solution**: Ensure backup cronjob has `REDIS_PASSWORD` environment variable:
```bash
kubectl get cronjob -n open-ace -o yaml | grep -A5 REDIS_PASSWORD
```

### References

- Issue #1895: K8s RBAC Permission Minimization and Redis Authentication Hardening
- Issue #1821: General K8s Deployment Hardening
- Redis Security Best Practices: https://redis.io/docs/management/security/
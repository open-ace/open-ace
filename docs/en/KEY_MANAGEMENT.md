# Key Management

> Encryption key derivation, rotation, and security best practices for Open-ACE.

## Overview

Open-ACE uses Fernet symmetric encryption to protect sensitive data at rest:

- API keys for remote workspaces (`api_key_store` table)
- SMTP passwords (`smtp_settings` table)
- Model Gateway API keys (`model_gateway_config` table)

Proxy tokens use HMAC-SHA256 signatures (not Fernet) for authentication.

## Key Derivation

The encryption key is derived from the `OPENACE_ENCRYPTION_KEY` environment variable:

```
OPENACE_ENCRYPTION_KEY (env var, >= 32 chars)
         │
         │ SHA-256 hash
         ▼
    32-byte key
         │
         │ base64.urlsafe_b64encode
         ▼
    Fernet key (44 chars)
         │
         ├────────────────┬────────────────┐
         ▼                ▼                ▼
   API Key          SMTP Password    Model Gateway
   Encryption       Encryption       Encryption
         │
         │ Same key for HMAC-SHA256
         ▼
   Proxy Token
   Signing
```

**Key derivation code**:

```python
import hashlib
import base64

key_env = os.environ.get("OPENACE_ENCRYPTION_KEY")
derived_key = hashlib.sha256(key_env.encode()).digest()
fernet_key = base64.urlsafe_b64encode(derived_key)
```

## Key Sharing Impact

The same key is used for:

1. **API Key encryption** - `api_key_store.encrypted_key`
2. **SMTP password encryption** - `smtp_settings.encrypted_password`
3. **Model Gateway encryption** - `model_gateway_config.encrypted_api_key`
4. **Proxy Token signing** - HMAC-SHA256 signatures for remote agent authentication

**Implications**:

- Key rotation requires re-encrypting all three data stores
- Active proxy tokens signed with the old key will fail validation after rotation
- Key compromise affects all four security domains

## Key Rotation

### Current Limitations

- **Single-key Fernet**: No support for MultiFernet multi-key decryption
- **Rotation requires downtime**: Cannot rotate keys without service restart
- **Manual process**: No automated key rotation mechanism

### Rotation Methods

#### Method A: Stop-and-Rotate (Recommended for small deployments)

**Prerequisites**:

- Database backup capability
- Planned maintenance window
- Root access to environment variables

**Steps**:

1. **Backup the database**

   ```bash
   # PostgreSQL
   pg_dump openace > openace_backup_$(date +%Y%m%d).sql

   # SQLite
   cp app.db app_backup_$(date +%Y%m%d).db
   ```

2. **Export encrypted data**

   ```bash
   python scripts/export_encrypted_data.py --output encrypted_data_backup.json
   ```

   This exports:
   - `api_key_store.encrypted_key` → plaintext API keys
   - `smtp_settings.encrypted_password` → plaintext SMTP passwords
   - `model_gateway_config.encrypted_api_key` → plaintext gateway keys

3. **Generate and set new key**

   ```bash
   # Generate a new 32-byte key
   NEW_KEY=$(openssl rand -hex 32)
   echo "New key: $NEW_KEY"

   # Update environment variable
   # Docker Compose: edit .env file
   # Kubernetes: update Secret
   # Systemd: edit /etc/open-ace/environment
   ```

4. **Restart the service**

   ```bash
   # Docker Compose
   docker-compose restart

   # Systemd
   sudo systemctl restart open-ace
   ```

5. **Re-encrypt and import data**

   ```bash
   python scripts/import_encrypted_data.py --input encrypted_data_backup.json
   ```

6. **Verify functionality**

   - Test API key storage and retrieval
   - Test SMTP email sending
   - Test Model Gateway calls
   - Note: Existing proxy tokens will be invalid (users need to restart sessions)

7. **Secure cleanup**

   ```bash
   # Remove plaintext backup after verification
   rm encrypted_data_backup.json

   # Optionally archive encrypted database backup
   gzip openace_backup_*.sql
   ```

#### Method B: MultiFernet Support (Future enhancement)

**Requirements**:

- Code modification to support `MultiFernet`
- Environment variable format: `KEY1;KEY2` (primary;fallback)
- Zero-downtime rotation capability

**Implementation needed**:

- Modify `_get_encryption_key()` to return key list
- Use `MultiFernet([key1, key2])` for decryption
- Use primary key for new encryption
- Gradual migration path

## Security Best Practices

### Key Generation

```bash
# Generate a strong random key (256 bits = 32 bytes = 64 hex chars)
openssl rand -hex 32
```

### Key Storage

- **Never commit to source control**
- Use environment variables or secrets management:
  - Docker Compose: `.env` file (add to `.gitignore`)
  - Kubernetes: Secrets resource
  - Cloud: AWS Secrets Manager, Azure Key Vault, GCP Secret Manager

### Key Rotation Schedule

- **Recommended**: Every 90 days
- **Required**: Immediately after suspected compromise
- **Document**: Maintain rotation log with timestamps

### Key Compromise Response

1. Immediately generate and set new key
2. Revoke all active proxy tokens (if applicable)
3. Rotate all encrypted credentials
4. Audit access logs for suspicious activity
5. Document incident and remediation steps

## Database Schema

### Encryption Version Field

Tables with encrypted data include an `encryption_version` field:

- `api_key_store.encryption_version` (default: 1)
- `smtp_settings.encryption_version` (default: 1)
- `model_gateway_config.encryption_version` (default: 1)

**Version mapping**:

| Version | Algorithm | Notes |
|---------|-----------|-------|
| 1 | Fernet (AES-128-CBC + HMAC-SHA256) | Current |
| 2+ | Reserved for future algorithms | e.g., AES-256-GCM |

Future algorithm upgrades will:

1. Support reading version 1 data
2. Write new data with version 2
3. Provide migration scripts for gradual transition

## Troubleshooting

### "Invalid Fernet key" errors

- Verify `OPENACE_ENCRYPTION_KEY` is set
- Check key format (should be hex or base64, >= 32 chars)
- Ensure no whitespace or newlines in the value

### Decryption failures after rotation

- Confirm you're using the correct key for the data's encryption version
- Check if data was encrypted with a different key
- Restore from backup if key is lost

### Proxy tokens invalid after rotation

- Expected behavior: tokens signed with old key
- Users need to restart remote sessions
- No action needed if sessions are short-lived

## Related Documentation

- [Remote Workspace](./REMOTE-WORKSPACE.md) - Feature overview
- [Deployment](./DEPLOYMENT.md) - Production deployment guide
- [Security Architecture](./SECURITY.md) - Security model details

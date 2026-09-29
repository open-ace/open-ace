# Key Management

> Encryption key derivation, rotation, and security best practices for Open-ACE.

## Overview

Open-ACE uses Fernet symmetric encryption to protect sensitive data at rest.
The same key directly encrypts **all 8 stores** (authoritative list in
[Key Sharing Impact](#key-sharing-impact) below):

- API keys for remote workspaces (`api_key_store` table)
- SMTP passwords (`smtp_settings` table)
- Model Gateway API keys (`model_gateway_config` table)
- SSO provider credentials (`sso_providers` table)
- DingTalk integration (`dingtalk_settings` table)
- Feishu integration (`feishu_settings` table)
- Webhook configs (`webhook_settings` table)
- Notification preferences (`notification_preferences` table)

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
         ├──────────────────────────────┐
         ▼                              ▼
   ALL 8 encrypted stores           Proxy Token
   (api_key_store / smtp_settings /   signing (HMAC-SHA256,
    model_gateway_config /             not Fernet)
    sso_providers / dingtalk_settings /
    feishu_settings / webhook_settings /
    notification_preferences —
    full list in "Key Sharing Impact")

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

The same key (`OPENACE_ENCRYPTION_KEY`, SHA-256-derived Fernet key) directly
encrypts every store below:

1. **API Key encryption** - `api_key_store.encrypted_key`
2. **SMTP password encryption** - `smtp_settings.encrypted_password`
3. **Model Gateway encryption** - `model_gateway_config.encrypted_api_key`
4. **SSO provider configs** - `sso_providers` (third-party credentials embedded in JSON)
5. **DingTalk integration** - `dingtalk_settings`
6. **Feishu integration** - `feishu_settings`
7. **Webhook configs** - `webhook_settings`
8. **Notification preferences** - `notification_preferences`

The same key also signs **Proxy Tokens** (HMAC-SHA256, remote agent
authentication).

**Implications**:

- Rotation must re-encrypt **ALL of the stores above in one pass** — rotating
  only a subset leaves the remaining ciphertexts undecryptable after the key
  switch (this is exactly why `scripts/rotate_sso_encryption.py` rotates every
  store in a single transaction)
- Active proxy tokens signed with the old key will fail validation after rotation
- Key compromise affects all of the security domains above

## Key Rotation

### Rotation Capabilities and Boundaries

- **Atomic full-store rotation**: `scripts/rotate_sso_encryption.py`
  re-encrypts EVERY store bound to this key in a single transaction (full
  rollback on any failure); `--verify` provides a no-write pre-flight
- **Single-key Fernet**: No MultiFernet multi-key decryption (see
  "MultiFernet Support (Future enhancement)" below)
- **Restart on switch**: the service must be restarted after the
  environment variable switches to the new key

### Rotation Method

#### Atomic full-store rotation (`rotate_sso_encryption.py`)

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

2. **Generate a new key (do NOT enable it yet)**

   ```bash
   NEW_KEY=$(openssl rand -hex 32)
   ```

3. **Stop every reader/writer that uses the old key** (app, scheduler/
   workers; the database itself stays up). This is a PRECONDITION of the
   script's safety, not an option: the script takes no table locks; each
   UPDATE is only optimistic on the value it scanned (a concurrent
   MODIFICATION of a scanned row → 0/2 affected rows → full rollback), but
   rows INSERTED after a table's scan are invisible inside the transaction,
   and the post-check can only REPORT committed mixed-key data, never roll
   it back — old-key ciphertext WILL remain if writers stay up.

4. **Keep the environment on the OLD key and run the pre-flight dry run**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY" --verify
   ```

   The pre-flight round-trips a probe with the new key and verifies every
   store decrypts under the old key; nothing is written on failure. The
   environment must stay on the OLD key for the whole rotation — switching
   early makes the old ciphertexts undecryptable and fails the pre-flight.

5. **Run the rotation (single transaction, per-UPDATE row-count validation; a concurrent modification rolls the whole rotation back)**

   ```bash
   python scripts/rotate_sso_encryption.py --new-key "$NEW_KEY"
   ```

   Every store the key directly protects is re-encrypted (registry-format
   ciphertexts prefixed `v1k<id>:` are bound to the `OPENACE_ENCRYPTION_KEYS`
   data keys and are skipped automatically), and the script re-verifies all
   stores with the new key afterwards.

6. **Switch the environment to the new key and restart ALL services**

   ```bash
   # Docker Compose: edit .env
   # Kubernetes: update Secret
   # Systemd: edit /etc/open-ace/environment
   docker-compose restart   # or: sudo systemctl restart open-ace
   ```

7. **Verify functionality**: API keys, SMTP, Model Gateway, SSO login,
   DingTalk/Feishu/webhooks/notifications. Existing proxy tokens are
   invalidated (users must restart sessions).

8. **Secure cleanup**: archive or remove the database backup after
   verification.

#### MultiFernet Support (Future enhancement)

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

1. Generate the new key but do **not** enable it yet; back up the affected
   stores and pause writes to them
2. Revoke all active proxy tokens (if applicable)
3. Keep `OPENACE_ENCRYPTION_KEY` set to the **old** key and rotate all
   encrypted credentials: `scripts/rotate_sso_encryption.py --new-key <NEW_KEY>`
   re-encrypts every store protected by the key in a **single transaction**
   (`sso_providers`, `api_key_store`, `smtp_settings`,
   `model_gateway_config`, `dingtalk_settings`, `feishu_settings`,
   `webhook_settings`, `notification_preferences`; `v1k<id>:`-prefixed
   key-registry ciphertexts are bound to the `OPENACE_ENCRYPTION_KEYS` data
   keys instead and are skipped automatically). Run the `--verify`
   pre-flight first. The script reads the environment variable as the old
   key and `--new-key` as the new one — switching the environment variable
   before rotating makes the old ciphertext undecryptable and fails the
   pre-flight
4. Only after the rotation completes (the script re-checks every store with
   the new key after writing) and no other store sharing this key remains,
   switch `OPENACE_ENCRYPTION_KEY` to the new key and restart the service
5. Audit access logs for suspicious activity; document incident and
   remediation steps

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

- [Remote Workspace](./REMOTE_WORKSPACE.md) - Feature overview
- [Deployment](./DEPLOYMENT.md) - Production deployment guide
- [Security Policy](https://github.com/open-ace/open-ace/security/policy) - Security reporting and policy details

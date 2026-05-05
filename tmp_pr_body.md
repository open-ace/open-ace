## Summary

Addresses critical security vulnerabilities identified across 8 rounds of adversarial review on issue #255. This PR implements all P0 fixes.

## Changes

### #245 — Session ownership checks
- Add ownership verification to `get_session`, `delete_session`, `restore_session`, `complete_session`, and `rename_session` endpoints
- Only session owner or admin can access/modify sessions — prevents unauthorized cross-user session access

### #267 — Authentication infrastructure
- **`require_auth()` now checks session expiry** — previously only `validate_session()` checked expiry, affecting 6 blueprints
- **5 `before_request` hooks** (workspace, remote, alerts, insights, quota) switched from `get_session()` to `validate_session()` for expiry enforcement
- **Session timeout reads from `security_settings` DB** instead of hardcoded 24h
- **Cookie `secure` auto-set** via `request.is_secure`; `max_age` follows DB timeout config

### #268 — API key encryption
- `_encrypt_key`/`_decrypt_key` now **raise `RuntimeError`** when `cryptography` package is unavailable, instead of silently degrading to base64 encoding

### #266 — Path traversal prevention
- Replace `os.path.abspath()` with `os.path.realpath()` in `fs.py` to resolve symlinks and prevent symlink-based path traversal attacks

### #269 — Error information leakage
- Replace **68 instances** of `str(e)` in error responses with generic `"Internal server error"` across 8 route files
- Detailed errors still logged server-side via `logger.error()`

## Files changed (13)
- `app/services/auth_service.py` — session expiry check, timeout from DB, login lockout
- `app/routes/workspace.py` — 5 session endpoints ownership checks
- `app/routes/remote.py`, `alerts.py`, `insights.py`, `quota.py` — `validate_session()` migration
- `app/routes/auth.py` — cookie security, timeout from DB
- `app/routes/fs.py` — `realpath()` for symlink resolution
- `app/routes/governance.py`, `fetch.py`, `roi.py`, `projects.py` — `str(e)` cleanup
- `app/modules/workspace/api_key_proxy.py` — raise on missing cryptography

## Test plan
- [x] App creates successfully without import errors
- [x] Service starts and health endpoint responds
- [x] Login returns unified error message (no info leakage)
- [ ] Session ownership: user A cannot access user B's session (manual test)
- [ ] Expired session tokens are rejected by all endpoints (manual test)

Fixes #245
Refs #255, #265, #266, #267, #268, #269

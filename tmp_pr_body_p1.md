## Summary

Phase 1-2 of #261: Standardize authentication across all route files with a unified `app/auth/decorators.py` framework.

Replaces **11+ scattered** `require_auth`/`require_admin`/`_require_admin` implementations with three consistent decorators:
- `@auth_required` — any authenticated user
- `@admin_required` — admin role required
- `@public_endpoint` — explicitly marks route as public (recognized by security scanner)

## Changes

### New: `app/auth/decorators.py`
- `_extract_token()` — unified token extraction from cookie → header → query param
- `_load_user_from_token()` — validates session and returns user dict
- `auth_required(ownership=None)` — auth decorator with optional ownership checks
- `admin_required` — admin-only decorator
- `public_endpoint` — marks route as intentionally unauthenticated

### Migrated route files (16 files, -796 lines, +660 lines)

| File | Pattern Replaced | Routes |
|------|-----------------|--------|
| `admin.py` | `require_admin(token)` proxy | 7 |
| `analytics.py` | `require_admin`/`require_auth` proxies + `auth_service` | 4 |
| `governance.py` | `require_admin`/`require_auth` proxies | 19 |
| `compliance.py` | `_require_admin()` inline function | 14 |
| `tenant.py` | `_require_admin()` inline function | 14 |
| `remote.py` | `before_request` + `auth_service.validate_session()` | ~30 |
| `workspace.py` | `before_request` + `auth_service.validate_session()` | ~40 |
| `alerts.py` | `before_request` + `auth_service.validate_session()` | ~8 |
| `insights.py` | Already partially migrated — fixed orphaned code | 3 |
| `quota.py` | Already migrated — no changes needed | 4 |
| `sso.py` | Inline `require_admin`/`require_auth` + broken `result` refs | 4 |
| `report.py` | Inline `require_auth` + `session_or_error` refs | 1 |
| `fs.py` | `before_request` cleanup | — |
| `projects.py` | `before_request` cleanup | — |

### Other updates
- **API security scanner**: Added `auth_required`, `admin_required`, `public_endpoint` to recognized auth patterns
- **Tests**: 13 unit tests for decorator framework (token extraction, auth/admin/public, g attributes)

## Not migrated
- `auth.py` — uses `auth_service` for login/logout business logic (not auth checking)
- `pages.py` — uses `auth_service.get_session()`/`auth_service.logout()` for page rendering

## Testing
- `python3 -c "from app import create_app; create_app()"` — app loads successfully
- `python3 scripts/lint/api_security_scanner.py` — no new violations
- `python3 -m pytest tests/unit/test_auth_decorators.py` — 13/13 passed
- `black` + `ruff` — all clean

Closes #261

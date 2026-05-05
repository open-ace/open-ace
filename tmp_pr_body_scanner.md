## Summary

Implements the API security scanner proposed in issue #255 (Phase 0).

## New Files

| File | Description |
|------|-------------|
| `scripts/lint/api_security_scanner.py` | AST-based scanner detecting routes missing auth |
| `scripts/lint/security_baseline.json` | 130 baseline suppressions for existing routes |

## Detection Rules

| Rule | Description |
|------|-------------|
| SEC001 | Route handler has no authentication (no decorator, no before_request, no inline auth) |
| SEC002 | User resource endpoint with ID param has no ownership check |
| SEC003 | Blueprint has no @before_request auth hook |

## Modified Files

| File | Change |
|------|--------|
| `.github/workflows/ci.yml` | Added `API Security Scan` step in lint job |
| `.pre-commit-config.yaml` | Added `api-security-scan` hook for `app/routes/` |

## Initial Scan Results

130 violations found in existing codebase — all suppressed via baseline. The scanner will **prevent new violations** from being introduced.

Key findings (to be addressed in follow-up PRs):
- `usage.py`, `analysis.py`, `messages.py`, `roi.py`, `tool_accounts.py` — 0 auth on all routes
- `tenant.py`, `compliance.py` — no blueprint-level auth
- `remote.py` — 8 session endpoints lack ownership checks

## Test Plan

- [x] Scanner runs clean against baseline (exit 0)
- [ ] Adding a new route without auth triggers SEC001
- [ ] CI lint job includes the scan step

Closes: #255

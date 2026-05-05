## PR 278: Resolve Security Baseline Violations (130 → 6)

Related to the security scanner baseline cleanup from this issue.

### What changed

This PR resolves **all actionable violations** detected by the `api_security_scanner.py`:

| Rule | Before | After | Fix |
|------|--------|-------|-----|
| SEC001 (no auth) | 102 | 0 | Added `@auth_required` / `@public_endpoint` to 20 routes across 5 files |
| SEC002 (no ownership) | 14 | 6* | Improved scanner ownership detection patterns; 6 are legitimate false positives (ownership in manager classes) |
| SEC003 (no before_request) | 14 | 0 | Route-level auth decorators cover all blueprints |
| **Total** | **130** | **6** | Baseline regenerated with only 6 SEC002 suppressions |

### Additional fixes

- **SQL003**: Fixed LIKE wildcard injection in `workspace.py` session search using `escape_like()` (relates to 246)
- **Black 25/26 compat**: Resolved formatting conflict in SQL string concatenation

### CI

All checks passed — lint (Black 26, isort 8, ruff 0.15), test (Python 3.9–3.12), build.

PR: 278

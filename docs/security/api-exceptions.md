# API Security Scanner Exception Management

## Overview

The API Security Scanner (`scripts/lint/api_security_scanner.py`) detects Flask routes missing authentication or ownership checks. This document describes how to manage exceptions (baseline suppressions) for this scanner.

**Related Issue**: #1897

## Rules

The scanner enforces three rules:

| Rule | Description | Severity |
|------|-------------|----------|
| SEC001 | Route handler has no authentication | High |
| SEC002 | Route with ID parameter lacks ownership check | Medium |
| SEC003 | Blueprint has no `@before_request` auth hook | Medium |

## Managing Exceptions

### Adding a New Exception

1. **Try to fix the code first**. Most violations should be fixed by:
   - Adding `@auth_required` decorator
   - Adding `@security_annotated(reason="...")` for non-standard ownership patterns
   - Implementing proper ownership checks

2. **If exception is necessary**, follow these steps:

   a. Run the scanner to generate the current violations:
   ```bash
   python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
   ```

   b. Edit `security_baseline.json` to add metadata for the new exception:
   ```json
   {
     "key": "SEC002|app/routes/example.py|/api/example/<int:id>",
     "rule": "SEC002",
     "file": "app/routes/example.py",
     "line": 100,
     "endpoint": "/api/example/<int:id>",
     "message": "...",
     "metadata": {
       "owner": "@username",
       "justification": "Business reason for the exception...",
       "reviewed_at": "YYYY-MM-DD",
       "expires_at": "YYYY-MM-DDTHH:MM:SSZ",
       "risk_level": "low|medium|high",
       "test_coverage": "tests/routes/test_example.py::test_example",
       "alternative_controls": ["control1", "control2"]
     }
   }
   ```

   c. Validate the metadata:
   ```bash
   python scripts/lint/validate_baseline_metadata.py
   ```

   d. Create a PR with:
   - The `security_baseline.json` changes
   - A clear explanation in the PR description
   - The `security-exception` label (if available)

### Required Metadata Fields

| Field | Description | Required |
|-------|-------------|----------|
| `owner` | GitHub username responsible for this exception | ✅ |
| `justification` | Business reason why this exception is necessary | ✅ |
| `test_coverage` | Test file that validates the security control | ✅ |
| `reviewed_at` | ISO date when this was last reviewed | Recommended |
| `expires_at` | ISO date when this should be re-reviewed | Recommended |
| `risk_level` | `low`, `medium`, or `high` | Recommended |
| `alternative_controls` | List of other security controls in place | Recommended |

### Removing an Exception

When you fix the code that was suppressed:

1. Run the scanner to verify no new violations:
   ```bash
   python scripts/lint/api_security_scanner.py
   ```

2. Remove the entry from `security_baseline.json`

3. Validate:
   ```bash
   python scripts/lint/validate_baseline_metadata.py
   ```

## Using `@security_annotated` Decorator

For endpoints that have ownership checks implemented in non-standard patterns (inline checks, helper functions, etc.), use the `@security_annotated` decorator to mark them as intentionally secured:

```python
from app.auth.decorators import security_annotated

@security_annotated(reason="Ownership via get_user_project + is_shared flag check")
def api_get_project(project_id):
    # ... existing code with ownership check
```

This decorator:
- Does NOT change runtime behavior
- Marks the endpoint as secured for the scanner
- Suppresses SEC002 violations

## Quarterly Review Process

A GitHub Actions workflow automatically creates a quarterly audit issue:

1. Review all baseline suppressions
2. Check if `expires_at` dates are current
3. Verify `owner` is still valid
4. Confirm `justification` is still accurate
5. Ensure `test_coverage` tests pass
6. Update metadata as needed

## Feature Flags

### Prompt Template Ownership (Issue #1897)

The prompt template ownership checks support a gradual rollout:

```bash
# Phase 1: Log-only mode (no rejection)
ENFORCE_PROMPT_OWNERSHIP=false

# Phase 2: Full enforcement
ENFORCE_PROMPT_OWNERSHIP=true  # (default)
```

## Troubleshooting

### Scanner reports false positives

1. Check if you have `@security_annotated` or `@auth_required` decorators
2. Ensure ownership patterns are recognized (see `OWNERSHIP_PATTERNS` in scanner)
3. If needed, add patterns to `scripts/lint/api_security_scanner.py`

### Metadata validation fails

Common issues:
- Missing required fields
- `expires_at` not in ISO format
- `expires_at` date has passed

### CI blocks PR for security

1. Fix the violation if possible
2. If exception is valid, follow the "Adding a New Exception" process above
3. Use `skip-security-check` label only in emergencies
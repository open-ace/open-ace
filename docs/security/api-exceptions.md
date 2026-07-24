# API Security Exceptions Management

**Issue**: #1897

## Overview

This document describes the process for managing API security baseline suppressions and exceptions in the Open ACE project.

## Security Scanner

The API security scanner (`scripts/lint/api_security_scanner.py`) detects security violations in Flask routes:

- **SEC001**: Route handler has no authentication
- **SEC002**: Route with ID parameter missing ownership check
- **SEC003**: Blueprint missing `@before_request` auth hook

## Baseline File

Security suppressions are stored in `scripts/lint/security_baseline.json`. Each suppression must have complete metadata:

```json
{
  "key": "SEC002|app/routes/workspace.py|/api/workspace/knowledge/<entry_id>",
  "rule": "SEC002",
  "file": "app/routes/workspace.py",
  "line": 2038,
  "endpoint": "/api/workspace/knowledge/<entry_id>",
  "message": "Route ... has ID param(s) ['entry_id'] but no ownership check",
  "metadata": {
    "owner": "@team-backend",
    "justification": "Reason why this suppression is necessary",
    "reviewed_at": "2026-07-23",
    "expires_at": "2027-01-23T00:00:00Z",
    "risk_level": "low",
    "test_coverage": "tests/routes/test_example.py::test_example",
    "alternative_controls": [
      "List of alternative security controls in place"
    ]
  }
}
```

### Required Metadata Fields

- **owner**: GitHub username or team responsible for this suppression
- **justification**: Clear explanation of why the exception is necessary
- **test_coverage**: Test file and test name that validates the security control

### Optional Metadata Fields

- **reviewed_at**: Date of last review (ISO format)
- **expires_at**: Expiration date (ISO format) - CI will fail if expired
- **risk_level**: Risk assessment (low/medium/high)
- **alternative_controls**: List of security controls in place
- **automated_check**: Metadata about automated checks

## Adding a New Suppression

### Step 1: Document the Exception

Before adding a suppression, ensure:

1. You have a valid reason for the exception
2. Alternative security controls are in place (e.g., workspace-level access control)
3. Test coverage exists or will be added

### Step 2: Use `@security_annotated` Decorator

For endpoints with ownership checks implemented in non-standard patterns:

```python
from app.auth.decorators import security_annotated

@workspace_bp.route("/resource/<int:resource_id>", methods=["GET"])
@security_annotated(reason="Ownership via get_user_resource + permission check")
def get_resource(resource_id):
    # Ownership check implemented inline
    resource = get_user_resource(user_id, resource_id)
    if not resource:
        return jsonify({"error": "Access denied"}), 403
    ...
```

### Step 3: Update Baseline

If the scanner still flags the endpoint after adding `@security_annotated`:

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### Step 4: Add Metadata

Edit `scripts/lint/security_baseline.json` and add complete metadata for the new suppression.

### Step 5: Validate

Run metadata validation:

```bash
python scripts/lint/validate_baseline_metadata.py
```

## Removing a Suppression

Suppressions should be removed when:

1. Code is fixed and security issue is resolved
2. Endpoint is removed
3. Alternative security controls are no longer necessary

### Step 1: Fix the Issue

Implement proper security controls or remove the endpoint.

### Step 2: Regenerate Baseline

```bash
python scripts/lint/api_security_scanner.py --baseline > scripts/lint/security_baseline.json
```

### Step 3: Validate

```bash
python scripts/lint/api_security_scanner.py
python scripts/lint/validate_baseline_metadata.py
```

## CI Integration

The security scanner runs in CI for every pull request:

1. **API Security Scanner**: Detects new violations not in baseline
2. **Baseline Diff Check**: Detects changes to baseline (requires justification)
3. **Metadata Validation**: Ensures all suppressions have complete metadata

### Bypassing Security Checks

In emergencies, use the `skip-security-check` label on PRs:

```bash
gh pr edit --add-label skip-security-check
```

⚠️ **Warning**: This should only be used in emergencies and requires review by security team.

## Quarterly Audit

Baseline suppressions are audited quarterly:

1. Check `expires_at` dates - CI will fail if expired
2. Review `owner` and `justification` - ensure they're still accurate
3. Verify `test_coverage` - ensure tests still pass
4. Assess `alternative_controls` - ensure they're still effective

Audit reminders are created automatically via GitHub Actions scheduled workflow.

## Feature Flags

Some security features can be enabled gradually using environment variables:

- `ENFORCE_PROMPT_OWNERSHIP`: Control prompt template ownership enforcement
  - `true` (default): Enforce ownership checks
  - `false`: Log only, do not reject requests (for gradual rollout)

## Security Review Checklist

When reviewing PRs with baseline changes:

- [ ] All new suppressions have complete metadata
- [ ] `owner` is a valid GitHub user/team
- [ ] `justification` clearly explains the exception
- [ ] `test_coverage` exists and tests the security control
- [ ] `expires_at` is set and not too far in the future
- [ ] `alternative_controls` are documented and effective
- [ ] PR description explains why the exception is necessary

## Related Documentation

- [Issue #1897](https://github.com/open-ace/open-ace/issues/1897): API Security Baseline Cleanup
- [API Security Scanner](../../scripts/lint/api_security_scanner.py)
- [Baseline Metadata Validator](../../scripts/lint/validate_baseline_metadata.py)
- [Baseline Diff Checker](../../scripts/lint/baseline_diff.py)

## Contact

For questions about API security exceptions:

- Security team: @security
- Backend team: @team-backend

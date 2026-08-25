# Filter Patterns API Migration Guide

## Overview

The `/api/content/filter/patterns` endpoint is deprecated and will be removed on **February 1, 2027**.

Use the canonical `/api/filter-rules` API instead, which provides:
- Persistent storage to database
- Full CRUD operations (Create, Read, Update, Delete)
- Pagination and filtering
- Input validation
- Idempotent creation

## Deprecation Timeline

| Date | Status |
|------|--------|
| August 2026 | Endpoint deprecated, returns deprecation warning |
| February 2027 | Endpoint returns `410 Gone` |

## Field Mapping

| Old Field (`/patterns`) | New Field (`/filter-rules`) | Notes |
|-------------------------|----------------------------|-------|
| `name` | `description` | Pattern description |
| `pattern` | `pattern` | Regex pattern (required) |
| `risk` | `severity` | `low`, `medium`, `high` |
| - | `type` | `keyword`, `regex`, `pii` (default: `keyword`) |
| - | `action` | `warn`, `block`, `redact` (default: `warn`) |
| - | `is_enabled` | Enable/disable rule (default: `true`) |

## Migration Examples

### Old API (Deprecated)

```bash
# POST /api/content/filter/patterns
curl -X POST https://api.example.com/api/content/filter/patterns \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "API Key Pattern",
    "pattern": "api[_-]?key\\s*[=:]\\s*[a-zA-Z0-9]{16,}",
    "risk": "high"
  }'
```

**Response:**
```json
{
  "success": true,
  "pattern": "API Key Pattern"
}
```

### New API (Recommended)

```bash
# POST /api/filter-rules
curl -X POST https://api.example.com/api/filter-rules \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "pattern": "api[_-]?key\\s*[=:]\\s*[a-zA-Z0-9]{16,}",
    "type": "regex",
    "severity": "high",
    "action": "block",
    "description": "API Key Pattern"
  }'
```

**Response:**
```json
{
  "success": true,
  "id": 42,
  "is_new": true
}
```

## New Features

### Pagination

```bash
GET /api/filter-rules?limit=20&offset=0
```

Response:
```json
{
  "rules": [...],
  "total": 100,
  "limit": 20,
  "offset": 0
}
```

### Filtering

```bash
# Filter by type
GET /api/filter-rules?type=regex

# Filter by severity
GET /api/filter-rules?severity=high

# Filter by enabled status
GET /api/filter-rules?is_enabled=true

# Combined filters
GET /api/filter-rules?type=regex&severity=high&is_enabled=true
```

### Get Single Rule

```bash
GET /api/filter-rules/42
```

Response:
```json
{
  "id": 42,
  "pattern": "api[_-]?key\\s*[=:]\\s*[a-zA-Z0-9]{16,}",
  "type": "regex",
  "severity": "high",
  "action": "block",
  "description": "API Key Pattern",
  "is_enabled": true,
  "created_at": "2026-08-25T10:00:00",
  "updated_at": null
}
```

### Update Rule

```bash
PUT /api/filter-rules/42
```

Request:
```json
{
  "action": "warn",
  "is_enabled": false
}
```

### Delete Rule

```bash
DELETE /api/filter-rules/42
```

## Idempotent Creation

Creating a rule with an existing `pattern` returns the existing rule instead of creating a duplicate:

**First request:**
```json
// Returns 201 Created, is_new: true
{"success": true, "id": 42, "is_new": true}
```

**Second request (same pattern):**
```json
// Returns 200 OK, is_new: false
{"success": true, "id": 42, "is_new": false}
```

## Validation

The new API validates:

1. **Type**: Must be `keyword`, `regex`, or `pii`
2. **Severity**: Must be `low`, `medium`, or `high`
3. **Action**: Must be `warn`, `block`, or `redact`
4. **Regex**: If `type=regex`, pattern must be valid regex
5. **ReDoS**: Regex patterns with nested quantifiers or alternation+quantifier combinations are rejected

Example error response:
```json
{
  "error": "Invalid regex pattern: unterminated group"
}
```

## Questions?

Contact the Open ACE team or open an issue on GitHub.
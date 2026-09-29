# Filter Patterns API Migration Guide — 过滤模式 API 迁移指南

[English](#english) | [中文](#中文)

---

## English

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
| February 2027 | Endpoint returns `410 Gone`. Requires a code change deployed at that time. |

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

> **Note**: Historical behavior — the deprecated endpoint now always returns `success:false` with a deprecation message and no longer persists writes.

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

---

## 中文

## 概述

`/api/content/filter/patterns` 端点已弃用，将于 **2027 年 2 月 1 日**移除。

请改用规范的 `/api/filter-rules` API，它提供：
- 持久化存储到数据库
- 完整的 CRUD 操作（Create、Read、Update、Delete）
- 分页与过滤
- 输入校验
- 幂等创建

## 弃用时间线

| 日期 | 状态 |
|------|--------|
| 2026 年 8 月 | 端点已弃用，返回弃用警告 |
| 2027 年 2 月 | 端点返回 `410 Gone`。届时需要部署对应的代码变更。 |

## 字段映射

| 旧字段（`/patterns`） | 新字段（`/filter-rules`） | 说明 |
|-------------------------|----------------------------|-------|
| `name` | `description` | 模式描述 |
| `pattern` | `pattern` | 正则模式（必填） |
| `risk` | `severity` | `low`、`medium`、`high` |
| - | `type` | `keyword`、`regex`、`pii`（默认：`keyword`） |
| - | `action` | `warn`、`block`、`redact`（默认：`warn`） |
| - | `is_enabled` | 启用/禁用规则（默认：`true`） |

## 迁移示例

### 旧 API（已弃用）

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

> **注**：历史行为——该弃用端点现在固定返回 `success:false` 弃用提示，不再持久化写入。

**响应：**
```json
{
  "success": true,
  "pattern": "API Key Pattern"
}
```

### 新 API（推荐）

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

**响应：**
```json
{
  "success": true,
  "id": 42,
  "is_new": true
}
```

## 新功能

### 分页

```bash
GET /api/filter-rules?limit=20&offset=0
```

响应：
```json
{
  "rules": [...],
  "total": 100,
  "limit": 20,
  "offset": 0
}
```

### 过滤

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

### 获取单条规则

```bash
GET /api/filter-rules/42
```

响应：
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

### 更新规则

```bash
PUT /api/filter-rules/42
```

请求：
```json
{
  "action": "warn",
  "is_enabled": false
}
```

### 删除规则

```bash
DELETE /api/filter-rules/42
```

## 幂等创建

使用已存在的 `pattern` 创建规则时，会返回现有规则，而不是创建重复项：

**第一次请求：**
```json
// Returns 201 Created, is_new: true
{"success": true, "id": 42, "is_new": true}
```

**第二次请求（相同 pattern）：**
```json
// Returns 200 OK, is_new: false
{"success": true, "id": 42, "is_new": false}
```

## 校验

新 API 会校验：

1. **Type**：必须是 `keyword`、`regex` 或 `pii`
2. **Severity**：必须是 `low`、`medium` 或 `high`
3. **Action**：必须是 `warn`、`block` 或 `redact`
4. **Regex**：如果 `type=regex`，pattern 必须是合法的正则表达式
5. **ReDoS**：拒绝包含嵌套量词或“交替+量词”组合的正则模式

错误响应示例：
```json
{
  "error": "Invalid regex pattern: unterminated group"
}
```

## 疑问？

请联系 Open ACE 团队，或在 GitHub 上提交 issue。

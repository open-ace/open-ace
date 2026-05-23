# Open ACE API Documentation

This document describes the REST API endpoints available in Open ACE (AI Computing Explorer).

## Overview

- **Base URL**: `http://localhost:5000/api` (default)
- **Authentication**: Session token via cookie or Bearer token in Authorization header
- **Content-Type**: `application/json` for most endpoints

## Authentication

Most API endpoints require authentication. The session token can be provided via:
- Cookie: `session_token`
- Header: `Authorization: Bearer <token>`

Admin-only endpoints require the user to have the `admin` role.

---

## Authentication API (`/api/auth`)

### Login

```
POST /api/auth/login
```

Authenticate a user and create a session.

**Request Body:**
```json
{
  "username": "string",
  "password": "string"
}
```

**Response:**
```json
{
  "success": true,
  "user": {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin"
  }
}
```

**Status Codes:**
- `200` - Success
- `400` - Missing username or password
- `401` - Invalid credentials

---

### Logout

```
POST /api/auth/logout
```

End the current session.

**Response:**
```json
{
  "success": true
}
```

---

### Check Authentication

```
GET /api/auth/check
```

Check if the current session is valid.

**Response:**
```json
{
  "authenticated": true,
  "user": {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin"
  }
}
```

---

### Get Profile

```
GET /api/auth/profile
```

Get the current user's profile information.

**Response:**
```json
{
  "id": 1,
  "username": "admin",
  "email": "admin@example.com",
  "role": "admin",
  "is_active": true,
  "created_at": "2024-01-01T00:00:00Z"
}
```

---

### Change Password

```
POST /api/auth/change-password
```

Change the current user's password.

**Request Body:**
```json
{
  "current_password": "string",
  "new_password": "string"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Password changed successfully"
}
```

---

## Admin API (`/api/admin`)

All admin endpoints require admin role.

### Get All Users

```
GET /api/admin/users
```

List all users in the system.

**Response:**
```json
[
  {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "is_active": true,
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

---

### Create User

```
POST /api/admin/users
```

Create a new user.

**Request Body:**
```json
{
  "username": "string",
  "email": "string",
  "password": "string",
  "role": "user"  // optional, defaults to "user"
}
```

**Response:**
```json
{
  "success": true,
  "user_id": 2
}
```

**Status Codes:**
- `201` - Created
- `400` - Invalid input or user already exists

---

### Update User

```
PUT /api/admin/users/<user_id>
```

Update a user's information.

**Request Body:**
```json
{
  "username": "string",      // optional
  "email": "string",         // optional
  "role": "string",          // optional
  "is_active": true,         // optional
  "linux_account": "string"  // optional
}
```

---

### Delete User

```
DELETE /api/admin/users/<user_id>
```

Delete a user. Cannot delete yourself.

---

### Update User Password

```
PUT /api/admin/users/<user_id>/password
```

Update a user's password (admin override).

**Request Body:**
```json
{
  "password": "string"
}
```

---

### Update User Quota

```
PUT /api/admin/users/<user_id>/quota
```

Update a user's token/request quotas.

**Request Body:**
```json
{
  "daily_token_quota": 100000,      // optional
  "monthly_token_quota": 1000000,   // optional
  "daily_request_quota": 1000,      // optional
  "monthly_request_quota": 10000    // optional
}
```

---

### Get Quota Usage

```
GET /api/admin/quota/usage
```

Get quota usage for all users.

---

## Usage API (`/api`)

### Get Summary

```
GET /api/summary
```

Get summary statistics for all tools.

**Query Parameters:**
- `host` - Filter by host name (optional)

**Response:**
```json
{
  "total_tokens": 1000000,
  "total_input_tokens": 500000,
  "total_output_tokens": 500000,
  "total_requests": 10000,
  "tools": [
    {
      "tool_name": "claude",
      "tokens": 500000,
      "requests": 5000
    }
  ]
}
```

---

### Refresh Summary

```
POST /api/summary/refresh
```

Refresh summary data from daily_messages table.

**Query Parameters:**
- `host` - Filter by host name (optional)

---

### Get Today's Usage

```
GET /api/today
```

Get today's usage for all tools.

**Query Parameters:**
- `host` - Filter by host name (optional)
- `tool` - Filter by tool name (optional)

---

### Get Tool Usage

```
GET /api/tool/<tool_name>/<days>
```

Get usage for a specific tool over N days.

**Query Parameters:**
- `host` - Filter by host name (optional)

---

### Get Date Usage

```
GET /api/date/<date_str>
```

Get usage for a specific date (format: YYYY-MM-DD).

**Query Parameters:**
- `host` - Filter by host name (optional)
- `tool` - Filter by tool name (optional)

---

### Get Range Usage

```
GET /api/range
```

Get usage for a date range.

**Query Parameters:**
- `start` - Start date (default: 7 days ago)
- `end` - End date (default: today)
- `tool` - Filter by tool name (optional)
- `host` - Filter by host name (optional)

---

### Get Tools List

```
GET /api/tools
```

Get list of all tools.

---

### Get Hosts List

```
GET /api/hosts
```

Get list of all hosts.

---

### Get Trend Data

```
GET /api/trend
```

Get usage trend data for charts.

**Query Parameters:**
- `start` - Start date (default: 30 days ago)
- `end` - End date (default: today)
- `host` - Filter by host name (optional)

---

## Messages API (`/api`)

### Get Messages

```
GET /api/messages
```

Get messages with pagination and filters.

**Query Parameters:**
- `date` - Filter by specific date
- `start_date` - Start date for range
- `end_date` - End date for range
- `tool` - Filter by tool name
- `host` - Filter by host name
- `sender` - Filter by sender name
- `role` - Filter by role (user/assistant)
- `search` - Search in content
- `limit` - Results limit (default: 50)
- `offset` - Results offset (default: 0)

---

### Get Senders

```
GET /api/senders
```

Get list of all senders.

**Query Parameters:**
- `host` - Filter by host name (optional)

---

### Get Conversation History

```
GET /api/conversation-history
```

Get conversation history.

**Query Parameters:**
- `date` - Filter by date
- `tool` - Filter by tool name
- `host` - Filter by host name
- `sender` - Filter by sender name
- `limit` - Results limit
- `offset` - Results offset

---

### Get Conversation Timeline

```
GET /api/conversation-timeline/<session_id>
```

Get timeline of messages for a conversation.

---

### Get Conversation Details

```
GET /api/conversation-details/<session_id>
```

Get details of a specific conversation.

---

### Get Messages Count

```
GET /api/messages/count
```

Get count of messages with filters.

---

## Analysis API (`/api/analysis`)

### Batch Analysis

```
GET /api/analysis/batch
```

Get all analysis data in a single request.

**Query Parameters:**
- `start` - Start date
- `end` - End date
- `host` - Filter by host name

---

### Key Metrics

```
GET /api/analysis/key-metrics
```

Get key metrics for the dashboard.

---

### Hourly Usage

```
GET /api/analysis/hourly-usage
```

Get hourly usage breakdown.

**Query Parameters:**
- `date` - Specific date
- `tool` - Filter by tool name
- `host` - Filter by host name

---

### Daily Hourly Usage

```
GET /api/analysis/daily-hourly-usage
```

Get daily and hourly usage patterns.

---

### Peak Usage

```
GET /api/analysis/peak-usage
```

Get peak usage periods.

---

### User Ranking

```
GET /api/analysis/user-ranking
```

Get user ranking by token usage.

**Query Parameters:**
- `limit` - Number of top users (default: 10)

---

### Conversation Stats

```
GET /api/analysis/conversation-stats
```

Get conversation statistics.

---

### User Segmentation

```
GET /api/analysis/user-segmentation
```

Get user segmentation data.

---

### Tool Comparison

```
GET /api/analysis/tool-comparison
```

Get tool comparison data.

---

### Anomaly Detection

```
GET /api/analysis/anomaly-detection
```

Get anomaly detection results.

**Query Parameters:**
- `type` - Filter by anomaly type
- `severity` - Filter by severity

---

### Anomaly Trend

```
GET /api/analysis/anomaly-trend
```

Get anomaly trend over time.

---

### Recommendations

```
GET /api/analysis/recommendations
```

Get usage optimization recommendations.

---

## Analytics API (`/api/analytics`)

Admin-only endpoints for advanced analytics.

### Usage Report

```
GET /api/analytics/report
```

Generate a comprehensive usage report.

**Query Parameters:**
- `end_date` - End date (default: today)
- `days` - Number of days (default: 30)
- `trends` - Include trends (default: true)
- `anomalies` - Include anomalies (default: true)

---

### Usage Forecast

```
GET /api/analytics/forecast
```

Get usage forecast.

**Query Parameters:**
- `days` - Forecast days (default: 7)

---

### Efficiency Metrics

```
GET /api/analytics/efficiency
```

Get efficiency metrics.

---

### Export Analytics

```
GET /api/analytics/export
```

Export analytics data.

**Query Parameters:**
- `format` - Export format: `json` or `csv` (default: json)
- `days` - Number of days (default: 30)

---

## Governance API (`/api`)

### Audit Logs

```
GET /api/audit/logs
GET /api/audit-logs
GET /api/governance/audit-logs
```

Get audit logs with filters (admin only).

**Query Parameters:**
- `user_id` - Filter by user ID
- `username` - Filter by username
- `action` - Filter by action type
- `resource_type` - Filter by resource type
- `severity` - Filter by severity
- `start_date` - Start date
- `end_date` - End date
- `limit` - Results limit (default: 100)
- `offset` - Results offset (default: 0)

---

### Export Audit Logs

```
GET /api/audit/logs/export
```

Export audit logs (admin only).

**Query Parameters:**
- `start_date` - Start date
- `end_date` - End date
- `format` - Export format: `json` or `csv`

---

### User Activity

```
GET /api/audit/user/<user_id>/activity
```

Get activity summary for a user (admin only).

**Query Parameters:**
- `days` - Number of days (default: 30)

---

### Quota Status

```
GET /api/quota/status
```

Get quota status for current user.

**Query Parameters:**
- `period` - Period type: `daily` or `monthly`

---

### All Quota Status

```
GET /api/quota/status/all
```

Get quota status for all users (admin only).

---

### Check Quota

```
POST /api/quota/check
```

Check if user has quota available.

**Request Body:**
```json
{
  "tokens": 1000,
  "requests": 1
}
```

---

### Quota Alerts

```
GET /api/quota/alerts
```

Get quota alerts (admin only).

---

### Acknowledge Alert

```
POST /api/quota/alerts/<alert_id>/acknowledge
```

Acknowledge a quota alert (admin only).

---

### Content Check

```
POST /api/content/check
```

Check content for sensitive information.

**Request Body:**
```json
{
  "content": "string"
}
```

---

### Filter Stats

```
GET /api/content/filter/stats
```

Get content filter statistics (admin only).

---

### Filter Rules

```
GET /api/filter-rules
```

Get all content filter rules (admin only).

---

### Create Filter Rule

```
POST /api/filter-rules
```

Create a new content filter rule (admin only).

**Request Body:**
```json
{
  "pattern": "string",
  "type": "keyword",       // keyword or regex
  "severity": "medium",    // low, medium, high
  "action": "warn",        // warn, block, review
  "description": "string",
  "is_enabled": true
}
```

---

### Update Filter Rule

```
PUT /api/filter-rules/<rule_id>
```

Update a content filter rule (admin only).

---

### Delete Filter Rule

```
DELETE /api/filter-rules/<rule_id>
```

Delete a content filter rule (admin only).

---

### Security Settings

```
GET /api/security-settings
PUT /api/security-settings
```

Get or update security settings (admin only).

---

## Alerts API (`/api/alerts`)

### List Alerts

```
GET /api/alerts
```

Get alerts with filters.

**Query Parameters:**
- `type` - Filter by alert type
- `severity` - Filter by severity
- `unread_only` - Only unread alerts (default: false)
- `limit` - Results limit (default: 50)
- `offset` - Results offset (default: 0)

---

### Unread Count

```
GET /api/alerts/unread-count
```

Get count of unread alerts.

---

### Mark Alert Read

```
POST /api/alerts/<alert_id>/read
```

Mark an alert as read.

---

### Mark All Read

```
POST /api/alerts/read-all
```

Mark all alerts as read.

---

### Delete Alert

```
DELETE /api/alerts/<alert_id>
```

Delete an alert.

---

### Notification Preferences

```
GET /api/alerts/preferences
PUT /api/alerts/preferences
```

Get or update notification preferences.

---

### Alert Stream (SSE)

```
GET /api/alerts/stream
```

Server-Sent Events stream for real-time alerts.

---

## Compliance API (`/api/compliance`)

Admin-only endpoints for compliance reporting.

### List Report Types

```
GET /api/compliance/reports
```

List available report types.

---

### Generate Report

```
POST /api/compliance/reports
```

Generate a compliance report.

**Request Body:**
```json
{
  "report_type": "usage_summary",
  "period_start": "2024-01-01",
  "period_end": "2024-01-31",
  "format": "json"  // json or csv
}
```

---

### List Saved Reports

```
GET /api/compliance/reports/saved
```

List saved reports.

---

### Get Saved Report

```
GET /api/compliance/reports/<report_id>
```

Get a saved report.

---

### Audit Patterns

```
GET /api/compliance/audit/patterns
```

Analyze audit patterns.

---

### Detect Anomalies

```
GET /api/compliance/audit/anomalies
```

Detect audit anomalies.

---

### User Profile

```
GET /api/compliance/audit/user/<user_id>/profile
```

Get user behavior profile.

---

### Security Score

```
GET /api/compliance/audit/security-score
```

Get security score.

---

### Retention Rules

```
GET /api/compliance/retention/rules
PUT /api/compliance/retention/rules
```

Get or set data retention rules.

---

### Run Cleanup

```
POST /api/compliance/retention/cleanup
```

Run data retention cleanup.

**Query Parameters:**
- `dry_run` - Simulate without deleting (default: false)

---

## ROI API (`/api/roi`)

### Get ROI

```
GET /api/roi
```

Get ROI metrics for a period.

**Query Parameters:**
- `start_date` - Start date
- `end_date` - End date
- `user_id` - Filter by user ID
- `tool_name` - Filter by tool name

---

### ROI Trend

```
GET /api/roi/trend
```

Get ROI trend over months.

---

### ROI by Tool

```
GET /api/roi/by-tool
```

Get ROI breakdown by tool.

---

### ROI by User

```
GET /api/roi/by-user
```

Get ROI breakdown by user.

---

### Cost Breakdown

```
GET /api/roi/cost-breakdown
```

Get detailed cost breakdown.

---

### Daily Costs

```
GET /api/roi/daily-costs
```

Get daily cost data for charting.

---

### ROI Summary

```
GET /api/roi/summary
```

Get ROI summary statistics.

---

### Optimization Suggestions

```
GET /api/optimization/suggestions
```

Get cost optimization suggestions.

---

### Cost Trend

```
GET /api/optimization/cost-trend
```

Get cost trend for optimization analysis.

---

### Efficiency Report

```
GET /api/optimization/efficiency
```

Get efficiency analysis report.

---

## Workspace API (`/api`)

### Prompt Templates

```
GET /api/prompts
POST /api/prompts
GET /api/prompts/<template_id>
PUT /api/prompts/<template_id>
DELETE /api/prompts/<template_id>
POST /api/prompts/<template_id>/render
GET /api/prompts/categories
GET /api/prompts/featured
```

Manage prompt templates.

**Create Prompt Request:**
```json
{
  "name": "string",
  "description": "string",
  "category": "general",
  "content": "string",
  "variables": [],
  "tags": [],
  "is_public": false
}
```

---

### Sessions

```
GET /api/sessions
POST /api/sessions
GET /api/sessions/<session_id>
DELETE /api/sessions/<session_id>
POST /api/sessions/<session_id>/messages
POST /api/sessions/<session_id>/complete
GET /api/sessions/stats
```

Manage agent sessions.

**Create Session Request:**
```json
{
  "tool_name": "string",
  "session_type": "chat",
  "title": "string",
  "host_name": "localhost",
  "context": {},
  "settings": {},
  "model": "string",
  "expires_in_hours": 24
}
```

---

## Tenant API (`/api/tenants`)

Admin-only endpoints for multi-tenant management.

### List Tenants

```
GET /api/tenants
```

List all tenants.

---

### Get Tenant

```
GET /api/tenants/<tenant_id>
GET /api/tenants/slug/<slug>
```

Get tenant by ID or slug.

---

### Create Tenant

```
POST /api/tenants
```

Create a new tenant.

**Request Body:**
```json
{
  "name": "string",
  "slug": "string",
  "plan": "standard",
  "contact_email": "string",
  "contact_name": "string",
  "trial_days": 14
}
```

---

### Update Tenant

```
PUT /api/tenants/<tenant_id>
```

Update tenant information.

---

### Update Quota

```
PUT /api/tenants/<tenant_id>/quota
```

Update tenant quota.

---

### Update Settings

```
PUT /api/tenants/<tenant_id>/settings
```

Update tenant settings.

---

### Suspend Tenant

```
POST /api/tenants/<tenant_id>/suspend
```

Suspend a tenant.

---

### Activate Tenant

```
POST /api/tenants/<tenant_id>/activate
```

Activate a suspended tenant.

---

### Delete Tenant

```
DELETE /api/tenants/<tenant_id>
```

Delete a tenant.

**Query Parameters:**
- `hard` - Hard delete (default: false)

---

### Tenant Usage

```
GET /api/tenants/<tenant_id>/usage
```

Get tenant usage history.

---

### Tenant Stats

```
GET /api/tenants/<tenant_id>/stats
```

Get tenant statistics.

---

### Check Quota

```
POST /api/tenants/<tenant_id>/check-quota
```

Check if tenant has quota available.

---

### Plan Quotas

```
GET /api/tenants/plans
```

Get quota configurations for all plans.

---

## SSO API (`/api/sso`)

### List Providers

```
GET /api/sso/providers
```

List available SSO providers.

---

### Register Provider

```
POST /api/sso/providers
```

Register a new SSO provider (admin only).

---

### Disable Provider

```
DELETE /api/sso/providers/<provider_name>
```

Disable an SSO provider (admin only).

---

### Start Login

```
GET /api/sso/login/<provider_name>
```

Start SSO login flow.

---

### Callback

```
GET /api/sso/callback/<provider_name>
```

Handle SSO callback.

---

### Get Session

```
GET /api/sso/session
```

Get current SSO session info.

---

### Logout

```
DELETE /api/sso/session
```

Logout from SSO session.

---

### User Identities

```
GET /api/sso/identities/<user_id>
DELETE /api/sso/identities/<user_id>/<provider_name>
```

Get or unlink SSO identities.

---

## Upload API (`/api/upload`)

Upload endpoints require `X-Upload-Auth` header with the upload authentication key.

### Upload Usage

```
POST /api/upload/usage
```

Upload usage data.

**Request Body:**
```json
{
  "date": "2024-01-01",
  "tool_name": "claude",
  "tokens_used": 1000,
  "input_tokens": 500,
  "output_tokens": 500,
  "cache_tokens": 0,
  "request_count": 10,
  "models_used": "claude-3",
  "host_name": "localhost"
}
```

---

### Upload Messages

```
POST /api/upload/messages
```

Upload message data.

**Request Body:**
```json
{
  "date": "2024-01-01",
  "tool_name": "claude",
  "messages": [
    {
      "message_id": "uuid",
      "role": "user",
      "content": "string",
      "tokens_used": 100,
      "timestamp": "2024-01-01T00:00:00Z"
    }
  ]
}
```

---

### Batch Upload

```
POST /api/upload/batch
```

Upload batch data (usage and messages).

---

## Fetch API (`/api`)

### Fetch Data

```
POST /api/fetch/data
```

Trigger data collection from all sources.

---

### Fetch Status

```
GET /api/fetch/status
```

Get data fetch status.

---

### Data Status

```
GET /api/data-status
```

Get data status information.

---

## Report API (`/api`)

### My Usage

```
GET /api/report/my-usage
```

Get current user's usage report.

**Query Parameters:**
- `start` - Start date (default: 30 days ago)
- `end` - End date (default: today)

---

## Error Responses

All endpoints return consistent error responses:

```json
{
  "error": "Error message description"
}
```

**Common Status Codes:**
- `400` - Bad Request (invalid input)
- `401` - Unauthorized (authentication required)
- `403` - Forbidden (admin access required)
- `404` - Not Found
- `500` - Internal Server Error

---

## Rate Limiting

API endpoints may be subject to rate limiting based on user quotas. Check quota status via `/api/quota/status`.

---

## WebSocket Support

For real-time alerts, the application supports:
- Server-Sent Events (SSE) via `/api/alerts/stream`
- WebSocket (if Flask-SocketIO is configured) on namespace `/alerts`

---

## Version

Current API version: v1 (implicit in URL structure)

For the latest version information, see the [CHANGELOG.md](../CHANGELOG.md).

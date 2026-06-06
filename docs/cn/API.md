# Open ACE API 文档

本文档描述 Open ACE (AI Computing Explorer) 中可用的 REST API 端点。

## 概述

- **Base URL**: `http://localhost:5000/api`（默认）
- **认证方式**: 通过 Cookie 的 session token 或 Authorization 头中的 Bearer token
- **Content-Type**: 大多数端点使用 `application/json`

## 认证

大多数 API 端点需要认证。Session token 可以通过以下方式提供：
- Cookie: `session_token`
- Header: `Authorization: Bearer <token>`

仅管理员可用的端点需要用户具有 `admin` 角色。

---

## 认证 API (`/api/auth`)

### 登录

```
POST /api/auth/login
```

认证用户并创建会话。

**请求体：**
```json
{
  "username": "string",
  "password": "string"
}
```

**响应：**
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

**状态码：**
- `200` - 成功
- `400` - 缺少用户名或密码
- `401` - 凭证无效

---

### 登出

```
POST /api/auth/logout
```

结束当前会话。

**响应：**
```json
{
  "success": true
}
```

---

### 检查认证状态

```
GET /api/auth/check
```

检查当前会话是否有效。

**响应：**
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

### 获取个人资料

```
GET /api/auth/profile
```

获取当前用户的个人资料信息。

**响应：**
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

### 修改密码

```
POST /api/auth/change-password
```

修改当前用户的密码。

**请求体：**
```json
{
  "current_password": "string",
  "new_password": "string"
}
```

**响应：**
```json
{
  "success": true,
  "message": "Password changed successfully"
}
```

---

## 管理员 API (`/api/admin`)

所有管理员端点需要 admin 角色。

### 获取所有用户

```
GET /api/admin/users
```

列出系统中所有用户。

**响应：**
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

### 创建用户

```
POST /api/admin/users
```

创建新用户。

**请求体：**
```json
{
  "username": "string",
  "email": "string",
  "password": "string",
  "role": "user"  // 可选，默认为 "user"
}
```

**响应：**
```json
{
  "success": true,
  "user_id": 2
}
```

**状态码：**
- `201` - 创建成功
- `400` - 输入无效或用户已存在

---

### 更新用户

```
PUT /api/admin/users/<user_id>
```

更新用户信息。

**请求体：**
```json
{
  "username": "string",      // 可选
  "email": "string",         // 可选
  "role": "string",          // 可选
  "is_active": true,         // 可选
  "linux_account": "string"  // 可选
}
```

---

### 删除用户

```
DELETE /api/admin/users/<user_id>
```

删除用户。不能删除自己。

---

### 更新用户密码

```
PUT /api/admin/users/<user_id>/password
```

更新用户密码（管理员覆盖）。

**请求体：**
```json
{
  "password": "string"
}
```

---

### 更新用户配额

```
PUT /api/admin/users/<user_id>/quota
```

更新用户的 token/请求配额。

**请求体：**
```json
{
  "daily_token_quota": 100000,      // 可选
  "monthly_token_quota": 1000000,   // 可选
  "daily_request_quota": 1000,      // 可选
  "monthly_request_quota": 10000    // 可选
}
```

---

### 获取配额使用情况

```
GET /api/admin/quota/usage
```

获取所有用户的配额使用情况。

---

## 使用统计 API (`/api`)

### 获取摘要

```
GET /api/summary
```

获取所有工具的汇总统计数据。

**查询参数：**
- `host` - 按主机名筛选（可选）

**响应：**
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

### 刷新摘要

```
POST /api/summary/refresh
```

从 daily_messages 表刷新摘要数据。

**查询参数：**
- `host` - 按主机名筛选（可选）

---

### 获取今日用量

```
GET /api/today
```

获取今日所有工具的使用量。

**查询参数：**
- `host` - 按主机名筛选（可选）
- `tool` - 按工具名筛选（可选）

---

### 获取工具用量

```
GET /api/tool/<tool_name>/<days>
```

获取指定工具在过去 N 天的使用量。

**查询参数：**
- `host` - 按主机名筛选（可选）

---

### 获取指定日期用量

```
GET /api/date/<date_str>
```

获取指定日期的使用量（格式：YYYY-MM-DD）。

**查询参数：**
- `host` - 按主机名筛选（可选）
- `tool` - 按工具名筛选（可选）

---

### 获取日期范围用量

```
GET /api/range
```

获取指定日期范围的使用量。

**查询参数：**
- `start` - 开始日期（默认：7 天前）
- `end` - 结束日期（默认：今天）
- `tool` - 按工具名筛选（可选）
- `host` - 按主机名筛选（可选）

---

### 获取工具列表

```
GET /api/tools
```

获取所有工具列表。

---

### 获取主机列表

```
GET /api/hosts
```

获取所有主机列表。

---

### 获取趋势数据

```
GET /api/trend
```

获取用于图表的使用趋势数据。

**查询参数：**
- `start` - 开始日期（默认：30 天前）
- `end` - 结束日期（默认：今天）
- `host` - 按主机名筛选（可选）

---

## 消息 API (`/api`)

### 获取消息

```
GET /api/messages
```

获取消息列表，支持分页和筛选。

**查询参数：**
- `date` - 按指定日期筛选
- `start_date` - 范围起始日期
- `end_date` - 范围结束日期
- `tool` - 按工具名筛选
- `host` - 按主机名筛选
- `sender` - 按发送者筛选
- `role` - 按角色筛选（user/assistant）
- `search` - 在内容中搜索
- `limit` - 结果数量限制（默认：50）
- `offset` - 结果偏移量（默认：0）

---

### 获取发送者列表

```
GET /api/senders
```

获取所有发送者列表。

**查询参数：**
- `host` - 按主机名筛选（可选）

---

### 获取对话历史

```
GET /api/conversation-history
```

获取对话历史。

**查询参数：**
- `date` - 按日期筛选
- `tool` - 按工具名筛选
- `host` - 按主机名筛选
- `sender` - 按发送者筛选
- `limit` - 结果数量限制
- `offset` - 结果偏移量

---

### 获取对话时间线

```
GET /api/conversation-timeline/<session_id>
```

获取对话的消息时间线。

---

### 获取对话详情

```
GET /api/conversation-details/<session_id>
```

获取指定对话的详细信息。

---

### 获取消息计数

```
GET /api/messages/count
```

获取符合筛选条件的消息数量。

---

## 分析 API (`/api/analysis`)

### 批量分析

```
GET /api/analysis/batch
```

在单个请求中获取所有分析数据。

**查询参数：**
- `start` - 开始日期
- `end` - 结束日期
- `host` - 按主机名筛选

---

### 关键指标

```
GET /api/analysis/key-metrics
```

获取仪表盘的关键指标。

---

### 每小时用量

```
GET /api/analysis/hourly-usage
```

获取每小时使用量明细。

**查询参数：**
- `date` - 指定日期
- `tool` - 按工具名筛选
- `host` - 按主机名筛选

---

### 每日每小时用量

```
GET /api/analysis/daily-hourly-usage
```

获取每日和每小时使用模式。

---

### 峰值用量

```
GET /api/analysis/peak-usage
```

获取峰值使用时段。

---

### 用户排名

```
GET /api/analysis/user-ranking
```

按 token 使用量获取用户排名。

**查询参数：**
- `limit` - 前N名用户数量（默认：10）

---

### 对话统计

```
GET /api/analysis/conversation-stats
```

获取对话统计数据。

---

### 用户分群

```
GET /api/analysis/user-segmentation
```

获取用户分群数据。

---

### 工具对比

```
GET /api/analysis/tool-comparison
```

获取工具对比数据。

---

### 异常检测

```
GET /api/analysis/anomaly-detection
```

获取异常检测结果。

**查询参数：**
- `type` - 按异常类型筛选
- `severity` - 按严重程度筛选

---

### 异常趋势

```
GET /api/analysis/anomaly-trend
```

获取异常趋势变化。

---

### 优化建议

```
GET /api/analysis/recommendations
```

获取使用优化建议。

---

## 高级分析 API (`/api/analytics`)

仅管理员可用的高级分析端点。

### 使用报告

```
GET /api/analytics/report
```

生成综合使用报告。

**查询参数：**
- `end_date` - 结束日期（默认：今天）
- `days` - 天数（默认：30）
- `trends` - 包含趋势（默认：true）
- `anomalies` - 包含异常（默认：true）

---

### 用量预测

```
GET /api/analytics/forecast
```

获取用量预测。

**查询参数：**
- `days` - 预测天数（默认：7）

---

### 效率指标

```
GET /api/analytics/efficiency
```

获取效率指标。

---

### 导出分析数据

```
GET /api/analytics/export
```

导出分析数据。

**查询参数：**
- `format` - 导出格式：`json` 或 `csv`（默认：json）
- `days` - 天数（默认：30）

---

## 治理 API (`/api`)

### 审计日志

```
GET /api/audit/logs
GET /api/audit-logs
GET /api/governance/audit-logs
```

获取审计日志，支持筛选（仅管理员）。

**查询参数：**
- `user_id` - 按用户 ID 筛选
- `username` - 按用户名筛选
- `action` - 按操作类型筛选
- `resource_type` - 按资源类型筛选
- `severity` - 按严重程度筛选
- `start_date` - 开始日期
- `end_date` - 结束日期
- `limit` - 结果数量限制（默认：100）
- `offset` - 结果偏移量（默认：0）

---

### 导出审计日志

```
GET /api/audit/logs/export
```

导出审计日志（仅管理员）。

**查询参数：**
- `start_date` - 开始日期
- `end_date` - 结束日期
- `format` - 导出格式：`json` 或 `csv`

---

### 用户活动

```
GET /api/audit/user/<user_id>/activity
```

获取用户活动摘要（仅管理员）。

**查询参数：**
- `days` - 天数（默认：30）

---

### 配额状态

```
GET /api/quota/status
```

获取当前用户的配额状态。

**查询参数：**
- `period` - 周期类型：`daily` 或 `monthly`

---

### 所有用户配额状态

```
GET /api/quota/status/all
```

获取所有用户的配额状态（仅管理员）。

---

### 检查配额

```
POST /api/quota/check
```

检查用户是否有可用配额。

**请求体：**
```json
{
  "tokens": 1000,
  "requests": 1
}
```

---

### 配额告警

```
GET /api/quota/alerts
```

获取配额告警（仅管理员）。

---

### 确认告警

```
POST /api/quota/alerts/<alert_id>/acknowledge
```

确认配额告警（仅管理员）。

---

### 内容检查

```
POST /api/content/check
```

检查内容中是否包含敏感信息。

**请求体：**
```json
{
  "content": "string"
}
```

---

### 过滤器统计

```
GET /api/content/filter/stats
```

获取内容过滤器统计信息（仅管理员）。

---

### 过滤规则列表

```
GET /api/filter-rules
```

获取所有内容过滤规则（仅管理员）。

---

### 创建过滤规则

```
POST /api/filter-rules
```

创建新的内容过滤规则（仅管理员）。

**请求体：**
```json
{
  "pattern": "string",
  "type": "keyword",       // keyword 或 regex
  "severity": "medium",    // low, medium, high
  "action": "warn",        // warn, block, review
  "description": "string",
  "is_enabled": true
}
```

---

### 更新过滤规则

```
PUT /api/filter-rules/<rule_id>
```

更新内容过滤规则（仅管理员）。

---

### 删除过滤规则

```
DELETE /api/filter-rules/<rule_id>
```

删除内容过滤规则（仅管理员）。

---

### 安全设置

```
GET /api/security-settings
PUT /api/security-settings
```

获取或更新安全设置（仅管理员）。

---

## 告警 API (`/api/alerts`)

### 告警列表

```
GET /api/alerts
```

获取告警列表，支持筛选。

**查询参数：**
- `type` - 按告警类型筛选
- `severity` - 按严重程度筛选
- `unread_only` - 仅未读告警（默认：false）
- `limit` - 结果数量限制（默认：50）
- `offset` - 结果偏移量（默认：0）

---

### 未读数量

```
GET /api/alerts/unread-count
```

获取未读告警数量。

---

### 标记已读

```
POST /api/alerts/<alert_id>/read
```

标记告警为已读。

---

### 全部标记已读

```
POST /api/alerts/read-all
```

标记所有告警为已读。

---

### 删除告警

```
DELETE /api/alerts/<alert_id>
```

删除告警。

---

### 通知偏好设置

```
GET /api/alerts/preferences
PUT /api/alerts/preferences
```

获取或更新通知偏好设置。

---

### 告警推送流（SSE）

```
GET /api/alerts/stream
```

Server-Sent Events 实时告警推送流。

---

## 合规 API (`/api/compliance`)

仅管理员可用的合规报告端点。

### 报告类型列表

```
GET /api/compliance/reports
```

列出可用的报告类型。

---

### 生成报告

```
POST /api/compliance/reports
```

生成合规报告。

**请求体：**
```json
{
  "report_type": "usage_summary",
  "period_start": "2024-01-01",
  "period_end": "2024-01-31",
  "format": "json"  // json 或 csv
}
```

---

### 已保存报告列表

```
GET /api/compliance/reports/saved
```

列出已保存的报告。

---

### 获取已保存报告

```
GET /api/compliance/reports/<report_id>
```

获取已保存的报告。

---

### 审计模式分析

```
GET /api/compliance/audit/patterns
```

分析审计模式。

---

### 检测异常

```
GET /api/compliance/audit/anomalies
```

检测审计异常。

---

### 用户画像

```
GET /api/compliance/audit/user/<user_id>/profile
```

获取用户行为画像。

---

### 安全评分

```
GET /api/compliance/audit/security-score
```

获取安全评分。

---

### 数据保留规则

```
GET /api/compliance/retention/rules
PUT /api/compliance/retention/rules
```

获取或设置数据保留规则。

---

### 执行清理

```
POST /api/compliance/retention/cleanup
```

执行数据保留清理。

**查询参数：**
- `dry_run` - 模拟执行，不实际删除（默认：false）

---

## 投资回报 API (`/api/roi`)

### 获取 ROI

```
GET /api/roi
```

获取指定时段的 ROI 指标。

**查询参数：**
- `start_date` - 开始日期
- `end_date` - 结束日期
- `user_id` - 按用户 ID 筛选
- `tool_name` - 按工具名筛选

---

### ROI 趋势

```
GET /api/roi/trend
```

获取月度 ROI 趋势。

---

### 按工具 ROI

```
GET /api/roi/by-tool
```

获取按工具分类的 ROI 明细。

---

### 按用户 ROI

```
GET /api/roi/by-user
```

获取按用户分类的 ROI 明细。

---

### 成本明细

```
GET /api/roi/cost-breakdown
```

获取详细的成本明细。

---

### 每日成本

```
GET /api/roi/daily-costs
```

获取每日成本数据（用于图表）。

---

### ROI 摘要

```
GET /api/roi/summary
```

获取 ROI 汇总统计。

---

### 优化建议

```
GET /api/optimization/suggestions
```

获取成本优化建议。

---

### 成本趋势

```
GET /api/optimization/cost-trend
```

获取成本趋势用于优化分析。

---

### 效率报告

```
GET /api/optimization/efficiency
```

获取效率分析报告。

---

## 工作区 API (`/api`)

### 提示词模板

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

管理提示词模板。

**创建提示词请求：**
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

### 会话管理

```
GET /api/sessions
POST /api/sessions
GET /api/sessions/<session_id>
DELETE /api/sessions/<session_id>
POST /api/sessions/<session_id>/messages
POST /api/sessions/<session_id>/complete
GET /api/sessions/stats
```

管理代理会话。

**创建会话请求：**
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

## 租户 API (`/api/tenants`)

仅管理员可用的多租户管理端点。

### 租户列表

```
GET /api/tenants
```

列出所有租户。

---

### 获取租户

```
GET /api/tenants/<tenant_id>
GET /api/tenants/slug/<slug>
```

按 ID 或 slug 获取租户信息。

---

### 创建租户

```
POST /api/tenants
```

创建新租户。

**请求体：**
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

### 更新租户

```
PUT /api/tenants/<tenant_id>
```

更新租户信息。

---

### 更新租户配额

```
PUT /api/tenants/<tenant_id>/quota
```

更新租户配额。

---

### 更新租户设置

```
PUT /api/tenants/<tenant_id>/settings
```

更新租户设置。

---

### 暂停租户

```
POST /api/tenants/<tenant_id>/suspend
```

暂停租户。

---

### 激活租户

```
POST /api/tenants/<tenant_id>/activate
```

激活已暂停的租户。

---

### 删除租户

```
DELETE /api/tenants/<tenant_id>
```

删除租户。

**查询参数：**
- `hard` - 硬删除（默认：false）

---

### 租户用量

```
GET /api/tenants/<tenant_id>/usage
```

获取租户使用历史。

---

### 租户统计

```
GET /api/tenants/<tenant_id>/stats
```

获取租户统计数据。

---

### 检查租户配额

```
POST /api/tenants/<tenant_id>/check-quota
```

检查租户是否有可用配额。

---

### 套餐配额

```
GET /api/tenants/plans
```

获取所有套餐的配额配置。

---

## SSO API (`/api/sso`)

### SSO 提供商列表

```
GET /api/sso/providers
```

列出可用的 SSO 提供商。

---

### 注册 SSO 提供商

```
POST /api/sso/providers
```

注册新的 SSO 提供商（仅管理员）。

---

### 禁用 SSO 提供商

```
DELETE /api/sso/providers/<provider_name>
```

禁用 SSO 提供商（仅管理员）。

---

### 发起 SSO 登录

```
GET /api/sso/login/<provider_name>
```

发起 SSO 登录流程。

---

### SSO 回调

```
GET /api/sso/callback/<provider_name>
```

处理 SSO 回调。

---

### 获取 SSO 会话

```
GET /api/sso/session
```

获取当前 SSO 会话信息。

---

### SSO 登出

```
DELETE /api/sso/session
```

退出 SSO 会话。

---

### 用户身份

```
GET /api/sso/identities/<user_id>
DELETE /api/sso/identities/<user_id>/<provider_name>
```

获取或解绑 SSO 身份。

---

## 数据上传 API (`/api/upload`)

上传端点需要在请求头中包含 `X-Upload-Auth` 认证密钥。

### 上传使用数据

```
POST /api/upload/usage
```

上传使用数据。

**请求体：**
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

### 上传消息

```
POST /api/upload/messages
```

上传消息数据。

**请求体：**
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

### 批量上传

```
POST /api/upload/batch
```

批量上传数据（使用量和消息）。

---

## 数据采集 API (`/api`)

### 触发数据采集

```
POST /api/fetch/data
```

触发从所有数据源采集数据。

---

### 采集状态

```
GET /api/fetch/status
```

获取数据采集状态。

---

### 数据状态

```
GET /api/data-status
```

获取数据状态信息。

---

## 报告 API (`/api`)

### 我的用量

```
GET /api/report/my-usage
```

获取当前用户的使用报告。

**查询参数：**
- `start` - 开始日期（默认：30 天前）
- `end` - 结束日期（默认：今天）

---

## 错误响应

所有端点返回一致的错误响应：

```json
{
  "error": "Error message description"
}
```

**常见状态码：**
- `400` - 请求错误（输入无效）
- `401` - 未授权（需要认证）
- `403` - 禁止访问（需要管理员权限）
- `404` - 未找到
- `500` - 服务器内部错误

---

## 速率限制

API 端点可能受用户配额的速率限制。通过 `/api/quota/status` 检查配额状态。

---

## WebSocket 支持

对于实时告警，应用支持：
- Server-Sent Events (SSE) 通过 `/api/alerts/stream`
- WebSocket（如果配置了 Flask-SocketIO），命名空间为 `/alerts`

---

## 版本

当前 API 版本：v1（隐含在 URL 结构中）

获取最新版本信息，请参阅 [CHANGELOG.md](https://github.com/open-ace/open-ace/blob/main/CHANGELOG.md)。

# 数据库模式

Open ACE 同时支持 SQLite（单机）和 PostgreSQL（生产环境）。模式包含 44 张表 + 1 个物化视图（完整列表见 schema-postgres.sql，以下为常用表）。

参考文件：`schema/schema-postgres.sql`

## 用户与认证

### users

核心用户表，支持基于角色的访问控制。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| username | varchar | UNIQUE, NOT NULL |
| password_hash | varchar | bcrypt（12 轮） |
| email | varchar | |
| is_admin | boolean | DEFAULT false |
| is_active | boolean | DEFAULT true |
| role | varchar | CHECK IN ('admin','manager','user') |
| daily_token_quota | integer | |
| monthly_token_quota | integer | |
| daily_request_quota | integer | |
| monthly_request_quota | integer | |
| tenant_id | integer | FK → tenants(id) ON DELETE SET NULL |
| must_change_password | boolean | DEFAULT false |
| system_account | text | 多用户模式下的 OS 用户名 |
| deleted_at | timestamp | 软删除 |
| avatar_url | varchar(500) | 头像 URL |

索引：`idx_users_active`, `idx_users_deleted`, `idx_users_email`, `idx_users_role`, `idx_users_tenant`

### sessions

基于 token 的认证会话。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| token | varchar | UNIQUE, NOT NULL |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| created_at | timestamp | |
| expires_at | timestamp | NOT NULL |
| is_active | boolean | DEFAULT true |

索引：`idx_sessions_active`, `idx_sessions_expires`, `idx_sessions_token`, `idx_sessions_user_id`

### web_user_auth_sessions

Web UI 认证会话。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| session_token | text | UNIQUE |
| created_at | timestamp | |
| expires_at | timestamp | |

### user_tool_accounts

将系统账户映射到不同 AI 工具的平台用户。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| tool_account | varchar(255) | UNIQUE |
| tool_type | varchar(50) | |
| description | varchar(255) | |

### user_daily_stats

按用户预聚合的每日使用量，用于优化查询。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | |
| requests | integer | DEFAULT 0 |
| tokens | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |

唯一约束：`(user_id, date)`

## 消息与会话

### daily_messages

核心消息表 — 所有 AI 交互的主数据存储。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| date | varchar | NOT NULL |
| tool_name | varchar | NOT NULL |
| host_name | varchar | DEFAULT 'localhost' |
| message_id | varchar | NOT NULL |
| parent_id | varchar | |
| role | varchar | NOT NULL (user/assistant/system) |
| content | text | |
| full_entry | text | |
| tokens_used | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| model | varchar | |
| timestamp | timestamp | |
| sender_id | varchar | |
| sender_name | varchar | |
| message_source | varchar | |
| conversation_id | varchar | |
| agent_session_id | varchar | |
| user_id | integer | |
| project_path | text | |

唯一约束：`(date, tool_name, message_id, host_name)`。18 个索引覆盖各种查询模式。

### agent_sessions

AI 代理会话追踪。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| session_id | text | UNIQUE |
| session_type | text | DEFAULT 'chat' |
| title | text | |
| tool_name | text | NOT NULL |
| host_name | text | DEFAULT 'localhost' |
| user_id | integer | |
| status | text | DEFAULT 'active' |
| total_tokens | integer | DEFAULT 0 |
| total_input_tokens | integer | DEFAULT 0 |
| total_output_tokens | integer | DEFAULT 0 |
| message_count | integer | DEFAULT 0 |
| model | text | |
| project_id | integer | |
| project_path | varchar(500) | |
| context | text | 会话上下文 |
| settings | text | 会话设置 |
| tags | text | 标签 |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |
| completed_at | timestamp | 完成时间 |
| expires_at | timestamp | 过期时间 |
| request_count | integer | DEFAULT 0，请求计数 |
| workspace_type | text | DEFAULT 'local'，工作区类型 |
| remote_machine_id | text | 关联远程机器 ID |
| paused_at | timestamp | 暂停时间 |

### session_messages

代理会话中的消息。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| session_id | text | FK → agent_sessions(session_id) |
| role | text | NOT NULL |
| content | text | |
| tokens_used | integer | DEFAULT 0 |
| model | text | |
| timestamp | timestamp | |
| metadata | text | |

## 统计

### daily_stats

按工具/主机/发送者聚合的每日统计。

| 列名 | 类型 | 说明 |
|------|------|------|
| date | varchar(10) | NOT NULL |
| tool_name | varchar(50) | NOT NULL |
| host_name | varchar(100) | DEFAULT 'localhost' |
| sender_name | varchar(100) | |
| total_tokens | bigint | NOT NULL |
| total_input_tokens | bigint | NOT NULL |
| total_output_tokens | bigint | NOT NULL |
| message_count | integer | NOT NULL |
| project_id | integer | |
| project_path | varchar(500) | |

唯一约束：`(date, tool_name, host_name, sender_name)`

### hourly_stats

按小时的使用量明细。

| 列名 | 类型 | 说明 |
|------|------|------|
| date | varchar(10) | NOT NULL |
| hour | integer | NOT NULL |
| tool_name | varchar(50) | NOT NULL |
| host_name | varchar(100) | DEFAULT 'localhost' |
| total_tokens | bigint | NOT NULL |
| total_input_tokens | bigint | NOT NULL |
| total_output_tokens | bigint | NOT NULL |
| message_count | integer | NOT NULL |

唯一约束：`(date, hour, tool_name, host_name)`

### daily_usage

带缓存 token 追踪的每日使用量。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1；租户级用量聚合键 |
| date | date | NOT NULL |
| tool_name | varchar | NOT NULL |
| host_name | varchar | DEFAULT 'localhost' |
| tokens_used | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |
| request_count | integer | DEFAULT 0 |
| models_used | text | |

唯一约束：`(tenant_id, date, tool_name, host_name)`

索引：`idx_usage_date`、`idx_usage_date_tool_host(tenant_id, date, tool_name, host_name)`、`idx_usage_tenant_date`

### usage_summary

按工具/主机汇总的仪表盘数据。

| 列名 | 类型 | 说明 |
|------|------|------|
| tool_name | varchar(50) | NOT NULL |
| host_name | varchar(100) | |
| days_count | integer | NOT NULL |
| total_tokens | bigint | NOT NULL |
| avg_tokens | bigint | NOT NULL |
| total_requests | integer | NOT NULL |
| total_input_tokens | bigint | NOT NULL |
| total_output_tokens | bigint | NOT NULL |
| first_date | varchar(10) | |
| last_date | varchar(10) | |

唯一约束：`(tool_name, host_name)`

### session_stats (物化视图)

从 daily_messages 中 agent_session_id IS NOT NULL 的记录聚合。提供会话级别的 token 计数、消息计数和时间戳范围。

## 多租户

### tenants

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| name | text | NOT NULL |
| slug | text | UNIQUE |
| status | text | CHECK IN ('active','suspended','trial','inactive') |
| plan | text | CHECK IN ('free','standard','premium','enterprise') |
| contact_email | text | |
| user_count | integer | DEFAULT 0 |
| total_tokens_used | integer | DEFAULT 0 |
| deleted_at | timestamp | 软删除 |

### tenant_settings (与 tenants 1:1)

| 列名 | 类型 | 说明 |
|------|------|------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| content_filter_enabled | boolean | DEFAULT true |
| audit_log_enabled | boolean | DEFAULT true |
| audit_log_retention_days | integer | DEFAULT 90 |
| data_retention_days | integer | DEFAULT 365 |
| sso_enabled | boolean | DEFAULT false |

### tenant_quotas (与 tenants 1:1)

| 列名 | 类型 | 说明 |
|------|------|------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| daily_token_limit | integer | DEFAULT 1,000,000 |
| monthly_token_limit | integer | DEFAULT 30,000,000 |
| max_users | integer | DEFAULT 100 |
| max_sessions_per_user | integer | DEFAULT 5 |

### tenant_usage

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| tenant_id | integer | FK → tenants(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| tokens_used | integer | DEFAULT 0 |
| requests_made | integer | DEFAULT 0 |
| active_users | integer | DEFAULT 0 |

唯一约束：`(tenant_id, date)`

## SSO

### sso_providers

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| name | text | UNIQUE |
| provider_type | text | NOT NULL (oauth2/oidc/saml) |
| config | text | NOT NULL（JSON；保存的是加密后的 `client_secret_encrypted`，不是明文 `client_secret`） |
| tenant_id | integer | FK → tenants(id) |
| is_active | boolean | DEFAULT true |

### sso_identities

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| provider_user_id | text | NOT NULL |

唯一约束：`(provider_name, provider_user_id)`

### sso_sessions

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| session_token | text | UNIQUE |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| access_token | text | |
| refresh_token | text | |
| expires_at | timestamp | |

## 治理与合规

### audit_logs

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| user_id | integer | |
| tenant_id | integer | 尽可能从操作者解析，用于租户级审计查询 |
| username | text | |
| action | text | NOT NULL |
| severity | text | DEFAULT 'info' |
| resource_type | text | |
| resource_id | text | |
| details | text | |
| ip_address | text | |
| success | boolean | DEFAULT true |

索引：`idx_audit_timestamp`、`idx_audit_user_id`、`idx_audit_tenant_id`、`idx_audit_action`、`idx_audit_severity`

### content_filter_rules

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| pattern | text | NOT NULL |
| type | text | DEFAULT 'keyword' |
| severity | text | DEFAULT 'medium' |
| action | text | DEFAULT 'warn' |
| is_enabled | boolean | DEFAULT true |

### security_settings

安全配置的键值存储。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| setting_key | varchar(100) | UNIQUE |
| setting_value | text | |
| description | text | |

### anomaly_status

异常状态追踪表。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| anomaly_type | varchar | 异常类型 |
| affected_users_hash | varchar | 受影响用户哈希 |
| status | varchar | 处理状态 |
| processed_by | integer | 处理人 ID |
| processed_at | timestamp | 处理时间 |
| created_at | timestamp | 创建时间 |

### insights_reports

AI 生成的使用洞察报告。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| user_id | integer | 用户 ID |
| start_date | varchar | 开始日期 |
| end_date | varchar | 结束日期 |
| overall_score | integer | 总体评分 |
| overall_assessment | text | 总体评估 |
| strengths | text | 优势（JSON） |
| areas_for_improvement | text | 改进建议（JSON） |
| suggestions | text | 建议（JSON） |
| usage_summary | text | 使用摘要（JSON） |
| model | varchar | 使用的模型 |
| raw_response | text | 原始 AI 响应 |
| created_at | timestamp | 创建时间 |

## 告警与配额

### alerts

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| alert_id | text | UNIQUE |
| alert_type | text | NOT NULL |
| severity | text | NOT NULL |
| title | text | NOT NULL |
| message | text | |
| user_id | integer | |
| read | boolean | DEFAULT false |

### quota_usage

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| period | text | DEFAULT 'daily' |
| tokens_used | integer | DEFAULT 0 |
| requests_used | integer | DEFAULT 0 |

唯一约束：`(user_id, date, period)`

### quota_alerts

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| alert_type | text | NOT NULL |
| quota_type | text | NOT NULL |
| threshold | real | NOT NULL |
| current_usage | integer | NOT NULL |
| quota_limit | integer | NOT NULL |
| percentage | real | NOT NULL |
| acknowledged | boolean | DEFAULT false |

### notification_preferences

| 列名 | 类型 | 说明 |
|------|------|------|
| user_id | integer | PK |
| email_enabled | boolean | DEFAULT true |
| push_enabled | boolean | DEFAULT true |
| min_severity | text | DEFAULT 'warning' |

## 工作区与项目

### projects

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1；用于按租户限定项目查找和唯一性 |
| path | varchar(500) | 对活动项目按 `(tenant_id, path)` 唯一 |
| name | varchar(200) | |
| description | text | |
| created_by | integer | |
| is_active | boolean | DEFAULT true |
| is_shared | boolean | DEFAULT false |

索引：`idx_projects_created_by`、`idx_projects_is_active`、`idx_projects_path(tenant_id, path)`、`idx_projects_tenant_created_by`

### user_projects

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| user_id | integer | NOT NULL |
| project_id | integer | NOT NULL |
| total_sessions | integer | DEFAULT 0 |
| total_tokens | bigint | DEFAULT 0 |
| total_requests | integer | DEFAULT 0 |

唯一约束：`(user_id, project_id)`

### prompt_templates

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| name | text | NOT NULL |
| category | text | DEFAULT 'general' |
| content | text | NOT NULL |
| variables | text | |
| tags | text | |
| author_id | integer | |
| is_public | boolean | DEFAULT false |
| is_featured | boolean | DEFAULT false |
| use_count | integer | DEFAULT 0 |

## 远程工作区

### remote_machines

远程工作区机器注册表。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| machine_id | text | 机器唯一标识 |
| machine_name | text | 机器名称 |
| hostname | text | 主机名 |
| os_type | text | 操作系统类型 |
| os_version | text | 操作系统版本 |
| ip_address | text | IP 地址 |
| status | text | 状态 |
| agent_version | text | Agent 版本 |
| capabilities | text | 能力（JSON） |
| cli_path | text | CLI 路径 |
| work_dir | text | 工作目录 |
| tenant_id | integer | 租户 ID |
| created_by | integer | 创建者 ID |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |
| last_heartbeat | timestamp | 最后心跳时间 |

### machine_assignments

用户与远程机器的分配关系。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| machine_id | text | 机器 ID |
| user_id | integer | 用户 ID |
| permission | text | 权限 |
| granted_by | integer | 授权者 |
| granted_at | timestamp | 授权时间 |

### api_key_store

API 密钥加密存储。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| tenant_id | integer | 租户 ID |
| provider | text | AI 服务商 |
| key_name | text | 密钥名称 |
| encrypted_key | text | 加密后的密钥 |
| key_hash | text | 密钥哈希 |
| base_url | text | API 基础 URL |
| is_active | boolean | DEFAULT true |
| created_by | integer | 创建者 ID |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |
| cli_tools | text | CLI 工具配置 |
| cli_settings | text | CLI 设置 |

## 协作

### teams

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| team_id | text | UNIQUE |
| name | text | NOT NULL |
| description | text | |
| owner_id | integer | |

### team_members

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| team_id | text | NOT NULL |
| user_id | integer | NOT NULL |
| role | text | DEFAULT 'member' |

唯一约束：`(team_id, user_id)`

### shared_sessions

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| share_id | text | UNIQUE |
| session_id | text | NOT NULL |
| shared_by | integer | |
| permission | text | DEFAULT 'view' |
| expires_at | timestamp | |

### knowledge_base

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| entry_id | text | UNIQUE |
| team_id | text | |
| title | text | NOT NULL |
| content | text | |
| category | text | DEFAULT 'general' |
| is_published | boolean | DEFAULT false |

### annotations

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| annotation_id | text | UNIQUE |
| session_id | text | NOT NULL |
| message_id | text | |
| user_id | integer | |
| content | text | |
| annotation_type | text | DEFAULT 'comment' |

## 同步

### sync_events

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| event_id | text | UNIQUE |
| event_type | text | NOT NULL |
| source | text | |
| session_id | text | |
| user_id | integer | |
| tool_name | text | |
| data | text | |

### retention_history

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| report_data | text | NOT NULL |

## 安全与权限

### login_attempts

登录失败尝试记录，用于账户锁定。

| 列名 | 类型 | 说明 |
|------|------|------|
| username | varchar | NOT NULL，查找键 |
| attempt_count | integer | DEFAULT 0 |
| locked_until | timestamp | 锁定截止时间 |

### user_permissions

用户级权限覆盖。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| user_id | integer | NOT NULL |
| permission | text | NOT NULL |
| granted_by | integer | 授权者 |
| granted_at | timestamp | DEFAULT CURRENT_TIMESTAMP |

索引：`idx_user_permissions_user`, `idx_user_permissions_permission`

### role_permissions

角色权限模板。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | integer PK | 自增 |
| role | text | NOT NULL |
| permission | text | NOT NULL |

索引：`idx_role_permissions_role`, `idx_role_permissions_permission`

## 外键汇总

| 子表 | 列名 | 父表 | 删除行为 |
|------|------|------|----------|
| users | tenant_id | tenants | SET NULL |
| sessions | user_id | users | CASCADE |
| web_user_auth_sessions | user_id | users | — |
| user_tool_accounts | user_id | users | CASCADE |
| user_daily_stats | user_id | users | CASCADE |
| session_messages | session_id | agent_sessions | — |
| quota_usage | user_id | users | CASCADE |
| quota_alerts | user_id | users | CASCADE |
| sso_identities | user_id | users | — |
| sso_sessions | user_id | users | — |
| sso_providers | tenant_id | tenants | — |
| tenant_quotas | tenant_id | tenants | CASCADE |
| tenant_settings | tenant_id | tenants | CASCADE |
| tenant_usage | tenant_id | tenants | CASCADE |
| anomaly_status | processed_by | users | — |
| insights_reports | user_id | users | — |

## 跨数据库兼容性

命名约定请参阅 [DATABASE-CONVENTIONS.md](DATABASE-CONVENTIONS.md)。`app/repositories/database.py` 中的 `adapt_sql()` 函数自动处理占位符转换（`?` → `%s`）。

# Database Schema

Open ACE supports both SQLite (single-machine) and PostgreSQL (production). The schema contains 44 tables + 1 materialized view, organized by domain.

Reference: `schema/schema-postgres.sql`

## User & Authentication

### users

Core user table with role-based access control.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| username | varchar | UNIQUE, NOT NULL |
| password_hash | varchar | bcrypt (12 rounds) |
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
| system_account | text | OS username for multi-user mode |
| deleted_at | timestamp | Soft delete |
| avatar_url | varchar(500) | Avatar URL |

Indexes: `idx_users_active`, `idx_users_deleted`, `idx_users_email`, `idx_users_role`, `idx_users_tenant`

### sessions

Authentication sessions with token-based access.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| token | varchar | UNIQUE, NOT NULL |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| created_at | timestamp | |
| expires_at | timestamp | NOT NULL |
| is_active | boolean | DEFAULT true |

Indexes: `idx_sessions_active`, `idx_sessions_expires`, `idx_sessions_token`, `idx_sessions_user_id`

### web_user_auth_sessions

Web UI authentication sessions.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| session_token | text | UNIQUE |
| created_at | timestamp | |
| expires_at | timestamp | |

### user_tool_accounts

Maps system accounts to platform users for different AI tools.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| tool_account | varchar(255) | UNIQUE |
| tool_type | varchar(50) | |
| description | varchar(255) | |

### user_daily_stats

Pre-aggregated daily usage per user for optimized queries.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | |
| requests | integer | DEFAULT 0 |
| tokens | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |

Unique: `(user_id, date)`

## Messages & Sessions

### daily_messages

Core message table — the primary data store for all AI interactions.

| Column | Type | Notes |
|--------|------|-------|
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

Unique: `(date, tool_name, message_id, host_name)`. 18 indexes covering query patterns.

### agent_sessions

AI agent session tracking.

| Column | Type | Notes |
|--------|------|-------|
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
| context | text | Session context |
| settings | text | Session settings |
| tags | text | Tags |
| created_at | timestamp | Creation time |
| updated_at | timestamp | Update time |
| completed_at | timestamp | Completion time |
| expires_at | timestamp | Expiry time |
| request_count | integer | DEFAULT 0, request count |
| workspace_type | text | DEFAULT 'local', workspace type |
| remote_machine_id | text | Associated remote machine ID |
| paused_at | timestamp | Pause time |

### session_messages

Messages within an agent session.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_id | text | FK → agent_sessions(session_id) |
| role | text | NOT NULL |
| content | text | |
| tokens_used | integer | DEFAULT 0 |
| model | text | |
| timestamp | timestamp | |
| metadata | text | |

## Statistics

### daily_stats

Aggregated daily statistics per tool/host/sender.

| Column | Type | Notes |
|--------|------|-------|
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

Unique: `(date, tool_name, host_name, sender_name)`

### hourly_stats

Hourly breakdown of usage.

| Column | Type | Notes |
|--------|------|-------|
| date | varchar(10) | NOT NULL |
| hour | integer | NOT NULL |
| tool_name | varchar(50) | NOT NULL |
| host_name | varchar(100) | DEFAULT 'localhost' |
| total_tokens | bigint | NOT NULL |
| total_input_tokens | bigint | NOT NULL |
| total_output_tokens | bigint | NOT NULL |
| message_count | integer | NOT NULL |

Unique: `(date, hour, tool_name, host_name)`

### daily_usage

Daily usage with cache token tracking.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| date | date | NOT NULL |
| tool_name | varchar | NOT NULL |
| host_name | varchar | DEFAULT 'localhost' |
| tokens_used | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |
| request_count | integer | DEFAULT 0 |
| models_used | text | |

Unique: `(date, tool_name, host_name)`

### usage_summary

Overall summary per tool/host for dashboard.

| Column | Type | Notes |
|--------|------|-------|
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

Unique: `(tool_name, host_name)`

### session_stats (Materialized View)

Aggregated from daily_messages where agent_session_id IS NOT NULL. Provides session-level token counts, message counts, and timestamp ranges.

## Multi-Tenant

### tenants

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| name | text | NOT NULL |
| slug | text | UNIQUE |
| status | text | CHECK IN ('active','suspended','trial','inactive') |
| plan | text | CHECK IN ('free','standard','premium','enterprise') |
| contact_email | text | |
| user_count | integer | DEFAULT 0 |
| total_tokens_used | integer | DEFAULT 0 |
| deleted_at | timestamp | Soft delete |

### tenant_settings (1:1 with tenants)

| Column | Type | Notes |
|--------|------|-------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| content_filter_enabled | boolean | DEFAULT true |
| audit_log_enabled | boolean | DEFAULT true |
| audit_log_retention_days | integer | DEFAULT 90 |
| data_retention_days | integer | DEFAULT 365 |
| sso_enabled | boolean | DEFAULT false |

### tenant_quotas (1:1 with tenants)

| Column | Type | Notes |
|--------|------|-------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| daily_token_limit | integer | DEFAULT 1,000,000 |
| monthly_token_limit | integer | DEFAULT 30,000,000 |
| max_users | integer | DEFAULT 100 |
| max_sessions_per_user | integer | DEFAULT 5 |

### tenant_usage

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| tenant_id | integer | FK → tenants(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| tokens_used | integer | DEFAULT 0 |
| requests_made | integer | DEFAULT 0 |
| active_users | integer | DEFAULT 0 |

Unique: `(tenant_id, date)`

## SSO

### sso_providers

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| name | text | UNIQUE |
| provider_type | text | NOT NULL (oauth2/oidc) |
| config | text | NOT NULL (JSON) |
| tenant_id | integer | FK → tenants(id) |
| is_active | boolean | DEFAULT true |

### sso_identities

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| provider_user_id | text | NOT NULL |

Unique: `(provider_name, provider_user_id)`

### sso_sessions

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_token | text | UNIQUE |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| access_token | text | |
| refresh_token | text | |
| expires_at | timestamp | |

### sso_auth_states

SSO authentication state temporary storage.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| state | text | UNIQUE, NOT NULL |
| provider_name | text | NOT NULL |
| user_id | integer | |
| redirect_url | text | |
| created_at | timestamp | DEFAULT CURRENT_TIMESTAMP |
| expires_at | timestamp | NOT NULL |

## Governance & Compliance

### audit_logs

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| user_id | integer | |
| username | text | |
| action | text | NOT NULL |
| severity | text | DEFAULT 'info' |
| resource_type | text | |
| resource_id | text | |
| details | text | |
| ip_address | text | |
| success | boolean | DEFAULT true |

Indexes: `idx_audit_timestamp`, `idx_audit_user_id`, `idx_audit_action`, `idx_audit_severity`

### content_filter_rules

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| pattern | text | NOT NULL |
| type | text | DEFAULT 'keyword' |
| severity | text | DEFAULT 'medium' |
| action | text | DEFAULT 'warn' |
| is_enabled | boolean | DEFAULT true |

### security_settings

Key-value store for security configuration.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| setting_key | varchar(100) | UNIQUE |
| setting_value | text | |
| description | text | |

### anomaly_status

Anomaly status tracking.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| anomaly_type | varchar | Anomaly type |
| affected_users_hash | varchar | Affected users hash |
| status | varchar | Processing status |
| processed_by | integer | Processor ID |
| processed_at | timestamp | Processing time |
| created_at | timestamp | Creation time |

### compliance_reports

Compliance report storage.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| report_type | text | NOT NULL |
| period_start | date | NOT NULL |
| period_end | date | NOT NULL |
| status | text | DEFAULT 'pending' |
| generated_by | integer | Generator ID |
| file_path | text | Report file path |
| summary | text | Summary |
| created_at | timestamp | Creation time |

### insights_reports

AI-generated usage insight reports.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| user_id | integer | User ID |
| start_date | varchar | Start date |
| end_date | varchar | End date |
| overall_score | integer | Overall score |
| overall_assessment | text | Overall assessment |
| strengths | text | Strengths (JSON) |
| areas_for_improvement | text | Areas for improvement (JSON) |
| suggestions | text | Suggestions (JSON) |
| usage_summary | text | Usage summary (JSON) |
| model | varchar | Model used |
| raw_response | text | Raw AI response |
| created_at | timestamp | Creation time |

## Security & Permissions

### login_attempts

Failed login attempt tracking for account lockout.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| username | text | NOT NULL |
| attempt_count | integer | DEFAULT 0 |
| locked_until | timestamp | Lock expiry time |
| last_attempt_at | timestamp | Last attempt time |

### user_permissions

User-level permission overrides.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| user_id | integer | NOT NULL |
| permission | text | NOT NULL |
| resource_type | text | Resource type |
| resource_id | text | Resource ID |
| granted_by | integer | Grantor |
| granted_at | timestamp | Grant time |

Indexes: `idx_user_permissions_user`, `idx_user_permissions_permission`

### role_permissions

Role permission templates.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| role | text | NOT NULL |
| permission | text | NOT NULL |
| resource_type | text | Resource type |
| created_at | timestamp | Creation time |

Indexes: `idx_role_permissions_role`, `idx_role_permissions_permission`

## Alerts & Quotas

### alerts

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| alert_id | text | UNIQUE |
| alert_type | text | NOT NULL |
| severity | text | NOT NULL |
| title | text | NOT NULL |
| message | text | |
| user_id | integer | |
| read | boolean | DEFAULT false |

### quota_usage

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| period | text | DEFAULT 'daily' |
| tokens_used | integer | DEFAULT 0 |
| requests_used | integer | DEFAULT 0 |

Unique: `(user_id, date, period)`

### quota_alerts

| Column | Type | Notes |
|--------|------|-------|
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

| Column | Type | Notes |
|--------|------|-------|
| user_id | integer | PK |
| email_enabled | boolean | DEFAULT true |
| push_enabled | boolean | DEFAULT true |
| min_severity | text | DEFAULT 'warning' |

## Workspace & Projects

### projects

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| path | varchar(500) | UNIQUE |
| name | varchar(200) | |
| description | text | |
| created_by | integer | |
| is_active | boolean | DEFAULT true |
| is_shared | boolean | DEFAULT false |

### user_projects

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | NOT NULL |
| project_id | integer | NOT NULL |
| total_sessions | integer | DEFAULT 0 |
| total_tokens | bigint | DEFAULT 0 |
| total_requests | integer | DEFAULT 0 |

Unique: `(user_id, project_id)`

### prompt_templates

| Column | Type | Notes |
|--------|------|-------|
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

## Remote Workspace

### remote_machines

Remote workspace machine registry.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| machine_id | text | Machine unique identifier |
| machine_name | text | Machine name |
| hostname | text | Hostname |
| os_type | text | OS type |
| os_version | text | OS version |
| ip_address | text | IP address |
| status | text | Status |
| agent_version | text | Agent version |
| capabilities | text | Capabilities (JSON) |
| cli_path | text | CLI path |
| work_dir | text | Working directory |
| tenant_id | integer | Tenant ID |
| created_by | integer | Creator ID |
| created_at | timestamp | Creation time |
| updated_at | timestamp | Update time |
| last_heartbeat | timestamp | Last heartbeat time |

### machine_assignments

User-to-remote-machine assignment relationships.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| machine_id | text | Machine ID |
| user_id | integer | User ID |
| permission | text | Permission level |
| granted_by | integer | Grantor ID |
| granted_at | timestamp | Grant time |

### api_key_store

Encrypted API key storage.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| tenant_id | integer | Tenant ID |
| provider | text | AI service provider |
| key_name | text | Key name |
| encrypted_key | text | Encrypted key |
| key_hash | text | Key hash |
| base_url | text | API base URL |
| is_active | boolean | DEFAULT true |
| created_by | integer | Creator ID |
| created_at | timestamp | Creation time |
| updated_at | timestamp | Update time |
| cli_tools | text | CLI tools configuration |
| cli_settings | text | CLI settings |

## Collaboration

### teams

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| team_id | text | UNIQUE |
| name | text | NOT NULL |
| description | text | |
| owner_id | integer | |

### team_members

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| team_id | text | NOT NULL |
| user_id | integer | NOT NULL |
| role | text | DEFAULT 'member' |

Unique: `(team_id, user_id)`

### shared_sessions

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| share_id | text | UNIQUE |
| session_id | text | NOT NULL |
| shared_by | integer | |
| permission | text | DEFAULT 'view' |
| expires_at | timestamp | |

### knowledge_base

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| entry_id | text | UNIQUE |
| team_id | text | |
| title | text | NOT NULL |
| content | text | |
| category | text | DEFAULT 'general' |
| is_published | boolean | DEFAULT false |

### annotations

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| annotation_id | text | UNIQUE |
| session_id | text | NOT NULL |
| message_id | text | |
| user_id | integer | |
| content | text | |
| annotation_type | text | DEFAULT 'comment' |

## Sync

### sync_events

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| event_id | text | UNIQUE |
| event_type | text | NOT NULL |
| source | text | |
| session_id | text | |
| user_id | integer | |
| tool_name | text | |
| data | text | |

### retention_history

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| report_data | text | NOT NULL |

## Foreign Key Summary

| Child Table | Column | Parent Table | On Delete |
|-------------|--------|--------------|-----------|
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
| api_key_store | tenant_id | tenants | CASCADE |

## Cross-Database Compatibility

See [DATABASE-CONVENTIONS.md](DATABASE-CONVENTIONS.md) for naming conventions. The `adapt_sql()` function in `app/repositories/database.py` handles placeholder conversion (`?` → `%s`) automatically.

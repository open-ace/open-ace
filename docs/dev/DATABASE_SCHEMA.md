# Database Schema Domain Map — 数据库模式领域地图

[English](#english) | [中文](#中文)

---

## English

This document is a **domain map** of the Open ACE database. For column-level
reference, the authoritative source is [`schema/schema-postgres.sql`](../../schema/schema-postgres.sql),
which contains all **104 tables + 1 materialized view**. When this document and
that file disagree, the SQL file wins. Open ACE supports both SQLite
(single-machine) and PostgreSQL (production); schema changes go through the
Alembic migrations in `migrations/versions/` (see
[SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md)).

## Domain Map — all 104 tables

Every table defined in `schema/schema-postgres.sql`, grouped by domain, one
line of responsibility each. Column-level details for the 46 most-used tables
follow in [Common tables in detail](#common-tables-in-detail-46-tables); for
all other tables, see the SQL file.

### Users & Authentication (8)

| Table | Responsibility |
|-------|----------------|
| `users` | Core accounts: role (`platform_admin`/`tenant_admin`/`manager`/`user`/`readonly`), tenant membership, soft delete, per-user token/request quotas, token-invalidation watermark |
| `sessions` | API auth sessions: token, user, expiry, active flag |
| `web_user_auth_sessions` | Web UI session tokens with their own expiry lifecycle |
| `user_permissions` | Per-user permission overrides granted by an admin |
| `role_permissions` | Role → permission template rows backing RBAC checks |
| `login_attempts` | Failed-login counters powering account lockout |
| `user_tool_accounts` | Mapping of discovered tool/OS accounts to platform users (mapping source, status, verification) |
| `tool_account_mapping_rules` | User-defined pattern rules that auto-map discovered tool accounts |

### Messages & Sessions (3)

| Table | Responsibility |
|-------|----------------|
| `daily_messages` | Analytical fact table of every AI message across tools/hosts; **not** a Workspace runtime source (see the session data contract) |
| `agent_sessions` | Authoritative Workspace session summary, one row per session |
| `session_messages` | Authoritative Workspace transcript per session (source-tagged, idempotent by `external_message_id`) |

The three session tables are governed by
[`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md),
which defines the data boundary between them and the single product semantics
of `request_count`.

### Statistics & Metering (8)

| Table | Responsibility |
|-------|----------------|
| `daily_stats` | Pre-aggregated daily stats per tool/host/sender |
| `hourly_stats` | Hourly usage breakdown per tool/host |
| `daily_usage` | Tenant-scoped daily usage incl. cache tokens; sync target for autonomous usage |
| `usage_summary` | All-time per-tool/host summary for dashboards |
| `user_daily_stats` | Per-user daily aggregates |
| `session_daily_usage` | Per-session, per-day incremental usage for long-running WebUI sessions (Issue #3338) |
| `request_performance` | Raw request performance events (TTFT, durations; Issue #3080) |
| `response_time_stats` | Pre-aggregated response-time percentiles per day/tool/host |

### Multi-Tenant (4)

| Table | Responsibility |
|-------|----------------|
| `tenants` | Tenant registry: status, plan, billing cycle, over-limit strategy, soft delete |
| `tenant_settings` | 1:1 tenant settings: filters, audit/retention knobs, SSO, branding, `allowed_tools` |
| `tenant_quotas` | 1:1 tenant limits (token/request quotas, max users, max sessions per user) |
| `tenant_usage` | Per-tenant daily usage counters |

### SSO (4)

| Table | Responsibility |
|-------|----------------|
| `sso_providers` | SSO provider configs (oauth2/oidc/saml) with encrypted secrets, tenant-scoped |
| `sso_identities` | Provider identity → local user links |
| `sso_sessions` | SSO session tokens with provider access/refresh tokens |
| `sso_auth_states` | Short-lived OAuth login-flow state (PKCE verifier, nonce) with TTL cleanup |

### Autonomous Development (8)

| Table | Responsibility |
|-------|----------------|
| `agent_runs` | Run timeline anchor, 1:1 with `agent_sessions` (`run_id == session_id`) |
| `agent_run_events` | Append-only run event stream (`run_id` is a plain index, not an FK, by design) |
| `agent_approvals` | Durable permission request/response pairs keyed by `request_id` |
| `autonomous_workflows` | Autonomous dev workflow state: requirements, repo/PR binding, CI repair, retries, locks |
| `workflow_events` | Per-workflow event log (optionally milestone-scoped) |
| `workflow_milestones` | Milestone/phase tracking per workflow: dev rounds, review sessions, GitHub issue/PR numbers |
| `policy_rules` | Versioned policy rule definitions with matchers |
| `policy_decisions` | Per-request policy evaluation audit trail |

### Compliance & Retention (5)

| Table | Responsibility |
|-------|----------------|
| `retention_policies` | Per-tenant, per-data-type retention rules (days, action, archive target) |
| `retention_executions` | Each retention run: status, locking, scanned/affected counts |
| `retention_evidence` | Per-execution proof: before/after counts, archive location and checksum |
| `legal_holds` | Legal hold markers that block retention deletion |
| `recycle_bin` | Soft-deleted records awaiting expiry or restore |

### Notification & Integrations (5)

| Table | Responsibility |
|-------|----------------|
| `webhook_settings` | Singleton outbound-webhook config (encrypted secret, private-URL policy) |
| `webhook_deliveries` | Per-alert webhook delivery attempts with retry/cooldown state |
| `dingtalk_settings` | Singleton DingTalk integration config (encrypted signing key) |
| `feishu_settings` | Singleton Feishu integration config (verification status) |
| `tenant_sensitive_keywords` | Tenant-scoped custom sensitive keywords (Issue #2789) |

### Scheduling & Operations (9)

| Table | Responsibility |
|-------|----------------|
| `scheduler_runs` | Per-job scheduler execution history incl. lock strategy and fencing tokens (Issue #2187/#2333) |
| `scheduler_leaders` | Distributed leader-election state per scheduler job |
| `consistency_violations` | Detected data drift with expected/actual values and repair status |
| `deregister_failures` | Failed session-termination batches during machine deregistration, replayed in background (Issue #2596) |
| `parse_failure_records` | Tool-output parse failures with retry tracking (Issue #2589) |
| `config_import_state` | Import state per config key when config moves into the database |
| `schema_metadata` | Schema init/version marker used as a DB-level compatibility guard (Issue #2330) |
| `tenant_migrations` | Tenant-move batches: affected users/sessions/projects and progress (Issue #2163) |
| `mapping_migration_status` | Progress bookkeeping for tool-account mapping backfills |

### Metering & Billing (10)

| Table | Responsibility |
|-------|----------------|
| `tenant_plans` | Plan catalog: quota defaults, pricing, feature flags |
| `tenant_period_history` | Per-tenant period usage archived at each quota reset |
| `usage_report_receipts` | Idempotency receipts for remote usage reports (Issue #1891) |
| `usage_report_rate_limits` | Sliding-window rate limiting for usage report ingestion |
| `aggregation_history` | Tenant usage aggregation runs with quality report and error state |
| `aggregation_locks` | Database-backed distributed locks for aggregation jobs |
| `backfill_logs` | Historical backfill job logs (counts, date range, status) |
| `alerts_history` | Delivery history of sent alerts (recipients, channels, status) |
| `archive_files` | Archived data files with checksum, record count and expiry |
| `tenant_keywords_version` | Keyword-list version counter per tenant for multi-process cache consistency |

### Security (7)

| Table | Responsibility |
|-------|----------------|
| `proxy_token_jtis` | JTI registry for proxy tokens: single-use/replay protection (Issue #1758) |
| `external_identity_nonces` | Replay-protection nonces for the external-identity HMAC probes (issuer + nonce + expiry; Issue #3459) |
| `encryption_keys` | Encryption key metadata (fingerprint, status, rotation); plaintext lives in env vars, never in the DB |
| `permission_checkpoints` | Resumable checkpoints for async permission setup scans |
| `permission_tasks` | Async shared-project permission setup tasks with progress (Issue #2746) |
| `tool_account_conflicts` | Conflict events when tool-account mappings collide (Issue #2761) |
| `test_execution_evidence` | Structured per-command test verdicts (framework results, coverage) |
| `command_execution_evidence` | Authoritative per-command execution record for autonomous runs |

### Remote Runtime (7)

| Table | Responsibility |
|-------|----------------|
| `remote_machines` | Registered remote machines: capabilities, heartbeat, token revoke timeout |
| `remote_runtime_commands` | HTTP-polling command queue for remote agents (request/response) |
| `remote_runtime_outputs` | Persistent SSE replay buffer per remote session |
| `machine_assignments` | User → machine permission assignments |
| `agent_tokens` | Hashed agent tokens with rotation, pending revoke and versioning (Issues #2499/#2530) |
| `registration_tokens` | One-time machine registration tokens (hash + consumption) |
| `api_key_store` | Encrypted AI provider API keys per tenant with CLI routing config |

### Other Domains (25)

Governance & audit:

| Table | Responsibility |
|-------|----------------|
| `audit_logs` | Platform audit trail (actor, action, severity, tenant resolution) |
| `compliance_reports` | Generated compliance report payloads per period |
| `insights_reports` | AI-generated usage insight reports |
| `anomaly_status` | Deduplicated anomaly detection state with processing status |
| `content_filter_rules` | Global content filter rules |
| `security_settings` | Security configuration key-value store |
| `retention_history` | Legacy retention report snapshots (timestamp + report data) |

Workspace & projects:

| Table | Responsibility |
|-------|----------------|
| `projects` | Registered project workspaces, tenant-scoped, with permission-setup status |
| `user_projects` | Per-user project rollups (sessions, tokens, requests, duration) |
| `project_categories` | Project category dictionary |
| `prompt_templates` | Reusable prompt templates; name is unique for idempotent seeding (Issue #2577) |

Alerting & quotas:

| Table | Responsibility |
|-------|----------------|
| `alerts` | User-facing alert inbox with action links |
| `quota_usage` | Per-user, per-period (and per-tool) quota consumption |
| `quota_alerts` | Quota threshold alerts with acknowledgment tracking |
| `notification_preferences` | Per-user notification channels (email/push/webhook, severity floor) |

Collaboration & sync:

| Table | Responsibility |
|-------|----------------|
| `teams` | Teams with settings |
| `team_members` | Team membership with roles |
| `shared_sessions` | Session share links with permissions and access counters |
| `knowledge_base` | Team knowledge-base entries |
| `annotations` | Session/message annotations with threading |
| `sync_events` | Multi-source sync event log |

Platform settings:

| Table | Responsibility |
|-------|----------------|
| `ai_agent_settings` | AI agent configuration key-value store |
| `model_gateway_config` | Singleton LiteLLM-compatible model gateway config |
| `smtp_settings` | SMTP relay config with encrypted password |
| `email_notification_logs` | Email delivery attempts with retry state |

## Common tables in detail (46 tables)

The tables below are the ones most referenced in code and docs, with condensed
column-level detail. All other tables (the remaining 58 of the 104) are
documented only by `schema/schema-postgres.sql` — consult it directly.

### Users & Authentication

#### users

Core user table with role-based access control.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| username | varchar | UNIQUE, NOT NULL |
| password_hash | varchar | bcrypt (12 rounds) |
| email | varchar | |
| is_admin | boolean | DEFAULT false |
| is_active | boolean | DEFAULT true |
| role | varchar | CHECK IN ('platform_admin','tenant_admin','manager','user','readonly') |
| daily_token_quota | bigint | Stored in M units (Issue #3018) |
| monthly_token_quota | bigint | Stored in M units (Issue #3018) |
| daily_request_quota | bigint | Stored as actual count (Issue #3018) |
| monthly_request_quota | bigint | Stored as actual count (Issue #3018) |
| tenant_id | integer | FK → tenants(id) ON DELETE SET NULL |
| must_change_password | boolean | DEFAULT false |
| system_account | text | OS username for multi-user mode |
| deleted_at | timestamp | Soft delete |
| avatar_url | varchar(500) | Avatar URL |
| auto_mapping_enabled | boolean | Auto tool-account mapping toggle |
| tenant_version | integer | Tenant-move tracking (Issue #2163) |
| tokens_valid_after | timestamp | Rejects tokens minted before this stamp (Issue #3379) |

Indexes: `idx_users_active`, `idx_users_deleted`, `idx_users_email`, `idx_users_role`, `idx_users_tenant`

#### sessions

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

#### web_user_auth_sessions

Web UI authentication sessions.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| session_token | text | UNIQUE |
| created_at | timestamp | |
| expires_at | timestamp | |

#### user_tool_accounts

Maps system accounts to platform users for different AI tools.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| tool_account | varchar(255) | UNIQUE |
| tool_type | varchar(50) | |
| description | varchar(255) | |
| mapping_source | varchar | How the mapping was created (Issue #2761) |
| mapping_status | varchar | Mapping lifecycle status |
| verification_status | varchar | Account-mapping verification (Issue #3273) |
| verified_at | timestamp | Verification time |
| tenant_id | integer | Tenant scope |

#### user_daily_stats

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

### Messages & Sessions

The read/write boundaries for these three tables are contractual — see
[`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md).

#### daily_messages

Core message fact table — the analytical store for all AI interactions.

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
| agent_session_id | varchar | Links to agent_sessions |
| user_id | integer | |
| project_path | text | |
| tenant_id | integer | Tenant scope |
| feishu_conversation_id | varchar | Feishu group chat |
| group_subject / is_group_chat | varchar / boolean | Group-chat context |

Unique: `(date, tool_name, message_id, host_name)`. 18 indexes covering query patterns.

#### agent_sessions

Authoritative Workspace session summary table (one row per session).

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_id | text | UNIQUE |
| tenant_id | integer | DEFAULT 1; tenant-scoped session lookup and write boundary |
| tenant_version | integer | Tenant-move tracking |
| session_type | text | DEFAULT 'chat' |
| title | text | |
| tool_name | text | NOT NULL |
| host_name | text | DEFAULT 'localhost' |
| user_id | integer | |
| status | text | DEFAULT 'active' |
| total_tokens | integer | DEFAULT 0 |
| total_input_tokens / total_output_tokens | integer | DEFAULT 0 |
| total_cache_read_tokens / total_cache_write_tokens | integer | Cache token totals |
| message_count | integer | DEFAULT 0 |
| request_count | integer | Distinct assistant responses (see the session data contract) |
| model | text | |
| project_id | integer | |
| project_path | varchar(500) | |
| context / settings / tags | text | Session context, settings, tags |
| created_at / updated_at / completed_at / expires_at / paused_at | timestamp | Lifecycle timestamps |
| workspace_type | text | DEFAULT 'local' |
| remote_machine_id | text | Associated remote machine |
| cli_session_id | text | CLI-side session identifier |
| daily_usage_synced | boolean | Idempotent sync flag to daily_usage (Issue #2585) |

#### session_messages

Authoritative transcript table within an agent session.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_id | text | FK → agent_sessions(session_id) |
| tenant_id | integer | DEFAULT 1; tenant-scoped message lookup |
| role | text | NOT NULL |
| content | text | |
| tokens_used | integer | DEFAULT 0 |
| model | text | |
| timestamp | timestamp | |
| metadata | text | |
| milestone_id | varchar | Autonomous milestone linkage |
| source | varchar | Write-path source tag |
| source_timestamp | timestamp | Original event time |
| external_message_id | varchar | Provider message id; idempotency key with (session_id, role) |
| content_blocks | text | Structured content replay |

Session indexes include `idx_agent_sessions_tenant_user`, `idx_agent_sessions_tenant_updated`, `idx_session_messages_tenant_session`, and `idx_session_messages_tenant_session_timestamp`.

### Statistics

#### daily_stats

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
| user_id | integer | |
| tenant_id | integer | Tenant scope |

Unique: `(date, tool_name, host_name, sender_name)`

#### hourly_stats

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
| tenant_id | integer | Tenant scope |

Unique: `(date, hour, tool_name, host_name)`

#### daily_usage

Daily usage with cache token tracking.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1; tenant-scoped usage aggregation key |
| date | date | NOT NULL |
| tool_name | varchar | NOT NULL |
| host_name | varchar | DEFAULT 'localhost' |
| tokens_used | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |
| request_count | integer | DEFAULT 0 |
| models_used | text | |

Unique: `(tenant_id, date, tool_name, host_name)`

Indexes: `idx_usage_date`, `idx_usage_date_tool_host(tenant_id, date, tool_name, host_name)`, `idx_usage_tenant_date`

#### usage_summary

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

#### session_stats (Materialized View)

Aggregated from daily_messages where agent_session_id IS NOT NULL. Provides session-level token counts, message counts, and timestamp ranges. This is the schema's single materialized view.

### Multi-Tenant

#### tenants

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| name | text | NOT NULL |
| slug | text | UNIQUE |
| status | text | CHECK IN ('active','suspended','trial','inactive') |
| plan | text | CHECK IN ('free','standard','premium','enterprise') |
| contact_email / contact_phone / contact_name | text | Contact details |
| quota / settings | text | JSON blobs |
| user_count | integer | DEFAULT 0 |
| total_tokens_used | integer | DEFAULT 0 |
| total_requests_made | integer | DEFAULT 0 |
| billing_day / billing_cycle_type / billing_cycle_start / billing_cycle_end | varied | Billing cycle |
| current_cycle_tokens | bigint | Tokens in the current cycle |
| over_limit_strategy / over_limit_price_per_token | text/numeric | Over-limit handling |
| usage_alert_threshold / usage_critical_threshold / alert_silence_hours | numeric | Usage alerting |
| trial_ends_at / subscription_ends_at | timestamp | Lifecycle |
| deleted_at | timestamp | Soft delete |

#### tenant_settings (1:1 with tenants)

| Column | Type | Notes |
|--------|------|-------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| content_filter_enabled | boolean | DEFAULT true |
| audit_log_enabled | boolean | DEFAULT true |
| audit_log_retention_days | integer | DEFAULT 90 |
| data_retention_days | integer | DEFAULT 365 |
| sso_enabled | boolean | DEFAULT false |
| sso_provider | text | Default provider name |
| custom_branding / branding_name / branding_logo_url | varied | Branding |
| auto_provision_users | text | Auto-provisioning policy |
| block_sensitive_keyword / sensitive_keyword_match_mode | boolean/text | Sensitive keyword policy |
| allowed_tools / roi_assumptions | text | JSON config (Issue #2788) |

#### tenant_quotas (1:1 with tenants)

| Column | Type | Notes |
|--------|------|-------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| daily_token_limit | bigint | DEFAULT 1,000,000 (actual token count, Issue #1259) |
| monthly_token_limit | bigint | DEFAULT 30,000,000 (actual token count, Issue #1259) |
| daily_request_limit | bigint | DEFAULT 10,000 (Issue #3018) |
| monthly_request_limit | bigint | DEFAULT 300,000 (Issue #3018) |
| max_users | integer | DEFAULT 100 |
| max_sessions_per_user | integer | DEFAULT 5 |

#### tenant_usage

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| tenant_id | integer | FK → tenants(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| tokens_used | integer | DEFAULT 0 |
| requests_made | integer | DEFAULT 0 |
| active_users | integer | DEFAULT 0 |
| new_users | integer | DEFAULT 0 |

Unique: `(tenant_id, date)`

### SSO

#### sso_providers

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| name | text | UNIQUE |
| provider_type | text | NOT NULL (oauth2/oidc/saml) |
| config | text | NOT NULL (JSON; stores encrypted `client_secret_encrypted` instead of plaintext `client_secret`) |
| tenant_id | integer | FK → tenants(id) |
| is_active | boolean | DEFAULT true |

#### sso_identities

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| provider_user_id | text | NOT NULL |
| provider_data | text | Raw provider claims |

Unique: `(provider_name, provider_user_id)`

#### sso_sessions

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_token | text | UNIQUE |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| access_token | text | |
| refresh_token | text | |
| expires_at | timestamp | |

### Governance & Compliance

#### audit_logs

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| user_id | integer | |
| tenant_id | integer | Resolved from the actor when available for tenant-scoped audit queries |
| username | text | |
| action | text | NOT NULL |
| severity | text | DEFAULT 'info' |
| resource_type | text | |
| resource_id | text | |
| details | text | |
| ip_address | text | |
| user_agent | text | |
| session_id | text | |
| success | boolean | DEFAULT true |
| error_message | text | |

Indexes: `idx_audit_timestamp`, `idx_audit_user_id`, `idx_audit_tenant_id`, `idx_audit_action`, `idx_audit_severity`

#### content_filter_rules

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| pattern | text | NOT NULL |
| type | text | DEFAULT 'keyword' |
| severity | text | DEFAULT 'medium' |
| action | text | DEFAULT 'warn' |
| is_enabled | boolean | DEFAULT true |
| description | text | |

#### security_settings

Key-value store for security configuration.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| setting_key | varchar(100) | UNIQUE |
| setting_value | text | |
| description | text | |

#### anomaly_status

Anomaly status tracking.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| anomaly_id | text | Stable anomaly identity (Issue #2749) |
| anomaly_type | varchar | Anomaly type |
| affected_users_hash | varchar | Affected users hash |
| tenant_id | integer | Tenant scope |
| status | varchar | Processing status |
| processed_by | integer | Processor ID |
| processed_at | timestamp | Processing time |
| created_at | timestamp | Creation time |

#### insights_reports

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

### Security & Permissions

#### login_attempts

Failed login attempt tracking for account lockout.

| Column | Type | Notes |
|--------|------|-------|
| username | varchar | NOT NULL, lookup key |
| attempt_count | integer | DEFAULT 0 |
| locked_until | timestamp | Lock expiry time |

#### user_permissions

User-level permission overrides.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| user_id | integer | NOT NULL |
| permission | text | NOT NULL |
| granted_by | integer | Grantor |
| granted_at | timestamp | DEFAULT CURRENT_TIMESTAMP |

Indexes: `idx_user_permissions_user`, `idx_user_permissions_permission`

#### role_permissions

Role permission templates.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| role | text | NOT NULL |
| permission | text | NOT NULL |

Indexes: `idx_role_permissions_role`, `idx_role_permissions_permission`

### Alerts & Quotas

#### alerts

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| alert_id | text | UNIQUE |
| alert_type | text | NOT NULL |
| severity | text | NOT NULL |
| title | text | NOT NULL |
| message | text | |
| user_id | integer | |
| username | text | |
| tool_name | text | |
| metadata | text | |
| read | boolean | DEFAULT false |
| action_url / action_text | text | Inline action link |

#### quota_usage

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| period | text | DEFAULT 'daily' |
| tool_name | text | Per-tool quota dimension |
| tokens_used | integer | DEFAULT 0 |
| requests_used | integer | DEFAULT 0 |

Unique: `(user_id, date, period, tool_name)`

#### quota_alerts

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| alert_type | text | NOT NULL |
| quota_type | text | NOT NULL |
| period | text | Quota period |
| threshold | real | NOT NULL |
| current_usage | integer | NOT NULL |
| quota_limit | integer | NOT NULL |
| percentage | real | NOT NULL |
| message | text | |
| acknowledged | boolean | DEFAULT false |
| acknowledged_at / acknowledged_by | timestamp/integer | Acknowledgment tracking |

#### notification_preferences

| Column | Type | Notes |
|--------|------|-------|
| user_id | integer | PK |
| email_enabled | boolean | DEFAULT true |
| push_enabled | boolean | DEFAULT true |
| webhook_url | text | Personal webhook |
| alert_types | text | Subscribed alert types |
| min_severity | text | DEFAULT 'warning' |
| notification_email | text | Dedicated notification address |
| email_verified | boolean | Address verification |
| dingtalk_webhook_secret | text | Encrypted DingTalk secret |

### Workspace & Projects

#### projects

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1; used for tenant-scoped project lookup and uniqueness |
| path | varchar(500) | UNIQUE per active `(tenant_id, path)` |
| name | varchar(200) | |
| description | text | |
| created_by | integer | |
| is_active | boolean | DEFAULT true |
| is_shared | boolean | DEFAULT false |
| permission_status | text | Shared-project permission setup status (Issue #2746) |
| permission_task_id | text | Associated permission_tasks task |

Indexes: `idx_projects_created_by`, `idx_projects_is_active`, `idx_projects_path(tenant_id, path)`, `idx_projects_tenant_created_by`

#### user_projects

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| user_id | integer | NOT NULL |
| project_id | integer | NOT NULL |
| first_access_at / last_access_at | timestamp | Access window |
| total_sessions | integer | DEFAULT 0 |
| total_tokens | bigint | DEFAULT 0 |
| total_requests | integer | DEFAULT 0 |
| total_duration_seconds | integer | DEFAULT 0 |

Unique: `(user_id, project_id)`

#### prompt_templates

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| name | text | NOT NULL, UNIQUE (Issue #2577) |
| description | text | |
| category | text | DEFAULT 'general' |
| content | text | NOT NULL |
| variables | text | |
| tags | text | |
| author_id | integer | |
| author_name | text | |
| is_public | boolean | DEFAULT false |
| is_featured | boolean | DEFAULT false |
| use_count | integer | DEFAULT 0 |

### Remote Workspace

#### remote_machines

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
| created_at / updated_at | timestamp | Timestamps |
| last_heartbeat | timestamp | Last heartbeat time |
| legacy_mode | boolean | Legacy protocol flag |
| token_revoke_timeout | integer | Delayed revocation window (Issue #2499) |

#### remote_runtime_commands

Persistent HTTP-polling command queue for remote agents.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| command_id | varchar(64) | UNIQUE; request_id for command-response calls |
| machine_id | text | Target remote machine |
| session_id | text | Optional remote session |
| command_type | text | Command name |
| payload | text | JSON command payload |
| status | varchar(32) | `pending`, `delivered`, or `responded` |
| response_payload | text | JSON response payload for synchronous commands |
| created_at | timestamp | Creation time |
| delivered_at | timestamp | Agent poll claim time |
| responded_at | timestamp | Response arrival time |
| expires_at | timestamp | Retention cutoff |

Indexes: `idx_remote_runtime_commands_machine_status`, `idx_remote_runtime_commands_expires`

#### remote_runtime_outputs

Persistent SSE replay buffer for remote session output.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| session_id | text | Remote session ID |
| event_index | integer | Monotonic per-session output index |
| stream | text | stdout, stderr, system, permission, request_state |
| payload | text | JSON output payload |
| created_at | timestamp | Creation time |
| expires_at | timestamp | Retention cutoff |

Unique: `(session_id, event_index)`

Indexes: `idx_remote_runtime_outputs_session_index`, `idx_remote_runtime_outputs_expires`

#### machine_assignments

User-to-remote-machine assignment relationships.

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | Auto-increment |
| machine_id | text | Machine ID |
| user_id | integer | User ID |
| permission | text | Permission level |
| granted_by | integer | Grantor ID |
| granted_at | timestamp | Grant time |

#### api_key_store

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
| scope / priority / weight | text/integer | Routing attributes |
| resolved_ips / resolved_at | text/timestamp | Pinned egress IPs (Issue #1894) |
| created_by | integer | Creator ID |
| created_at / updated_at | timestamp | Timestamps |
| cli_tools | text | CLI tools configuration |
| cli_settings | text | CLI settings |

### Collaboration

#### teams

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| team_id | text | UNIQUE |
| name | text | NOT NULL |
| description | text | |
| owner_id | integer | |
| settings | text | Team settings (JSON) |

#### team_members

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| team_id | text | NOT NULL |
| user_id | integer | NOT NULL |
| username | text | Denormalized member name |
| role | text | DEFAULT 'member' |
| joined_at | timestamp | |

Unique: `(team_id, user_id)`

#### shared_sessions

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| share_id | text | UNIQUE |
| session_id | text | NOT NULL |
| shared_by | integer | |
| shared_by_name | text | Denormalized sharer name |
| permission | text | DEFAULT 'view' |
| share_type / target_id / target_name | text | Share target (user/team/link) |
| allow_comments | boolean | DEFAULT true |
| allow_copy | boolean | DEFAULT true |
| expires_at | timestamp | |
| access_count | integer | DEFAULT 0 |
| last_accessed | timestamp | |

#### knowledge_base

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| entry_id | text | UNIQUE |
| team_id | text | |
| title | text | NOT NULL |
| content | text | |
| category | text | DEFAULT 'general' |
| tags | text | |
| author_id | integer | |
| author_name | text | |
| is_published | boolean | DEFAULT false |
| view_count | integer | DEFAULT 0 |

#### annotations

| Column | Type | Notes |
|--------|------|-------|
| id | integer PK | |
| annotation_id | text | UNIQUE |
| session_id | text | NOT NULL |
| message_id | text | |
| user_id | integer | |
| username | text | Denormalized author name |
| content | text | |
| annotation_type | text | DEFAULT 'comment' |
| parent_id | text | Threading |

### Sync & Retention Snapshots

#### sync_events

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
| metadata | text | |

#### retention_history

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
| anomaly_status | processed_by | users | — |
| insights_reports | user_id | users | — |

## Cross-Database Compatibility

See [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md) for naming conventions. The `adapt_sql()` function in `app/repositories/database.py` handles placeholder conversion (`?` → `%s`) automatically.

## Related documentation

- [`schema/schema-postgres.sql`](../../schema/schema-postgres.sql) — authoritative column-level reference (104 tables + 1 materialized view)
- [`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md) — data boundary contract for `agent_sessions`, `session_messages`, `daily_messages`
- [SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md) — how schema changes ship
- [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md) — field naming and type conventions

---

## 中文

本文是 Open ACE 数据库的**领域地图**。逐列参考以
[`schema/schema-postgres.sql`](../../schema/schema-postgres.sql) 为权威，该文件包含全部
**104 张表 + 1 个物化视图**；本文与该文件不一致时，以 SQL 文件为准。Open ACE 同时支持
SQLite（单机）与 PostgreSQL（生产）；模式变更统一通过 `migrations/versions/` 中的
Alembic 迁移完成（见 [SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md)）。

## 领域地图 — 全部 104 张表

`schema/schema-postgres.sql` 中定义的每一张表，按领域分组，每表一行职责。最常用的
46 张表的逐列细节见下文[常用表详表](#常用表详表)；其余表请直接查阅 SQL 文件。

### 用户与认证（8 张）

| 表 | 职责 |
|----|------|
| `users` | 核心账号表：角色（`platform_admin`/`tenant_admin`/`manager`/`user`/`readonly`）、租户归属、软删除、按用户的令牌/请求配额、令牌失效水位 |
| `sessions` | API 认证会话：令牌、用户、过期时间、激活标志 |
| `web_user_auth_sessions` | Web UI 会话令牌，拥有独立的过期生命周期 |
| `user_permissions` | 管理员授予的按用户权限覆盖 |
| `role_permissions` | 角色 → 权限模板行，支撑 RBAC 校验 |
| `login_attempts` | 登录失败计数，用于账号锁定 |
| `user_tool_accounts` | 发现的工具账号/系统账号到平台用户的映射（映射来源、状态、核验） |
| `tool_account_mapping_rules` | 用户定义的模式规则，用于自动映射发现的工具账号 |

### 消息与会话（3 张）

| 表 | 职责 |
|----|------|
| `daily_messages` | 跨工具/主机的全量消息分析事实表；**不是** Workspace 运行时数据源（见会话数据契约） |
| `agent_sessions` | 权威的 Workspace 会话汇总表，每会话一行 |
| `session_messages` | 权威的会话内消息转录表（带来源标记，按 `external_message_id` 幂等） |

三张会话表受
[`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md)
约束，该契约定义了三表之间的数据边界以及 `request_count` 的唯一产品语义。

### 统计计量（8 张）

| 表 | 职责 |
|----|------|
| `daily_stats` | 按工具/主机/发送人预聚合的日统计 |
| `hourly_stats` | 按工具/主机的逐小时用量 |
| `daily_usage` | 租户维度的日用量（含缓存令牌）；自治开发用量的同步目标 |
| `usage_summary` | 面板使用的按工具/主机全期汇总 |
| `user_daily_stats` | 按用户预聚合的日用量 |
| `session_daily_usage` | 长运行 WebUI 会话的按会话按日增量用量（Issue #3338） |
| `request_performance` | 原始请求性能事件（TTFT、耗时；Issue #3080） |
| `response_time_stats` | 按日/工具/主机预聚合的响应时间分位数 |

### 多租户（4 张）

| 表 | 职责 |
|----|------|
| `tenants` | 租户注册表：状态、套餐、计费周期、超限策略、软删除 |
| `tenant_settings` | 与租户 1:1 的设置：过滤、审计/保留开关、SSO、品牌、`allowed_tools` |
| `tenant_quotas` | 与租户 1:1 的限额（令牌/请求配额、用户数上限、人均会话数） |
| `tenant_usage` | 按租户的日用量计数 |

### SSO（4 张）

| 表 | 职责 |
|----|------|
| `sso_providers` | SSO 提供商配置（oauth2/oidc/saml），密钥加密存储，按租户隔离 |
| `sso_identities` | 提供商身份 → 本地用户的关联 |
| `sso_sessions` | SSO 会话令牌及提供商 access/refresh 令牌 |
| `sso_auth_states` | 短时 OAuth 登录流程状态（PKCE verifier、nonce），带 TTL 清理 |

### 自治开发（8 张）

| 表 | 职责 |
|----|------|
| `agent_runs` | 运行时间线锚点，与 `agent_sessions` 1:1（`run_id == session_id`） |
| `agent_run_events` | 追加式运行事件流（`run_id` 刻意设计为普通索引而非外键） |
| `agent_approvals` | 以 `request_id` 为键的持久化权限请求/响应对 |
| `autonomous_workflows` | 自治开发工作流状态：需求、仓库/PR 绑定、CI 修复、重试、锁 |
| `workflow_events` | 按工作流的事件日志（可关联里程碑） |
| `workflow_milestones` | 按工作流的里程碑/阶段跟踪：开发轮次、评审会话、GitHub issue/PR 编号 |
| `policy_rules` | 带匹配器的版本化策略规则定义 |
| `policy_decisions` | 按请求的策略评估审计记录 |

### 合规保留（5 张）

| 表 | 职责 |
|----|------|
| `retention_policies` | 按租户、按数据类型的保留规则（天数、动作、归档目标） |
| `retention_executions` | 每次保留执行：状态、加锁、扫描/影响行数 |
| `retention_evidence` | 每次执行的证据：前后行数、归档位置与校验和 |
| `legal_holds` | 阻止保留删除的法律保全标记 |
| `recycle_bin` | 等待过期或恢复的软删除记录 |

### 通知集成（5 张）

| 表 | 职责 |
|----|------|
| `webhook_settings` | 出站 Webhook 单例配置（加密密钥、私网 URL 策略） |
| `webhook_deliveries` | 按告警的 Webhook 投递尝试，带重试/冷却状态 |
| `dingtalk_settings` | 钉钉集成单例配置（加密签名密钥） |
| `feishu_settings` | 飞书集成单例配置（核验状态） |
| `tenant_sensitive_keywords` | 租户维度的自定义敏感词（Issue #2789） |

### 调度运维（9 张）

| 表 | 职责 |
|----|------|
| `scheduler_runs` | 按任务的调度执行历史，含锁策略与围栏令牌（Issue #2187/#2333） |
| `scheduler_leaders` | 每个调度任务的分布式选主状态 |
| `consistency_violations` | 检测到的数据漂移：期望/实际值与修复状态 |
| `deregister_failures` | 机器注销时失败的会话终止批次，供后台补偿重放（Issue #2596） |
| `parse_failure_records` | 工具输出解析失败及重试跟踪（Issue #2589） |
| `config_import_state` | 配置入库时每个配置键的导入状态 |
| `schema_metadata` | 模式初始化/版本标记，作为数据库层兼容性守卫（Issue #2330） |
| `tenant_migrations` | 租户迁移批次：受影响的用户/会话/项目及进度（Issue #2163） |
| `mapping_migration_status` | 工具账号映射回填的进度簿记 |

### 计量账务（10 张）

| 表 | 职责 |
|----|------|
| `tenant_plans` | 套餐目录：配额默认值、定价、功能开关 |
| `tenant_period_history` | 每次配额重置时归档的租户周期用量 |
| `usage_report_receipts` | 远端用量上报的幂等回执（Issue #1891） |
| `usage_report_rate_limits` | 用量上报接入的滑动窗口限流 |
| `aggregation_history` | 租户用量聚合运行记录，含质量报告与错误状态 |
| `aggregation_locks` | 聚合作业的数据库级分布式锁 |
| `backfill_logs` | 历史回填作业日志（行数、日期范围、状态） |
| `alerts_history` | 已发送告警的投递历史（收件人、渠道、状态） |
| `archive_files` | 归档数据文件：校验和、记录数、过期时间 |
| `tenant_keywords_version` | 租户敏感词版本计数器，用于多进程缓存一致性 |

### 安全（7 张）

| 表 | 职责 |
|----|------|
| `proxy_token_jtis` | 代理令牌 JTI 注册表：一次性/防重放（Issue #1758） |
| `external_identity_nonces` | 外部身份 HMAC 探测的防重放 nonce（issuer + nonce + 过期时间；Issue #3459） |
| `encryption_keys` | 加密密钥元数据（指纹、状态、轮换）；明文只存环境变量，绝不入库 |
| `permission_checkpoints` | 异步权限设置扫描的可恢复检查点 |
| `permission_tasks` | 共享项目异步权限设置任务及进度（Issue #2746） |
| `tool_account_conflicts` | 工具账号映射冲突事件（Issue #2761） |
| `test_execution_evidence` | 按命令的结构化测试判定（框架结果、覆盖率） |
| `command_execution_evidence` | 自治开发运行中按命令的权威执行记录 |

### 远程运行时（7 张）

| 表 | 职责 |
|----|------|
| `remote_machines` | 已注册的远程机器：能力、心跳、令牌撤销超时 |
| `remote_runtime_commands` | 远端 agent 的 HTTP 轮询命令队列（请求/响应） |
| `remote_runtime_outputs` | 远程会话的持久化 SSE 回放缓冲 |
| `machine_assignments` | 用户 → 机器的权限分配 |
| `agent_tokens` | 哈希存储的 agent 令牌，带轮换、延迟撤销与版本（Issue #2499/#2530） |
| `registration_tokens` | 一次性机器注册令牌（哈希 + 消费标记） |
| `api_key_store` | 按租户加密存储的 AI 提供商 API 密钥及 CLI 路由配置 |

### 其余领域（25 张）

治理与审计：

| 表 | 职责 |
|----|------|
| `audit_logs` | 平台审计流水（操作者、动作、严重级、租户归属） |
| `compliance_reports` | 按周期生成的合规报告载荷 |
| `insights_reports` | AI 生成的用量洞察报告 |
| `anomaly_status` | 去重后的异常检测状态及处理进度 |
| `content_filter_rules` | 全局内容过滤规则 |
| `security_settings` | 安全配置键值存储 |
| `retention_history` | 旧版保留报告快照（时间戳 + 报告数据） |

工作区与项目：

| 表 | 职责 |
|----|------|
| `projects` | 已注册的项目工作区，租户隔离，含权限设置状态 |
| `user_projects` | 按用户的项目汇总（会话、令牌、请求、时长） |
| `project_categories` | 项目分类字典 |
| `prompt_templates` | 可复用提示词模板；名称唯一以保证幂等播种（Issue #2577） |

告警与配额：

| 表 | 职责 |
|----|------|
| `alerts` | 面向用户的告警收件箱，带操作链接 |
| `quota_usage` | 按用户、按周期（及按工具）的配额消耗 |
| `quota_alerts` | 配额阈值告警，带确认跟踪 |
| `notification_preferences` | 按用户的通知渠道（邮件/推送/Webhook、严重级下限） |

协作与同步：

| 表 | 职责 |
|----|------|
| `teams` | 团队及其设置 |
| `team_members` | 团队成员及角色 |
| `shared_sessions` | 会话分享链接，带权限与访问计数 |
| `knowledge_base` | 团队知识库条目 |
| `annotations` | 会话/消息批注，支持回复串嵌套 |
| `sync_events` | 多源同步事件日志 |

平台设置：

| 表 | 职责 |
|----|------|
| `ai_agent_settings` | AI agent 配置键值存储 |
| `model_gateway_config` | LiteLLM 兼容模型网关单例配置 |
| `smtp_settings` | SMTP 发信配置，密码加密存储 |
| `email_notification_logs` | 邮件投递尝试及重试状态 |

## 常用表详表

以下表格是代码与文档中最常引用的表，逐列细节做了精简（共 46 张）。其余表（104 张中的另外
57 张）只由 `schema/schema-postgres.sql` 文档化——请直接查阅该文件。

### 用户与认证

#### users

核心用户表，支持基于角色的访问控制。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| username | varchar | UNIQUE, NOT NULL |
| password_hash | varchar | bcrypt（12 轮） |
| email | varchar | |
| is_admin | boolean | DEFAULT false |
| is_active | boolean | DEFAULT true |
| role | varchar | CHECK IN ('platform_admin','tenant_admin','manager','user','readonly') |
| daily_token_quota | bigint | 以 M 为单位存储（Issue #3018） |
| monthly_token_quota | bigint | 以 M 为单位存储（Issue #3018） |
| daily_request_quota | bigint | 按实际次数存储（Issue #3018） |
| monthly_request_quota | bigint | 按实际次数存储（Issue #3018） |
| tenant_id | integer | FK → tenants(id) ON DELETE SET NULL |
| must_change_password | boolean | DEFAULT false |
| system_account | text | 多用户模式下的 OS 用户名 |
| deleted_at | timestamp | 软删除 |
| avatar_url | varchar(500) | 头像 URL |
| auto_mapping_enabled | boolean | 工具账号自动映射开关 |
| tenant_version | integer | 租户迁移跟踪（Issue #2163） |
| tokens_valid_after | timestamp | 拒绝早于该时间戳签发的令牌（Issue #3379） |

索引：`idx_users_active`、`idx_users_deleted`、`idx_users_email`、`idx_users_role`、`idx_users_tenant`

#### sessions

基于令牌访问的认证会话。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| token | varchar | UNIQUE, NOT NULL |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| created_at | timestamp | |
| expires_at | timestamp | NOT NULL |
| is_active | boolean | DEFAULT true |

索引：`idx_sessions_active`、`idx_sessions_expires`、`idx_sessions_token`、`idx_sessions_user_id`

#### web_user_auth_sessions

Web UI 认证会话。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| session_token | text | UNIQUE |
| created_at | timestamp | |
| expires_at | timestamp | |

#### user_tool_accounts

将系统账号映射到平台用户的 AI 工具账号表。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| tool_account | varchar(255) | UNIQUE |
| tool_type | varchar(50) | |
| description | varchar(255) | |
| mapping_source | varchar | 映射创建方式（Issue #2761） |
| mapping_status | varchar | 映射生命周期状态 |
| verification_status | varchar | 账号映射核验（Issue #3273） |
| verified_at | timestamp | 核验时间 |
| tenant_id | integer | 租户维度 |

#### user_daily_stats

按用户预聚合的日用量，优化查询。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | |
| requests | integer | DEFAULT 0 |
| tokens | integer | DEFAULT 0 |
| input_tokens | integer | DEFAULT 0 |
| output_tokens | integer | DEFAULT 0 |
| cache_tokens | integer | DEFAULT 0 |

唯一约束：`(user_id, date)`

### 消息与会话

三张表的读写边界是契约性的——见
[`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md)。

#### daily_messages

核心消息事实表——全量 AI 交互的分析存储。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| date | varchar | NOT NULL |
| tool_name | varchar | NOT NULL |
| host_name | varchar | DEFAULT 'localhost' |
| message_id | varchar | NOT NULL |
| parent_id | varchar | |
| role | varchar | NOT NULL（user/assistant/system） |
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
| agent_session_id | varchar | 关联 agent_sessions |
| user_id | integer | |
| project_path | text | |
| tenant_id | integer | 租户维度 |
| feishu_conversation_id | varchar | 飞书群聊 |
| group_subject / is_group_chat | varchar / boolean | 群聊上下文 |

唯一约束：`(date, tool_name, message_id, host_name)`。18 个索引覆盖各类查询模式。

#### agent_sessions

权威的 Workspace 会话汇总表（每会话一行）。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| session_id | text | UNIQUE |
| tenant_id | integer | DEFAULT 1；租户维度会话查找与写入边界 |
| tenant_version | integer | 租户迁移跟踪 |
| session_type | text | DEFAULT 'chat' |
| title | text | |
| tool_name | text | NOT NULL |
| host_name | text | DEFAULT 'localhost' |
| user_id | integer | |
| status | text | DEFAULT 'active' |
| total_tokens | integer | DEFAULT 0 |
| total_input_tokens / total_output_tokens | integer | DEFAULT 0 |
| total_cache_read_tokens / total_cache_write_tokens | integer | 缓存令牌合计 |
| message_count | integer | DEFAULT 0 |
| request_count | integer | 去重后的 assistant 响应数（见会话数据契约） |
| model | text | |
| project_id | integer | |
| project_path | varchar(500) | |
| context / settings / tags | text | 会话上下文、设置、标签 |
| created_at / updated_at / completed_at / expires_at / paused_at | timestamp | 生命周期时间戳 |
| workspace_type | text | DEFAULT 'local' |
| remote_machine_id | text | 关联的远程机器 |
| cli_session_id | text | CLI 侧会话标识 |
| daily_usage_synced | boolean | 向 daily_usage 幂等同步的标志（Issue #2585） |

#### session_messages

agent 会话内的权威转录表。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| session_id | text | FK → agent_sessions(session_id) |
| tenant_id | integer | DEFAULT 1；租户维度消息查找 |
| role | text | NOT NULL |
| content | text | |
| tokens_used | integer | DEFAULT 0 |
| model | text | |
| timestamp | timestamp | |
| metadata | text | |
| milestone_id | varchar | 自治开发里程碑关联 |
| source | varchar | 写入路径来源标记 |
| source_timestamp | timestamp | 原始事件时间 |
| external_message_id | varchar | 提供商消息 id；与 (session_id, role) 共同构成幂等键 |
| content_blocks | text | 结构化内容回放 |

会话索引包括 `idx_agent_sessions_tenant_user`、`idx_agent_sessions_tenant_updated`、`idx_session_messages_tenant_session`、`idx_session_messages_tenant_session_timestamp`。

### 统计

#### daily_stats

按工具/主机/发送人聚合的日统计。

| 列 | 类型 | 说明 |
|----|------|------|
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
| user_id | integer | |
| tenant_id | integer | 租户维度 |

唯一约束：`(date, tool_name, host_name, sender_name)`

#### hourly_stats

按小时的用量分解。

| 列 | 类型 | 说明 |
|----|------|------|
| date | varchar(10) | NOT NULL |
| hour | integer | NOT NULL |
| tool_name | varchar(50) | NOT NULL |
| host_name | varchar(100) | DEFAULT 'localhost' |
| total_tokens | bigint | NOT NULL |
| total_input_tokens | bigint | NOT NULL |
| total_output_tokens | bigint | NOT NULL |
| message_count | integer | NOT NULL |
| tenant_id | integer | 租户维度 |

唯一约束：`(date, hour, tool_name, host_name)`

#### daily_usage

带缓存令牌跟踪的日用量。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1；租户维度用量聚合键 |
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

#### usage_summary

面向面板的按工具/主机全期汇总。

| 列 | 类型 | 说明 |
|----|------|------|
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

#### session_stats（物化视图）

从 daily_messages 中 `agent_session_id IS NOT NULL` 的行聚合而来，提供会话级令牌数、消息数与时间范围。这是模式中唯一的物化视图。

### 多租户

#### tenants

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| name | text | NOT NULL |
| slug | text | UNIQUE |
| status | text | CHECK IN ('active','suspended','trial','inactive') |
| plan | text | CHECK IN ('free','standard','premium','enterprise') |
| contact_email / contact_phone / contact_name | text | 联系信息 |
| quota / settings | text | JSON 配置 |
| user_count | integer | DEFAULT 0 |
| total_tokens_used | integer | DEFAULT 0 |
| total_requests_made | integer | DEFAULT 0 |
| billing_day / billing_cycle_type / billing_cycle_start / billing_cycle_end | 各异 | 计费周期 |
| current_cycle_tokens | bigint | 当前周期令牌数 |
| over_limit_strategy / over_limit_price_per_token | text/numeric | 超限处理策略 |
| usage_alert_threshold / usage_critical_threshold / alert_silence_hours | numeric | 用量告警阈值 |
| trial_ends_at / subscription_ends_at | timestamp | 生命周期 |
| deleted_at | timestamp | 软删除 |

#### tenant_settings（与 tenants 1:1）

| 列 | 类型 | 说明 |
|----|------|------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| content_filter_enabled | boolean | DEFAULT true |
| audit_log_enabled | boolean | DEFAULT true |
| audit_log_retention_days | integer | DEFAULT 90 |
| data_retention_days | integer | DEFAULT 365 |
| sso_enabled | boolean | DEFAULT false |
| sso_provider | text | 默认提供商名 |
| custom_branding / branding_name / branding_logo_url | 各异 | 品牌定制 |
| auto_provision_users | text | 自动开通策略 |
| block_sensitive_keyword / sensitive_keyword_match_mode | boolean/text | 敏感词策略 |
| allowed_tools / roi_assumptions | text | JSON 配置（Issue #2788） |

#### tenant_quotas（与 tenants 1:1）

| 列 | 类型 | 说明 |
|----|------|------|
| tenant_id | integer | UNIQUE FK → tenants(id) ON DELETE CASCADE |
| daily_token_limit | bigint | DEFAULT 1,000,000（按实际令牌数，Issue #1259） |
| monthly_token_limit | bigint | DEFAULT 30,000,000（按实际令牌数，Issue #1259） |
| daily_request_limit | bigint | DEFAULT 10,000（Issue #3018） |
| monthly_request_limit | bigint | DEFAULT 300,000（Issue #3018） |
| max_users | integer | DEFAULT 100 |
| max_sessions_per_user | integer | DEFAULT 5 |

#### tenant_usage

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| tenant_id | integer | FK → tenants(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| tokens_used | integer | DEFAULT 0 |
| requests_made | integer | DEFAULT 0 |
| active_users | integer | DEFAULT 0 |
| new_users | integer | DEFAULT 0 |

唯一约束：`(tenant_id, date)`

### SSO

#### sso_providers

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| name | text | UNIQUE |
| provider_type | text | NOT NULL（oauth2/oidc/saml） |
| config | text | NOT NULL（JSON；存储加密的 `client_secret_encrypted` 而非明文 `client_secret`） |
| tenant_id | integer | FK → tenants(id) |
| is_active | boolean | DEFAULT true |

#### sso_identities

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| provider_user_id | text | NOT NULL |
| provider_data | text | 提供商原始声明 |

唯一约束：`(provider_name, provider_user_id)`

#### sso_sessions

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| session_token | text | UNIQUE |
| user_id | integer | FK → users(id) |
| provider_name | text | NOT NULL |
| access_token | text | |
| refresh_token | text | |
| expires_at | timestamp | |

### 治理与合规

#### audit_logs

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| user_id | integer | |
| tenant_id | integer | 尽可能从操作者解析，用于租户维度的审计查询 |
| username | text | |
| action | text | NOT NULL |
| severity | text | DEFAULT 'info' |
| resource_type | text | |
| resource_id | text | |
| details | text | |
| ip_address | text | |
| user_agent | text | |
| session_id | text | |
| success | boolean | DEFAULT true |
| error_message | text | |

索引：`idx_audit_timestamp`、`idx_audit_user_id`、`idx_audit_tenant_id`、`idx_audit_action`、`idx_audit_severity`

#### content_filter_rules

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| pattern | text | NOT NULL |
| type | text | DEFAULT 'keyword' |
| severity | text | DEFAULT 'medium' |
| action | text | DEFAULT 'warn' |
| is_enabled | boolean | DEFAULT true |
| description | text | |

#### security_settings

安全配置键值存储。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| setting_key | varchar(100) | UNIQUE |
| setting_value | text | |
| description | text | |

#### anomaly_status

异常状态跟踪。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| anomaly_id | text | 稳定的异常标识（Issue #2749） |
| anomaly_type | varchar | 异常类型 |
| affected_users_hash | varchar | 受影响用户哈希 |
| tenant_id | integer | 租户维度 |
| status | varchar | 处理状态 |
| processed_by | integer | 处理者 ID |
| processed_at | timestamp | 处理时间 |
| created_at | timestamp | 创建时间 |

#### insights_reports

AI 生成的用量洞察报告。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| user_id | integer | 用户 ID |
| start_date | varchar | 开始日期 |
| end_date | varchar | 结束日期 |
| overall_score | integer | 总分 |
| overall_assessment | text | 总体评估 |
| strengths | text | 优势（JSON） |
| areas_for_improvement | text | 待改进项（JSON） |
| suggestions | text | 建议（JSON） |
| usage_summary | text | 用量摘要（JSON） |
| model | varchar | 所用模型 |
| raw_response | text | AI 原始响应 |
| created_at | timestamp | 创建时间 |

### 安全与权限

#### login_attempts

用于账号锁定的登录失败跟踪。

| 列 | 类型 | 说明 |
|----|------|------|
| username | varchar | NOT NULL，查找键 |
| attempt_count | integer | DEFAULT 0 |
| locked_until | timestamp | 锁定截止时间 |

#### user_permissions

用户级权限覆盖。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| user_id | integer | NOT NULL |
| permission | text | NOT NULL |
| granted_by | integer | 授权者 |
| granted_at | timestamp | DEFAULT CURRENT_TIMESTAMP |

索引：`idx_user_permissions_user`、`idx_user_permissions_permission`

#### role_permissions

角色权限模板。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| role | text | NOT NULL |
| permission | text | NOT NULL |

索引：`idx_role_permissions_role`、`idx_role_permissions_permission`

### 告警与配额

#### alerts

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| alert_id | text | UNIQUE |
| alert_type | text | NOT NULL |
| severity | text | NOT NULL |
| title | text | NOT NULL |
| message | text | |
| user_id | integer | |
| username | text | |
| tool_name | text | |
| metadata | text | |
| read | boolean | DEFAULT false |
| action_url / action_text | text | 内联操作链接 |

#### quota_usage

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| date | date | NOT NULL |
| period | text | DEFAULT 'daily' |
| tool_name | text | 按工具的配额维度 |
| tokens_used | integer | DEFAULT 0 |
| requests_used | integer | DEFAULT 0 |

唯一约束：`(user_id, date, period, tool_name)`

#### quota_alerts

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | FK → users(id) ON DELETE CASCADE |
| alert_type | text | NOT NULL |
| quota_type | text | NOT NULL |
| period | text | 配额周期 |
| threshold | real | NOT NULL |
| current_usage | integer | NOT NULL |
| quota_limit | integer | NOT NULL |
| percentage | real | NOT NULL |
| message | text | |
| acknowledged | boolean | DEFAULT false |
| acknowledged_at / acknowledged_by | timestamp/integer | 确认跟踪 |

#### notification_preferences

| 列 | 类型 | 说明 |
|----|------|------|
| user_id | integer | PK |
| email_enabled | boolean | DEFAULT true |
| push_enabled | boolean | DEFAULT true |
| webhook_url | text | 个人 Webhook |
| alert_types | text | 订阅的告警类型 |
| min_severity | text | DEFAULT 'warning' |
| notification_email | text | 专用通知邮箱 |
| email_verified | boolean | 邮箱核验 |
| dingtalk_webhook_secret | text | 加密的钉钉密钥 |

### 工作区与项目

#### projects

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| tenant_id | integer | DEFAULT 1；用于租户维度的项目查找与唯一性 |
| path | varchar(500) | 按生效的 `(tenant_id, path)` 唯一 |
| name | varchar(200) | |
| description | text | |
| created_by | integer | |
| is_active | boolean | DEFAULT true |
| is_shared | boolean | DEFAULT false |
| permission_status | text | 共享项目权限设置状态（Issue #2746） |
| permission_task_id | text | 关联的 permission_tasks 任务 |

索引：`idx_projects_created_by`、`idx_projects_is_active`、`idx_projects_path(tenant_id, path)`、`idx_projects_tenant_created_by`

#### user_projects

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| user_id | integer | NOT NULL |
| project_id | integer | NOT NULL |
| first_access_at / last_access_at | timestamp | 访问窗口 |
| total_sessions | integer | DEFAULT 0 |
| total_tokens | bigint | DEFAULT 0 |
| total_requests | integer | DEFAULT 0 |
| total_duration_seconds | integer | DEFAULT 0 |

唯一约束：`(user_id, project_id)`

#### prompt_templates

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| name | text | NOT NULL，UNIQUE（Issue #2577） |
| description | text | |
| category | text | DEFAULT 'general' |
| content | text | NOT NULL |
| variables | text | |
| tags | text | |
| author_id | integer | |
| author_name | text | |
| is_public | boolean | DEFAULT false |
| is_featured | boolean | DEFAULT false |
| use_count | integer | DEFAULT 0 |

### 远程工作区

#### remote_machines

远程工作区机器注册表。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| machine_id | text | 机器唯一标识 |
| machine_name | text | 机器名称 |
| hostname | text | 主机名 |
| os_type | text | 操作系统类型 |
| os_version | text | 操作系统版本 |
| ip_address | text | IP 地址 |
| status | text | 状态 |
| agent_version | text | agent 版本 |
| capabilities | text | 能力（JSON） |
| cli_path | text | CLI 路径 |
| work_dir | text | 工作目录 |
| tenant_id | integer | 租户 ID |
| created_by | integer | 创建者 ID |
| created_at / updated_at | timestamp | 时间戳 |
| last_heartbeat | timestamp | 最近心跳时间 |
| legacy_mode | boolean | 旧协议标志 |
| token_revoke_timeout | integer | 延迟撤销窗口（Issue #2499） |

#### remote_runtime_commands

远端 agent 的持久化 HTTP 轮询命令队列。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| command_id | varchar(64) | UNIQUE；命令-响应调用的 request_id |
| machine_id | text | 目标远程机器 |
| session_id | text | 可选的远程会话 |
| command_type | text | 命令名 |
| payload | text | JSON 命令载荷 |
| status | varchar(32) | `pending`、`delivered` 或 `responded` |
| response_payload | text | 同步命令的 JSON 响应载荷 |
| created_at | timestamp | 创建时间 |
| delivered_at | timestamp | agent 拉取认领时间 |
| responded_at | timestamp | 响应到达时间 |
| expires_at | timestamp | 保留截止时间 |

索引：`idx_remote_runtime_commands_machine_status`、`idx_remote_runtime_commands_expires`

#### remote_runtime_outputs

远程会话输出的持久化 SSE 回放缓冲。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| session_id | text | 远程会话 ID |
| event_index | integer | 会话内单调递增的输出序号 |
| stream | text | stdout、stderr、system、permission、request_state |
| payload | text | JSON 输出载荷 |
| created_at | timestamp | 创建时间 |
| expires_at | timestamp | 保留截止时间 |

唯一约束：`(session_id, event_index)`

索引：`idx_remote_runtime_outputs_session_index`、`idx_remote_runtime_outputs_expires`

#### machine_assignments

用户与远程机器的分配关系。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| machine_id | text | 机器 ID |
| user_id | integer | 用户 ID |
| permission | text | 权限级别 |
| granted_by | integer | 授权者 ID |
| granted_at | timestamp | 授权时间 |

#### api_key_store

加密的 API 密钥存储。

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | 自增 |
| tenant_id | integer | 租户 ID |
| provider | text | AI 服务提供商 |
| key_name | text | 密钥名称 |
| encrypted_key | text | 加密后的密钥 |
| key_hash | text | 密钥哈希 |
| base_url | text | API 基础 URL |
| is_active | boolean | DEFAULT true |
| scope / priority / weight | text/integer | 路由属性 |
| resolved_ips / resolved_at | text/timestamp | 固定出口 IP（Issue #1894） |
| created_by | integer | 创建者 ID |
| created_at / updated_at | timestamp | 时间戳 |
| cli_tools | text | CLI 工具配置 |
| cli_settings | text | CLI 设置 |

### 协作

#### teams

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| team_id | text | UNIQUE |
| name | text | NOT NULL |
| description | text | |
| owner_id | integer | |
| settings | text | 团队设置（JSON） |

#### team_members

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| team_id | text | NOT NULL |
| user_id | integer | NOT NULL |
| username | text | 反规范化的成员名 |
| role | text | DEFAULT 'member' |
| joined_at | timestamp | |

唯一约束：`(team_id, user_id)`

#### shared_sessions

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| share_id | text | UNIQUE |
| session_id | text | NOT NULL |
| shared_by | integer | |
| shared_by_name | text | 反规范化的分享者名 |
| permission | text | DEFAULT 'view' |
| share_type / target_id / target_name | text | 分享目标（用户/团队/链接） |
| allow_comments | boolean | DEFAULT true |
| allow_copy | boolean | DEFAULT true |
| expires_at | timestamp | |
| access_count | integer | DEFAULT 0 |
| last_accessed | timestamp | |

#### knowledge_base

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| entry_id | text | UNIQUE |
| team_id | text | |
| title | text | NOT NULL |
| content | text | |
| category | text | DEFAULT 'general' |
| tags | text | |
| author_id | integer | |
| author_name | text | |
| is_published | boolean | DEFAULT false |
| view_count | integer | DEFAULT 0 |

#### annotations

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| annotation_id | text | UNIQUE |
| session_id | text | NOT NULL |
| message_id | text | |
| user_id | integer | |
| username | text | 反规范化的作者名 |
| content | text | |
| annotation_type | text | DEFAULT 'comment' |
| parent_id | text | 父批注（回复串嵌套） |

### 同步与保留快照

#### sync_events

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| event_id | text | UNIQUE |
| event_type | text | NOT NULL |
| source | text | |
| session_id | text | |
| user_id | integer | |
| tool_name | text | |
| data | text | |
| metadata | text | |

#### retention_history

| 列 | 类型 | 说明 |
|----|------|------|
| id | integer PK | |
| timestamp | timestamp | DEFAULT CURRENT_TIMESTAMP |
| report_data | text | NOT NULL |

## 外键汇总

| 子表 | 列 | 父表 | 删除时 |
|------|----|------|--------|
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

## 跨数据库兼容

命名约定见 [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md)。`app/repositories/database.py` 中的 `adapt_sql()` 函数自动处理占位符转换（`?` → `%s`）。

## 相关文档

- [`schema/schema-postgres.sql`](../../schema/schema-postgres.sql) — 权威的逐列参考（104 张表 + 1 个物化视图）
- [`../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md`](../contracts/WORKSPACE_SESSION_DATA_CONTRACT.md) — `agent_sessions`、`session_messages`、`daily_messages` 三表的数据边界契约
- [SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md) — 模式变更如何发布
- [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md) — 字段命名与类型约定

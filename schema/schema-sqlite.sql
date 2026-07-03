-- Open-ACE Database Schema for SQLite
-- Converted from schema-postgres.sql
-- DO NOT EDIT MANUALLY

-- Setup session

CREATE TABLE agent_approvals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 request_id text NOT NULL,
 run_id text,
 session_id text,
 tool_name text,
 request_subtype text,
 request_details text,
 status text DEFAULT 'pending',
 decision text,
 decided_by integer,
 decided_by_name text,
 decision_metadata text,
 requested_at TIMESTAMP,
 decided_at TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agent_run_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id text,
 session_id text,
 event_type text DEFAULT '' NOT NULL,
 event_subtype text,
 role text,
 content text,
 tool_name text,
 provider text,
 model text,
 key_id text,
 user_id integer,
 tenant_id integer,
 machine_id text,
 metadata text,
 event_ts TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agent_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id text NOT NULL,
 session_id text NOT NULL,
 user_id integer,
 tenant_id integer,
 machine_id text,
 tool_name text,
 provider text,
 cli_tool text,
 model text,
 status text DEFAULT 'active',
 started_at TIMESTAMP,
 ended_at TIMESTAMP,
 total_tokens integer DEFAULT 0,
 total_input_tokens integer DEFAULT 0,
 total_output_tokens integer DEFAULT 0,
 total_requests integer DEFAULT 0,
 metadata text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agent_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id text NOT NULL,
 session_type text DEFAULT 'chat',
 title text,
 tool_name text NOT NULL,
 host_name text DEFAULT 'localhost',
 user_id integer,
 status text DEFAULT 'active',
 context text,
 settings text,
 total_tokens integer DEFAULT 0,
 total_input_tokens integer DEFAULT 0,
 total_output_tokens integer DEFAULT 0,
 message_count integer DEFAULT 0,
 model text,
 tags text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 completed_at TIMESTAMP,
 expires_at TIMESTAMP,
 project_id integer,
 project_path TEXT,
 request_count integer DEFAULT 0,
 workspace_type text DEFAULT 'local',
 remote_machine_id text,
 paused_at TIMESTAMP,
 cli_session_id text DEFAULT ''
);

CREATE TABLE agent_tokens (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token_hash TEXT NOT NULL,
 machine_id TEXT NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 is_revoked INTEGER DEFAULT 0,
 revoked_at TIMESTAMP,
 revoked_by integer,
 rotated_at TIMESTAMP
);

CREATE TABLE ai_agent_settings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 setting_key TEXT NOT NULL,
 setting_value text,
 description text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE alerts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 alert_id text NOT NULL,
 alert_type text NOT NULL,
 severity text NOT NULL,
 title text NOT NULL,
 message text,
 user_id integer,
 username text,
 tool_name text,
 metadata text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 read INTEGER DEFAULT 0,
 action_url text,
 action_text text
);

CREATE TABLE annotations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 annotation_id text NOT NULL,
 session_id text NOT NULL,
 message_id text,
 user_id integer,
 username text,
 content text,
 annotation_type text DEFAULT 'comment',
 "position" text,
 parent_id integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE anomaly_status (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 anomaly_type TEXT NOT NULL,
 affected_users_hash TEXT NOT NULL,
 status TEXT DEFAULT 'pending' NOT NULL,
 processed_by integer,
 processed_at TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE api_key_store (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 tenant_id integer,
 provider text NOT NULL,
 key_name text NOT NULL,
 encrypted_key text NOT NULL,
 key_hash text NOT NULL,
 base_url text,
 is_active INTEGER DEFAULT 1,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 cli_tools text,
 cli_settings text,
 scope text DEFAULT 'shared',
 priority integer DEFAULT 0,
 weight integer DEFAULT 100
);

CREATE TABLE audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 user_id integer,
 username text,
 action text NOT NULL,
 severity text DEFAULT 'info',
 resource_type text,
 resource_id text,
 details text,
 ip_address text,
 user_agent text,
 session_id text,
 success INTEGER DEFAULT 1,
 error_message text
);

CREATE TABLE autonomous_workflows (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 workflow_id TEXT NOT NULL,
 user_id integer,
 title text DEFAULT '',
 status text DEFAULT 'pending',
 requirements_text text DEFAULT '',
 requirements_issue_url text DEFAULT '',
 project_path text DEFAULT '',
 project_repo_url text DEFAULT '',
 is_new_project INTEGER DEFAULT 0,
 is_private INTEGER DEFAULT 1,
 cli_tool text DEFAULT '',
 model text DEFAULT '',
 permission_mode text DEFAULT 'auto-edit',
 branch_name text DEFAULT '',
 branch_strategy text DEFAULT 'new-branch',
 workspace_type text DEFAULT 'local',
 remote_machine_id text DEFAULT '',
 worktree_path text DEFAULT '',
 github_issue_number integer,
 github_pr_number integer,
 github_pr_url text DEFAULT '',
 current_phase text DEFAULT 'preparation',
 current_round integer DEFAULT 0,
 dev_round integer DEFAULT 1,
 max_plan_rounds integer DEFAULT 3,
 max_pr_review_rounds integer DEFAULT 5,
 require_full_review_rounds integer DEFAULT 0,
 total_tokens integer DEFAULT 0,
 total_input_tokens integer DEFAULT 0,
 total_output_tokens integer DEFAULT 0,
 total_requests integer DEFAULT 0,
 error_message text DEFAULT '',
 created_at TIMESTAMP,
 updated_at TIMESTAMP,
 completed_at TIMESTAMP,
 paused_at TIMESTAMP,
 planning_timeout_extension integer DEFAULT 0,
 parent_workflow_id text,
 fork_milestone_id text,
 user_feedback text DEFAULT '',
 original_branch_name text DEFAULT '',
 batch_id text,
 batch_order integer,
 batch_total integer,
 auto_merge INTEGER DEFAULT 1,
 definition_snapshot text,
 agent_pid integer,
 agent_session_id text DEFAULT '' NOT NULL,
 main_session_id text DEFAULT '' NOT NULL,
 review_session_id text DEFAULT '' NOT NULL,
 test_session_id text DEFAULT '' NOT NULL,
 content_language text DEFAULT 'en' NOT NULL,
 locked_at TIMESTAMP,
 locked_by text DEFAULT '',
 transient_retry_count integer DEFAULT 0,
 retry_count integer DEFAULT 0
);

CREATE TABLE compliance_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 report_id text NOT NULL,
 report_type text NOT NULL,
 generated_at TIMESTAMP NOT NULL,
 period_start TIMESTAMP NOT NULL,
 period_end TIMESTAMP NOT NULL,
 generated_by integer,
 tenant_id integer,
 report_data text NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE content_filter_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 pattern text NOT NULL,
 type text DEFAULT 'keyword',
 severity text DEFAULT 'medium',
 action text DEFAULT 'warn',
 is_enabled INTEGER DEFAULT 1,
 description text,
 created_at TIMESTAMP NOT NULL,
 updated_at TIMESTAMP
);

CREATE TABLE daily_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 date TEXT NOT NULL,
 tool_name TEXT NOT NULL,
 host_name TEXT DEFAULT 'localhost' NOT NULL,
 message_id TEXT NOT NULL,
 parent_id TEXT,
 role TEXT NOT NULL,
 content text,
 full_entry text,
 tokens_used integer DEFAULT 0,
 input_tokens integer DEFAULT 0,
 output_tokens integer DEFAULT 0,
 model TEXT,
 "timestamp" TIMESTAMP,
 sender_id TEXT,
 sender_name TEXT,
 message_source TEXT,
 feishu_conversation_id TEXT,
 group_subject TEXT,
 is_group_chat INTEGER,
 agent_session_id TEXT,
 conversation_id TEXT,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 deleted_at TIMESTAMP,
 user_id integer,
 project_path text,
    CONSTRAINT chk_daily_messages_input_tokens_positive CHECK ((input_tokens >= 0)),
    CONSTRAINT chk_daily_messages_output_tokens_positive CHECK ((output_tokens >= 0)),
    CONSTRAINT chk_daily_messages_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE TABLE daily_stats (
 date TEXT NOT NULL,
 tool_name TEXT NOT NULL,
 host_name TEXT DEFAULT 'localhost' NOT NULL,
 sender_name TEXT,
 total_tokens INTEGER NOT NULL,
 total_input_tokens INTEGER NOT NULL,
 total_output_tokens INTEGER NOT NULL,
 message_count integer NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 project_id integer,
 project_path TEXT,
 user_id integer
);

CREATE TABLE daily_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 date TEXT NOT NULL,
 tool_name TEXT NOT NULL,
 host_name TEXT DEFAULT 'localhost' NOT NULL,
 tokens_used integer DEFAULT 0,
 input_tokens integer DEFAULT 0,
 output_tokens integer DEFAULT 0,
 cache_tokens integer DEFAULT 0,
 request_count integer DEFAULT 0,
 models_used text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_daily_usage_cache_tokens_positive CHECK ((cache_tokens >= 0)),
    CONSTRAINT chk_daily_usage_input_tokens_positive CHECK ((input_tokens >= 0)),
    CONSTRAINT chk_daily_usage_output_tokens_positive CHECK ((output_tokens >= 0)),
    CONSTRAINT chk_daily_usage_request_count_positive CHECK ((request_count >= 0)),
    CONSTRAINT chk_daily_usage_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE TABLE email_notification_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 alert_id TEXT,
 recipient_email TEXT NOT NULL,
 subject TEXT NOT NULL,
 email_body text,
 sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 status TEXT DEFAULT 'pending' NOT NULL,
 error_message text,
 retry_count integer DEFAULT 0,
 next_retry_at TIMESTAMP
);

CREATE TABLE hourly_stats (
 date TEXT NOT NULL,
 hour integer NOT NULL,
 tool_name TEXT NOT NULL,
 host_name TEXT DEFAULT 'localhost' NOT NULL,
 total_tokens INTEGER NOT NULL,
 total_input_tokens INTEGER NOT NULL,
 total_output_tokens INTEGER NOT NULL,
 message_count integer NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE insights_reports (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 start_date TEXT NOT NULL,
 end_date TEXT NOT NULL,
 overall_score integer,
 overall_assessment text,
 strengths text,
 areas_for_improvement text,
 suggestions text,
 usage_summary text,
 model TEXT,
 raw_response text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE knowledge_base (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entry_id text NOT NULL,
 team_id text,
 title text NOT NULL,
 content text,
 category text DEFAULT 'general',
 tags text,
 author_id integer,
 author_name text,
 is_published INTEGER DEFAULT 0,
 view_count integer DEFAULT 0,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE login_attempts (
 username TEXT PRIMARY KEY NOT NULL,
 attempt_count integer DEFAULT 0 NOT NULL,
 locked_until TIMESTAMP
);

CREATE TABLE machine_assignments (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 machine_id text NOT NULL,
 user_id integer NOT NULL,
 permission text DEFAULT 'user',
 granted_by integer,
 granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE model_gateway_config (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 mode text DEFAULT 'direct',
 base_url text,
 encrypted_api_key text,
 encryption_version integer DEFAULT 1,
 model_prefix_mode INTEGER DEFAULT 0,
 model_prefix text,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE notification_preferences (
 user_id INTEGER PRIMARY KEY AUTOINCREMENT,
 email_enabled INTEGER DEFAULT 1,
 push_enabled INTEGER DEFAULT 1,
 webhook_url text,
 alert_types text,
 min_severity text DEFAULT 'warning',
 notification_email text,
 email_verified INTEGER DEFAULT 0
);

CREATE TABLE policy_decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 decision_id text NOT NULL,
 request_id text,
 run_id text,
 session_id text,
 tenant_id integer,
 workspace_scope text,
 machine_id text,
 model text,
 provider text,
 tool_name text,
 action text,
 resource_target text,
 args_digest text,
 normalization_profile_id text,
 normalization_profile_version integer,
 fingerprint_hash text,
 policy_rule_id integer,
 policy_rule_version integer,
 decision text NOT NULL,
 reason text,
 reviewer_identity text,
 issued_at TIMESTAMP,
 expires_at TIMESTAMP,
 consumed_at TIMESTAMP,
 remote_response_id text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE policy_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 rule_key text NOT NULL,
 name text NOT NULL,
 version integer DEFAULT 1 NOT NULL,
 is_current INTEGER DEFAULT 1,
 enabled INTEGER DEFAULT 1,
 tenant_id integer,
 project_path text,
 machine_id text,
 user_id integer,
 team_id text,
 policy_type text NOT NULL,
 pattern_type text DEFAULT 'glob',
 pattern text,
 value_list text,
 tool_name text,
 action text,
 effect text NOT NULL,
 priority integer DEFAULT 100,
 is_default INTEGER DEFAULT 0,
 approval_ttl_seconds integer,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 superseded_at TIMESTAMP,
 description text
);

CREATE TABLE project_categories (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name text NOT NULL,
 key_patterns text NOT NULL,
 sort_order integer DEFAULT 0,
 is_active INTEGER DEFAULT 1,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 path TEXT NOT NULL,
 name TEXT,
 description text,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 is_active INTEGER DEFAULT 1 NOT NULL,
 is_shared INTEGER DEFAULT 0 NOT NULL
);

CREATE TABLE prompt_templates (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name text NOT NULL,
 description text,
 category text DEFAULT 'general',
 content text NOT NULL,
 variables text,
 tags text,
 author_id integer,
 author_name text,
 is_public INTEGER DEFAULT 0,
 is_featured INTEGER DEFAULT 0,
 use_count integer DEFAULT 0,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE quota_alerts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 alert_type text NOT NULL,
 quota_type text NOT NULL,
 period text DEFAULT 'daily',
 threshold real NOT NULL,
 current_usage integer NOT NULL,
 quota_limit integer NOT NULL,
 percentage real NOT NULL,
 message text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 acknowledged INTEGER DEFAULT 0,
 acknowledged_at TIMESTAMP,
 acknowledged_by integer
);

CREATE TABLE quota_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 date TEXT NOT NULL,
 period text DEFAULT 'daily',
 tokens_used integer DEFAULT 0,
 requests_used integer DEFAULT 0,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 tool_name text,
    CONSTRAINT chk_quota_usage_requests_positive CHECK ((requests_used >= 0)),
    CONSTRAINT chk_quota_usage_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE TABLE registration_tokens (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token_hash TEXT NOT NULL,
 tenant_id integer NOT NULL,
 created_by integer NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 expires_at TIMESTAMP,
 is_consumed INTEGER DEFAULT 0,
 consumed_at TIMESTAMP
);

CREATE TABLE remote_machines (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 machine_id text NOT NULL,
 machine_name text NOT NULL,
 hostname text,
 os_type text,
 os_version text,
 ip_address text,
 status text DEFAULT 'offline',
 agent_version text,
 capabilities text,
 cli_path text,
 work_dir text,
 tenant_id integer,
 created_by integer,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 last_heartbeat TIMESTAMP,
 legacy_mode INTEGER DEFAULT 0
);

CREATE TABLE retention_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 report_data text NOT NULL
);

CREATE TABLE role_permissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 role text NOT NULL,
 permission text NOT NULL
);

CREATE TABLE security_settings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 setting_key TEXT NOT NULL,
 setting_value text,
 description text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE session_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id text NOT NULL,
 role text NOT NULL,
 content text,
 tokens_used integer DEFAULT 0,
 model text,
 "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 metadata text,
 milestone_id text DEFAULT '' NOT NULL,
 source text DEFAULT '' NOT NULL,
 source_timestamp TIMESTAMP,
 external_message_id text DEFAULT '' NOT NULL,
 content_blocks text
);

CREATE TABLE sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token TEXT NOT NULL,
 user_id integer NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 expires_at TIMESTAMP NOT NULL,
 is_active INTEGER DEFAULT 1
);

CREATE TABLE shared_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 share_id text NOT NULL,
 session_id text NOT NULL,
 shared_by integer,
 shared_by_name text,
 permission text DEFAULT 'view',
 share_type text DEFAULT 'user',
 target_id integer,
 target_name text,
 expires_at TIMESTAMP,
 allow_comments INTEGER DEFAULT 1,
 allow_copy INTEGER DEFAULT 1,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 access_count integer DEFAULT 0,
 last_accessed TIMESTAMP
);

CREATE TABLE smtp_settings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 smtp_host TEXT NOT NULL,
 smtp_port integer DEFAULT 587 NOT NULL,
 smtp_user TEXT,
 encrypted_password text,
 encryption_version integer DEFAULT 1,
 from_address TEXT NOT NULL,
 use_tls INTEGER DEFAULT 1,
 is_verified INTEGER DEFAULT 0,
 last_verified_at TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 created_by integer
);

CREATE TABLE sso_identities (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 provider_name text NOT NULL,
 provider_user_id text NOT NULL,
 provider_data text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 last_used_at TIMESTAMP
);

CREATE TABLE sso_providers (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name text NOT NULL,
 provider_type text NOT NULL,
 config text NOT NULL,
 tenant_id integer,
 is_active INTEGER DEFAULT 1,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sso_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_token text NOT NULL,
 user_id integer NOT NULL,
 provider_name text NOT NULL,
 access_token text,
 refresh_token text,
 expires_at TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sync_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id text NOT NULL,
 event_type text NOT NULL,
 "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 source text,
 session_id text,
 user_id integer,
 tool_name text,
 data text,
 metadata text
);

CREATE TABLE team_members (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 team_id text NOT NULL,
 user_id integer NOT NULL,
 username text,
 role text DEFAULT 'member',
 joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE teams (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 team_id text NOT NULL,
 name text NOT NULL,
 description text,
 owner_id integer,
 settings text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_quotas (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 tenant_id integer NOT NULL,
 daily_token_limit INTEGER DEFAULT 1000000,
 monthly_token_limit INTEGER DEFAULT 30000000,
 daily_request_limit integer DEFAULT 10000,
 monthly_request_limit integer DEFAULT 300000,
 max_users integer DEFAULT 100,
 max_sessions_per_user integer DEFAULT 5,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_settings (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 tenant_id integer NOT NULL,
 content_filter_enabled INTEGER DEFAULT 1,
 audit_log_enabled INTEGER DEFAULT 1,
 audit_log_retention_days integer DEFAULT 90,
 data_retention_days integer DEFAULT 365,
 sso_enabled INTEGER DEFAULT 0,
 sso_provider TEXT,
 custom_branding INTEGER DEFAULT 0,
 branding_name TEXT,
 branding_logo_url TEXT,
 auto_provision_users INTEGER DEFAULT 0,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_usage (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 tenant_id integer NOT NULL,
 date TEXT NOT NULL,
 tokens_used integer DEFAULT 0,
 requests_made integer DEFAULT 0,
 active_users integer DEFAULT 0,
 new_users integer DEFAULT 0,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenants (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name text NOT NULL,
 slug text NOT NULL,
 status text DEFAULT 'active',
 plan text DEFAULT 'standard',
 contact_email text,
 contact_phone text,
 contact_name text,
 quota text,
 settings text,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 trial_ends_at TIMESTAMP,
 subscription_ends_at TIMESTAMP,
 user_count integer DEFAULT 0,
 total_tokens_used integer DEFAULT 0,
 total_requests_made integer DEFAULT 0,
 deleted_at TIMESTAMP,
    CONSTRAINT chk_tenants_plan CHECK (plan IN ('free', 'standard', 'premium', 'enterprise')),
    CONSTRAINT chk_tenants_status CHECK (status IN ('active', 'suspended', 'trial', 'inactive'))
);

CREATE TABLE tool_account_mapping_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 pattern TEXT NOT NULL,
 match_type TEXT DEFAULT 'exact' NOT NULL,
 tool_type TEXT,
 priority integer DEFAULT 0 NOT NULL,
 is_auto INTEGER DEFAULT 1 NOT NULL,
 is_active INTEGER DEFAULT 1 NOT NULL,
 description TEXT,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE usage_summary (
 tool_name TEXT NOT NULL,
 host_name TEXT,
 days_count integer NOT NULL,
 total_tokens INTEGER NOT NULL,
 avg_tokens INTEGER NOT NULL,
 total_requests integer NOT NULL,
 total_input_tokens INTEGER NOT NULL,
 total_output_tokens INTEGER NOT NULL,
 first_date TEXT,
 last_date TEXT,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE user_daily_stats (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 date TEXT NOT NULL,
 requests integer DEFAULT 0 NOT NULL,
 tokens integer DEFAULT 0 NOT NULL,
 input_tokens integer DEFAULT 0 NOT NULL,
 output_tokens integer DEFAULT 0 NOT NULL,
 cache_tokens integer DEFAULT 0 NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE user_permissions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 permission text NOT NULL,
 granted_by integer,
 granted_at TIMESTAMP
);

CREATE TABLE user_projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 project_id integer NOT NULL,
 first_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 last_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
 total_sessions integer DEFAULT 0 NOT NULL,
 total_tokens INTEGER DEFAULT 0 NOT NULL,
 total_requests integer DEFAULT 0 NOT NULL,
 total_duration_seconds integer DEFAULT 0 NOT NULL
);

CREATE TABLE user_tool_accounts (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 tool_account TEXT NOT NULL,
 tool_type TEXT,
 description TEXT,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT NOT NULL,
 password_hash TEXT NOT NULL,
 email TEXT,
 is_admin INTEGER DEFAULT 0,
 is_active INTEGER DEFAULT 1,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 last_login TIMESTAMP,
 role TEXT DEFAULT 'user',
 daily_token_quota integer,
 monthly_token_quota integer,
 daily_request_quota integer,
 monthly_request_quota integer,
 deleted_at TIMESTAMP,
 system_account text,
 tenant_id integer,
 must_change_password INTEGER DEFAULT 0,
 avatar_url TEXT,
 auto_mapping_enabled INTEGER DEFAULT 1,
    CONSTRAINT chk_users_role CHECK ((role IN (('admin'), ('manager'), ('user'))))
);

CREATE TABLE web_user_auth_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id integer NOT NULL,
 session_token text NOT NULL,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 expires_at TIMESTAMP NOT NULL
);

CREATE TABLE workflow_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 workflow_id TEXT NOT NULL,
 milestone_id TEXT DEFAULT '',
 event_type text DEFAULT '' NOT NULL,
 event_data text DEFAULT '',
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflow_milestones (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 workflow_id TEXT NOT NULL,
 milestone_id TEXT NOT NULL,
 phase text DEFAULT '' NOT NULL,
 dev_round integer DEFAULT 1,
 round_number integer DEFAULT 0,
 milestone_type text DEFAULT '' NOT NULL,
 status text DEFAULT 'pending',
 title text DEFAULT '',
 description text DEFAULT '',
 session_id text DEFAULT '',
 review_session_id text DEFAULT '',
 github_issue_number integer,
 github_pr_number integer,
 github_comment_id text DEFAULT '',
 commit_shas text DEFAULT '',
 diff_stats text DEFAULT '',
 result_summary text DEFAULT '',
 plan_content text DEFAULT '',
 review_content text DEFAULT '',
 error_message text DEFAULT '',
 parent_milestone_id text DEFAULT '',
 fork_branch text DEFAULT '',
 metadata text DEFAULT '',
 started_at TIMESTAMP,
 completed_at TIMESTAMP,
 created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
 fork_workflow_id text DEFAULT '',
 phase_total_tokens integer DEFAULT 0 NOT NULL,
 phase_input_tokens integer DEFAULT 0 NOT NULL,
 phase_output_tokens integer DEFAULT 0 NOT NULL,
 phase_request_count integer DEFAULT 0 NOT NULL,
 tldr text DEFAULT '' NOT NULL
);

CREATE UNIQUE INDEX agent_approvals_request_id_key ON agent_approvals (request_id);

CREATE UNIQUE INDEX agent_runs_run_id_key ON agent_runs (run_id);

CREATE UNIQUE INDEX agent_sessions_session_id_key ON agent_sessions (session_id);

CREATE UNIQUE INDEX agent_tokens_token_hash_key ON agent_tokens (token_hash);

CREATE UNIQUE INDEX ai_agent_settings_setting_key_key ON ai_agent_settings (setting_key);

CREATE UNIQUE INDEX alerts_alert_id_key ON alerts (alert_id);

CREATE UNIQUE INDEX annotations_annotation_id_key ON annotations (annotation_id);

CREATE UNIQUE INDEX api_key_store_tenant_id_provider_key_name_key ON api_key_store (tenant_id, provider, key_name);

CREATE UNIQUE INDEX autonomous_workflows_workflow_id_key ON autonomous_workflows (workflow_id);

CREATE UNIQUE INDEX compliance_reports_report_id_key ON compliance_reports (report_id);

CREATE UNIQUE INDEX knowledge_base_entry_id_key ON knowledge_base (entry_id);

CREATE UNIQUE INDEX machine_assignments_machine_id_user_id_key ON machine_assignments (machine_id, user_id);

CREATE UNIQUE INDEX registration_tokens_token_hash_key ON registration_tokens (token_hash);

CREATE UNIQUE INDEX remote_machines_machine_id_key ON remote_machines (machine_id);

CREATE UNIQUE INDEX role_permissions_role_permission_key ON role_permissions (role, permission);

CREATE UNIQUE INDEX security_settings_setting_key_key ON security_settings (setting_key);

CREATE UNIQUE INDEX sessions_new_token_key1 ON sessions (token);

CREATE UNIQUE INDEX shared_sessions_share_id_key ON shared_sessions (share_id);

CREATE UNIQUE INDEX sso_identities_provider_name_provider_user_id_key ON sso_identities (provider_name, provider_user_id);

CREATE UNIQUE INDEX sso_providers_name_key ON sso_providers (name);

CREATE UNIQUE INDEX sso_sessions_session_token_key ON sso_sessions (session_token);

CREATE UNIQUE INDEX sync_events_event_id_key ON sync_events (event_id);

CREATE UNIQUE INDEX team_members_team_id_user_id_key ON team_members (team_id, user_id);

CREATE UNIQUE INDEX teams_team_id_key ON teams (team_id);

CREATE UNIQUE INDEX tenant_quotas_tenant_id_key ON tenant_quotas (tenant_id);

CREATE UNIQUE INDEX tenant_settings_tenant_id_key ON tenant_settings (tenant_id);

CREATE UNIQUE INDEX tenants_slug_key ON tenants (slug);

CREATE UNIQUE INDEX uq_daily_messages_date_tool_msg_host ON daily_messages (date, tool_name, message_id, host_name);

CREATE UNIQUE INDEX uq_daily_stats_date_tool_host_sender ON daily_stats (date, tool_name, host_name, sender_name);

CREATE UNIQUE INDEX uq_daily_usage_date_tool_host ON daily_usage (date, tool_name, host_name);

CREATE UNIQUE INDEX uq_hourly_stats_date_hour_tool_host ON hourly_stats (date, hour, tool_name, host_name);

CREATE UNIQUE INDEX uq_mapping_rule_user_pattern ON tool_account_mapping_rules (user_id, pattern, match_type);

CREATE UNIQUE INDEX uq_quota_usage_user_date_period_new ON quota_usage (user_id, date, period);

CREATE UNIQUE INDEX uq_tenant_usage_tenant_date_new ON tenant_usage (tenant_id, date);

CREATE UNIQUE INDEX uq_usage_summary_tool_host ON usage_summary (tool_name, host_name);

CREATE UNIQUE INDEX uq_user_daily_stats_user_date ON user_daily_stats (user_id, date);

CREATE UNIQUE INDEX uq_user_tool_account ON user_tool_accounts (tool_account);

CREATE UNIQUE INDEX user_permissions_user_id_permission_key ON user_permissions (user_id, permission);

CREATE UNIQUE INDEX users_username_key ON users (username);

CREATE UNIQUE INDEX web_user_auth_sessions_session_token_key ON web_user_auth_sessions (session_token);

CREATE UNIQUE INDEX workflow_milestones_milestone_id_key ON workflow_milestones (milestone_id);

CREATE INDEX idx_agent_approvals_run_id ON agent_approvals (run_id);

CREATE INDEX idx_agent_approvals_session_id ON agent_approvals (session_id);

CREATE INDEX idx_agent_approvals_status ON agent_approvals (status);

CREATE UNIQUE INDEX idx_agent_runs_session_id ON agent_runs (session_id);

CREATE INDEX idx_agent_runs_status ON agent_runs (status);

CREATE INDEX idx_agent_runs_user_id ON agent_runs (user_id);

CREATE INDEX idx_agent_sessions_project ON agent_sessions (project_id);

CREATE INDEX idx_agent_sessions_session_id ON agent_sessions (session_id);

CREATE INDEX idx_agent_sessions_session_type ON agent_sessions (session_type);

CREATE INDEX idx_agent_sessions_status ON agent_sessions (status);

CREATE INDEX idx_agent_sessions_tool_name ON agent_sessions (tool_name);

CREATE INDEX idx_agent_sessions_user_id ON agent_sessions (user_id);

CREATE INDEX idx_agent_tokens_hash ON agent_tokens (token_hash);

CREATE INDEX idx_agent_tokens_machine ON agent_tokens (machine_id);

CREATE INDEX idx_ai_agent_settings_key ON ai_agent_settings (setting_key);

CREATE INDEX idx_alerts_created_at ON alerts (created_at);

CREATE INDEX idx_alerts_read ON alerts (read);

CREATE INDEX idx_alerts_user_id ON alerts (user_id);

CREATE INDEX idx_annotations_session ON annotations (session_id);

CREATE INDEX idx_api_key_store_tenant_provider ON api_key_store (tenant_id, provider);

CREATE INDEX idx_audit_action ON audit_logs (action);

CREATE INDEX idx_audit_resource ON audit_logs (resource_type, resource_id);

CREATE INDEX idx_audit_severity ON audit_logs (severity);

CREATE INDEX idx_audit_timestamp ON audit_logs ("timestamp");

CREATE INDEX idx_audit_user_id ON audit_logs (user_id);

CREATE INDEX idx_daily_stats_date ON daily_stats (date);

CREATE INDEX idx_daily_stats_date_tool ON daily_stats (date, tool_name);

CREATE INDEX idx_daily_stats_date_tool_host ON daily_stats (date, tool_name, host_name);

CREATE INDEX idx_daily_stats_host ON daily_stats (host_name);

CREATE INDEX idx_daily_stats_project ON daily_stats (project_id);

CREATE INDEX idx_daily_stats_sender ON daily_stats (sender_name);

CREATE INDEX idx_daily_stats_tool ON daily_stats (tool_name);

CREATE INDEX idx_daily_stats_user_id ON daily_stats (user_id);

CREATE INDEX idx_email_logs_sent_at ON email_notification_logs (sent_at);

CREATE INDEX idx_email_logs_status ON email_notification_logs (status);

CREATE INDEX idx_email_logs_user_id ON email_notification_logs (user_id);

CREATE INDEX idx_email_logs_user_sent ON email_notification_logs (user_id, sent_at);

CREATE INDEX idx_events_workflow_created ON workflow_events (workflow_id, created_at);

CREATE INDEX idx_filter_rules_enabled ON content_filter_rules (is_enabled);

CREATE INDEX idx_filter_rules_type ON content_filter_rules (type);

CREATE INDEX idx_hourly_stats_date ON hourly_stats (date);

CREATE INDEX idx_hourly_stats_date_hour ON hourly_stats (date, hour);

CREATE INDEX idx_hourly_stats_hour ON hourly_stats (hour);

CREATE INDEX idx_insights_reports_user_date ON insights_reports (user_id, start_date, end_date);

CREATE INDEX idx_knowledge_team ON knowledge_base (team_id);

CREATE INDEX idx_login_attempts_locked_until ON login_attempts (locked_until);

CREATE INDEX idx_machine_assignments_user_id ON machine_assignments (user_id);

CREATE INDEX idx_mapping_rules_active ON tool_account_mapping_rules (is_active, priority);

CREATE INDEX idx_mapping_rules_user_id ON tool_account_mapping_rules (user_id);

CREATE INDEX idx_messages_agent_session_id ON daily_messages (agent_session_id);

CREATE INDEX idx_messages_agent_session_project ON daily_messages (agent_session_id, project_path);

CREATE INDEX idx_messages_conv_history ON daily_messages (agent_session_id, conversation_id, feishu_conversation_id, tool_name, host_name, sender_name, date, "timestamp", tokens_used, input_tokens, output_tokens, sender_id);

CREATE INDEX idx_messages_conversation ON daily_messages (date, conversation_id, agent_session_id);

CREATE INDEX idx_messages_date_role_sender_prefix ON daily_messages (date, role, sender_name);

CREATE INDEX idx_messages_date_role_timestamp ON daily_messages (date, role, "timestamp" DESC);

CREATE INDEX idx_messages_date_sender_id ON daily_messages (date, sender_id);

CREATE INDEX idx_messages_date_tool_host ON daily_messages (date, tool_name, host_name);

CREATE INDEX idx_messages_deleted ON daily_messages (deleted_at);

CREATE INDEX idx_messages_host_name ON daily_messages (host_name);

CREATE INDEX idx_messages_project_path ON daily_messages (project_path);

CREATE INDEX idx_messages_sender_date_role ON daily_messages (sender_name, date, role);

CREATE INDEX idx_messages_sender_id ON daily_messages (sender_id);

CREATE INDEX idx_messages_sender_name ON daily_messages (sender_name) WHERE (sender_name IS NOT NULL);

CREATE INDEX idx_messages_session_list_covering ON daily_messages (agent_session_id, tool_name, host_name, sender_name) WHERE (agent_session_id IS NOT NULL);

CREATE INDEX idx_messages_timestamp ON daily_messages ("timestamp");

CREATE INDEX idx_messages_tool_name ON daily_messages (tool_name);

CREATE INDEX idx_messages_usage_trend_covering ON daily_messages (date, role, sender_name) WHERE ((role) = 'assistant');

CREATE INDEX idx_messages_user_date_role_covering ON daily_messages (user_id, date, role) WHERE ((user_id IS NOT NULL) AND ((role) = 'assistant'));

CREATE INDEX idx_milestones_workflow_phase ON workflow_milestones (workflow_id, phase, status);

CREATE INDEX idx_milestones_workflow_round ON workflow_milestones (workflow_id, dev_round);

CREATE INDEX idx_policy_decisions_fingerprint ON policy_decisions (fingerprint_hash);

CREATE INDEX idx_policy_decisions_request_id ON policy_decisions (request_id);

CREATE INDEX idx_policy_decisions_session_id ON policy_decisions (session_id);

CREATE INDEX idx_policy_rules_current_enabled ON policy_rules (is_current, enabled);

CREATE INDEX idx_policy_rules_key_current ON policy_rules (rule_key, is_current);

CREATE INDEX idx_project_categories_sort_order ON project_categories (sort_order);

CREATE INDEX idx_projects_created_by ON projects (created_by);

CREATE INDEX idx_projects_is_active ON projects (is_active);

CREATE INDEX idx_projects_path ON projects (path);

CREATE INDEX idx_prompt_templates_author ON prompt_templates (author_id);

CREATE INDEX idx_prompt_templates_category ON prompt_templates (category);

CREATE INDEX idx_prompt_templates_public ON prompt_templates (is_public);

CREATE INDEX idx_quota_alerts_created ON quota_alerts (created_at);

CREATE INDEX idx_quota_alerts_unack ON quota_alerts (acknowledged, created_at);

CREATE INDEX idx_quota_alerts_user ON quota_alerts (user_id);

CREATE INDEX idx_quota_usage_date ON quota_usage (date);

CREATE INDEX idx_quota_usage_user ON quota_usage (user_id);

CREATE INDEX idx_registration_tokens_hash ON registration_tokens (token_hash);

CREATE INDEX idx_remote_machines_hostname_tenant ON remote_machines (hostname, tenant_id);

CREATE INDEX idx_remote_machines_machine_id ON remote_machines (machine_id);

CREATE INDEX idx_remote_machines_status ON remote_machines (status);

CREATE INDEX idx_run_events_created_at ON agent_run_events (created_at);

CREATE INDEX idx_run_events_event_type ON agent_run_events (event_type);

CREATE INDEX idx_run_events_run_id ON agent_run_events (run_id);

CREATE INDEX idx_run_events_session_id ON agent_run_events (session_id, id);

CREATE INDEX idx_security_settings_key ON security_settings (setting_key);

CREATE INDEX idx_session_messages_external_message_id ON session_messages (session_id, external_message_id);

CREATE INDEX idx_session_messages_session_id ON session_messages (session_id);

CREATE INDEX idx_session_messages_source ON session_messages (session_id, source);

CREATE INDEX idx_sessions_active ON sessions (is_active, expires_at);

CREATE INDEX idx_sessions_expires ON sessions (expires_at);

CREATE INDEX idx_sessions_token ON sessions (token);

CREATE INDEX idx_sessions_user_id ON sessions (user_id);

CREATE INDEX idx_shared_sessions_session ON shared_sessions (session_id);

CREATE INDEX idx_shared_sessions_target ON shared_sessions (target_id);

CREATE INDEX idx_sso_identities_provider ON sso_identities (provider_name, provider_user_id);

CREATE INDEX idx_sso_identities_user ON sso_identities (user_id);

CREATE INDEX idx_sso_providers_tenant ON sso_providers (tenant_id);

CREATE INDEX idx_sso_sessions_token ON sso_sessions (session_token);

CREATE INDEX idx_sso_sessions_user ON sso_sessions (user_id);

CREATE INDEX idx_sync_events_session_id ON sync_events (session_id);

CREATE INDEX idx_sync_events_timestamp ON sync_events ("timestamp");

CREATE INDEX idx_sync_events_user_id ON sync_events (user_id);

CREATE INDEX idx_team_members_team ON team_members (team_id);

CREATE INDEX idx_team_members_user ON team_members (user_id);

CREATE INDEX idx_teams_owner ON teams (owner_id);

CREATE INDEX idx_tenant_quotas_tenant ON tenant_quotas (tenant_id);

CREATE INDEX idx_tenant_settings_tenant ON tenant_settings (tenant_id);

CREATE INDEX idx_tenant_usage_date ON tenant_usage (date);

CREATE INDEX idx_tenant_usage_tenant ON tenant_usage (tenant_id);

CREATE INDEX idx_tenants_deleted ON tenants (deleted_at);

CREATE INDEX idx_tenants_slug ON tenants (slug);

CREATE INDEX idx_tenants_status ON tenants (status);

CREATE INDEX idx_tool_accounts_tool_account ON user_tool_accounts (tool_account);

CREATE INDEX idx_tool_accounts_user_id ON user_tool_accounts (user_id);

CREATE INDEX idx_usage_date ON daily_usage (date);

CREATE INDEX idx_usage_date_tool_host ON daily_usage (date, tool_name, host_name);

CREATE INDEX idx_usage_host_name ON daily_usage (host_name);

CREATE INDEX idx_usage_summary_host ON usage_summary (host_name);

CREATE INDEX idx_usage_summary_host_name_valid ON usage_summary (host_name) WHERE ((host_name IS NOT NULL) AND ((host_name) <> '') AND ((host_name) NOT LIKE '<%>') AND ((length((host_name)) >= 1) AND (length((host_name)) <= 253)));

CREATE INDEX idx_usage_summary_tool ON usage_summary (tool_name);

CREATE INDEX idx_usage_tool_name ON daily_usage (tool_name);

CREATE INDEX idx_user_daily_stats_date ON user_daily_stats (date DESC);

CREATE INDEX idx_user_daily_stats_user_date ON user_daily_stats (user_id, date DESC);

CREATE INDEX idx_user_projects_project ON user_projects (project_id);

CREATE INDEX idx_user_projects_user ON user_projects (user_id);

CREATE INDEX idx_users_active ON users (is_active);

CREATE INDEX idx_users_deleted ON users (deleted_at);

CREATE INDEX idx_users_email ON users (email);

CREATE INDEX idx_users_role ON users (role);

CREATE INDEX idx_users_tenant ON users (tenant_id);

CREATE INDEX idx_workflows_batch_order ON autonomous_workflows (batch_id, batch_order);

CREATE INDEX idx_workflows_parent ON autonomous_workflows (parent_workflow_id);

CREATE INDEX idx_workflows_status_created ON autonomous_workflows (status, created_at);

CREATE INDEX idx_workflows_user_status ON autonomous_workflows (user_id, status);

CREATE UNIQUE INDEX ix_anomaly_status_type_hash ON anomaly_status (anomaly_type, affected_users_hash);

CREATE UNIQUE INDEX policy_decisions_decision_id_key ON policy_decisions (decision_id);

CREATE UNIQUE INDEX policy_rules_rule_key_version_key ON policy_rules (rule_key, version);

CREATE UNIQUE INDEX uq_projects_path ON projects (path) WHERE (is_active IS TRUE);

CREATE UNIQUE INDEX uq_user_projects_user_project ON user_projects (user_id, project_id);

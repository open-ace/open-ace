-- Open-ACE Database Schema for SQLite
-- Converted from schema-postgres.sql

CREATE TABLE agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    session_type TEXT DEFAULT 'chat',
    title TEXT,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost',
    user_id INTEGER,
    status TEXT DEFAULT 'active',
    context TEXT,
    settings TEXT,
    total_tokens INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    model TEXT,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP,
    project_id INTEGER,
    project_path TEXT
);

CREATE TABLE alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL UNIQUE,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    user_id INTEGER,
    username TEXT,
    tool_name TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read INTEGER DEFAULT 0,
    action_url TEXT,
    action_text TEXT
);

CREATE TABLE annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annotation_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    message_id TEXT,
    user_id INTEGER,
    username TEXT,
    content TEXT,
    annotation_type TEXT DEFAULT 'comment',
    "position" TEXT,
    parent_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER,
    username TEXT,
    action TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    resource_type TEXT,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    user_agent TEXT,
    session_id TEXT,
    success INTEGER DEFAULT 1,
    error_message TEXT
);

CREATE TABLE content_filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    type TEXT DEFAULT 'keyword',
    severity TEXT DEFAULT 'medium',
    action TEXT DEFAULT 'warn',
    is_enabled INTEGER DEFAULT 1,
    description TEXT,
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
    content TEXT,
    full_entry TEXT,
    tokens_used INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
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
    user_id INTEGER,
    project_path TEXT,
    CONSTRAINT chk_daily_messages_input_tokens_positive CHECK (input_tokens >= 0),
    CONSTRAINT chk_daily_messages_output_tokens_positive CHECK (output_tokens >= 0),
    CONSTRAINT chk_daily_messages_tokens_positive CHECK (tokens_used >= 0)
);

CREATE TABLE daily_stats (
    date TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost' NOT NULL,
    sender_name TEXT,
    total_tokens INTEGER NOT NULL,
    total_input_tokens INTEGER NOT NULL,
    total_output_tokens INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    project_id INTEGER,
    project_path TEXT
);

CREATE TABLE daily_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost' NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_tokens INTEGER DEFAULT 0,
    request_count INTEGER DEFAULT 0,
    models_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_daily_usage_cache_tokens_positive CHECK (cache_tokens >= 0),
    CONSTRAINT chk_daily_usage_input_tokens_positive CHECK (input_tokens >= 0),
    CONSTRAINT chk_daily_usage_output_tokens_positive CHECK (output_tokens >= 0),
    CONSTRAINT chk_daily_usage_request_count_positive CHECK (request_count >= 0),
    CONSTRAINT chk_daily_usage_tokens_positive CHECK (tokens_used >= 0)
);

CREATE TABLE hourly_stats (
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    host_name TEXT DEFAULT 'localhost' NOT NULL,
    total_tokens INTEGER NOT NULL,
    total_input_tokens INTEGER NOT NULL,
    total_output_tokens INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE,
    team_id TEXT,
    title TEXT NOT NULL,
    content TEXT,
    category TEXT DEFAULT 'general',
    tags TEXT,
    author_id INTEGER,
    author_name TEXT,
    is_published INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE notification_preferences (
    user_id INTEGER PRIMARY KEY,
    email_enabled INTEGER DEFAULT 1,
    push_enabled INTEGER DEFAULT 1,
    webhook_url TEXT,
    alert_types TEXT,
    min_severity TEXT DEFAULT 'warning'
);

CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT,
    description TEXT,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    is_active INTEGER DEFAULT 1 NOT NULL,
    is_shared INTEGER DEFAULT 0 NOT NULL
);

CREATE TABLE prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT DEFAULT 'general',
    content TEXT NOT NULL,
    variables TEXT,
    tags TEXT,
    author_id INTEGER,
    author_name TEXT,
    is_public INTEGER DEFAULT 0,
    is_featured INTEGER DEFAULT 0,
    use_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE quota_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    quota_type TEXT NOT NULL,
    period TEXT DEFAULT 'daily',
    threshold REAL NOT NULL,
    current_usage INTEGER NOT NULL,
    quota_limit INTEGER NOT NULL,
    percentage REAL NOT NULL,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged INTEGER DEFAULT 0,
    acknowledged_at TIMESTAMP,
    acknowledged_by INTEGER
);

CREATE TABLE quota_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    period TEXT DEFAULT 'daily',
    tokens_used INTEGER DEFAULT 0,
    requests_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tool_name TEXT,
    CONSTRAINT chk_quota_usage_requests_positive CHECK (requests_used >= 0),
    CONSTRAINT chk_quota_usage_tokens_positive CHECK (tokens_used >= 0)
);

CREATE TABLE retention_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    report_data TEXT NOT NULL
);

CREATE TABLE security_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_key TEXT NOT NULL UNIQUE,
    setting_value TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tokens_used INTEGER DEFAULT 0,
    model TEXT,
    "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT
);

CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE shared_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    shared_by INTEGER,
    shared_by_name TEXT,
    permission TEXT DEFAULT 'view',
    share_type TEXT DEFAULT 'user',
    target_id INTEGER,
    target_name TEXT,
    expires_at TIMESTAMP,
    allow_comments INTEGER DEFAULT 1,
    allow_copy INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP
);

CREATE TABLE sso_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    provider_name TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    provider_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    UNIQUE (provider_name, provider_user_id)
);

CREATE TABLE sso_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    provider_type TEXT NOT NULL,
    config TEXT NOT NULL,
    tenant_id INTEGER,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sso_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    provider_name TEXT NOT NULL,
    access_token TEXT,
    refresh_token TEXT,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    "timestamp" TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT,
    session_id TEXT,
    user_id INTEGER,
    tool_name TEXT,
    data TEXT,
    metadata TEXT
);

CREATE TABLE team_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    role TEXT DEFAULT 'member',
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (team_id, user_id)
);

CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    owner_id INTEGER,
    settings TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'active',
    plan TEXT DEFAULT 'standard',
    contact_email TEXT,
    contact_phone TEXT,
    contact_name TEXT,
    quota TEXT,
    settings TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    trial_ends_at TIMESTAMP,
    subscription_ends_at TIMESTAMP,
    user_count INTEGER DEFAULT 0,
    total_tokens_used INTEGER DEFAULT 0,
    total_requests_made INTEGER DEFAULT 0,
    deleted_at TIMESTAMP
);

CREATE TABLE tenant_quotas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL UNIQUE,
    daily_token_limit INTEGER DEFAULT 1000000,
    monthly_token_limit INTEGER DEFAULT 30000000,
    daily_request_limit INTEGER DEFAULT 10000,
    monthly_request_limit INTEGER DEFAULT 300000,
    max_users INTEGER DEFAULT 100,
    max_sessions_per_user INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL UNIQUE,
    content_filter_enabled INTEGER DEFAULT 1,
    audit_log_enabled INTEGER DEFAULT 1,
    audit_log_retention_days INTEGER DEFAULT 90,
    data_retention_days INTEGER DEFAULT 365,
    sso_enabled INTEGER DEFAULT 0,
    sso_provider TEXT,
    custom_branding INTEGER DEFAULT 0,
    branding_name TEXT,
    branding_logo_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tenant_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    requests_made INTEGER DEFAULT 0,
    active_users INTEGER DEFAULT 0,
    new_users INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE usage_summary (
    tool_name TEXT NOT NULL,
    host_name TEXT,
    days_count INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    avg_tokens INTEGER NOT NULL,
    total_requests INTEGER NOT NULL,
    total_input_tokens INTEGER NOT NULL,
    total_output_tokens INTEGER NOT NULL,
    first_date TEXT,
    last_date TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    email TEXT,
    is_admin INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    role TEXT DEFAULT 'user',
    daily_token_quota INTEGER,
    monthly_token_quota INTEGER,
    daily_request_quota INTEGER,
    monthly_request_quota INTEGER,
    deleted_at TIMESTAMP,
    system_account TEXT,
    tenant_id INTEGER,
    must_change_password INTEGER DEFAULT 0
);

CREATE TABLE user_daily_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    requests INTEGER DEFAULT 0 NOT NULL,
    tokens INTEGER DEFAULT 0 NOT NULL,
    input_tokens INTEGER DEFAULT 0 NOT NULL,
    output_tokens INTEGER DEFAULT 0 NOT NULL,
    cache_tokens INTEGER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE user_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    first_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_access_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    total_sessions INTEGER DEFAULT 0 NOT NULL,
    total_tokens INTEGER DEFAULT 0 NOT NULL,
    total_requests INTEGER DEFAULT 0 NOT NULL,
    total_duration_seconds INTEGER DEFAULT 0 NOT NULL,
    UNIQUE (user_id, project_id)
);

CREATE TABLE user_tool_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tool_account TEXT NOT NULL,
    tool_type TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE web_user_auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

-- Indexes
CREATE INDEX idx_agent_sessions_project ON agent_sessions (project_id);
CREATE INDEX idx_agent_sessions_session_id ON agent_sessions (session_id);
CREATE INDEX idx_agent_sessions_status ON agent_sessions (status);
CREATE INDEX idx_agent_sessions_tool_name ON agent_sessions (tool_name);
CREATE INDEX idx_agent_sessions_user_id ON agent_sessions (user_id);

CREATE INDEX idx_alerts_created_at ON alerts (created_at);
CREATE INDEX idx_alerts_read ON alerts (read);
CREATE INDEX idx_alerts_user_id ON alerts (user_id);

CREATE INDEX idx_annotations_session ON annotations (session_id);

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

CREATE INDEX idx_filter_rules_enabled ON content_filter_rules (is_enabled);
CREATE INDEX idx_filter_rules_type ON content_filter_rules (type);

CREATE INDEX idx_hourly_stats_date ON hourly_stats (date);
CREATE INDEX idx_hourly_stats_date_hour ON hourly_stats (date, hour);
CREATE INDEX idx_hourly_stats_hour ON hourly_stats (hour);

CREATE INDEX idx_knowledge_team ON knowledge_base (team_id);

CREATE INDEX idx_messages_agent_session_id ON daily_messages (agent_session_id);
CREATE INDEX idx_messages_agent_session_project ON daily_messages (agent_session_id, project_path);
CREATE INDEX idx_messages_conversation ON daily_messages (date, conversation_id, agent_session_id);
CREATE INDEX idx_messages_date_role_timestamp ON daily_messages (date, role, "timestamp" DESC);
CREATE INDEX idx_messages_date_sender_id ON daily_messages (date, sender_id);
CREATE INDEX idx_messages_date_tool_host ON daily_messages (date, tool_name, host_name);
CREATE INDEX idx_messages_deleted ON daily_messages (deleted_at);
CREATE INDEX idx_messages_host_name ON daily_messages (host_name);
CREATE INDEX idx_messages_project_path ON daily_messages (project_path);
CREATE INDEX idx_messages_sender_date_role ON daily_messages (sender_name, date, role);
CREATE INDEX idx_messages_sender_id ON daily_messages (sender_id);
CREATE INDEX idx_messages_timestamp ON daily_messages ("timestamp");
CREATE INDEX idx_messages_tool_name ON daily_messages (tool_name);

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

CREATE INDEX idx_security_settings_key ON security_settings (setting_key);

CREATE INDEX idx_session_messages_session_id ON session_messages (session_id);

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

-- Unique constraints for tables without inline UNIQUE
CREATE UNIQUE INDEX uq_daily_messages_date_tool_msg_host ON daily_messages (date, tool_name, message_id, host_name);
CREATE UNIQUE INDEX uq_daily_stats_date_tool_host_sender ON daily_stats (date, tool_name, host_name, sender_name);
CREATE UNIQUE INDEX uq_daily_usage_date_tool_host ON daily_usage (date, tool_name, host_name);
CREATE UNIQUE INDEX uq_hourly_stats_date_hour_tool_host ON hourly_stats (date, hour, tool_name, host_name);
CREATE UNIQUE INDEX uq_quota_usage_user_date_period_new ON quota_usage (user_id, date, period);
CREATE UNIQUE INDEX uq_tenant_usage_tenant_date_new ON tenant_usage (tenant_id, date);
CREATE UNIQUE INDEX uq_usage_summary_tool_host ON usage_summary (tool_name, host_name);
CREATE UNIQUE INDEX uq_user_daily_stats_user_date ON user_daily_stats (user_id, date);
CREATE UNIQUE INDEX uq_user_tool_account ON user_tool_accounts (tool_account);

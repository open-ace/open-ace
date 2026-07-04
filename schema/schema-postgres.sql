-- Open-ACE Database Schema for PostgreSQL
-- Auto-generated from pg_dump
-- DO NOT EDIT MANUALLY

-- Setup session
SET client_encoding = 'UTF8';

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;
SET default_tablespace = '';
SET default_table_access_method = heap;
CREATE TABLE agent_approvals (
    id integer NOT NULL,
    request_id text NOT NULL,
    run_id text,
    session_id text,
    tool_name text,
    request_subtype text,
    request_details text,
    status text DEFAULT 'pending'::text,
    decision text,
    decided_by integer,
    decided_by_name text,
    decision_metadata text,
    requested_at timestamp without time zone,
    decided_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE agent_approvals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE agent_approvals_id_seq OWNED BY agent_approvals.id;
CREATE TABLE agent_run_events (
    id integer NOT NULL,
    run_id text,
    session_id text,
    event_type text DEFAULT ''::text NOT NULL,
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
    event_ts timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE agent_run_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE agent_run_events_id_seq OWNED BY agent_run_events.id;
CREATE TABLE agent_runs (
    id integer NOT NULL,
    run_id text NOT NULL,
    session_id text NOT NULL,
    user_id integer,
    tenant_id integer,
    machine_id text,
    tool_name text,
    provider text,
    cli_tool text,
    model text,
    status text DEFAULT 'active'::text,
    started_at timestamp without time zone,
    ended_at timestamp without time zone,
    total_tokens integer DEFAULT 0,
    total_input_tokens integer DEFAULT 0,
    total_output_tokens integer DEFAULT 0,
    total_requests integer DEFAULT 0,
    metadata text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE agent_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE agent_runs_id_seq OWNED BY agent_runs.id;
CREATE TABLE agent_sessions (
    id integer NOT NULL,
    session_id text NOT NULL,
    session_type text DEFAULT 'chat'::text,
    title text,
    tool_name text NOT NULL,
    host_name text DEFAULT 'localhost'::text,
    user_id integer,
    status text DEFAULT 'active'::text,
    context text,
    settings text,
    total_tokens integer DEFAULT 0,
    total_input_tokens integer DEFAULT 0,
    total_output_tokens integer DEFAULT 0,
    message_count integer DEFAULT 0,
    model text,
    tags text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamp without time zone,
    expires_at timestamp without time zone,
    project_id integer,
    project_path character varying(500),
    request_count integer DEFAULT 0,
    workspace_type text DEFAULT 'local'::text,
    remote_machine_id text,
    paused_at timestamp without time zone,
    cli_session_id text DEFAULT ''::text
);

CREATE SEQUENCE agent_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE agent_sessions_id_seq OWNED BY agent_sessions.id;
CREATE TABLE agent_tokens (
    id integer NOT NULL,
    token_hash character varying NOT NULL,
    machine_id character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    is_revoked boolean DEFAULT false,
    revoked_at timestamp without time zone,
    revoked_by integer,
    rotated_at timestamp without time zone
);

CREATE SEQUENCE agent_tokens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE agent_tokens_id_seq OWNED BY agent_tokens.id;
CREATE TABLE ai_agent_settings (
    id integer NOT NULL,
    setting_key character varying(100) NOT NULL,
    setting_value text,
    description text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE ai_agent_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ai_agent_settings_id_seq OWNED BY ai_agent_settings.id;
CREATE TABLE alerts (
    id integer NOT NULL,
    alert_id text NOT NULL,
    alert_type text NOT NULL,
    severity text NOT NULL,
    title text NOT NULL,
    message text,
    user_id integer,
    username text,
    tool_name text,
    metadata text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    read boolean DEFAULT false,
    action_url text,
    action_text text
);

CREATE SEQUENCE alerts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE alerts_id_seq OWNED BY alerts.id;
CREATE TABLE annotations (
    id integer NOT NULL,
    annotation_id text NOT NULL,
    session_id text NOT NULL,
    message_id text,
    user_id integer,
    username text,
    content text,
    annotation_type text DEFAULT 'comment'::text,
    "position" text,
    parent_id integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE annotations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE annotations_id_seq OWNED BY annotations.id;
CREATE TABLE anomaly_status (
    id integer NOT NULL,
    anomaly_type character varying(100) NOT NULL,
    affected_users_hash character varying(64) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    processed_by integer,
    processed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE anomaly_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE anomaly_status_id_seq OWNED BY anomaly_status.id;
CREATE TABLE api_key_store (
    id integer NOT NULL,
    tenant_id integer,
    provider text NOT NULL,
    key_name text NOT NULL,
    encrypted_key text NOT NULL,
    key_hash text NOT NULL,
    base_url text,
    is_active boolean DEFAULT true,
    created_by integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    cli_tools text,
    cli_settings text,
    scope text DEFAULT 'shared'::text,
    priority integer DEFAULT 0,
    weight integer DEFAULT 100
);

CREATE SEQUENCE api_key_store_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE api_key_store_id_seq OWNED BY api_key_store.id;
CREATE TABLE audit_logs (
    id integer NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    user_id integer,
    username text,
    action text NOT NULL,
    severity text DEFAULT 'info'::text,
    resource_type text,
    resource_id text,
    details text,
    ip_address text,
    user_agent text,
    session_id text,
    success boolean DEFAULT true,
    error_message text
);

CREATE SEQUENCE audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE audit_logs_id_seq OWNED BY audit_logs.id;
CREATE TABLE autonomous_workflows (
    id integer NOT NULL,
    workflow_id character varying(36) NOT NULL,
    user_id integer,
    title text DEFAULT ''::text,
    status text DEFAULT 'pending'::text,
    requirements_text text DEFAULT ''::text,
    requirements_issue_url text DEFAULT ''::text,
    project_path text DEFAULT ''::text,
    project_repo_url text DEFAULT ''::text,
    is_new_project boolean DEFAULT false,
    is_private boolean DEFAULT true,
    cli_tool text DEFAULT ''::text,
    model text DEFAULT ''::text,
    permission_mode text DEFAULT 'auto-edit'::text,
    branch_name text DEFAULT ''::text,
    branch_strategy text DEFAULT 'new-branch'::text,
    workspace_type text DEFAULT 'local'::text,
    remote_machine_id text DEFAULT ''::text,
    worktree_path text DEFAULT ''::text,
    github_issue_number integer,
    github_pr_number integer,
    github_pr_url text DEFAULT ''::text,
    current_phase text DEFAULT 'preparation'::text,
    current_round integer DEFAULT 0,
    dev_round integer DEFAULT 1,
    max_plan_rounds integer DEFAULT 3,
    max_pr_review_rounds integer DEFAULT 5,
    require_full_review_rounds boolean DEFAULT false,
    total_tokens integer DEFAULT 0,
    total_input_tokens integer DEFAULT 0,
    total_output_tokens integer DEFAULT 0,
    total_requests integer DEFAULT 0,
    error_message text DEFAULT ''::text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    completed_at timestamp without time zone,
    paused_at timestamp without time zone,
    planning_timeout_extension integer DEFAULT 0,
    parent_workflow_id text,
    fork_milestone_id text,
    user_feedback text DEFAULT ''::text,
    original_branch_name text DEFAULT ''::text,
    batch_id text,
    batch_order integer,
    batch_total integer,
    auto_merge boolean DEFAULT true,
    definition_snapshot text,
    agent_pid integer,
    agent_session_id text DEFAULT ''::text NOT NULL,
    main_session_id text DEFAULT ''::text NOT NULL,
    review_session_id text DEFAULT ''::text NOT NULL,
    test_session_id text DEFAULT ''::text NOT NULL,
    content_language text DEFAULT 'en'::text NOT NULL,
    locked_at timestamp without time zone,
    locked_by text DEFAULT ''::text,
    transient_retry_count integer DEFAULT 0,
    retry_count integer DEFAULT 0,
    test_retries integer DEFAULT 0,
    skip_retries integer DEFAULT 0,
    dev_retries_on_test_fail integer DEFAULT 0
);

CREATE SEQUENCE autonomous_workflows_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE autonomous_workflows_id_seq OWNED BY autonomous_workflows.id;
CREATE TABLE compliance_reports (
    id integer NOT NULL,
    report_id text NOT NULL,
    report_type text NOT NULL,
    generated_at timestamp without time zone NOT NULL,
    period_start timestamp without time zone NOT NULL,
    period_end timestamp without time zone NOT NULL,
    generated_by integer,
    tenant_id integer,
    report_data text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE compliance_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE compliance_reports_id_seq OWNED BY compliance_reports.id;
CREATE TABLE content_filter_rules (
    id integer NOT NULL,
    pattern text NOT NULL,
    type text DEFAULT 'keyword'::text,
    severity text DEFAULT 'medium'::text,
    action text DEFAULT 'warn'::text,
    is_enabled boolean DEFAULT true,
    description text,
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone
);

CREATE SEQUENCE content_filter_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE content_filter_rules_id_seq OWNED BY content_filter_rules.id;
CREATE TABLE daily_messages (
    id integer NOT NULL,
    date character varying NOT NULL,
    tool_name character varying NOT NULL,
    host_name character varying DEFAULT 'localhost'::character varying NOT NULL,
    message_id character varying NOT NULL,
    parent_id character varying,
    role character varying NOT NULL,
    content text,
    full_entry text,
    tokens_used integer DEFAULT 0,
    input_tokens integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    model character varying,
    "timestamp" timestamp without time zone,
    sender_id character varying,
    sender_name character varying,
    message_source character varying,
    feishu_conversation_id character varying,
    group_subject character varying,
    is_group_chat boolean,
    agent_session_id character varying,
    conversation_id character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    deleted_at timestamp without time zone,
    user_id integer,
    project_path text,
    CONSTRAINT chk_daily_messages_input_tokens_positive CHECK ((input_tokens >= 0)),
    CONSTRAINT chk_daily_messages_output_tokens_positive CHECK ((output_tokens >= 0)),
    CONSTRAINT chk_daily_messages_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE SEQUENCE daily_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE daily_messages_id_seq OWNED BY daily_messages.id;
CREATE TABLE daily_stats (
    date character varying(10) NOT NULL,
    tool_name character varying(50) NOT NULL,
    host_name character varying(100) DEFAULT 'localhost'::character varying NOT NULL,
    sender_name character varying(100),
    total_tokens bigint NOT NULL,
    total_input_tokens bigint NOT NULL,
    total_output_tokens bigint NOT NULL,
    message_count integer NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    project_id integer,
    project_path character varying(500),
    user_id integer
);

CREATE TABLE daily_usage (
    id integer NOT NULL,
    date date NOT NULL,
    tool_name character varying NOT NULL,
    host_name character varying DEFAULT 'localhost'::character varying NOT NULL,
    tokens_used integer DEFAULT 0,
    input_tokens integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    cache_tokens integer DEFAULT 0,
    request_count integer DEFAULT 0,
    models_used text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_daily_usage_cache_tokens_positive CHECK ((cache_tokens >= 0)),
    CONSTRAINT chk_daily_usage_input_tokens_positive CHECK ((input_tokens >= 0)),
    CONSTRAINT chk_daily_usage_output_tokens_positive CHECK ((output_tokens >= 0)),
    CONSTRAINT chk_daily_usage_request_count_positive CHECK ((request_count >= 0)),
    CONSTRAINT chk_daily_usage_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE SEQUENCE daily_usage_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE daily_usage_id_seq OWNED BY daily_usage.id;
CREATE TABLE email_notification_logs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    alert_id character varying,
    recipient_email character varying(255) NOT NULL,
    subject character varying(500) NOT NULL,
    email_body text,
    sent_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    error_message text,
    retry_count integer DEFAULT 0,
    next_retry_at timestamp without time zone
);

CREATE SEQUENCE email_notification_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE email_notification_logs_id_seq OWNED BY email_notification_logs.id;
CREATE TABLE hourly_stats (
    date character varying(10) NOT NULL,
    hour integer NOT NULL,
    tool_name character varying(50) NOT NULL,
    host_name character varying(100) DEFAULT 'localhost'::character varying NOT NULL,
    total_tokens bigint NOT NULL,
    total_input_tokens bigint NOT NULL,
    total_output_tokens bigint NOT NULL,
    message_count integer NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE insights_reports (
    id integer NOT NULL,
    user_id integer NOT NULL,
    start_date character varying(10) NOT NULL,
    end_date character varying(10) NOT NULL,
    overall_score integer,
    overall_assessment text,
    strengths text,
    areas_for_improvement text,
    suggestions text,
    usage_summary text,
    model character varying(50),
    raw_response text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE insights_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE insights_reports_id_seq OWNED BY insights_reports.id;
CREATE TABLE knowledge_base (
    id integer NOT NULL,
    entry_id text NOT NULL,
    team_id text,
    title text NOT NULL,
    content text,
    category text DEFAULT 'general'::text,
    tags text,
    author_id integer,
    author_name text,
    is_published boolean DEFAULT false,
    view_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE knowledge_base_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE knowledge_base_id_seq OWNED BY knowledge_base.id;
CREATE TABLE login_attempts (
    username character varying(255) NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp without time zone
);

CREATE TABLE machine_assignments (
    id integer NOT NULL,
    machine_id text NOT NULL,
    user_id integer NOT NULL,
    permission text DEFAULT 'user'::text,
    granted_by integer,
    granted_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE machine_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE machine_assignments_id_seq OWNED BY machine_assignments.id;
CREATE TABLE model_gateway_config (
    id integer NOT NULL,
    mode text DEFAULT 'direct'::text,
    base_url text,
    encrypted_api_key text,
    encryption_version integer DEFAULT 1,
    model_prefix_mode boolean DEFAULT false,
    model_prefix text,
    created_by integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE model_gateway_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE model_gateway_config_id_seq OWNED BY model_gateway_config.id;
CREATE TABLE notification_preferences (
    user_id integer NOT NULL,
    email_enabled boolean DEFAULT true,
    push_enabled boolean DEFAULT true,
    webhook_url text,
    alert_types text,
    min_severity text DEFAULT 'warning'::text,
    notification_email text,
    email_verified boolean DEFAULT false
);

CREATE TABLE policy_decisions (
    id integer NOT NULL,
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
    issued_at timestamp without time zone,
    expires_at timestamp without time zone,
    consumed_at timestamp without time zone,
    remote_response_id text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE policy_decisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE policy_decisions_id_seq OWNED BY policy_decisions.id;
CREATE TABLE policy_rules (
    id integer NOT NULL,
    rule_key text NOT NULL,
    name text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    is_current boolean DEFAULT true,
    enabled boolean DEFAULT true,
    tenant_id integer,
    project_path text,
    machine_id text,
    user_id integer,
    team_id text,
    policy_type text NOT NULL,
    pattern_type text DEFAULT 'glob'::text,
    pattern text,
    value_list text,
    tool_name text,
    action text,
    effect text NOT NULL,
    priority integer DEFAULT 100,
    is_default boolean DEFAULT false,
    approval_ttl_seconds integer,
    created_by integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    superseded_at timestamp without time zone,
    description text
);

CREATE SEQUENCE policy_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE policy_rules_id_seq OWNED BY policy_rules.id;
CREATE TABLE project_categories (
    id integer NOT NULL,
    name text NOT NULL,
    key_patterns text NOT NULL,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE project_categories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE project_categories_id_seq OWNED BY project_categories.id;
CREATE TABLE projects (
    id integer NOT NULL,
    path character varying(500) NOT NULL,
    name character varying(200),
    description text,
    created_by integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_shared boolean DEFAULT false NOT NULL
);

CREATE SEQUENCE projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE projects_id_seq OWNED BY projects.id;
CREATE TABLE prompt_templates (
    id integer NOT NULL,
    name text NOT NULL,
    description text,
    category text DEFAULT 'general'::text,
    content text NOT NULL,
    variables text,
    tags text,
    author_id integer,
    author_name text,
    is_public boolean DEFAULT false,
    is_featured boolean DEFAULT false,
    use_count integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE prompt_templates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE prompt_templates_id_seq OWNED BY prompt_templates.id;
CREATE TABLE quota_alerts (
    id integer NOT NULL,
    user_id integer NOT NULL,
    alert_type text NOT NULL,
    quota_type text NOT NULL,
    period text DEFAULT 'daily'::text,
    threshold real NOT NULL,
    current_usage integer NOT NULL,
    quota_limit integer NOT NULL,
    percentage real NOT NULL,
    message text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    acknowledged boolean DEFAULT false,
    acknowledged_at timestamp without time zone,
    acknowledged_by integer
);

CREATE SEQUENCE quota_alerts_new_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE quota_alerts_new_id_seq OWNED BY quota_alerts.id;
CREATE TABLE quota_usage (
    id integer NOT NULL,
    user_id integer NOT NULL,
    date date NOT NULL,
    period text DEFAULT 'daily'::text,
    tokens_used integer DEFAULT 0,
    requests_used integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    tool_name text,
    CONSTRAINT chk_quota_usage_requests_positive CHECK ((requests_used >= 0)),
    CONSTRAINT chk_quota_usage_tokens_positive CHECK ((tokens_used >= 0))
);

CREATE SEQUENCE quota_usage_new_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE quota_usage_new_id_seq OWNED BY quota_usage.id;
CREATE TABLE registration_tokens (
    id integer NOT NULL,
    token_hash character varying NOT NULL,
    tenant_id integer NOT NULL,
    created_by integer NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    expires_at timestamp without time zone,
    is_consumed boolean DEFAULT false,
    consumed_at timestamp without time zone
);

CREATE SEQUENCE registration_tokens_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE registration_tokens_id_seq OWNED BY registration_tokens.id;
CREATE TABLE remote_machines (
    id integer NOT NULL,
    machine_id text NOT NULL,
    machine_name text NOT NULL,
    hostname text,
    os_type text,
    os_version text,
    ip_address text,
    status text DEFAULT 'offline'::text,
    agent_version text,
    capabilities text,
    cli_path text,
    work_dir text,
    tenant_id integer,
    created_by integer,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat timestamp without time zone,
    legacy_mode boolean DEFAULT false
);

CREATE SEQUENCE remote_machines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE remote_machines_id_seq OWNED BY remote_machines.id;
CREATE TABLE retention_history (
    id integer NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    report_data text NOT NULL
);

CREATE SEQUENCE retention_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE retention_history_id_seq OWNED BY retention_history.id;
CREATE TABLE role_permissions (
    id integer NOT NULL,
    role text NOT NULL,
    permission text NOT NULL
);

CREATE SEQUENCE role_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE role_permissions_id_seq OWNED BY role_permissions.id;
CREATE TABLE security_settings (
    id integer NOT NULL,
    setting_key character varying(100) NOT NULL,
    setting_value text,
    description text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE security_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE security_settings_id_seq OWNED BY security_settings.id;
CREATE TABLE session_messages (
    id integer NOT NULL,
    session_id text NOT NULL,
    role text NOT NULL,
    content text,
    tokens_used integer DEFAULT 0,
    model text,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    metadata text,
    milestone_id text DEFAULT ''::text NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    source_timestamp timestamp without time zone,
    external_message_id text DEFAULT ''::text NOT NULL,
    content_blocks text
);

CREATE SEQUENCE session_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE session_messages_id_seq OWNED BY session_messages.id;
CREATE MATERIALIZED VIEW session_stats AS
 SELECT daily_messages.agent_session_id AS session_id,
    daily_messages.tool_name,
    daily_messages.host_name,
    daily_messages.sender_name,
    max((daily_messages.sender_id)::text) AS sender_id,
    max((daily_messages.date)::text) AS date,
    count(*) AS message_count,
    sum(daily_messages.tokens_used) AS total_tokens,
    sum(daily_messages.input_tokens) AS total_input_tokens,
    sum(daily_messages.output_tokens) AS total_output_tokens,
    min(daily_messages."timestamp") AS created_at,
    max(daily_messages."timestamp") AS updated_at,
    max(daily_messages.project_path) AS project_path
   FROM daily_messages
  WHERE (daily_messages.agent_session_id IS NOT NULL)
  GROUP BY daily_messages.agent_session_id, daily_messages.tool_name, daily_messages.host_name, daily_messages.sender_name
  WITH NO DATA;

CREATE TABLE sessions (
    id integer NOT NULL,
    token character varying NOT NULL,
    user_id integer NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL,
    is_active boolean DEFAULT true
);

CREATE SEQUENCE sessions_new_id_seq1
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE sessions_new_id_seq1 OWNED BY sessions.id;
CREATE TABLE shared_sessions (
    id integer NOT NULL,
    share_id text NOT NULL,
    session_id text NOT NULL,
    shared_by integer,
    shared_by_name text,
    permission text DEFAULT 'view'::text,
    share_type text DEFAULT 'user'::text,
    target_id integer,
    target_name text,
    expires_at timestamp without time zone,
    allow_comments boolean DEFAULT true,
    allow_copy boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    access_count integer DEFAULT 0,
    last_accessed timestamp without time zone
);

CREATE SEQUENCE shared_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE shared_sessions_id_seq OWNED BY shared_sessions.id;
CREATE TABLE smtp_settings (
    id integer NOT NULL,
    smtp_host character varying(255) NOT NULL,
    smtp_port integer DEFAULT 587 NOT NULL,
    smtp_user character varying(255),
    encrypted_password text,
    encryption_version integer DEFAULT 1,
    from_address character varying(255) NOT NULL,
    use_tls boolean DEFAULT true,
    is_verified boolean DEFAULT false,
    last_verified_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by integer
);

CREATE SEQUENCE smtp_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE smtp_settings_id_seq OWNED BY smtp_settings.id;
CREATE TABLE sso_auth_states (
    state text NOT NULL,
    code_verifier text NOT NULL,
    provider_name text NOT NULL,
    nonce text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sso_identities (
    id integer NOT NULL,
    user_id integer NOT NULL,
    provider_name text NOT NULL,
    provider_user_id text NOT NULL,
    provider_data text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    last_used_at timestamp without time zone
);

CREATE SEQUENCE sso_identities_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE sso_identities_id_seq OWNED BY sso_identities.id;
CREATE TABLE sso_providers (
    id integer NOT NULL,
    name text NOT NULL,
    provider_type text NOT NULL,
    config text NOT NULL,
    tenant_id integer,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE sso_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE sso_providers_id_seq OWNED BY sso_providers.id;
CREATE TABLE sso_sessions (
    id integer NOT NULL,
    session_token text NOT NULL,
    user_id integer NOT NULL,
    provider_name text NOT NULL,
    access_token text,
    refresh_token text,
    expires_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE sso_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE sso_sessions_id_seq OWNED BY sso_sessions.id;
CREATE TABLE sync_events (
    id integer NOT NULL,
    event_id text NOT NULL,
    event_type text NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    source text,
    session_id text,
    user_id integer,
    tool_name text,
    data text,
    metadata text
);

CREATE SEQUENCE sync_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE sync_events_id_seq OWNED BY sync_events.id;
CREATE TABLE team_members (
    id integer NOT NULL,
    team_id text NOT NULL,
    user_id integer NOT NULL,
    username text,
    role text DEFAULT 'member'::text,
    joined_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE team_members_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE team_members_id_seq OWNED BY team_members.id;
CREATE TABLE teams (
    id integer NOT NULL,
    team_id text NOT NULL,
    name text NOT NULL,
    description text,
    owner_id integer,
    settings text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE teams_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE teams_id_seq OWNED BY teams.id;
CREATE TABLE tenant_quotas (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    daily_token_limit bigint DEFAULT 1000000,
    monthly_token_limit bigint DEFAULT 30000000,
    daily_request_limit integer DEFAULT 10000,
    monthly_request_limit integer DEFAULT 300000,
    max_users integer DEFAULT 100,
    max_sessions_per_user integer DEFAULT 5,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE tenant_quotas_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE tenant_quotas_id_seq OWNED BY tenant_quotas.id;
CREATE TABLE tenant_settings (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    content_filter_enabled boolean DEFAULT true,
    audit_log_enabled boolean DEFAULT true,
    audit_log_retention_days integer DEFAULT 90,
    data_retention_days integer DEFAULT 365,
    sso_enabled boolean DEFAULT false,
    sso_provider character varying(50),
    custom_branding boolean DEFAULT false,
    branding_name character varying(100),
    branding_logo_url character varying(500),
    auto_provision_users boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE tenant_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE tenant_settings_id_seq OWNED BY tenant_settings.id;
CREATE TABLE tenant_usage (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    date date NOT NULL,
    tokens_used integer DEFAULT 0,
    requests_made integer DEFAULT 0,
    active_users integer DEFAULT 0,
    new_users integer DEFAULT 0,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE tenant_usage_new_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE tenant_usage_new_id_seq OWNED BY tenant_usage.id;
CREATE TABLE tenants (
    id integer NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    status text DEFAULT 'active'::text,
    plan text DEFAULT 'standard'::text,
    contact_email text,
    contact_phone text,
    contact_name text,
    quota text,
    settings text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    trial_ends_at timestamp without time zone,
    subscription_ends_at timestamp without time zone,
    user_count integer DEFAULT 0,
    total_tokens_used integer DEFAULT 0,
    total_requests_made integer DEFAULT 0,
    deleted_at timestamp without time zone,
    CONSTRAINT chk_tenants_plan CHECK ((plan = ANY (ARRAY['free'::text, 'standard'::text, 'premium'::text, 'enterprise'::text]))),
    CONSTRAINT chk_tenants_status CHECK ((status = ANY (ARRAY['active'::text, 'suspended'::text, 'trial'::text, 'inactive'::text])))
);

CREATE SEQUENCE tenants_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE tenants_id_seq OWNED BY tenants.id;
CREATE TABLE tool_account_mapping_rules (
    id integer NOT NULL,
    user_id integer NOT NULL,
    pattern character varying(255) NOT NULL,
    match_type character varying(20) DEFAULT 'exact'::character varying NOT NULL,
    tool_type character varying(50),
    priority integer DEFAULT 0 NOT NULL,
    is_auto boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    description character varying(255),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE tool_account_mapping_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE tool_account_mapping_rules_id_seq OWNED BY tool_account_mapping_rules.id;
CREATE TABLE usage_summary (
    tool_name character varying(50) NOT NULL,
    host_name character varying(100),
    days_count integer NOT NULL,
    total_tokens bigint NOT NULL,
    avg_tokens bigint NOT NULL,
    total_requests integer NOT NULL,
    total_input_tokens bigint NOT NULL,
    total_output_tokens bigint NOT NULL,
    first_date character varying(10),
    last_date character varying(10),
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE user_daily_stats (
    id integer NOT NULL,
    user_id integer NOT NULL,
    date date NOT NULL,
    requests integer DEFAULT 0 NOT NULL,
    tokens integer DEFAULT 0 NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    cache_tokens integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE user_daily_stats_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE user_daily_stats_id_seq OWNED BY user_daily_stats.id;
CREATE TABLE user_permissions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    permission text NOT NULL,
    granted_by integer,
    granted_at timestamp without time zone
);

CREATE SEQUENCE user_permissions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE user_permissions_id_seq OWNED BY user_permissions.id;
CREATE TABLE user_projects (
    id integer NOT NULL,
    user_id integer NOT NULL,
    project_id integer NOT NULL,
    first_access_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    last_access_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    total_sessions integer DEFAULT 0 NOT NULL,
    total_tokens bigint DEFAULT 0 NOT NULL,
    total_requests integer DEFAULT 0 NOT NULL,
    total_duration_seconds integer DEFAULT 0 NOT NULL
);

CREATE SEQUENCE user_projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE user_projects_id_seq OWNED BY user_projects.id;
CREATE TABLE user_tool_accounts (
    id integer NOT NULL,
    user_id integer NOT NULL,
    tool_account character varying(255) NOT NULL,
    tool_type character varying(50),
    description character varying(255),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE user_tool_accounts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE user_tool_accounts_id_seq OWNED BY user_tool_accounts.id;
CREATE TABLE users (
    id integer NOT NULL,
    username character varying NOT NULL,
    password_hash character varying NOT NULL,
    email character varying,
    is_admin boolean DEFAULT false,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    last_login timestamp without time zone,
    role character varying DEFAULT 'user'::character varying,
    daily_token_quota integer,
    monthly_token_quota integer,
    daily_request_quota integer,
    monthly_request_quota integer,
    deleted_at timestamp without time zone,
    system_account text,
    tenant_id integer,
    must_change_password boolean DEFAULT false,
    avatar_url character varying(500),
    auto_mapping_enabled boolean DEFAULT true,
    CONSTRAINT chk_users_role CHECK (((role)::text = ANY (ARRAY[('admin'::character varying)::text, ('manager'::character varying)::text, ('user'::character varying)::text])))
);

CREATE SEQUENCE users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE users_id_seq OWNED BY users.id;
CREATE TABLE web_user_auth_sessions (
    id integer NOT NULL,
    user_id integer NOT NULL,
    session_token text NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    expires_at timestamp without time zone NOT NULL
);

CREATE SEQUENCE web_user_auth_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE web_user_auth_sessions_id_seq OWNED BY web_user_auth_sessions.id;
CREATE TABLE workflow_events (
    id integer NOT NULL,
    workflow_id character varying(36) NOT NULL,
    milestone_id character varying(36) DEFAULT ''::character varying,
    event_type text DEFAULT ''::text NOT NULL,
    event_data text DEFAULT ''::text,
    created_at timestamp without time zone DEFAULT now()
);

CREATE SEQUENCE workflow_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE workflow_events_id_seq OWNED BY workflow_events.id;
CREATE TABLE workflow_milestones (
    id integer NOT NULL,
    workflow_id character varying(36) NOT NULL,
    milestone_id character varying(36) NOT NULL,
    phase text DEFAULT ''::text NOT NULL,
    dev_round integer DEFAULT 1,
    round_number integer DEFAULT 0,
    milestone_type text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'pending'::text,
    title text DEFAULT ''::text,
    description text DEFAULT ''::text,
    session_id text DEFAULT ''::text,
    review_session_id text DEFAULT ''::text,
    github_issue_number integer,
    github_pr_number integer,
    github_comment_id text DEFAULT ''::text,
    commit_shas text DEFAULT ''::text,
    diff_stats text DEFAULT ''::text,
    result_summary text DEFAULT ''::text,
    plan_content text DEFAULT ''::text,
    review_content text DEFAULT ''::text,
    error_message text DEFAULT ''::text,
    parent_milestone_id text DEFAULT ''::text,
    fork_branch text DEFAULT ''::text,
    metadata text DEFAULT ''::text,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    fork_workflow_id text DEFAULT ''::text,
    phase_total_tokens integer DEFAULT 0 NOT NULL,
    phase_input_tokens integer DEFAULT 0 NOT NULL,
    phase_output_tokens integer DEFAULT 0 NOT NULL,
    phase_request_count integer DEFAULT 0 NOT NULL,
    tldr text DEFAULT ''::text NOT NULL
);

CREATE SEQUENCE workflow_milestones_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE workflow_milestones_id_seq OWNED BY workflow_milestones.id;
ALTER TABLE ONLY agent_approvals ALTER COLUMN id SET DEFAULT nextval('agent_approvals_id_seq'::regclass);

ALTER TABLE ONLY agent_run_events ALTER COLUMN id SET DEFAULT nextval('agent_run_events_id_seq'::regclass);

ALTER TABLE ONLY agent_runs ALTER COLUMN id SET DEFAULT nextval('agent_runs_id_seq'::regclass);

ALTER TABLE ONLY agent_sessions ALTER COLUMN id SET DEFAULT nextval('agent_sessions_id_seq'::regclass);

ALTER TABLE ONLY agent_tokens ALTER COLUMN id SET DEFAULT nextval('agent_tokens_id_seq'::regclass);

ALTER TABLE ONLY ai_agent_settings ALTER COLUMN id SET DEFAULT nextval('ai_agent_settings_id_seq'::regclass);

ALTER TABLE ONLY alerts ALTER COLUMN id SET DEFAULT nextval('alerts_id_seq'::regclass);

ALTER TABLE ONLY annotations ALTER COLUMN id SET DEFAULT nextval('annotations_id_seq'::regclass);

ALTER TABLE ONLY anomaly_status ALTER COLUMN id SET DEFAULT nextval('anomaly_status_id_seq'::regclass);

ALTER TABLE ONLY api_key_store ALTER COLUMN id SET DEFAULT nextval('api_key_store_id_seq'::regclass);

ALTER TABLE ONLY audit_logs ALTER COLUMN id SET DEFAULT nextval('audit_logs_id_seq'::regclass);

ALTER TABLE ONLY autonomous_workflows ALTER COLUMN id SET DEFAULT nextval('autonomous_workflows_id_seq'::regclass);

ALTER TABLE ONLY compliance_reports ALTER COLUMN id SET DEFAULT nextval('compliance_reports_id_seq'::regclass);

ALTER TABLE ONLY content_filter_rules ALTER COLUMN id SET DEFAULT nextval('content_filter_rules_id_seq'::regclass);

ALTER TABLE ONLY daily_messages ALTER COLUMN id SET DEFAULT nextval('daily_messages_id_seq'::regclass);

ALTER TABLE ONLY daily_usage ALTER COLUMN id SET DEFAULT nextval('daily_usage_id_seq'::regclass);

ALTER TABLE ONLY email_notification_logs ALTER COLUMN id SET DEFAULT nextval('email_notification_logs_id_seq'::regclass);

ALTER TABLE ONLY insights_reports ALTER COLUMN id SET DEFAULT nextval('insights_reports_id_seq'::regclass);

ALTER TABLE ONLY knowledge_base ALTER COLUMN id SET DEFAULT nextval('knowledge_base_id_seq'::regclass);

ALTER TABLE ONLY machine_assignments ALTER COLUMN id SET DEFAULT nextval('machine_assignments_id_seq'::regclass);

ALTER TABLE ONLY model_gateway_config ALTER COLUMN id SET DEFAULT nextval('model_gateway_config_id_seq'::regclass);

ALTER TABLE ONLY policy_decisions ALTER COLUMN id SET DEFAULT nextval('policy_decisions_id_seq'::regclass);

ALTER TABLE ONLY policy_rules ALTER COLUMN id SET DEFAULT nextval('policy_rules_id_seq'::regclass);

ALTER TABLE ONLY project_categories ALTER COLUMN id SET DEFAULT nextval('project_categories_id_seq'::regclass);

ALTER TABLE ONLY projects ALTER COLUMN id SET DEFAULT nextval('projects_id_seq'::regclass);

ALTER TABLE ONLY prompt_templates ALTER COLUMN id SET DEFAULT nextval('prompt_templates_id_seq'::regclass);

ALTER TABLE ONLY quota_alerts ALTER COLUMN id SET DEFAULT nextval('quota_alerts_new_id_seq'::regclass);

ALTER TABLE ONLY quota_usage ALTER COLUMN id SET DEFAULT nextval('quota_usage_new_id_seq'::regclass);

ALTER TABLE ONLY registration_tokens ALTER COLUMN id SET DEFAULT nextval('registration_tokens_id_seq'::regclass);

ALTER TABLE ONLY remote_machines ALTER COLUMN id SET DEFAULT nextval('remote_machines_id_seq'::regclass);

ALTER TABLE ONLY retention_history ALTER COLUMN id SET DEFAULT nextval('retention_history_id_seq'::regclass);

ALTER TABLE ONLY role_permissions ALTER COLUMN id SET DEFAULT nextval('role_permissions_id_seq'::regclass);

ALTER TABLE ONLY security_settings ALTER COLUMN id SET DEFAULT nextval('security_settings_id_seq'::regclass);

ALTER TABLE ONLY session_messages ALTER COLUMN id SET DEFAULT nextval('session_messages_id_seq'::regclass);

ALTER TABLE ONLY sessions ALTER COLUMN id SET DEFAULT nextval('sessions_new_id_seq1'::regclass);

ALTER TABLE ONLY shared_sessions ALTER COLUMN id SET DEFAULT nextval('shared_sessions_id_seq'::regclass);

ALTER TABLE ONLY smtp_settings ALTER COLUMN id SET DEFAULT nextval('smtp_settings_id_seq'::regclass);

ALTER TABLE ONLY sso_identities ALTER COLUMN id SET DEFAULT nextval('sso_identities_id_seq'::regclass);

ALTER TABLE ONLY sso_providers ALTER COLUMN id SET DEFAULT nextval('sso_providers_id_seq'::regclass);

ALTER TABLE ONLY sso_sessions ALTER COLUMN id SET DEFAULT nextval('sso_sessions_id_seq'::regclass);

ALTER TABLE ONLY sync_events ALTER COLUMN id SET DEFAULT nextval('sync_events_id_seq'::regclass);

ALTER TABLE ONLY team_members ALTER COLUMN id SET DEFAULT nextval('team_members_id_seq'::regclass);

ALTER TABLE ONLY teams ALTER COLUMN id SET DEFAULT nextval('teams_id_seq'::regclass);

ALTER TABLE ONLY tenant_quotas ALTER COLUMN id SET DEFAULT nextval('tenant_quotas_id_seq'::regclass);

ALTER TABLE ONLY tenant_settings ALTER COLUMN id SET DEFAULT nextval('tenant_settings_id_seq'::regclass);

ALTER TABLE ONLY tenant_usage ALTER COLUMN id SET DEFAULT nextval('tenant_usage_new_id_seq'::regclass);

ALTER TABLE ONLY tenants ALTER COLUMN id SET DEFAULT nextval('tenants_id_seq'::regclass);

ALTER TABLE ONLY tool_account_mapping_rules ALTER COLUMN id SET DEFAULT nextval('tool_account_mapping_rules_id_seq'::regclass);

ALTER TABLE ONLY user_daily_stats ALTER COLUMN id SET DEFAULT nextval('user_daily_stats_id_seq'::regclass);

ALTER TABLE ONLY user_permissions ALTER COLUMN id SET DEFAULT nextval('user_permissions_id_seq'::regclass);

ALTER TABLE ONLY user_projects ALTER COLUMN id SET DEFAULT nextval('user_projects_id_seq'::regclass);

ALTER TABLE ONLY user_tool_accounts ALTER COLUMN id SET DEFAULT nextval('user_tool_accounts_id_seq'::regclass);

ALTER TABLE ONLY users ALTER COLUMN id SET DEFAULT nextval('users_id_seq'::regclass);

ALTER TABLE ONLY web_user_auth_sessions ALTER COLUMN id SET DEFAULT nextval('web_user_auth_sessions_id_seq'::regclass);

ALTER TABLE ONLY workflow_events ALTER COLUMN id SET DEFAULT nextval('workflow_events_id_seq'::regclass);

ALTER TABLE ONLY workflow_milestones ALTER COLUMN id SET DEFAULT nextval('workflow_milestones_id_seq'::regclass);

ALTER TABLE ONLY agent_approvals
    ADD CONSTRAINT agent_approvals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_approvals
    ADD CONSTRAINT agent_approvals_request_id_key UNIQUE (request_id);

ALTER TABLE ONLY agent_run_events
    ADD CONSTRAINT agent_run_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_runs
    ADD CONSTRAINT agent_runs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_runs
    ADD CONSTRAINT agent_runs_run_id_key UNIQUE (run_id);

ALTER TABLE ONLY agent_sessions
    ADD CONSTRAINT agent_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_sessions
    ADD CONSTRAINT agent_sessions_session_id_key UNIQUE (session_id);

ALTER TABLE ONLY agent_tokens
    ADD CONSTRAINT agent_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY agent_tokens
    ADD CONSTRAINT agent_tokens_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY ai_agent_settings
    ADD CONSTRAINT ai_agent_settings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ai_agent_settings
    ADD CONSTRAINT ai_agent_settings_setting_key_key UNIQUE (setting_key);

ALTER TABLE ONLY alerts
    ADD CONSTRAINT alerts_alert_id_key UNIQUE (alert_id);

ALTER TABLE ONLY alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY annotations
    ADD CONSTRAINT annotations_annotation_id_key UNIQUE (annotation_id);

ALTER TABLE ONLY annotations
    ADD CONSTRAINT annotations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY anomaly_status
    ADD CONSTRAINT anomaly_status_pkey PRIMARY KEY (id);

ALTER TABLE ONLY api_key_store
    ADD CONSTRAINT api_key_store_pkey PRIMARY KEY (id);

ALTER TABLE ONLY api_key_store
    ADD CONSTRAINT api_key_store_tenant_id_provider_key_name_key UNIQUE (tenant_id, provider, key_name);

ALTER TABLE ONLY audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY autonomous_workflows
    ADD CONSTRAINT autonomous_workflows_pkey PRIMARY KEY (id);

ALTER TABLE ONLY autonomous_workflows
    ADD CONSTRAINT autonomous_workflows_workflow_id_key UNIQUE (workflow_id);

ALTER TABLE ONLY compliance_reports
    ADD CONSTRAINT compliance_reports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY compliance_reports
    ADD CONSTRAINT compliance_reports_report_id_key UNIQUE (report_id);

ALTER TABLE ONLY content_filter_rules
    ADD CONSTRAINT content_filter_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY daily_messages
    ADD CONSTRAINT daily_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY daily_usage
    ADD CONSTRAINT daily_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY email_notification_logs
    ADD CONSTRAINT email_notification_logs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY insights_reports
    ADD CONSTRAINT insights_reports_pkey PRIMARY KEY (id);

ALTER TABLE ONLY knowledge_base
    ADD CONSTRAINT knowledge_base_entry_id_key UNIQUE (entry_id);

ALTER TABLE ONLY knowledge_base
    ADD CONSTRAINT knowledge_base_pkey PRIMARY KEY (id);

ALTER TABLE ONLY login_attempts
    ADD CONSTRAINT login_attempts_pkey PRIMARY KEY (username);

ALTER TABLE ONLY machine_assignments
    ADD CONSTRAINT machine_assignments_machine_id_user_id_key UNIQUE (machine_id, user_id);

ALTER TABLE ONLY machine_assignments
    ADD CONSTRAINT machine_assignments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY model_gateway_config
    ADD CONSTRAINT model_gateway_config_pkey PRIMARY KEY (id);

ALTER TABLE ONLY notification_preferences
    ADD CONSTRAINT notification_preferences_pkey PRIMARY KEY (user_id);

ALTER TABLE ONLY policy_decisions
    ADD CONSTRAINT policy_decisions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY policy_rules
    ADD CONSTRAINT policy_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY project_categories
    ADD CONSTRAINT project_categories_pkey PRIMARY KEY (id);

ALTER TABLE ONLY projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY prompt_templates
    ADD CONSTRAINT prompt_templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quota_alerts
    ADD CONSTRAINT quota_alerts_new_pkey PRIMARY KEY (id);

ALTER TABLE ONLY quota_usage
    ADD CONSTRAINT quota_usage_new_pkey PRIMARY KEY (id);

ALTER TABLE ONLY registration_tokens
    ADD CONSTRAINT registration_tokens_pkey PRIMARY KEY (id);

ALTER TABLE ONLY registration_tokens
    ADD CONSTRAINT registration_tokens_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY remote_machines
    ADD CONSTRAINT remote_machines_machine_id_key UNIQUE (machine_id);

ALTER TABLE ONLY remote_machines
    ADD CONSTRAINT remote_machines_pkey PRIMARY KEY (id);

ALTER TABLE ONLY retention_history
    ADD CONSTRAINT retention_history_pkey PRIMARY KEY (id);

ALTER TABLE ONLY role_permissions
    ADD CONSTRAINT role_permissions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY role_permissions
    ADD CONSTRAINT role_permissions_role_permission_key UNIQUE (role, permission);

ALTER TABLE ONLY security_settings
    ADD CONSTRAINT security_settings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY security_settings
    ADD CONSTRAINT security_settings_setting_key_key UNIQUE (setting_key);

ALTER TABLE ONLY session_messages
    ADD CONSTRAINT session_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY sessions
    ADD CONSTRAINT sessions_new_pkey1 PRIMARY KEY (id);

ALTER TABLE ONLY sessions
    ADD CONSTRAINT sessions_new_token_key1 UNIQUE (token);

ALTER TABLE ONLY shared_sessions
    ADD CONSTRAINT shared_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY shared_sessions
    ADD CONSTRAINT shared_sessions_share_id_key UNIQUE (share_id);

ALTER TABLE ONLY sso_auth_states
    ADD CONSTRAINT sso_auth_states_pkey PRIMARY KEY (state);

ALTER TABLE ONLY sso_identities
    ADD CONSTRAINT sso_identities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY sso_identities
    ADD CONSTRAINT sso_identities_provider_name_provider_user_id_key UNIQUE (provider_name, provider_user_id);

ALTER TABLE ONLY sso_providers
    ADD CONSTRAINT sso_providers_name_key UNIQUE (name);

ALTER TABLE ONLY sso_providers
    ADD CONSTRAINT sso_providers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY sso_sessions
    ADD CONSTRAINT sso_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY sso_sessions
    ADD CONSTRAINT sso_sessions_session_token_key UNIQUE (session_token);

ALTER TABLE ONLY sync_events
    ADD CONSTRAINT sync_events_event_id_key UNIQUE (event_id);

ALTER TABLE ONLY sync_events
    ADD CONSTRAINT sync_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY team_members
    ADD CONSTRAINT team_members_pkey PRIMARY KEY (id);

ALTER TABLE ONLY team_members
    ADD CONSTRAINT team_members_team_id_user_id_key UNIQUE (team_id, user_id);

ALTER TABLE ONLY teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);

ALTER TABLE ONLY teams
    ADD CONSTRAINT teams_team_id_key UNIQUE (team_id);

ALTER TABLE ONLY tenant_quotas
    ADD CONSTRAINT tenant_quotas_pkey PRIMARY KEY (id);

ALTER TABLE ONLY tenant_quotas
    ADD CONSTRAINT tenant_quotas_tenant_id_key UNIQUE (tenant_id);

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_tenant_id_key UNIQUE (tenant_id);

ALTER TABLE ONLY tenant_usage
    ADD CONSTRAINT tenant_usage_new_pkey PRIMARY KEY (id);

ALTER TABLE ONLY tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY tenants
    ADD CONSTRAINT tenants_slug_key UNIQUE (slug);

ALTER TABLE ONLY tool_account_mapping_rules
    ADD CONSTRAINT tool_account_mapping_rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY daily_messages
    ADD CONSTRAINT uq_daily_messages_date_tool_msg_host UNIQUE (date, tool_name, message_id, host_name);

ALTER TABLE ONLY daily_stats
    ADD CONSTRAINT uq_daily_stats_date_tool_host_sender UNIQUE (date, tool_name, host_name, sender_name);

ALTER TABLE ONLY daily_usage
    ADD CONSTRAINT uq_daily_usage_date_tool_host UNIQUE (date, tool_name, host_name);

ALTER TABLE ONLY hourly_stats
    ADD CONSTRAINT uq_hourly_stats_date_hour_tool_host UNIQUE (date, hour, tool_name, host_name);

ALTER TABLE ONLY tool_account_mapping_rules
    ADD CONSTRAINT uq_mapping_rule_user_pattern UNIQUE (user_id, pattern, match_type);

ALTER TABLE ONLY quota_usage
    ADD CONSTRAINT uq_quota_usage_user_date_period_new UNIQUE (user_id, date, period);

ALTER TABLE ONLY smtp_settings
    ADD CONSTRAINT uq_smtp_settings_single PRIMARY KEY (id);

ALTER TABLE ONLY tenant_usage
    ADD CONSTRAINT uq_tenant_usage_tenant_date_new UNIQUE (tenant_id, date);

ALTER TABLE ONLY usage_summary
    ADD CONSTRAINT uq_usage_summary_tool_host UNIQUE (tool_name, host_name);

ALTER TABLE ONLY user_daily_stats
    ADD CONSTRAINT uq_user_daily_stats_user_date UNIQUE (user_id, date);

ALTER TABLE ONLY user_tool_accounts
    ADD CONSTRAINT uq_user_tool_account UNIQUE (tool_account);

ALTER TABLE ONLY user_daily_stats
    ADD CONSTRAINT user_daily_stats_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_permissions
    ADD CONSTRAINT user_permissions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_permissions
    ADD CONSTRAINT user_permissions_user_id_permission_key UNIQUE (user_id, permission);

ALTER TABLE ONLY user_projects
    ADD CONSTRAINT user_projects_pkey PRIMARY KEY (id);

ALTER TABLE ONLY user_tool_accounts
    ADD CONSTRAINT user_tool_accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

ALTER TABLE ONLY users
    ADD CONSTRAINT users_username_key UNIQUE (username);

ALTER TABLE ONLY web_user_auth_sessions
    ADD CONSTRAINT web_user_auth_sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY web_user_auth_sessions
    ADD CONSTRAINT web_user_auth_sessions_session_token_key UNIQUE (session_token);

ALTER TABLE ONLY workflow_events
    ADD CONSTRAINT workflow_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY workflow_milestones
    ADD CONSTRAINT workflow_milestones_milestone_id_key UNIQUE (milestone_id);

ALTER TABLE ONLY workflow_milestones
    ADD CONSTRAINT workflow_milestones_pkey PRIMARY KEY (id);

CREATE INDEX idx_agent_approvals_run_id ON agent_approvals USING btree (run_id);


--
--

CREATE INDEX idx_agent_approvals_session_id ON agent_approvals USING btree (session_id);

CREATE INDEX idx_agent_approvals_status ON agent_approvals USING btree (status);


--
--

CREATE UNIQUE INDEX idx_agent_runs_session_id ON agent_runs USING btree (session_id);

CREATE INDEX idx_agent_runs_status ON agent_runs USING btree (status);


--
--

CREATE INDEX idx_agent_runs_user_id ON agent_runs USING btree (user_id);

CREATE INDEX idx_agent_sessions_project ON agent_sessions USING btree (project_id);


--
--

CREATE INDEX idx_agent_sessions_session_id ON agent_sessions USING btree (session_id);

CREATE INDEX idx_agent_sessions_session_type ON agent_sessions USING btree (session_type);


--
--

CREATE INDEX idx_agent_sessions_status ON agent_sessions USING btree (status);

CREATE INDEX idx_agent_sessions_tool_name ON agent_sessions USING btree (tool_name);


--
--

CREATE INDEX idx_agent_sessions_user_id ON agent_sessions USING btree (user_id);

CREATE INDEX idx_agent_tokens_hash ON agent_tokens USING btree (token_hash);


--
--

CREATE INDEX idx_agent_tokens_machine ON agent_tokens USING btree (machine_id);

CREATE INDEX idx_ai_agent_settings_key ON ai_agent_settings USING btree (setting_key);


--
--

CREATE INDEX idx_alerts_created_at ON alerts USING btree (created_at);

CREATE INDEX idx_alerts_read ON alerts USING btree (read);


--
--

CREATE INDEX idx_alerts_user_id ON alerts USING btree (user_id);

CREATE INDEX idx_annotations_session ON annotations USING btree (session_id);


--
--

CREATE INDEX idx_api_key_store_tenant_provider ON api_key_store USING btree (tenant_id, provider);

CREATE INDEX idx_audit_action ON audit_logs USING btree (action);


--
--

CREATE INDEX idx_audit_resource ON audit_logs USING btree (resource_type, resource_id);

CREATE INDEX idx_audit_severity ON audit_logs USING btree (severity);


--
--

CREATE INDEX idx_audit_timestamp ON audit_logs USING btree ("timestamp");

CREATE INDEX idx_audit_user_id ON audit_logs USING btree (user_id);


--
--

CREATE INDEX idx_daily_stats_date ON daily_stats USING btree (date);

CREATE INDEX idx_daily_stats_date_tool ON daily_stats USING btree (date, tool_name);


--
--

CREATE INDEX idx_daily_stats_date_tool_host ON daily_stats USING btree (date, tool_name, host_name);

CREATE INDEX idx_daily_stats_host ON daily_stats USING btree (host_name);


--
--

CREATE INDEX idx_daily_stats_project ON daily_stats USING btree (project_id);

CREATE INDEX idx_daily_stats_sender ON daily_stats USING btree (sender_name);


--
--

CREATE INDEX idx_daily_stats_tool ON daily_stats USING btree (tool_name);

CREATE INDEX idx_daily_stats_user_id ON daily_stats USING btree (user_id);


--
--

CREATE INDEX idx_email_logs_sent_at ON email_notification_logs USING btree (sent_at);

CREATE INDEX idx_email_logs_status ON email_notification_logs USING btree (status);


--
--

CREATE INDEX idx_email_logs_user_id ON email_notification_logs USING btree (user_id);

CREATE INDEX idx_email_logs_user_sent ON email_notification_logs USING btree (user_id, sent_at);


--
--

CREATE INDEX idx_events_workflow_created ON workflow_events USING btree (workflow_id, created_at);

CREATE INDEX idx_filter_rules_enabled ON content_filter_rules USING btree (is_enabled);


--
--

CREATE INDEX idx_filter_rules_type ON content_filter_rules USING btree (type);

CREATE INDEX idx_hourly_stats_date ON hourly_stats USING btree (date);


--
--

CREATE INDEX idx_hourly_stats_date_hour ON hourly_stats USING btree (date, hour);

CREATE INDEX idx_hourly_stats_hour ON hourly_stats USING btree (hour);


--
--

CREATE INDEX idx_insights_reports_user_date ON insights_reports USING btree (user_id, start_date, end_date);

CREATE INDEX idx_knowledge_team ON knowledge_base USING btree (team_id);


--
--

CREATE INDEX idx_login_attempts_locked_until ON login_attempts USING btree (locked_until);

CREATE INDEX idx_machine_assignments_user_id ON machine_assignments USING btree (user_id);


--
--

CREATE INDEX idx_mapping_rules_active ON tool_account_mapping_rules USING btree (is_active, priority);

CREATE INDEX idx_mapping_rules_user_id ON tool_account_mapping_rules USING btree (user_id);


--
--

CREATE INDEX idx_messages_agent_session_id ON daily_messages USING btree (agent_session_id);

CREATE INDEX idx_messages_agent_session_project ON daily_messages USING btree (agent_session_id, project_path);


--
--

CREATE INDEX idx_messages_conv_history ON daily_messages USING btree (agent_session_id, conversation_id, feishu_conversation_id, tool_name, host_name, sender_name, date, "timestamp", tokens_used, input_tokens, output_tokens, sender_id);

CREATE INDEX idx_messages_conversation ON daily_messages USING btree (date, conversation_id, agent_session_id);


--
--

CREATE INDEX idx_messages_date_role_sender_prefix ON daily_messages USING btree (date, role, sender_name varchar_pattern_ops);

CREATE INDEX idx_messages_date_role_timestamp ON daily_messages USING btree (date, role, "timestamp" DESC);


--
--

CREATE INDEX idx_messages_date_sender_id ON daily_messages USING btree (date, sender_id);

CREATE INDEX idx_messages_date_tool_host ON daily_messages USING btree (date, tool_name, host_name);


--
--

CREATE INDEX idx_messages_deleted ON daily_messages USING btree (deleted_at);

CREATE INDEX idx_messages_host_name ON daily_messages USING btree (host_name);


--
--

CREATE INDEX idx_messages_project_path ON daily_messages USING btree (project_path);

CREATE INDEX idx_messages_sender_date_role ON daily_messages USING btree (sender_name, date, role);


--
--

CREATE INDEX idx_messages_sender_id ON daily_messages USING btree (sender_id);

CREATE INDEX idx_messages_sender_name ON daily_messages USING btree (sender_name) WHERE (sender_name IS NOT NULL);


--
--

CREATE INDEX idx_messages_session_list_covering ON daily_messages USING btree (agent_session_id, tool_name, host_name, sender_name) INCLUDE ("timestamp", tokens_used, input_tokens, output_tokens, sender_id, date) WHERE (agent_session_id IS NOT NULL);

CREATE INDEX idx_messages_timestamp ON daily_messages USING btree ("timestamp");


--
--

CREATE INDEX idx_messages_tool_name ON daily_messages USING btree (tool_name);

CREATE INDEX idx_messages_usage_trend_covering ON daily_messages USING btree (date, role, sender_name) INCLUDE (tokens_used) WHERE ((role)::text = 'assistant'::text);


--
--

CREATE INDEX idx_messages_user_date_role_covering ON daily_messages USING btree (user_id, date, role) INCLUDE (tokens_used) WHERE ((user_id IS NOT NULL) AND ((role)::text = 'assistant'::text));

CREATE INDEX idx_milestones_workflow_phase ON workflow_milestones USING btree (workflow_id, phase, status);


--
--

CREATE INDEX idx_milestones_workflow_round ON workflow_milestones USING btree (workflow_id, dev_round);

CREATE INDEX idx_policy_decisions_fingerprint ON policy_decisions USING btree (fingerprint_hash);


--
--

CREATE INDEX idx_policy_decisions_request_id ON policy_decisions USING btree (request_id);

CREATE INDEX idx_policy_decisions_session_id ON policy_decisions USING btree (session_id);


--
--

CREATE INDEX idx_policy_rules_current_enabled ON policy_rules USING btree (is_current, enabled);

CREATE INDEX idx_policy_rules_key_current ON policy_rules USING btree (rule_key, is_current);


--
--

CREATE INDEX idx_project_categories_sort_order ON project_categories USING btree (sort_order);

CREATE INDEX idx_projects_created_by ON projects USING btree (created_by);


--
--

CREATE INDEX idx_projects_is_active ON projects USING btree (is_active);

CREATE INDEX idx_projects_path ON projects USING btree (path);


--
--

CREATE INDEX idx_prompt_templates_author ON prompt_templates USING btree (author_id);

CREATE INDEX idx_prompt_templates_category ON prompt_templates USING btree (category);


--
--

CREATE INDEX idx_prompt_templates_public ON prompt_templates USING btree (is_public);

CREATE INDEX idx_quota_alerts_created ON quota_alerts USING btree (created_at);


--
--

CREATE INDEX idx_quota_alerts_unack ON quota_alerts USING btree (acknowledged, created_at);

CREATE INDEX idx_quota_alerts_user ON quota_alerts USING btree (user_id);


--
--

CREATE INDEX idx_quota_usage_date ON quota_usage USING btree (date);

CREATE INDEX idx_quota_usage_user ON quota_usage USING btree (user_id);


--
--

CREATE INDEX idx_registration_tokens_hash ON registration_tokens USING btree (token_hash);

CREATE INDEX idx_remote_machines_hostname_tenant ON remote_machines USING btree (hostname, tenant_id);


--
--

CREATE INDEX idx_remote_machines_machine_id ON remote_machines USING btree (machine_id);

CREATE INDEX idx_remote_machines_status ON remote_machines USING btree (status);


--
--

CREATE INDEX idx_run_events_created_at ON agent_run_events USING btree (created_at);

CREATE INDEX idx_run_events_event_type ON agent_run_events USING btree (event_type);


--
--

CREATE INDEX idx_run_events_run_id ON agent_run_events USING btree (run_id);

CREATE INDEX idx_run_events_session_id ON agent_run_events USING btree (session_id, id);


--
--

CREATE INDEX idx_security_settings_key ON security_settings USING btree (setting_key);

CREATE INDEX idx_session_messages_external_message_id ON session_messages USING btree (session_id, external_message_id);


--
--

CREATE INDEX idx_session_messages_session_id ON session_messages USING btree (session_id);

CREATE INDEX idx_session_messages_source ON session_messages USING btree (session_id, source);


--
--

CREATE INDEX idx_session_stats_session_id ON session_stats USING btree (session_id);

CREATE INDEX idx_session_stats_tool_host ON session_stats USING btree (tool_name, host_name);


--
--

CREATE INDEX idx_session_stats_updated_at ON session_stats USING btree (updated_at DESC);

CREATE INDEX idx_sessions_active ON sessions USING btree (is_active, expires_at);


--
--

CREATE INDEX idx_sessions_expires ON sessions USING btree (expires_at);

CREATE INDEX idx_sessions_token ON sessions USING btree (token);


--
--

CREATE INDEX idx_sessions_user_id ON sessions USING btree (user_id);

CREATE INDEX idx_shared_sessions_session ON shared_sessions USING btree (session_id);


--
--

CREATE INDEX idx_shared_sessions_target ON shared_sessions USING btree (target_id);

CREATE INDEX idx_sso_identities_provider ON sso_identities USING btree (provider_name, provider_user_id);


--
--

CREATE INDEX idx_sso_identities_user ON sso_identities USING btree (user_id);

CREATE INDEX idx_sso_providers_tenant ON sso_providers USING btree (tenant_id);


--
--

CREATE INDEX idx_sso_sessions_token ON sso_sessions USING btree (session_token);

CREATE INDEX idx_sso_sessions_user ON sso_sessions USING btree (user_id);


--
--

CREATE INDEX idx_sync_events_session_id ON sync_events USING btree (session_id);

CREATE INDEX idx_sync_events_timestamp ON sync_events USING btree ("timestamp");


--
--

CREATE INDEX idx_sync_events_user_id ON sync_events USING btree (user_id);

CREATE INDEX idx_team_members_team ON team_members USING btree (team_id);


--
--

CREATE INDEX idx_team_members_user ON team_members USING btree (user_id);

CREATE INDEX idx_teams_owner ON teams USING btree (owner_id);


--
--

CREATE INDEX idx_tenant_quotas_tenant ON tenant_quotas USING btree (tenant_id);

CREATE INDEX idx_tenant_settings_tenant ON tenant_settings USING btree (tenant_id);


--
--

CREATE INDEX idx_tenant_usage_date ON tenant_usage USING btree (date);

CREATE INDEX idx_tenant_usage_tenant ON tenant_usage USING btree (tenant_id);


--
--

CREATE INDEX idx_tenants_deleted ON tenants USING btree (deleted_at);

CREATE INDEX idx_tenants_slug ON tenants USING btree (slug);


--
--

CREATE INDEX idx_tenants_status ON tenants USING btree (status);

CREATE INDEX idx_tool_accounts_tool_account ON user_tool_accounts USING btree (tool_account);


--
--

CREATE INDEX idx_tool_accounts_user_id ON user_tool_accounts USING btree (user_id);

CREATE INDEX idx_usage_date ON daily_usage USING btree (date);


--
--

CREATE INDEX idx_usage_date_tool_host ON daily_usage USING btree (date, tool_name, host_name);

CREATE INDEX idx_usage_host_name ON daily_usage USING btree (host_name);


--
--

CREATE INDEX idx_usage_summary_host ON usage_summary USING btree (host_name);

CREATE INDEX idx_usage_summary_host_name_valid ON usage_summary USING btree (host_name) WHERE ((host_name IS NOT NULL) AND ((host_name)::text <> ''::text) AND ((host_name)::text !~~ '<%>'::text) AND ((length((host_name)::text) >= 1) AND (length((host_name)::text) <= 253)));


--
--

CREATE INDEX idx_usage_summary_tool ON usage_summary USING btree (tool_name);

CREATE INDEX idx_usage_tool_name ON daily_usage USING btree (tool_name);


--
--

CREATE INDEX idx_user_daily_stats_date ON user_daily_stats USING btree (date DESC);

CREATE INDEX idx_user_daily_stats_user_date ON user_daily_stats USING btree (user_id, date DESC);


--
--

CREATE INDEX idx_user_projects_project ON user_projects USING btree (project_id);

CREATE INDEX idx_user_projects_user ON user_projects USING btree (user_id);


--
--

CREATE INDEX idx_users_active ON users USING btree (is_active);

CREATE INDEX idx_users_deleted ON users USING btree (deleted_at);


--
--

CREATE INDEX idx_users_email ON users USING btree (email);

CREATE INDEX idx_users_role ON users USING btree (role);


--
--

CREATE INDEX idx_users_tenant ON users USING btree (tenant_id);

CREATE INDEX idx_workflows_batch_order ON autonomous_workflows USING btree (batch_id, batch_order);


--
--

CREATE INDEX idx_workflows_parent ON autonomous_workflows USING btree (parent_workflow_id);

CREATE INDEX idx_workflows_status_created ON autonomous_workflows USING btree (status, created_at);


--
--

CREATE INDEX idx_workflows_user_status ON autonomous_workflows USING btree (user_id, status);

CREATE UNIQUE INDEX ix_anomaly_status_type_hash ON anomaly_status USING btree (anomaly_type, affected_users_hash);


--
--

CREATE UNIQUE INDEX policy_decisions_decision_id_key ON policy_decisions USING btree (decision_id);

CREATE UNIQUE INDEX policy_rules_rule_key_version_key ON policy_rules USING btree (rule_key, version);


--
--

CREATE UNIQUE INDEX uq_projects_path ON projects USING btree (path) WHERE (is_active IS TRUE);

CREATE UNIQUE INDEX uq_user_projects_user_project ON user_projects USING btree (user_id, project_id);


--
--

ALTER TABLE ONLY anomaly_status
    ADD CONSTRAINT anomaly_status_processed_by_fkey FOREIGN KEY (processed_by) REFERENCES users(id);

ALTER TABLE ONLY api_key_store
    ADD CONSTRAINT api_key_store_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id);

ALTER TABLE ONLY api_key_store
    ADD CONSTRAINT api_key_store_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id);

ALTER TABLE ONLY autonomous_workflows
    ADD CONSTRAINT autonomous_workflows_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_daily_stats
    ADD CONSTRAINT fk_user_daily_stats_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY users
    ADD CONSTRAINT fk_users_tenant FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE SET NULL;

ALTER TABLE ONLY insights_reports
    ADD CONSTRAINT insights_reports_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE ONLY machine_assignments
    ADD CONSTRAINT machine_assignments_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES users(id);

ALTER TABLE ONLY machine_assignments
    ADD CONSTRAINT machine_assignments_machine_id_fkey FOREIGN KEY (machine_id) REFERENCES remote_machines(machine_id);

ALTER TABLE ONLY machine_assignments
    ADD CONSTRAINT machine_assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE ONLY quota_alerts
    ADD CONSTRAINT quota_alerts_new_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY quota_usage
    ADD CONSTRAINT quota_usage_new_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY remote_machines
    ADD CONSTRAINT remote_machines_created_by_fkey FOREIGN KEY (created_by) REFERENCES users(id);

ALTER TABLE ONLY remote_machines
    ADD CONSTRAINT remote_machines_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id);

ALTER TABLE ONLY session_messages
    ADD CONSTRAINT session_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id);

ALTER TABLE ONLY sessions
    ADD CONSTRAINT sessions_new_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY sso_identities
    ADD CONSTRAINT sso_identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE ONLY sso_providers
    ADD CONSTRAINT sso_providers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id);

ALTER TABLE ONLY sso_sessions
    ADD CONSTRAINT sso_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE ONLY tenant_quotas
    ADD CONSTRAINT tenant_quotas_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY tenant_settings
    ADD CONSTRAINT tenant_settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY tenant_usage
    ADD CONSTRAINT tenant_usage_new_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY tool_account_mapping_rules
    ADD CONSTRAINT tool_account_mapping_rules_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY user_tool_accounts
    ADD CONSTRAINT user_tool_accounts_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE ONLY web_user_auth_sessions
    ADD CONSTRAINT web_user_auth_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id);

ALTER TABLE ONLY workflow_milestones
    ADD CONSTRAINT workflow_milestones_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES autonomous_workflows(workflow_id) ON DELETE CASCADE;

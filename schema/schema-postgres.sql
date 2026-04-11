-- Open-ACE Database Schema for PostgreSQL
-- Auto-generated from pg_dump
-- DO NOT EDIT MANUALLY

-- Setup session
SET client_encoding = 'UTF8';

SET client_encoding = 'UTF8';
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
    project_path character varying(500)
);

CREATE SEQUENCE agent_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

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
    read integer DEFAULT 0,
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
    success integer DEFAULT 1,
    error_message text
);

CREATE SEQUENCE audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

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
    project_path character varying(500)
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
    is_published integer DEFAULT 0,
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

CREATE TABLE notification_preferences (
    user_id integer NOT NULL,
    email_enabled integer DEFAULT 1,
    push_enabled integer DEFAULT 1,
    webhook_url text,
    alert_types text,
    min_severity text DEFAULT 'warning'::text
);

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
    is_public integer DEFAULT 0,
    is_featured integer DEFAULT 0,
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
    acknowledged integer DEFAULT 0,
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

CREATE TABLE session_messages (
    id integer NOT NULL,
    session_id text NOT NULL,
    role text NOT NULL,
    content text,
    tokens_used integer DEFAULT 0,
    model text,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    metadata text
);

CREATE SEQUENCE session_messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

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
    allow_comments integer DEFAULT 1,
    allow_copy integer DEFAULT 1,
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

CREATE TABLE sso_providers (
    id integer NOT NULL,
    name text NOT NULL,
    provider_type text NOT NULL,
    config text NOT NULL,
    tenant_id integer,
    is_active integer DEFAULT 1,
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

CREATE TABLE tenant_quotas (
    id integer NOT NULL,
    tenant_id integer NOT NULL,
    daily_token_limit integer DEFAULT 1000000,
    monthly_token_limit integer DEFAULT 30000000,
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

CREATE TABLE users (
    id integer NOT NULL,
    username character varying NOT NULL,
    password_hash character varying NOT NULL,
    email character varying,
    is_admin integer DEFAULT 0,
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
    must_change_password integer DEFAULT 0,
    CONSTRAINT chk_users_role CHECK (((role)::text = ANY ((ARRAY['admin'::character varying, 'manager'::character varying, 'user'::character varying])::text[])))
);

CREATE SEQUENCE users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

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

CREATE INDEX idx_agent_sessions_project ON agent_sessions USING btree (project_id);


--
-- Name: idx_agent_sessions_session_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_agent_sessions_session_id ON agent_sessions USING btree (session_id);

CREATE INDEX idx_agent_sessions_status ON agent_sessions USING btree (status);


--
-- Name: idx_agent_sessions_tool_name; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_agent_sessions_tool_name ON agent_sessions USING btree (tool_name);

CREATE INDEX idx_agent_sessions_user_id ON agent_sessions USING btree (user_id);


--
-- Name: idx_alerts_created_at; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_alerts_created_at ON alerts USING btree (created_at);

CREATE INDEX idx_alerts_read ON alerts USING btree (read);


--
-- Name: idx_alerts_user_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_alerts_user_id ON alerts USING btree (user_id);

CREATE INDEX idx_annotations_session ON annotations USING btree (session_id);


--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_audit_action ON audit_logs USING btree (action);

CREATE INDEX idx_audit_resource ON audit_logs USING btree (resource_type, resource_id);


--
-- Name: idx_audit_severity; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_audit_severity ON audit_logs USING btree (severity);

CREATE INDEX idx_audit_timestamp ON audit_logs USING btree ("timestamp");


--
-- Name: idx_audit_user_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_audit_user_id ON audit_logs USING btree (user_id);

CREATE INDEX idx_daily_stats_date ON daily_stats USING btree (date);


--
-- Name: idx_daily_stats_date_tool; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_daily_stats_date_tool ON daily_stats USING btree (date, tool_name);

CREATE INDEX idx_daily_stats_date_tool_host ON daily_stats USING btree (date, tool_name, host_name);


--
-- Name: idx_daily_stats_host; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_daily_stats_host ON daily_stats USING btree (host_name);

CREATE INDEX idx_daily_stats_project ON daily_stats USING btree (project_id);


--
-- Name: idx_daily_stats_sender; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_daily_stats_sender ON daily_stats USING btree (sender_name);

CREATE INDEX idx_daily_stats_tool ON daily_stats USING btree (tool_name);


--
-- Name: idx_filter_rules_enabled; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_filter_rules_enabled ON content_filter_rules USING btree (is_enabled);

CREATE INDEX idx_filter_rules_type ON content_filter_rules USING btree (type);


--
-- Name: idx_hourly_stats_date; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_hourly_stats_date ON hourly_stats USING btree (date);

CREATE INDEX idx_hourly_stats_date_hour ON hourly_stats USING btree (date, hour);


--
-- Name: idx_hourly_stats_hour; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_hourly_stats_hour ON hourly_stats USING btree (hour);

CREATE INDEX idx_knowledge_team ON knowledge_base USING btree (team_id);


--
-- Name: idx_messages_agent_session_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_agent_session_id ON daily_messages USING btree (agent_session_id);

CREATE INDEX idx_messages_agent_session_project ON daily_messages USING btree (agent_session_id, project_path);


--
-- Name: idx_messages_conversation; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_conversation ON daily_messages USING btree (date, conversation_id, agent_session_id);

CREATE INDEX idx_messages_date_role_sender_prefix ON daily_messages USING btree (date, role, sender_name varchar_pattern_ops);


--
-- Name: idx_messages_date_role_timestamp; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_date_role_timestamp ON daily_messages USING btree (date, role, "timestamp" DESC);

CREATE INDEX idx_messages_date_sender_id ON daily_messages USING btree (date, sender_id);


--
-- Name: idx_messages_date_tool_host; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_date_tool_host ON daily_messages USING btree (date, tool_name, host_name);

CREATE INDEX idx_messages_deleted ON daily_messages USING btree (deleted_at);


--
-- Name: idx_messages_host_name; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_host_name ON daily_messages USING btree (host_name);

CREATE INDEX idx_messages_project_path ON daily_messages USING btree (project_path);


--
-- Name: idx_messages_sender_date_role; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_sender_date_role ON daily_messages USING btree (sender_name, date, role);

CREATE INDEX idx_messages_sender_id ON daily_messages USING btree (sender_id);


--
-- Name: idx_messages_session_list_covering; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_session_list_covering ON daily_messages USING btree (agent_session_id, tool_name, host_name, sender_name) INCLUDE ("timestamp", tokens_used, input_tokens, output_tokens, sender_id, date) WHERE (agent_session_id IS NOT NULL);

CREATE INDEX idx_messages_timestamp ON daily_messages USING btree ("timestamp");


--
-- Name: idx_messages_tool_name; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_tool_name ON daily_messages USING btree (tool_name);

CREATE INDEX idx_messages_usage_trend_covering ON daily_messages USING btree (date, role, sender_name) INCLUDE (tokens_used) WHERE ((role)::text = 'assistant'::text);


--
-- Name: idx_messages_user_date_role_covering; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_messages_user_date_role_covering ON daily_messages USING btree (user_id, date, role) INCLUDE (tokens_used) WHERE ((user_id IS NOT NULL) AND ((role)::text = 'assistant'::text));

CREATE INDEX idx_projects_created_by ON projects USING btree (created_by);


--
-- Name: idx_projects_is_active; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_projects_is_active ON projects USING btree (is_active);

CREATE INDEX idx_projects_path ON projects USING btree (path);


--
-- Name: idx_prompt_templates_author; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_prompt_templates_author ON prompt_templates USING btree (author_id);

CREATE INDEX idx_prompt_templates_category ON prompt_templates USING btree (category);


--
-- Name: idx_prompt_templates_public; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_prompt_templates_public ON prompt_templates USING btree (is_public);

CREATE INDEX idx_quota_alerts_created ON quota_alerts USING btree (created_at);


--
-- Name: idx_quota_alerts_unack; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_quota_alerts_unack ON quota_alerts USING btree (acknowledged, created_at);

CREATE INDEX idx_quota_alerts_user ON quota_alerts USING btree (user_id);


--
-- Name: idx_quota_usage_date; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_quota_usage_date ON quota_usage USING btree (date);

CREATE INDEX idx_quota_usage_user ON quota_usage USING btree (user_id);


--
-- Name: idx_security_settings_key; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_security_settings_key ON security_settings USING btree (setting_key);

CREATE INDEX idx_session_messages_session_id ON session_messages USING btree (session_id);


--
-- Name: idx_session_stats_session_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_session_stats_session_id ON session_stats USING btree (session_id);

CREATE INDEX idx_session_stats_tool_host ON session_stats USING btree (tool_name, host_name);


--
-- Name: idx_session_stats_updated_at; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_session_stats_updated_at ON session_stats USING btree (updated_at DESC);

CREATE INDEX idx_sessions_active ON sessions USING btree (is_active, expires_at);


--
-- Name: idx_sessions_expires; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sessions_expires ON sessions USING btree (expires_at);

CREATE INDEX idx_sessions_token ON sessions USING btree (token);


--
-- Name: idx_sessions_user_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sessions_user_id ON sessions USING btree (user_id);

CREATE INDEX idx_shared_sessions_session ON shared_sessions USING btree (session_id);


--
-- Name: idx_shared_sessions_target; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_shared_sessions_target ON shared_sessions USING btree (target_id);

CREATE INDEX idx_sso_identities_provider ON sso_identities USING btree (provider_name, provider_user_id);


--
-- Name: idx_sso_identities_user; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sso_identities_user ON sso_identities USING btree (user_id);

CREATE INDEX idx_sso_providers_tenant ON sso_providers USING btree (tenant_id);


--
-- Name: idx_sso_sessions_token; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sso_sessions_token ON sso_sessions USING btree (session_token);

CREATE INDEX idx_sso_sessions_user ON sso_sessions USING btree (user_id);


--
-- Name: idx_sync_events_session_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sync_events_session_id ON sync_events USING btree (session_id);

CREATE INDEX idx_sync_events_timestamp ON sync_events USING btree ("timestamp");


--
-- Name: idx_sync_events_user_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_sync_events_user_id ON sync_events USING btree (user_id);

CREATE INDEX idx_team_members_team ON team_members USING btree (team_id);


--
-- Name: idx_team_members_user; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_team_members_user ON team_members USING btree (user_id);

CREATE INDEX idx_teams_owner ON teams USING btree (owner_id);


--
-- Name: idx_tenant_quotas_tenant; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_tenant_quotas_tenant ON tenant_quotas USING btree (tenant_id);

CREATE INDEX idx_tenant_settings_tenant ON tenant_settings USING btree (tenant_id);


--
-- Name: idx_tenant_usage_date; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_tenant_usage_date ON tenant_usage USING btree (date);

CREATE INDEX idx_tenant_usage_tenant ON tenant_usage USING btree (tenant_id);


--
-- Name: idx_tenants_deleted; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_tenants_deleted ON tenants USING btree (deleted_at);

CREATE INDEX idx_tenants_slug ON tenants USING btree (slug);


--
-- Name: idx_tenants_status; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_tenants_status ON tenants USING btree (status);

CREATE INDEX idx_tool_accounts_tool_account ON user_tool_accounts USING btree (tool_account);


--
-- Name: idx_tool_accounts_user_id; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_tool_accounts_user_id ON user_tool_accounts USING btree (user_id);

CREATE INDEX idx_usage_date ON daily_usage USING btree (date);


--
-- Name: idx_usage_date_tool_host; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_usage_date_tool_host ON daily_usage USING btree (date, tool_name, host_name);

CREATE INDEX idx_usage_host_name ON daily_usage USING btree (host_name);


--
-- Name: idx_usage_summary_host; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_usage_summary_host ON usage_summary USING btree (host_name);

CREATE INDEX idx_usage_summary_tool ON usage_summary USING btree (tool_name);


--
-- Name: idx_usage_tool_name; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_usage_tool_name ON daily_usage USING btree (tool_name);

CREATE INDEX idx_user_daily_stats_date ON user_daily_stats USING btree (date DESC);


--
-- Name: idx_user_daily_stats_user_date; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_user_daily_stats_user_date ON user_daily_stats USING btree (user_id, date DESC);

CREATE INDEX idx_user_projects_project ON user_projects USING btree (project_id);


--
-- Name: idx_user_projects_user; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_user_projects_user ON user_projects USING btree (user_id);

CREATE INDEX idx_users_active ON users USING btree (is_active);


--
-- Name: idx_users_deleted; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_users_deleted ON users USING btree (deleted_at);

CREATE INDEX idx_users_email ON users USING btree (email);


--
-- Name: idx_users_role; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE INDEX idx_users_role ON users USING btree (role);

CREATE INDEX idx_users_tenant ON users USING btree (tenant_id);


--
-- Name: uq_projects_path; Type: INDEX; Schema: public; Owner: rhuang
--

CREATE UNIQUE INDEX uq_projects_path ON projects USING btree (path);

CREATE UNIQUE INDEX uq_user_projects_user_project ON user_projects USING btree (user_id, project_id);


--
-- Name: user_daily_stats fk_user_daily_stats_user; Type: FK CONSTRAINT; Schema: public; Owner: rhuang
--

ALTER TABLE ONLY user_daily_stats
    ADD CONSTRAINT fk_user_daily_stats_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

\unrestrict eIcQyuAFlwyUzz6hHRUtKWbAmIIimEqJkwMTSXDKgIFz3O7MvqZeeeBAcai7Jfg
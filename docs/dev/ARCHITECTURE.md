# Architecture — 系统架构

[English](#english) | [中文](#中文)

---

## English

## System Overview

For a detailed explanation of how Claude / Codex / ZCode / Qwen local token usage is collected, computed, stored, and consumed across the stack, see [TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md).

Open ACE (AI Computing Explorer) is an enterprise AI workspace platform with three layers:

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (React SPA)                   │
│  Work Mode (all users)        Manage Mode (admin only)   │
│  ┌────────┬──────────┐       ┌──────────────────────┐   │
│  │Session │Workspace │       │  Dashboard / Admin    │   │
│  │ List   │(iframe)  │       │  Pages (20+)          │   │
│  │        │Terminal  │       │                       │   │
│  └────────┴──────────┘       └──────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────┴──────────────────────────────────┐
│                  Flask API Server                         │
│  39 Blueprints │ 42 Services │ 26 Repositories │ 6 Modules│
│  Background Schedulers │ Middleware │ Auth                 │
└──────────┬───────────────────┬───────────────────────────┘
           │                   │
┌──────────┴──────┐  ┌────────┴────────────────────────────┐
│ SQLite/PostgreSQL│  │       Remote Agent (daemon)          │
│  103 tables     │  │  HTTP polling │ CLI subprocesses     │
│  Alembic         │  │  WS terminal   │ Session sync        │
└─────────────────┘  │  Claude/Qwen/Codex/ZCode/OpenClaw     │
                      └─────────────────────────────────────┘
```

Module counts are file counts excluding `__init__.py` under `app/routes/`, `app/services/`, `app/repositories/`, and `app/modules/` as of this writing; they drift as code is added, so treat them as approximate.

## Backend Architecture

### Layered Architecture

```
Routes (Flask Blueprints)
  → Services (business logic, schedulers)
    → Repositories (data access)
      → Database abstraction (SQLite or PostgreSQL)

Modules (domain logic):
  analytics/  compliance/  governance/  policy/  sso/  workspace/
```

### Application Entry Point

`app/__init__.py` — `create_app()` factory:
1. Creates Flask app
2. Applies `ProxyFix` middleware for nginx
3. Configures `SECRET_KEY`
4. Registers error handlers (JSON for API, standard for pages)
5. Registers 39 Flask Blueprints
6. Runs `ensure_all_tables()` for DDL schema initialization
7. Starts background schedulers

### Blueprint Routes

| Blueprint | Prefix | Description |
|-----------|--------|-------------|
| `admin_bp` | `/api` | User CRUD, system account creation |
| `ai_agent_settings_bp` | `/api` | AI agent settings management |
| `alerts_bp` | `/api` | Alert management, WebSocket push |
| `analysis_bp` | `/api` | Usage analysis, trends, anomalies |
| `analytics_bp` | `/api` | Enterprise analytics, CSV export |
| `api_keys_bp` | `/api` | API key management and scoping |
| `autonomous_bp` | `/api/autonomous` | Autonomous development workflows, CI repair, acceptance |
| `auth_bp` | `/api` | Login, register, logout, sessions, avatars |
| `encryption_keys_bp` | `/api` | Encryption key management |
| `feature_flags_bp` | `/api` | Feature flag management |
| `feishu_config_bp` | `/api` | Feishu integration configuration |
| `frontend_errors_bp` | `/api` | Frontend error reporting |
| `compliance_bp` | `/api/compliance` | Compliance reports, data retention |
| `fetch_bp` | `/api` | Data collection scripts, fetch status |
| `fs_bp` | `/api` | File system browsing |
| `governance_bp` | `/api` | Audit logs, quotas, content filtering |
| `insights_bp` | `/api` | AI conversation insights |
| `mapping_rules_bp` | `/api` | Tenant isolation mapping rules |
| `model_gateway_bp` | `/api` | Model gateway configuration (LiteLLM-compatible) |
| `messages_bp` | `/api` | Message data, pagination, export |
| `notification_integrations_bp` | `/api` | Notification and collaboration settings |
| `pages_bp` | `/` | React SPA catch-all |
| `policy_bp` | `/api` | Policy rules engine |
| `project_categories_bp` | `/api` | Project category management |
| `projects_bp` | `/api` | Project CRUD, stats, file scanning |
| `quota_bp` | `/api` | Quota checking, enforcement |
| `run_timeline_bp` | `/api/remote` | Autonomous session run timeline events (`/api/remote/sessions/<id>/events` and `/approvals`) |
| `remote_bp` | `/api/remote` | Remote machines, sessions, LLM proxy |
| `report_bp` | `/api` | Usage reports |
| `roi_bp` | `/api` | ROI analysis, cost optimization |
| `sso_bp` | `/api/sso` | SSO provider management, OAuth2/OIDC/SAML |
| `smtp_config_bp` | `/api` | SMTP configuration management |
| `system_bp` | `/api` | Scheduler status and system info |
| `tenant_bp` | `/api/tenants` | Multi-tenant management |
| `tool_accounts_bp` | `/api` | User-tool-account mapping |
| `upload_bp` | `/api` | External data ingestion |
| `usage_bp` | `/api` | Usage data, CSV export |
| `workspace_bp` | `/api/workspace` | Sessions, prompts, tool connections |
| `workspace_isolation_bp` | `/api/workspace` | Workspace isolation capability contract |

### Services

| Service | Description |
|---------|-------------|
| `AnalysisService` | Batch analysis, metrics, anomaly detection (ThreadPoolExecutor 4) |
| `AuthService` | Authentication, session management, rate limiting |
| `DataFetchScheduler` | Background scheduler, runs fetch scripts every 5 min |
| `InsightsService` | AI insights via GLM-5 model |
| `MessageService` | Message query, filter, pagination |
| `PermissionService` | RBAC, role-permission mapping, custom permissions |
| `QuotaEnforcementScheduler` | Quota checking every 60s, session termination |
| `SummaryService` | Pre-aggregated usage summary |
| `TenantService` | Multi-tenant CRUD, plan-based quotas |
| `UsageService` | Usage data, per-tool stats |
| `UserDailyStatsAggregator` | Aggregates daily_messages to user_daily_stats |
| `WebUIManager` | Per-user qwen-code-webui processes (ports 3100-3200) |
| `WorkspaceService` | Coordinates collaboration, prompts, sessions |

### Repositories

| Repository | Description |
|------------|-------------|
| `DailyStatsRepository` | Pre-aggregated daily statistics |
| `GovernanceRepository` | Content filter rules, security settings |
| `InsightsReportRepository` | Insights report CRUD |
| `MessageRepository` | Message data access against daily_messages |
| `ProjectRepository` | Project CRUD, stats |
| `TenantRepository` | Tenant CRUD, settings, users |
| `UsageRepository` | Daily usage, per-tool stats, CSV export |
| `UserRepository` | User CRUD, auth lookups |
| `UserToolAccountRepository` | User-tool-account mapping |

### Module Packages

**`app/modules/analytics/`** — Usage analytics, ROI calculation, cost optimization

**`app/modules/compliance/`** — Audit analysis, compliance reports (SOX/GDPR/HIPAA), data retention

**`app/modules/governance/`** — Audit logging, alert notifications, quota management, content filtering (PII detection)

**`app/modules/sso/`** — SSO provider lifecycle, OAuth2 authorization code flow, OIDC with ID token verification, and SAML 2.0 SP metadata/AuthnRequest/ACS handling

**`app/modules/workspace/`** — API key proxy (encrypted storage), collaboration, prompt library, remote agent/session managers, session persistence, state sync, terminal store, tool connector, WebSocket proxy

### Data Models

| Model | Description |
|-------|-------------|
| `User` + `UserRole` + `Permission` | User management with role-based access |
| `Message` | Message tracking with tokens and metadata |
| `Session` | Authentication session |
| `Tenant` + `TenantSettings` + `TenantUsage` | Multi-tenant with plan-based quotas |
| `Usage` | Usage tracking per date/tool |
| `Project` + `ProjectStats` | Project management and statistics |
| `UserToolAccount` | Maps users to AI tool sender names |

## Database

### Dual-Database Support

The `Database` abstraction layer in `app/repositories/database.py` transparently supports both SQLite (single-machine) and PostgreSQL (production):

- **`adapt_sql(query)`** — Converts `?` placeholders to `%s` for PostgreSQL
- **`is_postgresql()`** — Detects active database type from `DATABASE_URL`
- **Connection pooling** — `psycopg2.pool.ThreadedConnectionPool` (min=1, max=10)
- **`Database` class** — DI-friendly wrapper with `execute()`, `fetch_one()`, `fetch_all()`, `table_exists()`
- Default SQLite path: `~/.open-ace/ace.db`

See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) for the full table reference.

## Middleware

| Middleware | Purpose |
|------------|---------|
| `ProxyFix` | Trusts `X-Forwarded-For` / `X-Forwarded-Proto` from nginx |
| CORS headers | `after_request` handler for `/api/` routes from loopback WebUI origins plus explicit allowlist entries |
| OPTIONS handler | Preflight CORS responses |
| Error handlers | JSON for API routes, standard HTTP for pages |
| `/livez` | Process liveness probe, no dependency checks |
| `/readyz` | Readiness probe: database, config, dependencies |
| `/metrics` | Prometheus metrics exposition |
| `/security-status` | Security posture summary |
| `/health` | **Deprecated** — delegates to `/readyz`, response includes `deprecated: true` |

## Background Services

| Scheduler | Interval | Description |
|-----------|----------|-------------|
| `DataFetchScheduler` | 5 min (min 60s) | Runs fetch scripts, refreshes materialized views, aggregates stats, checks quotas |
| `QuotaEnforcementScheduler` | 60s (min 30s) | Checks user quotas, terminates exceeded sessions, generates alerts |

Both are singleton daemon threads started in `create_app()`, wrapped in try/except to prevent startup failure.

## Frontend Architecture

### Tech Stack

- **React 18** + **TypeScript** + **Vite 6**
- **TanStack React Query v5** — data fetching with 1-min stale time
- **Zustand v5** — state management with localStorage persistence
- **Bootstrap 5** + **Headless UI** — styling and accessible components
- **Chart.js** + react-chartjs-2 — data visualization
- **xterm.js** — terminal emulation
- **react-router-dom v7** — dual-track routing

### Dual-Track Routing

**Work Mode** (`/work/*`) — all users:
- 3-panel layout: session list + workspace (iframe) + assist panel
- Routes: sessions, prompts, usage, insights, workspace

**Manage Mode** (`/manage/*`) — admin only:
- Sidebar navigation layout with 20+ admin pages
- Routes: dashboard, analysis, messages, audit, quota, compliance, security, users, tenants, projects, remote machines, SSO settings

See [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) for the complete frontend reference.

## Remote Agent Architecture

```
┌──────────┐   HTTP Polling   ┌──────────────┐
│  Agent   │ ◄──────────────► │  Flask API   │
│ (daemon) │   1s interval     │              │
└────┬─────┘                  └──────────────┘
     │ subprocess
     ▼
┌──────────────────────────────────────────────┐
│              CLI Adapters                     │
│  Claude Code │ Qwen Code │ Codex │ OpenClaw  │
└──────────────────────────────────────────────┘
     │ WebSocket
     ▼
┌──────────────────────────────────────────────┐
│           Terminal Server (PTY)               │
│  64KB output buffer │ HMAC auth │ reconnect  │
└──────────────────────────────────────────────┘
```

The remote agent runs as a Python daemon on remote machines, providing:
- **HTTP polling** — registers machine, polls for commands every 1s, heartbeats every 60s
- **CLI subprocess management** — spawns Claude Code, Qwen Code, Codex, or OpenClaw
- **WebSocket terminal** — browser connects to PTY via terminal server
- **Session sync** — scans `~/.claude/`, `~/.qwen/`, `~/.codex/` for session history, syncs to server every 30s

See [REMOTE_AGENT.md](../guide/REMOTE_AGENT.md) for the client-side guide and [REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md) for the server-side guide.

## Authentication

`app/auth/decorators.py` (plus `machine_access_required` / `machine_admin_required` in `app/routes/remote.py`) defines **10 authentication decorators** — `@admin_required` (deprecated), `@auth_required`, `@platform_admin_required`, `@same_tenant_or_platform_admin`, `@machine_access_required`, `@same_tenant_user_required`, `@machine_admin_required`, `@api_key_admin_required`, `@any_admin_required`, and `@tenant_member_required` — along with the `@public_endpoint` / `@security_annotated` scanner markers.

Session token extraction order: `session_token` cookie → `Authorization: Bearer` header (query-parameter session tokens are rejected; a small path allowlist accepts WebUI/proxy/browser URL tokens with audit logging).

Roles: `user`, `readonly`, `manager`, `tenant_admin`, `platform_admin`, plus legacy `admin`.

See [PERMISSION_MODEL.md](PERMISSION_MODEL.md) for the full role universe, decorator inventory with usage counts, and the `OPENACE_PLATFORM_ADMIN_STRICT_MODE` strict-mode switch.

---

## 中文

## 系统总览

关于 Claude / Codex / ZCode / Qwen 本地 token 如何抓取、计算、落库以及被各层消费，请参阅 [TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md)。

Open ACE (AI Computing Explorer) 是一个企业级 AI 工作区平台，包含三层架构：

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (React SPA)                   │
│  Work Mode (all users)        Manage Mode (admin only)   │
│  ┌────────┬──────────┐       ┌──────────────────────┐   │
│  │Session │Workspace │       │  Dashboard / Admin    │   │
│  │ List   │(iframe)  │       │  Pages (20+)          │   │
│  │        │Terminal  │       │                       │   │
│  └────────┴──────────┘       └──────────────────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP / WebSocket
┌──────────────────────┴──────────────────────────────────┐
│                  Flask API Server                         │
│  39 Blueprints │ 42 Services │ 26 Repositories │ 6 Modules│
│  Background Schedulers │ Middleware │ Auth                 │
└──────────┬───────────────────┬───────────────────────────┘
           │                   │
┌──────────┴──────┐  ┌────────┴────────────────────────────┐
│ SQLite/PostgreSQL│  │       Remote Agent (daemon)          │
│  103 tables     │  │  HTTP polling │ CLI subprocesses     │
│  Alembic         │  │  WS terminal   │ Session sync        │
└─────────────────┘  │  Claude/Qwen/Codex/ZCode/OpenClaw     │
                      └─────────────────────────────────────┘
```

模块计数是 `app/routes/`、`app/services/`、`app/repositories/`、`app/modules/` 下不含 `__init__.py` 的文件数（撰写本文时）；随着代码增加会漂移，请视为近似值。

## 后端架构

### 分层架构

```
Routes (Flask Blueprints)
  → Services (business logic, schedulers)
    → Repositories (data access)
      → Database abstraction (SQLite or PostgreSQL)

Modules (domain logic):
  analytics/  compliance/  governance/  policy/  sso/  workspace/
```

### 应用入口

`app/__init__.py` — `create_app()` 工厂函数：
1. 创建 Flask 应用
2. 应用 `ProxyFix` 中间件以支持 nginx
3. 配置 `SECRET_KEY`
4. 注册错误处理器（API 返回 JSON，页面返回标准格式）
5. 注册 39 个 Flask Blueprint
6. 运行 `ensure_all_tables()` 初始化 DDL 模式
7. 启动后台调度器

### Blueprint 路由

| Blueprint | 前缀 | 说明 |
|-----------|------|------|
| `admin_bp` | `/api` | 用户 CRUD、系统账户创建 |
| `ai_agent_settings_bp` | `/api` | AI Agent 设置管理 |
| `alerts_bp` | `/api` | 告警管理、WebSocket 推送 |
| `analysis_bp` | `/api` | 使用分析、趋势、异常检测 |
| `analytics_bp` | `/api` | 企业分析、CSV 导出 |
| `api_keys_bp` | `/api` | API Key 管理与作用域 |
| `autonomous_bp` | `/api/autonomous` | 自主开发工作流、CI 修复、验收 |
| `auth_bp` | `/api` | 登录、注册、登出、会话、头像 |
| `encryption_keys_bp` | `/api` | 加密密钥管理 |
| `feature_flags_bp` | `/api` | 功能开关管理 |
| `feishu_config_bp` | `/api` | 飞书集成配置 |
| `frontend_errors_bp` | `/api` | 前端错误上报 |
| `compliance_bp` | `/api/compliance` | 合规报告、数据保留 |
| `fetch_bp` | `/api` | 数据采集脚本、采集状态 |
| `fs_bp` | `/api` | 文件系统浏览 |
| `governance_bp` | `/api` | 审计日志、配额、内容过滤 |
| `insights_bp` | `/api` | AI 对话洞察 |
| `mapping_rules_bp` | `/api` | 租户隔离映射规则 |
| `model_gateway_bp` | `/api` | 模型网关配置（LiteLLM 兼容） |
| `messages_bp` | `/api` | 消息数据、分页、导出 |
| `notification_integrations_bp` | `/api` | 通知与协作集成设置 |
| `pages_bp` | `/` | React SPA 全局捕获 |
| `policy_bp` | `/api` | 策略规则引擎 |
| `project_categories_bp` | `/api` | 项目分类管理 |
| `projects_bp` | `/api` | 项目 CRUD、统计、文件扫描 |
| `quota_bp` | `/api` | 配额检查、执行 |
| `run_timeline_bp` | `/api/remote` | 自主会话运行时间线事件（`/api/remote/sessions/<id>/events` 与 `/approvals`） |
| `remote_bp` | `/api/remote` | 远程机器、会话、LLM 代理 |
| `report_bp` | `/api` | 使用报告 |
| `roi_bp` | `/api` | ROI 分析、成本优化 |
| `sso_bp` | `/api/sso` | SSO 提供商管理、OAuth2/OIDC/SAML |
| `smtp_config_bp` | `/api` | SMTP 配置管理 |
| `system_bp` | `/api` | 调度器状态与系统信息 |
| `tenant_bp` | `/api/tenants` | 多租户管理 |
| `tool_accounts_bp` | `/api` | 用户-工具-账户映射 |
| `upload_bp` | `/api` | 外部数据导入 |
| `usage_bp` | `/api` | 使用数据、CSV 导出 |
| `workspace_bp` | `/api/workspace` | 会话、提示词、工具连接 |
| `workspace_isolation_bp` | `/api/workspace` | 工作区隔离能力契约 |

### 服务层

| 服务 | 说明 |
|------|------|
| `AnalysisService` | 批量分析、指标、异常检测（ThreadPoolExecutor 4 线程） |
| `AuthService` | 认证、会话管理、速率限制 |
| `DataFetchScheduler` | 后台调度器，每 5 分钟运行采集脚本 |
| `InsightsService` | 通过 GLM-5 模型提供 AI 洞察 |
| `MessageService` | 消息查询、筛选、分页 |
| `PermissionService` | RBAC、角色-权限映射、自定义权限 |
| `QuotaEnforcementScheduler` | 每 60 秒检查配额，终止超额会话 |
| `SummaryService` | 预聚合使用摘要 |
| `TenantService` | 多租户 CRUD、基于套餐的配额 |
| `UsageService` | 使用数据、按工具统计 |
| `UserDailyStatsAggregator` | 将 daily_messages 聚合到 user_daily_stats |
| `WebUIManager` | 每用户 qwen-code-webui 进程（端口 3100-3200） |
| `WorkspaceService` | 协调协作、提示词、会话 |

### 仓储层

| 仓储 | 说明 |
|------|------|
| `DailyStatsRepository` | 预聚合每日统计 |
| `GovernanceRepository` | 内容过滤规则、安全设置 |
| `InsightsReportRepository` | 洞察报告 CRUD |
| `MessageRepository` | 基于 daily_messages 的消息数据访问 |
| `ProjectRepository` | 项目 CRUD、统计 |
| `TenantRepository` | 租户 CRUD、设置、用户 |
| `UsageRepository` | 每日使用量、按工具统计、CSV 导出 |
| `UserRepository` | 用户 CRUD、认证查询 |
| `UserToolAccountRepository` | 用户-工具-账户映射 |

### 模块包

**`app/modules/analytics/`** — 使用分析、ROI 计算、成本优化

**`app/modules/compliance/`** — 审计分析、合规报告（SOX/GDPR/HIPAA）、数据保留

**`app/modules/governance/`** — 审计日志、告警通知、配额管理、内容过滤（PII 检测）

**`app/modules/sso/`** — SSO 提供商生命周期、OAuth2 授权码流程、带 ID Token 验证的 OIDC，以及 SAML 2.0 SP metadata/AuthnRequest/ACS 处理

**`app/modules/workspace/`** — API Key 代理（加密存储）、协作、提示词库、远程代理/会话管理器、会话持久化、状态同步、终端存储、工具连接器、WebSocket 代理

### 数据模型

| 模型 | 说明 |
|------|------|
| `User` + `UserRole` + `Permission` | 基于角色的用户管理 |
| `Message` | 带 token 和元数据的消息追踪 |
| `Session` | 认证会话 |
| `Tenant` + `TenantSettings` + `TenantUsage` | 多租户与基于套餐的配额 |
| `Usage` | 按日期/工具的使用追踪 |
| `Project` + `ProjectStats` | 项目管理和统计 |
| `UserToolAccount` | 用户到 AI 工具发送者名称的映射 |

## 数据库

### 双数据库支持

`app/repositories/database.py` 中的 `Database` 抽象层同时支持 SQLite（单机）和 PostgreSQL（生产环境）：

- **`adapt_sql(query)`** — 将 `?` 占位符转换为 PostgreSQL 的 `%s`
- **`is_postgresql()`** — 从 `DATABASE_URL` 检测当前数据库类型
- **连接池** — `psycopg2.pool.ThreadedConnectionPool`（min=1, max=10）
- **`Database` 类** — 支持 DI 的封装，提供 `execute()`、`fetch_one()`、`fetch_all()`、`table_exists()`
- 默认 SQLite 路径：`~/.open-ace/ace.db`

完整表结构参考请参阅 [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)。

## 中间件

| 中间件 | 用途 |
|--------|------|
| `ProxyFix` | 信任来自 nginx 的 `X-Forwarded-For` / `X-Forwarded-Proto` |
| CORS 头 | `after_request` 处理器，用于 `/api/` 路由的 loopback WebUI 跨域和显式白名单来源 |
| OPTIONS 处理器 | 预检 CORS 响应 |
| 错误处理器 | API 路由返回 JSON，页面返回标准 HTTP |
| `/livez` | 进程存活探针，不检查依赖 |
| `/readyz` | 就绪探针：数据库、配置、依赖 |
| `/metrics` | Prometheus 指标暴露 |
| `/security-status` | 安全状态摘要 |
| `/health` | **已废弃** —— 委托给 `/readyz`，响应包含 `deprecated: true` |

## 后台服务

| 调度器 | 间隔 | 说明 |
|--------|------|------|
| `DataFetchScheduler` | 5 分钟（最小 60 秒） | 运行采集脚本、刷新物化视图、聚合统计、检查配额 |
| `QuotaEnforcementScheduler` | 60 秒（最小 30 秒） | 检查用户配额、终止超额会话、生成告警 |

两者都是在 `create_app()` 中启动的单例守护线程，包裹在 try/except 中以防止启动失败。

## 前端架构

### 技术栈

- **React 18** + **TypeScript** + **Vite 6**
- **TanStack React Query v5** — 数据获取，1 分钟 stale time
- **Zustand v5** — 状态管理，支持 localStorage 持久化
- **Bootstrap 5** + **Headless UI** — 样式和无障碍组件
- **Chart.js** + react-chartjs-2 — 数据可视化
- **xterm.js** — 终端模拟
- **react-router-dom v7** — 双轨路由

### 双轨路由

**工作模式** (`/work/*`) — 所有用户：
- 3 面板布局：会话列表 + 工作区（iframe）+ 辅助面板
- 路由：sessions、prompts、usage、insights、workspace

**管理模式** (`/manage/*`) — 仅管理员：
- 侧边栏导航布局，20+ 管理页面
- 路由：dashboard、analysis、messages、audit、quota、compliance、security、users、tenants、projects、remote machines、SSO settings

完整前端参考请参阅 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)。

## 远程代理架构

```
┌──────────┐   HTTP Polling   ┌──────────────┐
│  Agent   │ ◄──────────────► │  Flask API   │
│ (daemon) │   1s interval     │              │
└────┬─────┘                  └──────────────┘
     │ subprocess
     ▼
┌──────────────────────────────────────────────┐
│              CLI Adapters                     │
│  Claude Code │ Qwen Code │ Codex │ OpenClaw  │
└──────────────────────────────────────────────┘
     │ WebSocket
     ▼
┌──────────────────────────────────────────────┐
│           Terminal Server (PTY)               │
│  64KB output buffer │ HMAC auth │ reconnect  │
└──────────────────────────────────────────────┘
```

远程代理作为 Python 守护进程运行在远程机器上，提供：
- **HTTP 轮询** — 注册机器，每 1 秒轮询命令，每 60 秒心跳
- **CLI 子进程管理** — 启动 Claude Code、Qwen Code、Codex 或 OpenClaw
- **WebSocket 终端** — 浏览器通过终端服务器连接 PTY
- **会话同步** — 扫描 `~/.claude/`、`~/.qwen/`、`~/.codex/` 的会话历史，每 30 秒同步到服务器

客户端指南请参阅 [REMOTE_AGENT.md](../guide/REMOTE_AGENT.md)，服务端指南请参阅 [REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md)。

## 认证

`app/auth/decorators.py`（外加 `app/routes/remote.py` 中的 `machine_access_required` / `machine_admin_required`）定义了 **10 个认证装饰器** —— `@admin_required`（已弃用）、`@auth_required`、`@platform_admin_required`、`@same_tenant_or_platform_admin`、`@machine_access_required`、`@same_tenant_user_required`、`@machine_admin_required`、`@api_key_admin_required`、`@any_admin_required`、`@tenant_member_required` —— 以及 `@public_endpoint` / `@security_annotated` 两个扫描器标记。

Session token 提取顺序：`session_token` cookie → `Authorization: Bearer` 头（查询参数中的 session token 会被拒绝；少量路径白名单接受 WebUI/proxy/browser URL token 并写审计日志）。

角色：`user`、`readonly`、`manager`、`tenant_admin`、`platform_admin`，以及遗留的 `admin`。

完整角色宇宙、带使用计数的装饰器清单和 `OPENACE_PLATFORM_ADMIN_STRICT_MODE` 严格模式开关，请参阅 [PERMISSION_MODEL.md](PERMISSION_MODEL.md)。

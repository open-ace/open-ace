# Architecture

## System Overview

For a detailed explanation of how Claude / Codex / ZCode / Qwen local token usage is collected, computed, stored, and consumed across the stack, see [token-accounting.md](token-accounting.md).

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
│  23 Blueprints │ 14 Services │ 11 Repositories │ 31 Mods │
│  Background Schedulers │ Middleware │ Auth                 │
└──────────┬───────────────────┬───────────────────────────┘
           │                   │
┌──────────┴──────┐  ┌────────┴────────────────────────────┐
│ SQLite/PostgreSQL│  │       Remote Agent (daemon)          │
│  35+ tables      │  │  HTTP polling │ CLI subprocesses     │
│  Alembic         │  │  WS terminal   │ Session sync        │
└─────────────────┘  │  Claude/Qwen/Codex/OpenClaw          │
                      └─────────────────────────────────────┘
```

## Backend Architecture

### Layered Architecture

```
Routes (Flask Blueprints)
  → Services (business logic, schedulers)
    → Repositories (data access)
      → Database abstraction (SQLite or PostgreSQL)

Modules (domain logic):
  analytics/  compliance/  governance/  sso/  workspace/
```

### Application Entry Point

`app/__init__.py` — `create_app()` factory:
1. Creates Flask app
2. Applies `ProxyFix` middleware for nginx
3. Configures `SECRET_KEY`
4. Registers error handlers (JSON for API, standard for pages)
5. Registers 23 Flask Blueprints
6. Runs `ensure_all_tables()` for DDL schema initialization
7. Starts background schedulers

### Blueprint Routes

| Blueprint | Prefix | Description |
|-----------|--------|-------------|
| `admin_bp` | `/api` | User CRUD, system account creation |
| `alerts_bp` | `/api` | Alert management, WebSocket push |
| `analysis_bp` | `/api` | Usage analysis, trends, anomalies |
| `analytics_bp` | `/api` | Enterprise analytics, CSV export |
| `auth_bp` | `/api` | Login, register, logout, sessions, avatars |
| `compliance_bp` | `/api/compliance` | Compliance reports, data retention |
| `fetch_bp` | `/api` | Data collection scripts, fetch status |
| `fs_bp` | `/api` | File system browsing |
| `governance_bp` | `/api` | Audit logs, quotas, content filtering |
| `insights_bp` | `/api` | AI conversation insights |
| `messages_bp` | `/api` | Message data, pagination, export |
| `pages_bp` | `/` | React SPA catch-all |
| `projects_bp` | `/api` | Project CRUD, stats, file scanning |
| `quota_bp` | `/api` | Quota checking, enforcement |
| `remote_bp` | `/api/remote` | Remote machines, sessions, LLM proxy |
| `report_bp` | `/api` | Usage reports |
| `roi_bp` | `/api` | ROI analysis, cost optimization |
| `sso_bp` | `/api/sso` | SSO provider management, OAuth2/OIDC/SAML |
| `tenant_bp` | `/api/tenants` | Multi-tenant management |
| `tool_accounts_bp` | `/api` | User-tool-account mapping |
| `upload_bp` | `/api` | External data ingestion |
| `usage_bp` | `/api` | Usage data, CSV export |
| `workspace_bp` | `/api/workspace` | Sessions, prompts, tool connections |

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

See [DATABASE-SCHEMA.md](DATABASE-SCHEMA.md) for the full table reference.

## Middleware

| Middleware | Purpose |
|------------|---------|
| `ProxyFix` | Trusts `X-Forwarded-For` / `X-Forwarded-Proto` from nginx |
| CORS headers | `after_request` handler for `/api/` routes from loopback WebUI origins plus explicit allowlist entries |
| OPTIONS handler | Preflight CORS responses |
| Error handlers | JSON for API routes, standard HTTP for pages |
| `/health` | Returns service status and git commit hash |

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

See [FRONTEND-GUIDE.md](FRONTEND-GUIDE.md) for the complete frontend reference.

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

See [REMOTE-AGENT.md](REMOTE-AGENT.md) for the client-side guide and [REMOTE-WORKSPACE.md](REMOTE-WORKSPACE.md) for the server-side guide.

## Authentication

Three auth decorators in `app/auth/decorators.py`:

| Decorator | Purpose |
|-----------|---------|
| `@auth_required` | Valid session token required; optional `ownership='session'` or `'machine'` |
| `@admin_required` | Admin role required |
| `@public_endpoint` | Marks intentionally public endpoints |

Token extraction order: `session_token` cookie → `Authorization: Bearer` header → `token` query param.

See [PERMISSION-MODEL.md](PERMISSION-MODEL.md) for the full permission model.

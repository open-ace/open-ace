# 系统架构

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
│  39 Blueprints │ 41 Services │ 26 Repositories │ 6 Modules│
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
| `run_timeline_bp` | `/api` | 自主会话运行时间线事件 |
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
| `/health` | 返回服务状态和 git commit hash |

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

客户端指南请参阅 [REMOTE_AGENT.md](REMOTE_AGENT.md)，服务端指南请参阅 [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md)。

## 认证

`app/auth/decorators.py` 中的三个认证装饰器：

| 装饰器 | 用途 |
|--------|------|
| `@auth_required` | 需要有效的 session token；可选 `ownership='session'` 或 `'machine'` |
| `@admin_required` | 需要 admin 角色 |
| `@public_endpoint` | 标记为有意公开的端点 |

Token 提取顺序：`session_token` cookie → `Authorization: Bearer` 头 → `token` 查询参数。

完整权限模型请参阅 [PERMISSION_MODEL.md](PERMISSION_MODEL.md)。

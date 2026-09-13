# Open ACE Documentation

[English](#english) | [中文](#中文)

---

## English

User-facing guides live in [en/](en/). Engineering reference docs sit at the top
level of `docs/` and under [security/](security/), [api/](api/) and
[architecture/](architecture/).

### Guides (en/)

| Document | Description |
|----------|-------------|
| [**INTRO**](en/INTRO.md) | Product introduction, core capabilities, and quick start guide |
| [**ARCHITECTURE**](en/ARCHITECTURE.md) | System architecture overview — backend, frontend, and remote agent layers |
| [**AUTONOMOUS_DEVELOPMENT**](en/AUTONOMOUS_DEVELOPMENT.md) | AI autonomous development — lifecycle, three-session design, CI repair, isolation, and maintenance |
| [**API**](en/API.md) | Complete REST API reference for all endpoints |
| [**DATABASE_SCHEMA**](en/DATABASE_SCHEMA.md) | Database tables, columns, foreign keys, and indexes |
| [**DATABASE_CONVENTIONS**](en/DATABASE_CONVENTIONS.md) | Naming conventions for database fields and migrations |
| [**SCHEMA_MIGRATION_GUIDE**](en/SCHEMA_MIGRATION_GUIDE.md) | Alembic migration strategy, workflow, and troubleshooting |
| [**PERMISSION_MODEL**](en/PERMISSION_MODEL.md) | Role-based access control, authentication, and authorization |
| [**FRONTEND_GUIDE**](en/FRONTEND_GUIDE.md) | React/TypeScript frontend development guide |
| [**REMOTE_AGENT**](en/REMOTE_AGENT.md) | Remote agent client — installation, configuration, CLI tools |
| [**REMOTE_WORKSPACE**](en/REMOTE_WORKSPACE.md) | Remote workspace from server perspective — deployment, management UI, API |
| [**DEPLOYMENT**](en/DEPLOYMENT.md) | Docker deployment and multi-user workspace setup |
| [**KUBERNETES**](en/KUBERNETES.md) | Single-instance Kubernetes deployment guide with manifests reference |
| [**NGINX**](en/NGINX.md) | Nginx reverse proxy configuration for HTTPS and WebSocket |
| [**DEVELOPMENT**](en/DEVELOPMENT.md) | Development environment setup, project structure, and testing |
| [**FEISHU_CONFIG**](en/FEISHU_CONFIG.md) | Feishu/Lark integration configuration guide |
| [**DINGTALK_CONFIG**](en/DINGTALK_CONFIG.md) | DingTalk integration configuration guide |
| [**SAML_CONFIG**](en/SAML_CONFIG.md) | SAML 2.0 SSO provider configuration guide |
| [**CONCEPTS**](en/CONCEPTS.md) | Core concept definitions — Request, Message, Session, Conversation |
| [**TOKEN_ACCOUNTING**](en/TOKEN_ACCOUNTING.md) | Deep dive into Claude / Codex / ZCode / Qwen token collection, computation, storage, and downstream usage |
| [**WORKSPACE_SESSION_DATA_CONTRACT**](en/WORKSPACE_SESSION_DATA_CONTRACT.md) | Data boundary of the workspace session tables and the product semantics of `request_count` |

### Engineering reference (top level)

| Document | Description | Language |
|----------|-------------|----------|
| [**TEST_LAYERS**](TEST_LAYERS.md) | Test taxonomy, placement, and CI execution semantics — the authoritative spec | 中文 |
| [**MODEL_GATEWAY**](MODEL_GATEWAY.md) | LiteLLM-compatible model gateway — routing, config, failover | English |
| [**SANDBOX_BACKENDS**](SANDBOX_BACKENDS.md) | Where autonomous agents execute — sandbox backends, capabilities, and trade-offs | English |
| [**TRANSCRIPT_CONTRACT**](TRANSCRIPT_CONTRACT.md) | Pinned contract for remote session transcripts, `content_blocks`, and replay | English |
| [**WORKSPACE_ISOLATION_CAPABILITIES**](WORKSPACE_ISOLATION_CAPABILITIES.md) | Versioned capability contract for local multi-user workspace isolation | 中文 |
| [**GH_CLI_VERSION_COMPATIBILITY**](GH_CLI_VERSION_COMPATIBILITY.md) | gh CLI version matrix, pinning policy, and audit notes | 中文 |
| [**TENANT_ADMIN_PERMISSIONS**](TENANT_ADMIN_PERMISSIONS.md) | Tenant-admin permission model (issue #2179) | 中文 |
| [**REPOSITORY_SETUP**](REPOSITORY_SETUP.md) | GitHub repository topics, labels, releases, and demo checklist | English |

Also see [security/](security/) (SSH boundaries, API exception registry,
Bandit baseline), [api/](api/) (generated API permission matrix, filter
pattern migrations), and [architecture/](architecture/) (autonomous phase
contracts). Process write-ups — fix retrospectives, runbooks, audits, and
agent handoffs — are archived in [dev-notes/](dev-notes/README.md).
Launch and outreach materials live in [marketing/](marketing/README.md).

### Reading Guide by Role

| Role | Recommended Reading |
|------|---------------------|
| New to Open ACE | INTRO → ARCHITECTURE → DEVELOPMENT |
| Autonomous development maintainer | AUTONOMOUS_DEVELOPMENT → ARCHITECTURE → SANDBOX_BACKENDS |
| Frontend developer | FRONTEND_GUIDE → DEVELOPMENT |
| DevOps / Deployment | DEPLOYMENT → KUBERNETES → NGINX |
| API integrator | API → PERMISSION_MODEL → CONCEPTS |
| Managing remote machines | REMOTE_WORKSPACE → REMOTE_AGENT |
| Growing the project | MARKETING → REPOSITORY_SETUP |

---

## 中文

面向使用者的指南在 [cn/](cn/) 目录。工程参考文档位于 `docs/` 顶层以及
[security/](security/)、[api/](api/)、[architecture/](architecture/) 下。

### 指南（cn/）

| 文档 | 说明 |
|------|------|
| [**INTRO**](cn/INTRO.md) | 产品介绍、核心功能和快速入门指南 |
| [**ARCHITECTURE**](cn/ARCHITECTURE.md) | 系统架构总览 — 后端、前端和远程代理层 |
| [**AUTONOMOUS_DEVELOPMENT**](cn/AUTONOMOUS_DEVELOPMENT.md) | AI 自主开发 — 生命周期、三会话设计、CI 修复、隔离执行和维护指南 |
| [**API**](cn/API.md) | 完整的 REST API 端点参考文档 |
| [**DATABASE_SCHEMA**](cn/DATABASE_SCHEMA.md) | 数据库表、列、外键和索引 |
| [**DATABASE_CONVENTIONS**](cn/DATABASE_CONVENTIONS.md) | 数据库字段和迁移的命名规范 |
| [**SCHEMA_MIGRATION_GUIDE**](cn/SCHEMA_MIGRATION_GUIDE.md) | Alembic 迁移策略、工作流和故障排查 |
| [**PERMISSION_MODEL**](cn/PERMISSION_MODEL.md) | 基于角色的访问控制、认证和授权 |
| [**FRONTEND_GUIDE**](cn/FRONTEND_GUIDE.md) | React/TypeScript 前端开发指南 |
| [**REMOTE_AGENT**](cn/REMOTE_AGENT.md) | 远程代理客户端 — 安装、配置、CLI 工具 |
| [**REMOTE_WORKSPACE**](cn/REMOTE_WORKSPACE.md) | 服务端视角的远程工作区 — 部署、管理界面、API |
| [**DEPLOYMENT**](cn/DEPLOYMENT.md) | Docker 部署和多用户工作空间配置 |
| [**KUBERNETES**](cn/KUBERNETES.md) | 单实例 Kubernetes 部署指南及 manifests 参考 |
| [**NGINX**](cn/NGINX.md) | Nginx 反向代理配置（HTTPS 和 WebSocket） |
| [**DEVELOPMENT**](cn/DEVELOPMENT.md) | 开发环境搭建、项目结构和测试 |
| [**FEISHU_CONFIG**](cn/FEISHU_CONFIG.md) | 飞书集成配置指南 |
| [**DINGTALK_CONFIG**](cn/DINGTALK_CONFIG.md) | 钉钉集成配置指南 |
| [**SAML_CONFIG**](cn/SAML_CONFIG.md) | SAML 2.0 SSO Provider 配置指南 |
| [**CONCEPTS**](cn/CONCEPTS.md) | 核心概念定义 — Request、Message、Session、Conversation |
| [**TOKEN_ACCOUNTING**](cn/TOKEN_ACCOUNTING.md) | Claude / Codex / ZCode / Qwen token 抓取、计算、落库和下游消费链路说明 |
| [**WORKSPACE_SESSION_DATA_CONTRACT**](cn/WORKSPACE_SESSION_DATA_CONTRACT.md) | Workspace 会话三表的数据边界与 `request_count` 产品语义 |

### 工程参考（顶层）

见上方英文区 "Engineering reference" 表格（TEST_LAYERS、MODEL_GATEWAY、
SANDBOX_BACKENDS、TRANSCRIPT_CONTRACT、WORKSPACE_ISOLATION_CAPABILITIES、
GH_CLI_VERSION_COMPATIBILITY、TENANT_ADMIN_PERMISSIONS、REPOSITORY_SETUP）。
安全规范在 [security/](security/)，API 参考在 [api/](api/)，自主开发阶段契约在
[architecture/](architecture/)。过程性文档（修复复盘、runbook、审计、agent 交接）
归档于 [dev-notes/](dev-notes/README.md)，发布传播材料在
[marketing/](marketing/README.md)。

### 按角色阅读指南

| 角色 | 推荐阅读顺序 |
|------|--------------|
| 初次了解 Open ACE | INTRO → ARCHITECTURE → DEVELOPMENT |
| 自主开发维护者 | AUTONOMOUS_DEVELOPMENT → ARCHITECTURE → SANDBOX_BACKENDS |
| 前端开发者 | FRONTEND_GUIDE → DEVELOPMENT |
| 运维 / 部署 | DEPLOYMENT → KUBERNETES → NGINX |
| API 集成 | API → PERMISSION_MODEL → CONCEPTS |
| 管理远程机器 | REMOTE_WORKSPACE → REMOTE_AGENT |
| 推广项目 | MARKETING → REPOSITORY_SETUP |

---

## Directory Structure / 目录结构

```
docs/
├── README.md            ← You are here / 你在这里
├── en/                  # English guides / 英文指南
├── cn/                  # 中文指南
├── security/            # Security baselines and boundaries / 安全规范
├── api/                 # API reference artifacts (generated matrix, migrations) / API 参考
├── architecture/        # Cross-cutting architecture contracts / 架构契约
├── marketing/           # Launch and outreach materials / 发布传播材料
├── images/              # Documentation images / 文档图片
└── dev-notes/           # Process write-up archive (fix retros, audits, runbooks) / 过程文档归档
```

Naming: curated documents use `UPPER_SNAKE_CASE.md`. The `dev-notes/` archive
keeps its own `<issue>-<slug>.md` convention (see its README).

命名约定：正式文档统一 `大写蛇形.md`；`dev-notes/` 归档区沿用其
`<issue 编号>-<短描述>.md` 约定（见其 README）。

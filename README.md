<p align="center">
  <img src="docs/images/logo.svg" alt="Open ACE Logo" width="120" height="120">
</p>

<h1 align="center">Open ACE</h1>

<p align="center">
  <strong>AI Computing Explorer</strong><br>
  <em>自托管 AI Coding Agent 工作台与治理控制面</em>
</p>

<p align="center">
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/Python-3.9%2B-green.svg" alt="Python">
  <img src="https://img.shields.io/badge/React-18.3-61DAFB.svg" alt="React">
  <img src="https://img.shields.io/badge/Flask-2.x-orange.svg" alt="Flask">
</p>

<p align="center">
  <a href="https://www.open-ace.com">Website</a> ·
  <a href="https://open-ace.github.io/open-ace-docs/docs/intro">Docs</a> ·
  <a href="https://github.com/open-ace/open-ace-docs">Docs Repo</a> ·
  <a href="ROADMAP.md">Roadmap</a> ·
  <a href="https://github.com/open-ace/open-ace/discussions">Discussions</a>
</p>

---

<a name="中文"></a>

## 🎯 这是什么？

**Open ACE** 是一个开源的**自托管 AI Coding Agent 工作台与治理控制面**：开发者可以在浏览器里统一使用 Claude Code、Qwen Code、Codex、OpenClaw 等 AI 编码工具，并把它们运行在团队自己的远程机器上；管理员可以集中管理 API Key、权限、成本、配额、审计和合规。

它适合已经把 AI Coding Agent 引入真实研发流程的组织，尤其适合需要私有化部署、内网远程机器、统一密钥代理、团队配额和可追溯审计的研发团队、IT 团队和 AI 平台团队。

| 问题 | 解决方案 |
|------|----------|
| 🤖 **团队同时使用多个 AI Coding Agent** | 多 CLI 工作台统一 Claude Code、Qwen Code、Codex、OpenClaw 等工具 |
| 🖥️ **Agent 需要跑在内网、测试机或 GPU 机器上** | Remote Agent 让 AI CLI 直接在目标机器执行 |
| 🔑 **API Key 不该散落在个人电脑和远程机器上** | API Key Proxy 把真实密钥留在服务端，只下发短期代理令牌 |
| 📊 **成本、配额、风险和审计需要可见** | Manage 模式提供用量、成本、配额、异常、审计和合规视图 |

**你可以用 Open ACE 做什么：**

- 给团队一个统一入口，管理本地/远程 AI coding 会话、提示词、历史记录和项目上下文
- 通过 Remote Agent 把 Claude Code、Qwen Code、Codex、OpenClaw 等 CLI 跑在团队自己的开发机、测试机或 GPU 机器上
- 加密保存 LLM API Key，通过短期代理令牌给本地和远程会话安全调用模型
- 给管理者一套控制面板，查看 Token、成本、异常、配额、审计、合规报告和 ROI
- 在自己的网络里部署，保留企业数据边界，并逐步接入 SSO、飞书/钉钉和 Kubernetes

## 🔥 近期功能亮点

| 功能 | 为什么重要 |
|------|------------|
| AI Coding Agent 控制面 | 把团队已在使用的多个 AI CLI 纳入统一入口、权限、成本和审计体系 |
| 远程工作区与 Remote Agent | 用户在浏览器里选择远程机器，AI CLI 直接在目标机器运行，无需反复传 SSH 凭据 |
| 多 CLI 适配器 | Claude Code、Qwen Code、Codex、OpenClaw 统一接入，包含会话恢复、权限模式和历史同步 |
| API Key 代理 | API Key 加密存储在服务器，远程 Agent 只拿短期代理令牌，统一配额和用量统计 |
| 终端与 VSCode/code-server 代理 | 支持浏览器终端、远程目录浏览和 code-server/VSCode 访问，适合真实研发工作流 |
| 合规与报表 | 支持审计追踪、配额检查、合规报告生成和 CSV 下载 |
| 开源协作基础 | 已补齐 Roadmap、Security Policy、Issue 模板、Dependabot、CODEOWNERS 和适合新手的 Issue 标签 |

## ✨ 两种模式，双重价值

### 🚀 Work 模式 — 让 AI 成为你的超级助手

> 面向每一位员工，提供流畅的 AI 交互体验

<p align="center">
  <img src="docs/images/work-mode-zh.png" alt="Work 模式截图" width="80%">
</p>

**核心能力：**
- 🤖 **多 AI 工具集成** — Claude Code、Qwen Code、Codex、OpenClaw 一个入口统一使用
- 🖥️ **远程工作区** — 在浏览器里选择远程机器，启动 AI 编码会话、终端和目录浏览
- 💬 **智能会话管理** — 历史记录、会话恢复、上下文记忆和跨工具会话同步
- 📝 **提示词库** — 团队共享优质提示词，最佳实践一键复用
- 🔍 **快速检索** — 跨会话搜索历史对话，知识沉淀不丢失

---

### 📊 Manage 模式 — 让 AI 治理有据可依

> 面向管理者，提供全方位的 AI 使用洞察与管控

<p align="center">
  <img src="docs/images/manage-mode-zh.png" alt="Manage 模式截图" width="80%">
</p>

**核心能力：**
- 📈 **用量可视化** — Token 消耗趋势、成本分析、使用热力图，一目了然
- 🔑 **API Key 治理** — 加密存储 API Key，通过代理令牌调用模型，避免密钥下发到远程机器
- 🚨 **智能告警** — 配额预警、异常检测、超支提醒，风险早知道
- 📋 **合规审计** — 敏感内容检测、对话记录追溯、合规报告生成和 CSV 下载
- 👥 **多租户管理** — 部门隔离、权限控制、资源配额，精细化管理
- 💰 **ROI 分析** — 基于可见、可配置假设的 ROI 规划估算与效率量化

---

## 🏢 为什么选择 Open ACE？

| 特性 | 说明 |
|------|------|
| 🔒 **默认自托管** | 私有化部署，数据和 API Key 留在自己的网络与数据库里 |
| 🎛️ **Agent 控制面** | 统一管理工具、用户、机器、密钥、配额、成本和审计，而不是替代每个 AI CLI |
| 🌐 **多工具工作台** | Claude Code、Qwen Code、Codex、OpenClaw 统一入口、统一历史、统一治理 |
| 🖥️ **远程执行** | Remote Agent 让 AI CLI 在目标机器运行，适合研发服务器、测试环境和 GPU 机器 |
| 📊 **治理可观测** | Token、成本、配额、异常、审计、合规和 ROI 统一分析 |
| 🔌 **企业集成** | 支持 SSO、飞书/钉钉、Kubernetes、反向代理和多租户权限模型 |
| 🆓 **开放协作** | Apache 2.0 协议，Roadmap、贡献指南和 good first issue 已就位 |

---

## 🚀 5 分钟快速开始

### 方式一：一键部署（推荐）

```bash
# 克隆项目
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 构建并启动
docker compose up -d --build

# 访问 http://localhost:19888
```

> 💡 生产环境部署请参考 [部署指南](scripts/install-central/docker-method/README.md)

### 方式二：源码安装

```bash
# 1. 克隆项目
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 2. 安装后端依赖
pip install -r requirements.txt

# 3. 安装前端依赖并构建
cd frontend && npm install && npm run build && cd ..

# 4. 初始化配置和数据库
python3 cli.py config init
alembic upgrade head
python3 scripts/init_db.py

# 5. 启动服务
python3 server.py

# 访问 http://localhost:19888
```

### 默认账号

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | admin | admin123 |

> ⚠️ 默认账号仅用于本地首次启动。生产环境请务必显式设置 `SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`、`UPLOAD_AUTH_KEY`，修改默认密码，并参考 [部署指南](docs/cn/DEPLOYMENT.md) 完成安全配置。

---

## 📖 功能详解

### 📊 数据分析

| 功能 | 描述 |
|------|------|
| 趋势分析 | 按日/周/月查看 Token 使用趋势 |
| 对比分析 | 不同时间段、不同工具的用量对比 |
| 热力图 | 使用高峰时段可视化 |
| 成本分析 | Token 消耗与成本换算 |

### 💬 消息追踪

| 功能 | 描述 |
|------|------|
| 对话历史 | 查看完整对话记录 |
| 消息搜索 | 按关键词、用户、时间筛选 |
| 会话导出 | 导出对话记录用于审计 |
| 时间线视图 | 可视化展示对话流程 |

### 🖥️ 远程工作区

| 功能 | 描述 |
|------|------|
| Remote Agent | 在 Linux/macOS/Windows 远程机器上运行 AI CLI 守护进程 |
| CLI 适配器 | 支持 Claude Code、Qwen Code、Codex、OpenClaw 的启动、会话恢复和权限模式 |
| 浏览器终端 | 通过 WebSocket PTY 访问远程 shell，并支持断线后的屏幕恢复 |
| VSCode/code-server | 代理远程 code-server/VSCode 路径，方便在浏览器中继续开发 |
| API Key 代理 | 真实密钥只保存在服务器，远程会话通过短期代理令牌访问模型 |

### 🔔 告警中心

| 功能 | 描述 |
|------|------|
| 配额告警 | 用量达到阈值自动提醒 |
| 异常检测 | 识别异常使用模式 |
| 邮件通知 | 定期发送用量报告 |
| 飞书推送 | 实时告警推送到飞书群 |

### 👥 用户管理

| 功能 | 描述 |
|------|------|
| 多租户 | 支持多部门/团队隔离 |
| 角色权限 | 管理员/普通用户角色区分 |
| SSO 集成 | 支持企业单点登录 |
| 飞书同步 | 自动同步飞书组织架构 |

### 📋 合规与报表

| 功能 | 描述 |
|------|------|
| 审计日志 | 追踪用户、会话、工具和关键管理操作 |
| 合规检查 | 覆盖数据留存、配额使用、敏感内容和访问控制 |
| 报告生成 | 生成企业合规报告，并支持 JSON/CSV 等格式 |
| 风险建议 | 基于检查结果给出治理建议和后续动作 |

---

## 🛠️ 技术栈

<table>
<tr>
<td width="50%">

### 后端
- **Python 3.9+**
- **Flask** — Web 框架
- **SQLAlchemy** — ORM
- **PostgreSQL / SQLite** — 数据库
- **Alembic** — 数据库迁移

</td>
<td width="50%">

### 前端
- **React 18** — UI 框架
- **TypeScript** — 类型安全
- **Vite** — 构建工具
- **Bootstrap 5** — UI 组件
- **Chart.js** — 数据可视化

</td>
</tr>
</table>

---

## 📁 项目结构

```
open-ace/
├── server.py                 # Web 服务入口
├── cli.py                 # CLI 工具入口
├── app/                   # 后端应用
│   ├── routes/            # API 路由
│   ├── services/          # 业务逻辑
│   ├── modules/           # 工作区、合规等领域模块
│   ├── models/            # 数据模型
│   └── repositories/      # 数据访问
├── frontend/              # 前端应用
│   ├── src/               # 源代码
│   ├── e2e/               # Playwright 端到端测试
│   └── package.json       # 依赖配置
├── remote-agent/          # 远程工作区 Agent 与 CLI 适配器
├── k8s/                   # Kubernetes 部署清单
├── migrations/            # Alembic 数据库迁移
├── schema/                # 数据库 Schema 辅助文件
├── scripts/               # 核心脚本与运维辅助文件
│   ├── fetch_*.py         # 数据收集
│   ├── cron/              # 定时任务脚本
│   ├── systemd/           # systemd service/timer 示例
│   └── shared/            # 共享模块
├── static/                # 运行时静态资源与前端构建产物
├── docs/                  # 文档
└── tests/                 # 测试
```

---

## 📚 文档

`docs/` 目录保存产品文档源代码；对外发布的 Docusaurus 文档站在独立仓库 `open-ace/open-ace-docs` 中构建和部署。

| 文档 | 说明 |
|------|------|
| [架构说明](docs/cn/ARCHITECTURE.md) | 系统架构与核心概念 |
| [部署指南](docs/cn/DEPLOYMENT.md) | 本地与生产环境部署 |
| [开发指南](docs/cn/DEVELOPMENT.md) | 参与开发 |
| [远程工作区](docs/cn/REMOTE-WORKSPACE.md) | 远程机器、Agent、API Key 代理与安全设计 |
| [远程 Agent](docs/cn/REMOTE-AGENT.md) | Agent 安装、CLI 适配器、终端和会话同步 |
| [权限模型](docs/cn/PERMISSION-MODEL.md) | 租户、角色与访问控制 |
| [Kubernetes](docs/cn/KUBERNETES.md) | K8s 单实例参考部署 |
| [飞书配置](docs/cn/FEISHU_CONFIG.md) | 飞书集成配置 |
| [API 文档](docs/cn/API.md) | API 接口说明 |
| [仓库设置](docs/REPOSITORY_SETUP.md) | GitHub topics、labels、分支保护和发布检查清单 |

---

## 🤝 贡献

我们欢迎所有形式的贡献！

- 🐛 发现 Bug？[提交 Issue](https://github.com/open-ace/open-ace/issues)
- 💡 有想法？[参与讨论](https://github.com/open-ace/open-ace/discussions)
- 🗺️ 想了解计划？查看 [Roadmap](ROADMAP.md)
- 🔧 想贡献代码？阅读 [贡献指南](CONTRIBUTING.md)
- 🌱 第一次贡献？从 [`good first issue`](https://github.com/open-ace/open-ace/labels/good%20first%20issue) 或 [`help wanted`](https://github.com/open-ace/open-ace/labels/help%20wanted) 开始

适合参与的方向包括：远程工作区体验、CLI 适配器、部署脚本、文档翻译、前端可用性、测试覆盖和企业集成。

---

## 📄 许可证

本项目采用 [Apache 2.0](LICENSE) 许可证开源。

---

<p align="center">
  <strong>把 AI Coding Agent 接进来，把密钥、成本和风险管起来</strong><br>
  <em>Open ACE — self-hosted workspace and control plane for AI coding agents</em>
</p>

---

<a name="english"></a>

## 🎯 What is This?

**Open ACE** is an open-source **self-hosted workspace and control plane for AI coding agents**: developers can use Claude Code, Qwen Code, Codex, OpenClaw, and similar tools from one browser workspace while running them on the team's own remote machines; administrators can centralize API keys, access control, quotas, cost visibility, audit trails, and compliance.

It is built for teams moving AI coding agents into real engineering workflows, especially organizations that need self-hosted deployment, private-network machines, centralized key proxying, team quotas, and traceable audit records.

| Challenge | Solution |
|-----------|----------|
| 🤖 **Teams use multiple AI coding agents** | Multi-CLI workspace for Claude Code, Qwen Code, Codex, OpenClaw, and more |
| 🖥️ **Agents need to run on internal, staging, or GPU machines** | Remote Agent runs AI CLIs directly on target machines |
| 🔑 **API keys should not spread across laptops and remote boxes** | API Key Proxy keeps real keys on the server and issues short-lived proxy tokens |
| 📊 **Cost, quotas, risk, and audit need visibility** | Manage Mode tracks usage, cost, quotas, anomalies, audit trails, and compliance |

**What you can do with Open ACE:**

- Give teams one place for local/remote AI coding sessions, prompts, history, and project context
- Run Claude Code, Qwen Code, Codex, OpenClaw, and similar CLIs on your own development, staging, or GPU machines through the Remote Agent
- Store LLM API keys centrally and issue short-lived proxy tokens to local and remote sessions
- Give administrators a control plane for tokens, cost, anomalies, quotas, audits, compliance reports, and ROI
- Deploy inside your own network while integrating SSO, Feishu/DingTalk, and Kubernetes over time

## 🔥 Recent Highlights

| Capability | Why it matters |
|------------|----------------|
| AI coding agent control plane | Bring the team's existing AI CLIs under one access, key, quota, cost, and audit model |
| Remote Workspace and Remote Agent | Users choose a remote machine in the browser and run AI CLIs there without repeatedly sharing SSH credentials |
| Multi-CLI adapters | Claude Code, Qwen Code, Codex, and OpenClaw share one workspace with session recovery, permission modes, and history sync |
| API Key proxy | Keys are encrypted on the server; remote agents only receive short-lived proxy tokens with unified quota and usage tracking |
| Terminal and VSCode/code-server proxy | Browser terminal, remote directory browsing, and code-server/VSCode access make the workspace useful for real development loops |
| Compliance and reporting | Audit trails, quota checks, compliance reports, and CSV downloads are available for governance workflows |
| Open-source readiness | Roadmap, Security Policy, issue templates, Dependabot, CODEOWNERS, and beginner-friendly labels are in place |

## ✨ Two Modes, Double Value

### 🚀 Work Mode — Make AI Your Super Assistant

> For every employee, providing a seamless AI interaction experience

<p align="center">
  <img src="docs/images/work-mode-en.png" alt="Work Mode Screenshot" width="80%">
</p>

**Key Capabilities:**
- 🤖 **Multi-AI Integration** — Claude Code, Qwen Code, Codex, and OpenClaw behind one workspace
- 🖥️ **Remote Workspace** — Choose a remote machine in the browser, then start AI coding sessions, terminals, and directory browsing
- 💬 **Smart Session Management** — History, session recovery, context memory, and cross-tool history sync
- 📝 **Prompt Library** — Share best practices across your team with reusable prompts
- 🔍 **Quick Search** — Search across all conversations, knowledge preserved

---

### 📊 Manage Mode — Data-Driven AI Governance

> For administrators, providing comprehensive AI usage insights and control

<p align="center">
  <img src="docs/images/manage-mode-en.png" alt="Manage Mode Screenshot" width="80%">
</p>

**Key Capabilities:**
- 📈 **Usage Visualization** — Token consumption trends, cost analysis, heatmaps at a glance
- 🔑 **API Key Governance** — Encrypt API keys on the server and call models through scoped proxy tokens
- 🚨 **Smart Alerts** — Quota warnings, anomaly detection, overspending alerts — know risks early
- 📋 **Compliance Audit** — Sensitive content detection, conversation trails, compliance reports, and CSV downloads
- 👥 **Multi-tenant Management** — Department isolation, permission control, resource quotas
- 💰 **ROI Analysis** — Configurable ROI planning estimates with transparent assumptions and efficiency metrics

---

## 🏢 Why Open ACE?

| Feature | Description |
|---------|-------------|
| 🔒 **Self-hosted by default** | Keep data and API keys inside your own network and database |
| 🎛️ **Agent control plane** | Manage tools, users, machines, keys, quotas, cost, and audit trails without replacing every AI CLI |
| 🌐 **Multi-tool workspace** | Claude Code, Qwen Code, Codex, and OpenClaw with unified access, history, and governance |
| 🖥️ **Remote execution** | Remote Agent runs AI CLIs on development servers, staging boxes, or GPU machines |
| 📊 **Governance observability** | Analyze tokens, cost, quotas, anomalies, audits, compliance, and ROI together |
| 🔌 **Enterprise integration** | SSO, Feishu/DingTalk, Kubernetes, reverse proxy, and multi-tenant permissions |
| 🆓 **Open collaboration** | Apache 2.0, roadmap, contributor guide, and good first issues are ready |

---

## 🚀 Quick Start in 5 Minutes

### Option 1: One-click Deploy (Recommended)

```bash
# Clone the project
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# Build and start
docker compose up -d --build

# Visit http://localhost:19888 (AI + ace mnemonic port)
```

> 💡 For production deployment, see [Deployment Guide](docs/en/DEPLOYMENT.md)

### Option 2: From Source

```bash
# 1. Clone the project
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 2. Install backend dependencies
pip install -r requirements.txt

# 3. Install frontend dependencies and build
cd frontend && npm install && npm run build && cd ..

# 4. Initialize configuration and database
python3 cli.py config init
alembic upgrade head
python3 scripts/init_db.py

# 5. Start the server
python3 server.py

# Visit http://localhost:19888 (AI + ace mnemonic port)
```

### Default Credentials

| Role | Username | Password |
|------|----------|----------|
| Admin | admin | admin123 |

> ⚠️ The default account is only for the first local startup. For production, explicitly set `SECRET_KEY`, `OPENACE_ENCRYPTION_KEY`, and `UPLOAD_AUTH_KEY`, change the default password, and follow the [Deployment Guide](docs/en/DEPLOYMENT.md).

---

## 📖 Feature Details

### 📊 Analytics

| Feature | Description |
|---------|-------------|
| Trend Analysis | View token usage trends by day/week/month |
| Comparison | Compare usage across time periods and tools |
| Heatmap | Visualize peak usage hours |
| Cost Analysis | Convert token consumption to costs |

### 💬 Message Tracking

| Feature | Description |
|---------|-------------|
| Conversation History | View complete conversation records |
| Message Search | Filter by keyword, user, time |
| Session Export | Export conversations for audit |
| Timeline View | Visualize conversation flow |

### 🖥️ Remote Workspace

| Feature | Description |
|---------|-------------|
| Remote Agent | Run AI CLI daemons on Linux/macOS/Windows remote machines |
| CLI Adapters | Start, resume, and control permission modes for Claude Code, Qwen Code, Codex, and OpenClaw |
| Browser Terminal | Access remote shells through WebSocket PTY with screen recovery after disconnects |
| VSCode/code-server | Proxy remote code-server/VSCode paths so users can continue development in the browser |
| API Key Proxy | Keep real keys on the server and let remote sessions call models through short-lived proxy tokens |

### 🔔 Alert Center

| Feature | Description |
|---------|-------------|
| Quota Alerts | Automatic notifications when thresholds reached |
| Anomaly Detection | Identify unusual usage patterns |
| Email Reports | Periodic usage reports via email |
| Feishu Push | Real-time alerts to Feishu groups |

### 👥 User Management

| Feature | Description |
|---------|-------------|
| Multi-tenant | Department/team isolation |
| Role Permissions | Admin/user role distinction |
| SSO Integration | Enterprise single sign-on support |
| Feishu Sync | Auto-sync Feishu organization structure |

### 📋 Compliance and Reporting

| Feature | Description |
|---------|-------------|
| Audit Logs | Track users, sessions, tools, and key administration actions |
| Compliance Checks | Cover data retention, quota usage, sensitive content, and access control |
| Report Generation | Generate governance reports with JSON/CSV export paths |
| Risk Recommendations | Turn report results into practical follow-up actions |

---

## 🛠️ Tech Stack

<table>
<tr>
<td width="50%">

### Backend
- **Python 3.9+**
- **Flask** — Web Framework
- **SQLAlchemy** — ORM
- **PostgreSQL / SQLite** — Database
- **Alembic** — Migrations

</td>
<td width="50%">

### Frontend
- **React 18** — UI Framework
- **TypeScript** — Type Safety
- **Vite** — Build Tool
- **Bootstrap 5** — UI Components
- **Chart.js** — Visualization

</td>
</tr>
</table>

---

## 📁 Project Structure

```
open-ace/
├── server.py                 # Web server entry
├── cli.py                 # CLI tool entry
├── app/                   # Backend application
│   ├── routes/            # API routes
│   ├── services/          # Business logic
│   ├── modules/           # Domain modules: workspace, compliance, and more
│   ├── models/            # Data models
│   └── repositories/      # Data access
├── frontend/              # Frontend application
│   ├── src/               # Source code
│   ├── e2e/               # Playwright end-to-end tests
│   └── package.json       # Dependencies
├── remote-agent/          # Remote Workspace agent and CLI adapters
├── k8s/                   # Kubernetes manifests
├── migrations/            # Alembic database migrations
├── schema/                # Database schema helpers
├── scripts/               # Core scripts and operational helpers
│   ├── fetch_*.py         # Data collection
│   ├── cron/              # Scheduled task scripts
│   ├── systemd/           # systemd service/timer examples
│   └── shared/            # Shared modules
├── static/                # Runtime static assets and frontend build output
├── docs/                  # Documentation
└── tests/                 # Tests
```

---

## 📚 Documentation

The `docs/` directory is the source of truth for product documentation. The published Docusaurus docs site is built and deployed from the separate `open-ace/open-ace-docs` repository.

| Document | Description |
|----------|-------------|
| [Architecture](docs/en/ARCHITECTURE.md) | System architecture and concepts |
| [Deployment](docs/en/DEPLOYMENT.md) | Local and production deployment |
| [Development](docs/en/DEVELOPMENT.md) | Contributing guide |
| [Remote Workspace](docs/en/REMOTE-WORKSPACE.md) | Remote machines, Agent, API Key proxy, and security design |
| [Remote Agent](docs/en/REMOTE-AGENT.md) | Agent install, CLI adapters, terminal, and session sync |
| [Permission Model](docs/en/PERMISSION-MODEL.md) | Tenants, roles, and access control |
| [Kubernetes](docs/en/KUBERNETES.md) | Single-instance K8s deployment reference |
| [Feishu Config](docs/en/FEISHU_CONFIG.md) | Feishu integration |
| [API Reference](docs/en/API.md) | API documentation |
| [Repository Setup](docs/REPOSITORY_SETUP.md) | GitHub topics, labels, branch protection, and release checklist |

---

## 🤝 Contributing

We welcome all forms of contribution!

- 🐛 Found a bug? [Submit an Issue](https://github.com/open-ace/open-ace/issues)
- 💡 Have an idea? [Join the discussion](https://github.com/open-ace/open-ace/discussions)
- 🗺️ Want to see what's next? Read the [Roadmap](ROADMAP.md)
- 🔧 Want to contribute code? Read the [Contributing Guide](CONTRIBUTING.md)
- 🌱 First contribution? Start with [`good first issue`](https://github.com/open-ace/open-ace/labels/good%20first%20issue) or [`help wanted`](https://github.com/open-ace/open-ace/labels/help%20wanted)

Good places to help include Remote Workspace UX, CLI adapters, deployment scripts, documentation translation, frontend usability, test coverage, and enterprise integrations.

---

## 📄 License

This project is licensed under the [Apache 2.0 License](LICENSE).

---

<p align="center">
  <strong>Bring AI coding agents in. Keep keys, cost, and risk under control.</strong><br>
  <em>Open ACE — self-hosted workspace and control plane for AI coding agents</em>
</p>

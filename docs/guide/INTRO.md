# Open ACE - Self-Hosted Control Plane for AI Coding Agents — Open ACE - 面向 AI Coding Agent 的自托管控制面

[English](#english) | [中文](#中文)

---

## English

> **ACE** = **AI Computing Explorer**

This guide explains what Open ACE is, which teams it is built for, and which parts of the product matter most when you evaluate it.

---

## Project Positioning

Open ACE is an open-source, self-hosted control plane for AI coding agents.

It brings three layers together:

1. A browser workspace where developers run AI coding sessions
2. A remote execution layer for running agents on internal machines
3. A governance plane for keys, quotas, audit, compliance, and cost visibility

The project is aimed at teams that want more than a single-chat assistant. It is for organizations moving AI coding into real engineering workflows, where issues, repositories, credentials, remote machines, and reviewability all matter.

## What Problems It Solves

| Team problem | What Open ACE provides |
|--------------|------------------------|
| Multiple AI coding tools are used in parallel | One workspace and control plane for Claude Code, Qwen Code, Codex, OpenClaw, and ZCode |
| Agents need to run on internal, staging, or GPU machines | Remote Agent executes CLI tools on controlled Linux, macOS, and Windows machines |
| API keys should not be copied across laptops and remote boxes | API Key Proxy keeps real keys on the server and issues short-lived, revocable proxy tokens |
| AI work needs to be inspectable after the fact | Autonomous workflow timelines, milestone summaries, final code changes, and run provenance keep execution visible |
| Platform and security teams need governance | Quotas, anomalies, audit logs, compliance checks, multi-tenant controls, and SSO are built into the same system |

## Core Product Layers

### 1. Autonomous Development Workflows

Open ACE can turn GitHub issues into structured AI development runs.

Core workflow capabilities include:

- Issue-driven runs with preparation, planning, development, review, reporting, and merge phases
- Batch issue handling for operating on more than one GitHub issue from a single workflow
- Pause, resume, retry, cancel, and auto-merge controls
- Fork-from-here behavior at the milestone level
- Final code-change summaries and milestone-level diff inspection
- Timeline views that expose outputs, status changes, review results, and token usage

This makes Open ACE more than a browser wrapper around an AI CLI. It becomes an operational surface for autonomous or semi-autonomous repository work.

### 2. Workspace and Remote Execution

Open ACE provides a browser-based workspace for both local and remote AI coding sessions.

#### Work Mode

Work Mode is the day-to-day surface for developers:

- Browser workspace for AI coding sessions
- Session history and recovery
- Prompt library and reuse
- Multiple workspace tabs
- Conversation and execution visibility

#### Remote Agent

Remote Agent lets teams run AI coding tools on controlled machines instead of pushing all work onto a local laptop.

Key capabilities include:

- Token-based machine registration and remote identity management
- CLI adapters with session resume and permission-mode handling
- Browser terminal access
- Remote file and project workflows
- code-server / VSCode proxy access for in-browser development
- Run Timeline APIs for persisted remote execution provenance

### 3. Governance and Administration

Open ACE also acts as a management plane for AI engineering usage.

#### Manage Mode

Manage Mode is for platform, IT, and governance teams:

- Usage dashboards and trend analysis
- Quota management and quota alerts
- Anomaly detection
- Audit Center and Security Center
- Compliance and retention workflows
- Tenant, user, and permission management
- ROI views with visible planning assumptions

#### Security and Access

- API Key Proxy with encrypted server-side key storage
- Short-lived, revocable proxy tokens for local and remote sessions
- OIDC / OAuth2 / SAML 2.0 SSO support
- Multi-tenant organization model
- Content filtering and audit logging
- Feishu and DingTalk integration paths for org sync and alerts

## Supported AI Coding Tools

| Tool | Support in Open ACE | Notes |
|------|----------------------|-------|
| Claude Code | Yes | Workspace sessions, remote execution, permission modes, session recovery |
| Qwen Code | Yes | Workspace sessions, remote execution, session recovery, usage integration |
| Codex | Yes | Workspace sessions and remote execution through CLI adapters |
| OpenClaw | Yes | Workspace support plus session and message synchronization |
| ZCode | Yes | Remote Agent support, session sync, and persistent `app-server` execution mode |

## Why Teams Evaluate It

Open ACE is especially relevant when a team needs some combination of the following:

- Self-hosted deployment inside a private network
- Shared access to multiple AI coding agents
- Remote execution on development, staging, or GPU machines
- Centralized LLM credential handling
- Explainable autonomous runs instead of opaque background automation
- Governance visibility across token usage, cost, quotas, anomalies, and audits

## Deployment and Integration Story

Open ACE supports several deployment paths:

- Docker Compose quick start
- Source installation for development
- Package-based install workflows
- Kubernetes reference deployment
- Reverse proxy deployment with Nginx

The stack uses:

- **Backend**: Python 3.10+, Flask, SQLAlchemy, Alembic
- **Frontend**: React 18, TypeScript, Vite, Bootstrap 5
- **Database**: SQLite and PostgreSQL
- **Remote execution**: Remote Agent with CLI adapters, terminal relay, and session sync

## Who Should Read What Next

Start with the guide that matches your evaluation path:

- [Deployment Guide](DEPLOYMENT.md) - local and production setup
- [Remote Agent Guide](REMOTE_AGENT.md) - remote execution model, adapters, and machine registration
- [Remote Workspace](REMOTE_WORKSPACE.md) - browser workspace and server-side remote-session design
- [Permission Model](../dev/PERMISSION_MODEL.md) - roles, admin boundaries, and access model
- [Architecture](../dev/ARCHITECTURE.md) - backend, frontend, and runtime structure

## Bottom Line

Open ACE is not just a usage dashboard and not just a browser shell around an AI CLI.

It is a self-hosted control plane for teams that want to:

- run multiple AI coding agents in one place,
- execute them on controlled machines,
- automate issue-driven engineering work,
- and keep the whole process observable, governable, and reviewable.

---

## 中文

> **ACE** = **AI Computing Explorer**（AI 计算探索器）

本文档介绍 Open ACE 的产品定位、适用团队，以及评估时最值得关注的能力。

---

## 项目定位

Open ACE 是一个开源、可自托管的 AI Coding Agent 控制面。

它把三层能力放在同一个系统里：

1. 开发者日常使用的浏览器工作台
2. 面向内网和远程机器的执行层
3. 面向平台与治理团队的密钥、配额、审计、合规和成本控制面

它并不是单纯的聊天助手界面，也不只是一个 Token 看板。它更适合那些已经准备把 AI Coding Agent 放进真实研发流程的团队：代码仓库、GitHub issue、远程机器、凭据、安全边界、审核与追踪都需要被认真处理。

## 它解决什么问题

| 团队问题 | Open ACE 的做法 |
|---------|----------------|
| 团队同时使用多个 AI 编码工具 | 用一个工作台和控制面统一 Claude Code、Qwen Code、Codex、OpenClaw、ZCode |
| Agent 需要跑在内网、测试机或 GPU 机器上 | Remote Agent 在受控的 Linux、macOS、Windows 机器上执行 CLI 工具 |
| API Key 不该散落在个人电脑和远程机器上 | API Key Proxy 把真实密钥留在服务端，只下发短生命周期、可回收的代理令牌 |
| AI 工作完成后难以复盘 | 自主开发工作流时间线、里程碑摘要、最终代码变更和运行溯源让执行过程可见 |
| 平台和安全团队需要治理能力 | 配额、异常、审计、合规、多租户和 SSO 都在同一套系统里 |

## 核心产品层

### 1. AI 自主开发工作流

Open ACE 可以把 GitHub issue 变成结构化的 AI 研发流程。

核心能力包括：

- 以 issue 为起点的 preparation、planning、development、review、report、merge 等阶段
- 一个工作流批量处理多个 GitHub issue
- 暂停、恢复、重试、取消、自动合并
- 在里程碑节点执行 Fork From Here
- 最终代码变更摘要和里程碑级 diff 查看
- 时间线界面展示输出、状态变化、审查结果和 Token 使用情况

这意味着 Open ACE 不只是把 AI CLI 放进浏览器，而是把自主或半自主研发流程做成了一个可以运营的产品面。

### 2. 工作台与远程执行

Open ACE 提供浏览器工作台，既支持本地会话，也支持远程 AI 编码会话。

#### Work 模式

Work 模式面向开发者日常使用：

- 浏览器化 AI 编码工作台
- 会话历史与恢复
- 提示词库与复用
- 多工作台标签页
- 对会话与执行过程的可视化

#### Remote Agent

Remote Agent 让团队把 AI Coding Tool 跑在受控机器上，而不是只能依赖本地电脑。

关键能力包括：

- 基于 token 的机器注册与远程身份管理
- 处理权限模式和会话恢复的 CLI 适配器
- 浏览器终端访问
- 面向远程项目目录的开发流程
- code-server / VSCode 代理，支持继续在浏览器内开发
- Run Timeline API，用于持久化远程执行溯源

### 3. 治理与管理

Open ACE 还是一套 AI 工程治理控制面。

#### Manage 模式

Manage 模式面向平台、IT 和治理团队：

- 用量仪表板与趋势分析
- 配额管理与配额告警
- 异常检测
- 审计中心与安全中心
- 合规与数据保留流程
- 租户、用户和权限管理
- 带可见假设的 ROI 视图

#### 安全与访问控制

- 通过 API Key Proxy 在服务端加密保存密钥
- 给本地和远程会话发放短生命周期、可回收的代理令牌
- 支持 OIDC / OAuth2 / SAML 2.0 单点登录
- 多租户组织模型
- 内容过滤与审计日志
- 飞书 / 钉钉组织同步与告警集成路径

## 已支持的 AI Coding Tool

| 工具 | Open ACE 支持情况 | 说明 |
|------|-------------------|------|
| Claude Code | 已支持 | 工作台会话、远程执行、权限模式、会话恢复 |
| Qwen Code | 已支持 | 工作台会话、远程执行、会话恢复、用量集成 |
| Codex | 已支持 | 通过 CLI 适配器接入工作台与远程执行 |
| OpenClaw | 已支持 | 支持工作台接入以及会话、消息同步 |
| ZCode | 已支持 | 支持 Remote Agent、会话同步，以及持久化 `app-server` 执行模式 |

## 为什么值得评估

如果你的团队需要下面这些能力中的一部分或大部分，Open ACE 就值得认真看：

- 在私有网络里自托管部署
- 统一接入多个 AI Coding Agent
- 在开发机、测试机或 GPU 机器上远程执行
- 集中管理 LLM 凭据
- 让自主工作流可解释、可回放，而不是黑盒后台任务
- 把 Token、成本、配额、异常和审计放在同一张治理视图里

## 部署与集成能力

Open ACE 支持多种部署路径：

- Docker Compose 快速启动
- 面向开发环境的源码安装
- 包安装/升级路径
- Kubernetes 参考部署
- Nginx 反向代理部署

技术栈包括：

- **后端**：Python 3.10+、Flask、SQLAlchemy、Alembic
- **前端**：React 18、TypeScript、Vite、Bootstrap 5
- **数据库**：SQLite、PostgreSQL
- **远程执行**：Remote Agent、CLI adapters、terminal relay、session sync

## 接下来先读什么

按你的评估路径继续读：

- [部署指南](DEPLOYMENT.md) - 本地与生产部署
- [Remote Agent 指南](REMOTE_AGENT.md) - 远程执行模型、适配器、机器注册
- [Remote Workspace](REMOTE_WORKSPACE.md) - 浏览器工作台与服务端远程会话设计
- [权限模型](../dev/PERMISSION_MODEL.md) - 角色、管理边界与访问控制
- [系统架构](../dev/ARCHITECTURE.md) - 后端、前端与运行时结构

## 一句话总结

Open ACE 既不是单纯的用量看板，也不是把 AI CLI 套壳进浏览器。

它是一套给团队使用的自托管控制面，用来：

- 在一个地方运行多个 AI Coding Agent，
- 把它们放到受控机器上执行，
- 自动化 issue 驱动的研发工作，
- 并让整个过程可见、可管、可复盘。

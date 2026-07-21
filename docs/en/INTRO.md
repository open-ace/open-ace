# Open ACE - Self-Hosted Control Plane for AI Coding Agents

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

- [Deployment Guide](./DEPLOYMENT.md) - local and production setup
- [Remote Agent Guide](./REMOTE-AGENT.md) - remote execution model, adapters, and machine registration
- [Remote Workspace](./REMOTE-WORKSPACE.md) - browser workspace and server-side remote-session design
- [Permission Model](./PERMISSION-MODEL.md) - roles, admin boundaries, and access model
- [Architecture](./ARCHITECTURE.md) - backend, frontend, and runtime structure

## Bottom Line

Open ACE is not just a usage dashboard and not just a browser shell around an AI CLI.

It is a self-hosted control plane for teams that want to:

- run multiple AI coding agents in one place,
- execute them on controlled machines,
- automate issue-driven engineering work,
- and keep the whole process observable, governable, and reviewable.

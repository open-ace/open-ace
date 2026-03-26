# Open ACE - Enterprise AI Work Platform

> **ACE** = **AI Computing Explorer**

This document provides a quick overview of Open ACE's positioning, core features, and how to get started.

---

## Project Positioning

Open ACE is an **enterprise-grade AI work platform** designed to help organizations efficiently track, analyze, manage, and govern AI tool usage. It's not just a token tracker—it's a comprehensive platform that bridges the gap between technical AI usage data and business decision-making.

### Target Users

| User Type | Primary Needs | Platform Features |
|-----------|--------------|-------------------|
| **Individual Developers** | Track personal usage, understand costs | Work mode dashboard, session management, prompt library |
| **Team Leads** | Monitor team usage, optimize costs | Analytics, ROI analysis, quota management |
| **IT Administrators** | User management, security, compliance | User/tenant management, SSO, audit logs, compliance controls |
| **Executives** | Cost oversight, ROI measurement, strategic decisions | Usage analytics, trend analysis, ROI reports |

---

## Core Capabilities

### 1. Multi-Tool Token Tracking

Supports major AI tools with automatic log parsing:

| Tool | Data Collected |
|------|----------------|
| **Claude** | Token usage, requests, models, input/output breakdown |
| **Qwen** | Token usage, requests, cache usage, models |
| **OpenClaw** | Agent session data, tool calls, conversation history |

**Key Features:**
- Automatic data collection via scheduled scripts
- Per-tool, per-host, per-user breakdown
- Input/output token tracking for cost optimization
- Request counting for API quota management

### 2. Dual-Mode Interface

Open ACE provides two distinct interfaces based on user role:

#### Work Mode (`/work/*`) - For All Users
- **Workspace**: Interactive AI tool interface
- **Sessions**: View and manage conversation history
- **Prompts**: Save, organize, and reuse prompt templates
- **Dashboard**: Personal usage overview

#### Manage Mode (`/manage/*`) - For Administrators
- **Dashboard**: Team/organization-wide statistics
- **Analysis**: Trend analysis, anomaly detection, ROI calculations
- **Messages**: Advanced filtering and search
- **User Management**: Create, edit, delete users
- **Tenant Management**: Multi-tenant organization structure
- **Audit Center**: Comprehensive audit logging and analysis
- **Security Center**: Content filtering, access control
- **Compliance**: Data retention, reporting

### 3. Advanced Analytics

#### Usage Analytics
- Daily/weekly/monthly usage trends
- Heatmaps showing usage patterns
- Tool comparison charts
- Model usage breakdown

#### Anomaly Detection
- Unusual usage patterns identification
- Failed request rate monitoring
- Off-hours activity alerts
- Automated anomaly reporting

#### ROI Analysis
- Cost per conversation
- Token efficiency metrics
- Tool ROI calculation
- Budget vs actual spending

### 4. Enterprise Governance

#### Quota Management
- Daily/monthly token quotas per user
- Daily/monthly request quotas
- Alert thresholds (80% warning, 95% critical)
- Real-time quota status monitoring

#### User & Tenant Management
- Role-based access control (Admin, Manager, User)
- Multi-tenant organization support
- Team collaboration features
- Permission granularization

#### Security & Compliance
- SSO/SAML integration (OIDC, OAuth2)
- Comprehensive audit logging
- Content filtering
- Data retention policies
- GDPR-ready data export

### 5. Workspace & Tool Integration

#### Unified Tool Connector
- **Tool Health Monitoring**: Real-time status of all AI tools
- **Tool Registration**: Dynamic tool discovery and registration
- **Capability Detection**: Automatic detection of tool features (streaming, vision, tools)
- **Model Mapping**: Support for multiple models per tool

#### State Synchronization
- **Session Persistence**: Save and resume conversations
- **State Recovery**: Automatically restore session state on reconnect
- **Cross-Device Sync**: Continue work across different devices
- **Tab Management**: Multiple conversation tabs within workspace

#### Workspace Configuration
The workspace can be configured to connect to various AI tool services:

```json
{
  "workspace": {
    "enabled": true,
    "url": "http://localhost:3000",
    "tool_urls": {
      "openclaw": "http://localhost:3001",
      "claude": "http://localhost:3002",
      "qwen": "http://localhost:3003"
    }
  }
}
```

#### Prompt Library
- **Template Categories**: General, Coding, Writing, Analysis, Translation, Summarization
- **Variable Support**: Template variables for dynamic content
- **Tagging System**: Organize prompts with custom tags
- **Usage Tracking**: See which prompts are used most
- **Public/Private Sharing**: Share within team or keep private
- **Featured Prompts**: Highlight recommended templates

#### Collaboration Features
- **Session Sharing**: Share conversations with team members
- **Prompt Library**: Centralized prompt repository
- **Team Workspaces**: Shared work environments
- **Collaborative Annotations**: Add notes to conversations

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Data Collection Layer                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ fetch_claude│  │ fetch_qwen  │  │fetch_openclaw│         │
│  │   (logs)    │  │   (logs)    │  │   (logs)    │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                 │
│         └────────────────┼────────────────┘                 │
│                          ▼                                  │
│                  ┌─────────────────┐                        │
│                  │   Database      │                        │
│                  │ (SQLite/PostgreSQL)                     │
│                  └─────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Routes    │  │  Services   │  │  Modules    │         │
│  │  (API)      │  │ (Business)  │  │ (Core)      │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                 │
│         └────────────────┼────────────────┘                 │
│                          ▼                                  │
│                  ┌─────────────────┐                        │
│                  │   Flask App     │                        │
│                  └─────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      Presentation Layer                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   Web UI    │  │    CLI      │  │   API       │         │
│  │ (React)     │  │  (Python)   │  │ (REST)      │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.9+, Flask |
| **Frontend** | React 18, TypeScript, Bootstrap 5, Chart.js |
| **Database** | SQLite (dev), PostgreSQL (production) |
| **Build** | Vite, TypeScript |
| **Testing** | pytest, Playwright, Vitest |
| **Auth** | Session-based, OIDC, OAuth2 |

---

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/open-ace.git
cd open-ace

# Install Python dependencies
pip install -r requirements.txt

# Initialize configuration
python3 cli.py config init
```

### Configuration

Edit `~/.open-ace/config.json`:

```json
{
  "host_name": "my-server",
  "tools": {
    "claude": { "enabled": true, "log_path": "~/.claude/projects" },
    "qwen": { "enabled": true, "log_path": "~/.qwen/projects" },
    "openclaw": { "enabled": true, "log_path": "~/.openclaw/agents" }
  },
  "email": {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "sender": "noreply@example.com"
  }
}
```

### Start the Server

```bash
# Start web server
python3 web.py

# Visit http://localhost:5001
# Default login: admin / admin123
```

### Collect Data

```bash
# Collect from all tools
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py

# Or set up cron for automatic collection
crontab -e
# Add: 30 0 * * * cd /path/to/open-ace && python3 scripts/fetch_*.py
```

---

## CLI Commands

```bash
# View usage statistics
python3 cli.py today        # Today's usage
python3 cli.py today --tool qwen   # Today's usage for specific tool
python3 cli.py today --host myserver  # Usage from specific host

python3 cli.py top          # Last 7 days usage
python3 cli.py top --days 14  # Last 14 days
python3 cli.py summary      # Total summary

# Generate reports
python3 cli.py report       # Generate email report
python3 cli.py report --email user@example.com  # Email to specific address
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/summary` | Usage summary |
| GET | `/api/today` | Today's usage |
| GET | `/api/messages` | Messages with filters |
| GET | `/api/analysis/trend` | Usage trends |
| GET | `/api/analysis/anomaly` | Anomaly detection |
| GET | `/api/analysis/roi` | ROI analysis |
| GET | `/api/users` | User list (admin) |
| POST | `/api/users` | Create user (admin) |
| GET | `/api/tenants` | Tenant list (admin) |
| GET | `/api/sessions` | Session list |
| GET | `/api/prompts` | Prompt library |

---

## Deployment Options

### Single Machine (Personal/Small Team)

```bash
# All components on one machine
python3 web.py          # Web server
python3 cli.py config init  # Configuration
crontab -e              # Scheduled data collection
```

### Docker (Production)

```bash
# Deploy with Docker Compose
docker compose up -d

# Manage services
docker compose ps
docker compose logs -f
docker compose restart
```

### Central Server + Remote Collectors (Enterprise)

```
┌───────────────────────┐       ┌───────────────────────┐
│   Central Server      │       │    Remote Machine     │
│  ┌─────────────────┐  │       │  ┌─────────────────┐  │
│  │   web.py        │  │       │  │ fetch_openclaw  │  │
│  │   (Dashboard)   │  │       │  │                 │  │
│  └────────┬────────┘  │       │  └────────┬────────┘  │
│           │           │       │           │           │
│           ▼           │       │           ▼           │
│  ┌─────────────────┐  │       │  ┌─────────────────┐  │
│  │   PostgreSQL    │◄─┼───────┼──│   SQLite        │  │
│  │   (Central)     │  │Upload │  │   (Remote)      │  │
│  └─────────────────┘  │       │  └─────────────────┘  │
└───────────────────────┘       └───────────────────────┘
```

---

## Key Concepts

| Concept | Description | Example |
|---------|-------------|---------|
| **Request** | Single API call to LLM | User message → 1 request |
| **Message** | Individual message by role | user, assistant, toolResult |
| **Conversation** | One round of dialogue | User sends → AI responds |
| **Session** | Tool-level session | qwen code process lifetime |

**Relationship**: Session → Conversations → Requests + Messages

---

## Quick Reference

### Default Login Credentials
```
Username: admin
Password: admin123
```
**Important**: Change immediately after first login!

### Default Ports
| Service | Port | URL |
|---------|------|-----|
| Web Server | 5001 | http://localhost:5001 |
| Workspace (if enabled) | 3000 | http://localhost:3000 |

### Configuration Location
- **Config File**: `~/.open-ace/config.json`
- **Database**: `~/.open-ace/usage.db` (SQLite) or PostgreSQL
- **Logs**: `~/logs/open-ace/`

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    User Actions                              │
│  • Send message to AI tool                                  │
│  • Use prompt template                                      │
│  • Share session                                            │
│  • Query usage data                                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  AI Tool Logs                                │
│  • Claude: ~/.claude/projects/*.json                        │
│  • Qwen: ~/.qwen/projects/*.json                            │
│  • OpenClaw: ~/.openclaw/agents/*.json                      │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Data Collection (Scheduled)                     │
│  • fetch_claude.py                                          │
│  • fetch_qwen.py                                            │
│  • fetch_openclaw.py                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Database Layer                              │
│  • daily_usage: Aggregated token statistics                 │
│  • daily_messages: Individual message records               │
│  • sessions: Conversation sessions                          │
│  • prompts: Prompt templates                                │
│  • users: User accounts & quotas                            │
│  • audit_logs: System activity logs                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Application Layer                           │
│  • API Routes: REST endpoints                               │
│  • Services: Business logic                                 │
│  • Modules: Core functionality                              │
│  • Repositories: Data access                                │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                  Presentation Layer                          │
│  • Web UI: React dashboard                                  │
│  • CLI: Command-line queries                                │
│  • Email: Automated reports                                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### Core Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `daily_usage` | Token usage statistics | date, tool_name, host_name, tokens_used, input_tokens, output_tokens |
| `daily_messages` | Individual messages | message_id, role, content, tokens_used, sender_id, conversation_id |
| `sessions` | Conversation sessions | session_id, session_type, status, created_at, tool_name |
| `prompts` | Prompt templates | name, category, content, variables, is_public, use_count |
| `users` | User accounts | username, email, role, is_active, daily_token_quota |
| `audit_logs` | System activity | action, user_id, timestamp, details |
| `quotas` | Quota tracking | user_id, period, tokens_used, requests_made, quota_limit |
| `teams` | Team collaboration | team_id, name, owner_id, members |

---

## Feature Status

### ✅ Implemented

- Multi-tool token tracking (Claude, Qwen, OpenClaw)
- Web dashboard with interactive visualizations
- CLI tool for quick queries
- Email report generation
- User authentication & role-based access
- Quota management & alerts
- SSO/SAML integration (OIDC, OAuth2)
- Audit logging & compliance features
- Tenant management
- Prompt library & session management
- Analytics & ROI analysis
- Anomaly detection
- Multi-language support (English, Chinese)

### 🔄 In Progress

- Advanced collaboration features
- Real-time notifications
- Mobile responsive improvements

### 📋 Planned

- Kubernetes deployment
- Advanced workflow automation
- Custom reporting builder
- AI-powered cost optimization suggestions

---

## Documentation

| Document | Purpose |
|----------|---------|
| [INTRO.md](./INTRO.md) | **This file** - Quick overview & getting started |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | System architecture & data models |
| [CONCEPTS.md](./CONCEPTS.md) | Core concepts & terminology |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | Deployment guides (local, Docker, enterprise) |
| [DEVELOPMENT.md](./DEVELOPMENT.md) | Development setup & contribution guide |
| [FEISHU_CONFIG.md](./FEISHU_CONFIG.md) | Feishu integration configuration |

---

## Summary

Open ACE is a comprehensive enterprise AI work platform that provides:

| Category | Features |
|----------|----------|
| **Tracking** | Multi-tool token usage, request counting, input/output breakdown |
| **Analytics** | Trends, anomalies, ROI, heatmaps, comparisons |
| **Management** | Users, tenants, quotas, roles, permissions |
| **Governance** | Audit logs, security, compliance, retention |
| **Collaboration** | Session sharing, prompt library, team workspaces |
| **Integration** | SSO/SAML, tool connector, state sync |

**For individual developers**: Track personal usage and optimize costs
**For teams**: Monitor usage, share prompts, collaborate on sessions
**For enterprises**: Full governance, security, compliance, and multi-tenant support

---

## Getting Help

1. **Documentation**: Check the docs directory for detailed guides
2. **Issues**: Report bugs or request features on GitHub
3. **Contributing**: See CONTRIBUTING.md for development guidelines
4. **License**: Apache 2.0 - free for commercial use

---

## Why Open ACE?

| Feature | Open ACE | Custom Solution |
|---------|----------|-----------------|
| **Setup Time** | 15 minutes | 2-4 weeks |
| **Multi-Tool** | Built-in | Custom coding |
| **Analytics** | Out-of-box | From scratch |
| **Governance** | Enterprise-ready | Complex to build |
| **Maintenance** | Active community | Full team required |
| **Cost** | Free (Apache 2.0) | High TCO |

---

**Ready to get started?** → [Deployment Guide](./DEPLOYMENT.md)

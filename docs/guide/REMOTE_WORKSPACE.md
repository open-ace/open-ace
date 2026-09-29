# Remote Workspace — Remote Workspace 远程工作区

[English](#english) | [中文](#中文)

---

## English

> Lets users select a remote machine in the browser and start an AI coding session — the AI CLI runs directly on the remote machine, with no SSH and no repeated setup.

> This page documents the feature from the **server perspective**: architecture, admin setup, APIs, and security. Agent-side operations (installer TLS options in depth, start/stop scripts, `config.json` keys, the `openace` CLI, terminal server details) are documented in [REMOTE_AGENT.md](REMOTE_AGENT.md), which links back here.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Deployment Checklist](#deployment-checklist)
- [Quick Start](#quick-start)
- [Server-Side Configuration](#server-side-configuration)
- [Remote Agent Installation](#remote-agent-installation)
- [Managing Remote Machines](#managing-remote-machines)
- [Management UI](#management-ui)
- [User Guide](#user-guide)
- [API Reference](#api-reference)
- [Security Design](#security-design)
- [Supported CLI Tools](#supported-cli-tools)
- [Environment Variable Reference](#environment-variable-reference)
- [Troubleshooting](#troubleshooting)

---

## Overview

### What Problem Does It Solve

By default, an Open ACE workspace can only run on the server itself. When users need to operate a remote machine (e.g. a dev/test server), they have to instruct the AI in each session to connect via SSH, provide credentials repeatedly, and frequently run into SSH failures.

### How It Solves It

The remote workspace feature lets users pick a remote machine when creating a session. The AI CLI runs directly on the remote machine's Agent and reaches the LLM API through the server-side proxy. API keys never leave the server.

### Core Features

| Feature | Description |
|---------|-------------|
| Multi-CLI support | Qwen Code, Claude Code, OpenClaw, etc. |
| API key proxy | Keys are encrypted on the server; the remote Agent only gets a short-lived, revocable proxy token |
| One-line install | `curl ... \| bash` deploys the remote machine |
| Unified quota | Local and remote sessions share the same quota system |
| Auto-reconnect | Agent reconnects with exponential backoff after disconnection |

---

## Architecture

```
[Browser UI] <--HTTP/WS--> [Open ACE Server] <--HTTP Polling--> [Remote Agent]
                                  |                                |
                           [API Key encrypted store]         [qwen-code-cli]
                           [Quota manager]                   [claude-code]
                           [Session manager]                 [openclaw]
```

### Message Flow

1. User types a message in the browser → `POST /api/remote/sessions/{id}/chat`
2. The server queues the command and waits for the Agent to pick it up via HTTP polling
3. The Agent feeds the message to the CLI subprocess
4. The CLI needs an LLM call → the request goes to `POST /api/remote/llm-proxy`
5. The server validates the quota, injects the real API key, and forwards to the LLM provider
6. The LLM streams back: provider → server → Agent → server → browser
7. The server records token usage

### Communication

The Agent uses a single transport to talk to the server:

| Mode | Status | Use Case | Notes |
|------|--------|----------|-------|
| HTTP polling | Implemented; the only transport | All scenarios | Agent POSTs actively to `/api/remote/agent/message`; server returns pending commands; broadly compatible |
| WebSocket | Removed | - | The legacy `GET /api/remote/agent/ws` endpoint is deprecated and now answers `410 Gone`, directing clients to HTTP polling |

There is no WebSocket-first fallback logic anymore: current agents use HTTP polling only, and no server-side WebSocket worker (gevent or otherwise) is needed for command transport.

---

## Deployment Topology and Runtime State

Remote Workspace is safe to run behind the Kubernetes reference deployment. The shareable control-plane state is persisted, while live socket bridges remain process-local:

- HTTP-polling command queues, command responses, session-to-machine bindings, session output replay, machines, sessions, messages, quotas, and audit entries are persisted.
- Remote session control APIs such as send, pause, resume, stop, abort, model update, and permission response can be served by any web pod; the next agent poll claims the persisted command.
- SSE reconnects can replay persisted session output after a browser disconnect or web-pod restart.
- Live terminal relay WebSocket objects are still process-local. An active browser-to-agent terminal bridge should use sticky routing or reconnect after failover.

Tenant isolation covers users, projects, workspace sessions and messages, daily usage aggregates, audit logs, remote machines, session ownership, machine permissions, and quotas. System administrators intentionally have global operational visibility for support and incident response.

---

## Deployment Checklist

From zero to working, complete these steps in order:

### Server Side (Open ACE Server)

```bash
# 1. Confirm the codebase has the remote workspace module
ls app/modules/workspace/api_key_proxy.py \
   app/modules/workspace/remote_agent_manager.py \
   app/modules/workspace/remote_session_manager.py \
   app/routes/remote.py

# 2. Run database migrations (creates remote_machines, machine_assignments, api_key_store)
cd /path/to/open-ace
alembic upgrade head

# 3. Set the encryption key (strongly recommended for production)
export OPENACE_ENCRYPTION_KEY="<your-random-32byte-key>"
# Or generate with: openssl rand -hex 32

# 4. Restart the service
sudo systemctl restart open-ace  # or your usual startup command

# 5. Verify the API is reachable
curl -s http://localhost:19888/api/remote/agent/install.sh | head -5
# Should print the install script
```

### Administrator Actions

```bash
# 1. Admin login
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. Store an LLM API key (required for remote sessions)
curl -b cookies.txt -X POST http://<server>:19888/api/remote/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key_name": "production",
    "api_key": "sk-xxx...",
    "base_url": "https://api.openai.com/v1"
  }'

# 3. Generate a registration token
curl -b cookies.txt -X POST http://<server>:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
# → {"registration_token": "abc123..."}
```

### Remote Machine

```bash
# On the remote machine, run the one-line installer (replace <token> with the registration token from the previous step)
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <token>

# If curl returns 404, the server is missing the install-script route — use manual installation (below)
```

### Assign Users

```bash
# Get the machine_id (from the install output or the admin UI)
curl -b cookies.txt http://<server>:19888/api/remote/machines

# Assign a user (get user_id from the user management page)
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

After these steps, users can select the remote machine in the browser workspace and create a session.

---

## Quick Start

### Prerequisites

- Open ACE server deployed and running
- The remote machine can reach the server's HTTP port
- Python 3.8+ installed on the remote machine
- Node.js installed on the remote machine (for installing CLI tools)

### Three Steps

**Step 1: Admin generates a registration token**

In the Open ACE admin UI (Manage Mode → Remote Workspace → Remote Machines → Generate Registration Token), or via API:

```bash
# Admin login to get session_token
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# Generate a registration token
curl -b cookies.txt -X POST http://<server>:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
```

Response:
```json
{"registration_token": "a1b2c3d4e5f6..."}
```

**Step 2: Install the Agent on the remote machine**

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <registration-token>
```

> **Note**: If this command returns 404, the server has not yet deployed the install-script route. Confirm the server code is up to date and restarted. See [Manual Installation](#manual-installation) for a workaround.

The installer will automatically:
1. Download the Agent files from the server to `~/.open-ace-agent/`
2. Install Python dependencies (websocket-client, requests)
3. Install CLI tools (default qwen-code-cli; claude-code optional)
4. Generate a machine_id and register with the server
5. Install as a system service (Linux: systemd; macOS: launchd)

**Step 3: Assign a user**

Admin does this in the UI (Remote Machine detail modal → Assign Users), or via API:

```bash
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

The user can now see the machine in the browser and create remote sessions.

---

## Server-Side Configuration

### Database Migration

The Remote Workspace tables are part of the baseline schema (see `schema/schema-postgres.sql` and `DATABASE_SCHEMA.md`):

| Table | Purpose |
|-------|---------|
| `remote_machines` | Registered remote machines |
| `machine_assignments` | Machine-to-user assignments |
| `api_key_store` | Encrypted LLM API keys |

It also adds two columns to the `agent_sessions` table:
- `workspace_type` — `local` or `remote`
- `remote_machine_id` — the associated remote machine ID

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `OPENACE_ENCRYPTION_KEY` | Yes (production) | Dedicated encryption key for API keys and SMTP passwords | Development-only fallback outside production |
| `SECRET_KEY` | Yes | Flask session key | `dev-secret-key` (development only) |

### API Key Management

In the admin UI (Manage Mode → Remote Workspace → API Keys → Add API Key), or via API:

```bash
# Store an OpenAI API key
curl -b cookies.txt -X POST http://<server>:19888/api/remote/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key_name": "production",
    "api_key": "sk-xxx...",
    "base_url": "https://api.openai.com/v1"
  }'
```

Supported providers:
- `openai` — OpenAI / Qwen and other OpenAI-compatible APIs
- `anthropic` — Anthropic / Claude API
- `google` — Google Gemini API

### Admin-Page API Keys vs Environment-Variable API Keys

The system has **two independent API-key mechanisms** serving different scenarios. They **do not share state and do not affect each other**:

| | Environment-Variable Keys (e.g. `auth.env.OPENAI_API_KEY`) | Admin-Page API Keys |
|---|---|---|
| **Used by** | Local workspaces (qwen-code-webui in the iframe) | Remote workspaces (Agent on the remote machine) |
| **Storage** | Server config file / environment variables | `api_key_store` table (Fernet encrypted) |
| **Who gets the real key** | The local CLI uses it directly | Only the server knows it; the remote Agent only gets a short-lived, revocable proxy token |
| **How to manage** | Edit config file, restart service | UI operations in the admin page, no restart needed |
| **Who manages it** | Server ops | System administrator (web admin UI) |

**Important**: If no API key is added in the admin page, remote machines **cannot call the LLM**. Remote Workspace does not fall back to `OPENAI_API_KEY` from the environment. When the remote Agent calls the LLM, the server only looks up keys from the `api_key_store` table — if none is found, it returns:

```json
{"error": {"message": "No API key configured for provider 'openai'", "type": "config_error"}}
```

**Therefore, after enabling Remote Workspace, the admin must add API keys in the admin page for every required LLM provider.**

---

## Remote Agent Installation

### One-Line Install (Recommended)

**Linux / macOS:**

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <token>
```

**Windows (PowerShell):**

```powershell
Invoke-WebRequest -Uri "http://<server>:19888/api/remote/agent/install.ps1" | Invoke-Expression
```

### Install Parameters

This is the canonical installer parameter reference (kept in sync with the agent-side guide; see [REMOTE_AGENT.md](REMOTE_AGENT.md) for TLS policy, start/stop scripts, and the `openace` CLI).

| Parameter | Required | Description | Default |
|-----------|----------|-------------|---------|
| `--server URL` | Yes | Open ACE server URL | - |
| `--token TOKEN` | Yes | Admin-generated registration token | - |
| `--name NAME` | No | Display name for the machine | hostname |
| `--install-cli TOOL` | No | CLI tool to install | `qwen-code-cli` |
| `--dir DIR` | No | Install directory | `~/.open-ace-agent` |
| `--ca-bundle PATH` | No | PEM CA bundle for a private/self-signed server certificate | - |
| `--insecure-skip-tls-verify` | No | Disable TLS verification (dangerous, explicit only; persists `skip_ssl_verify` plus the `allow_insecure_tls` admin gate) | off |

Example — install Claude Code:

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server https://ace.example.com \
              --token abc123... \
              --install-cli claude-code \
              --name "Production Server"
```

### Manual Installation

If you cannot use the one-line script (e.g. the server is missing the install-script route), you can install manually:

```bash
# 1. Copy remote-agent/ to the remote machine
scp -r remote-agent/ user@remote:~/.open-ace-agent/

# 2. Install dependencies
cd ~/.open-ace-agent
pip3 install -r requirements.txt

# 3. Install CLI tool
npm install -g @qwen-code/qwen-code@0.23.3
#    (requires Node.js >= 22: engines.node of @qwen-code/qwen-code@0.23.3)

# 4. Create the config file
cat > config.json << 'EOF'
{
    "server_url": "https://ace.example.com",
    "machine_id": "",
    "machine_name": "My Server",
    "registration_token": "<from admin>",
    "cli_tool": "qwen-code-cli"
}
EOF

# 5. Register the machine (you can also just run agent.py and let it auto-register)
# Manual registration:
MACHINE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
curl -X POST "${SERVER_URL}/api/remote/agent/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"registration_token\": \"<token>\",
        \"machine_id\": \"${MACHINE_ID}\",
        \"machine_name\": \"$(hostname)\",
        \"hostname\": \"$(hostname)\",
        \"os_type\": \"$(uname -s | tr '[:upper:]' '[:lower:]')\",
        \"os_version\": \"$(uname -r)\",
        \"capabilities\": {},
        \"agent_version\": \"1.0.0\"
    }"

# 6. Update machine_id in config.json
# 7. Run the Agent
python3 agent.py

# 8. (Optional) Install as a system service — see "Service Management" below
```

### Agent Directory Layout

```
~/.open-ace-agent/
├── agent.py              # Main daemon
├── config.py             # Configuration management
├── config.json           # Config file
├── executor.py           # CLI subprocess management
├── system_info.py        # System info collection
├── requirements.txt      # Python dependencies
├── machine_id            # Unique machine identifier (auto-generated)
├── agent.log             # Runtime log
├── agent-error.log       # Error log
└── cli_adapters/
    ├── __init__.py       # Adapter registry
    ├── base.py           # Adapter base class
    ├── qwen_code.py      # Qwen Code adapter
    ├── claude_code.py    # Claude Code adapter
    ├── codex_cli.py      # Codex CLI adapter
    ├── codex_jsonl_parser.py  # Codex session parsing
    ├── usage_parser.py   # Usage extraction helpers
    ├── zcode.py          # ZCode adapter
    └── openclaw.py       # OpenClaw adapter
```

### Service Management

**Linux (systemd):**

```bash
# Status
sudo systemctl status open-ace-agent

# Logs
sudo journalctl -u open-ace-agent -f

# Restart
sudo systemctl restart open-ace-agent

# Stop
sudo systemctl stop open-ace-agent
```

**macOS (launchd):**

```bash
# Logs
tail -f ~/.open-ace-agent/agent.log

# Stop
launchctl unload ~/Library/LaunchAgents/com.open-ace.agent.plist

# Start
launchctl load ~/Library/LaunchAgents/com.open-ace.agent.plist
```

**Manual run (for debugging):**

```bash
cd ~/.open-ace-agent
python3 agent.py
```

---

## Managing Remote Machines

### Registering a New Machine

Click "Generate Registration Token" in the admin UI, or via API:

```bash
# 1. Admin login
curl -c cookies.txt -X POST http://localhost:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. Generate registration token
curl -b cookies.txt -X POST http://localhost:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
# → {"registration_token": "abc123..."}
```

### Listing All Machines

```bash
curl -b cookies.txt http://localhost:19888/api/remote/machines
```

Sample response:

```json
{
  "machines": [
    {
      "machine_id": "uuid-xxx",
      "machine_name": "Production Server",
      "hostname": "prod-01.example.com",
      "os_type": "linux",
      "os_version": "Ubuntu 22.04",
      "status": "online",
      "connected": true,
      "capabilities": {"cpu_cores": 16, "memory_gb": 64},
      "agent_version": "1.0.0"
    }
  ]
}
```

### Assigning Users

```bash
# Grant a user access to a machine
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

Permission levels:
- `user` — can use the machine to create sessions
- `admin` — can use the machine and manage users/sessions on it (machine-level admin)

### Permission Model

| Action | System Admin | Machine Admin | Regular User |
|--------|--------------|---------------|--------------|
| Create session | ✅ | ✅ | ✅ |
| Manage own sessions | ✅ | ✅ | ✅ |
| View/stop others' sessions (on this machine) | ✅ | ✅ | ❌ |
| Browse machine files | ✅ | ✅ | ✅ |
| Assign/revoke users (on this machine) | ✅ | ✅ | ❌ |
| Unregister machine | ✅ | ❌ | ❌ |
| Generate registration token | ✅ | ❌ | ❌ |
| Manage API keys | ✅ | ❌ | ❌ |

Machine admins can:
- Assign regular users to this machine (cannot grant admin permission)
- Revoke access for regular users (cannot revoke an admin user)
- View and stop other users' sessions on this machine

Machine admins cannot:
- Unregister the machine
- Generate registration tokens
- Manage API keys
- Promote another user to admin

### Revoking User Access

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:19888/api/remote/machines/<machine_id>/assign/<user_id>
```

### Unregistering a Machine

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:19888/api/remote/machines/<machine_id>
```

---

## Management UI

System admins can perform every operation through the Open ACE web admin UI. The management pages (`/manage/*`) are restricted to system admins. Machine admins manage users and sessions on their machines via API.

> The **Remote Workspace** sidebar group (Remote Machines and API Keys pages) is only visible to system admins. Machine admins perform user-management actions through the API (see API Reference below).

### Remote Machine Management (System Admin)

**Path**: Manage Mode → Remote Workspace → Remote Machines (`/manage/remote/machines`)

**Features**:

| Action | Description |
|--------|-------------|
| Generate registration token | Click to generate a one-time registration token; modal shows the token, copy button, and install command |
| Machine list | Table: name, hostname, OS, status (online/offline badge), Agent version, last heartbeat |
| Machine details | Modal with full info (capabilities JSON) + assigned users |
| Assign user | In the details modal, pick a user and permission level (user/admin) and assign to the machine |
| Revoke user | In the details modal, revoke a user's access in the user list |
| Unregister machine | Confirms twice, then removes the machine from the list |

**Stats cards**: Top of page shows total machines, online count, offline count.

### API Key Management (System Admin)

**Path**: Manage Mode → Remote Workspace → API Keys (`/manage/remote/api-keys`)

**Features**:

| Action | Description |
|--------|-------------|
| Add API key | Modal: select provider (OpenAI/Anthropic/Google), enter Key Name, API Key (password input), optional Base URL |
| Key list | Table: Provider (colored badge), Key Name, Base URL, status, creation time. **Key value is always masked** |
| Delete API key | Confirms twice, then deletes |

### Remote Sessions (All Users)

**Create a remote session**: Workspace → click "New Session" → choose "Remote Workspace" → pick an online machine → enter project path (defaults to the machine's work_dir) → click Create.

**Session list**: In the left-side session list, remote sessions show a blue cloud icon (`bi-cloud-fill`) and the machine name.

**Resume a session**: Click any remote session → modal shows session details → click "Resume Session"; the workspace tab opens in remote mode for that session.

**Session controls** (Manage Mode sessions page): Active remote sessions show Pause and Stop buttons; paused sessions show Resume.

**Remote output**: The bottom of the session details modal has a "Remote Output" area that streams CLI output in terminal style (monospace font, dark background).

### Machine Admin Actions (API)

Machine admins manage users and sessions on their machine through the API; they cannot access the admin UI.

```bash
# Machine admin login to get token
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<username>","password":"<password>"}'

# List machines assigned to me (includes current_user_permission field)
curl -b cookies.txt http://<server>:19888/api/remote/machines

# List users assigned to a machine
curl -b cookies.txt http://<server>:19888/api/remote/machines/<machine_id>/users

# Assign a user to the machine (permission is forced to user; admin cannot be granted)
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "admin"}'
# → permission actually stored as "user"

# Revoke a regular user (cannot revoke an admin; returns 403)
curl -b cookies.txt -X DELETE \
  http://<server>:19888/api/remote/machines/<machine_id>/assign/<user_id>

# View another user's session on this machine
curl -b cookies.txt http://<server>:19888/api/remote/sessions/<session_id>

# Stop another user's session on this machine
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/sessions/<session_id>/stop
```

---

## User Guide

### Using Remote Workspace in the Web UI

1. Log in to the Open ACE web UI and enter Work Mode
2. Click "New Session" in the left panel
3. In the modal, choose "Remote Workspace" as the type
4. Pick an online remote machine from the list
5. Enter the project path (defaults to the machine's working directory)
6. Click "Create"; a new workspace tab opens in remote mode
7. Interact with the AI in the workspace — all operations run on the remote machine

### Viewing and Resuming Remote Sessions

1. In the left session list, remote sessions show a blue cloud icon
2. Click a remote session — a modal shows details and remote output
3. Click "Resume Session"; the workspace tab opens in remote mode and resumes the session

### Managing Remote Sessions (Manage Mode)

1. Go to Manage Mode → Sessions page
2. Remote sessions show a blue "Remote" badge and the machine name
3. Active remote sessions: can be paused or stopped
4. Paused remote sessions: can be resumed
5. Click a session card to see details, including remote output

### Viewing Available Machines

```bash
# After login, a user can view the online machines assigned to them
curl -b cookies.txt http://localhost:19888/api/remote/machines/available
```

### Creating a Remote Session

```bash
curl -b cookies.txt -X POST http://localhost:19888/api/remote/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "machine_id": "<machine_id>",
    "project_path": "/home/user/my-project",
    "cli_tool": "qwen-code-cli",
    "model": "qwen3-coder-plus",
    "title": "Fix login bug"
  }'
```

Parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `machine_id` | Yes | Target machine ID |
| `project_path` | Yes | Working directory on the remote machine |
| `cli_tool` | No | CLI tool name; defaults to `qwen-code-cli` |
| `model` | No | Model name to use |
| `title` | No | Session title |

### Sending a Message

```bash
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "Please review main.py for me"}'
```

### Viewing Session State

```bash
curl -b cookies.txt \
  http://localhost:19888/api/remote/sessions/<session_id>
```

Response includes session state and all output:

```json
{
  "session": {
    "session_id": "uuid-xxx",
    "status": "active",
    "machine_id": "uuid-yyy",
    "project_path": "/home/user/my-project",
    "model": "qwen3-coder-plus",
    "total_tokens": 2300,
    "message_count": 3,
    "output": [
      {"data": "{\"type\":\"thinking\",...}", "stream": "stdout", "timestamp": "..."},
      {"data": "{\"type\":\"assistant\",...}", "stream": "stdout", "timestamp": "..."}
    ]
  }
}
```

### Stopping a Session

```bash
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/stop
```

### Pausing / Resuming a Session

```bash
# Pause
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/pause

# Resume
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/resume
```

---

## API Reference

### Machine Management

| Method | Path | Description | Permission |
|--------|------|-------------|------------|
| `POST` | `/api/remote/machines/register` | Generate a machine registration token | System admin |
| `GET` | `/api/remote/machines` | List machines (admins see all; users see assigned) | Logged-in user |
| `GET` | `/api/remote/machines/<id>` | Get machine details | Logged-in user (must be assigned) |
| `DELETE` | `/api/remote/machines/<id>` | Unregister machine | System admin |
| `POST` | `/api/remote/machines/<id>/assign` | Assign a user | System admin / machine admin |
| `DELETE` | `/api/remote/machines/<id>/assign/<uid>` | Revoke user access | System admin / machine admin |
| `GET` | `/api/remote/machines/<id>/users` | List users assigned to a machine | System admin / machine admin |

### API Key Management (Admin)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/remote/api-keys` | List all API keys (key value masked) |
| `POST` | `/api/remote/api-keys` | Store a new API key (encrypted at rest) |
| `DELETE` | `/api/remote/api-keys/<id>` | Delete the specified API key |

### Session Management

| Method | Path | Description | Permission |
|--------|------|-------------|------------|
| `GET` | `/api/remote/machines/available` | Get online machines available to the current user | Logged-in user |
| `POST` | `/api/remote/sessions` | Create a remote session | Logged-in user |
| `GET` | `/api/remote/sessions/<id>` | Get session state and output | Session owner / system admin / machine admin |
| `POST` | `/api/remote/sessions/<id>/chat` | Send a message | Session owner / system admin / machine admin |
| `POST` | `/api/remote/sessions/<id>/stop` | Stop a session | Session owner / system admin / machine admin |
| `POST` | `/api/remote/sessions/<id>/pause` | Pause a session | Session owner / system admin / machine admin |
| `POST` | `/api/remote/sessions/<id>/resume` | Resume a session | Session owner / system admin / machine admin |

### Agent Install & File Distribution

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/remote/agent/install.sh` | Get the install script |
| `GET` | `/api/remote/agent/files/<path>` | Get Agent source files (for the installer to download) |

### Agent Communication

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/remote/agent/register` | Agent registration (uses the registration token) |
| `GET` | `/api/remote/agent/ws` | Deprecated legacy WebSocket transport; always returns `410 Gone` — use HTTP polling via `/api/remote/agent/message` |
| `POST` | `/api/remote/agent/message` | HTTP polling transport (recommended) |
| `POST` | `/api/remote/usage-report` | Agent reports token usage |

### LLM Proxy

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/remote/llm-proxy` | Proxy an LLM API request |
| `*` | `/api/remote/llm-proxy/<path>` | Proxy any LLM request path |

### Remote File Browsing

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/remote/machines/<id>/browse` | Browse the remote machine filesystem (requires WebSocket support; currently reserved) |

---

## Security Design

### Authentication & Authorization

```
┌─────────────┐   registration token (one-time)   ┌─────────────┐
│   Admin      │ ───────────────────────→           │ Remote Agent │
│  (server)    │                                    │ (remote)     │
└─────────────┘                                    └─────────────┘
       │                                                  │
       │ Assigns user permission                          │ Proxy token (5 min)
       ↓                                                  ↓
┌─────────────┐     session_token      ┌─────────────┐
│   User       │ ←────────────────────→ │  Open ACE    │
│  (browser)   │                        │   Server     │
└─────────────┘                        └─────────────┘
```

### Security Mechanisms

| Area | Mechanism |
|------|-----------|
| **Machine registration** | Admin generates a 256-bit random one-time token. Once used by an Agent, the token is immediately invalidated. |
| **Transport encryption** | All traffic should go over HTTPS (TLS). Agent authenticates via token. |
| **API key storage** | Uses Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256). The encryption key is derived via SHA-256 from `OPENACE_ENCRYPTION_KEY` and is never stored in the database. Falls back to Base64 encoding (insecure, dev only) if the `cryptography` package is not installed. |
| **API key access** | The remote Agent receives an HMAC-SHA256 signed proxy token, never the real key. Tokens are server-issued, tracked by `jti`, revocable, and use a configurable TTL (4 hours by default for general sessions; HA pool tokens default to 15 minutes). The real key is never written to disk on the remote machine. |
| **Access control** | The `machine_assignments` table controls which users can use which machines; the `permission` field distinguishes `user`/`admin`. Machine admins may delegate management of users and sessions on their machine. Registration, unregistration, and token generation are restricted to system admins. |
| **Session isolation** | Users can only access their own sessions. System admins and machine admins can view/stop other users' sessions on their machine. |
| **Unified quota** | Local and remote sessions share the `quota_usage` table and are billed uniformly. |
| **Audit log** | All remote operations are recorded via the existing `AuditLogger`. |

### LLM Proxy Security Flow

1. The remote CLI sends a request to `/api/remote/llm-proxy` with `Authorization: Bearer <proxy_token>`
2. The server verifies the proxy token's signature (HMAC-SHA256), server-side issuance record, expiry, and session lifecycle status
3. The server decrypts the real API key from `api_key_store`
4. The server replaces the Authorization header with the real key and forwards to the LLM provider
5. The response is streamed back; token usage is parsed and recorded

**Core principle: the API key is never transmitted to the remote machine.**

---

## Supported CLI Tools

### Built-in Adapters

| CLI Tool | Identifier | Install Command | Environment Variables |
|----------|-----------|-----------------|----------------------|
| Qwen Code | `qwen-code-cli` | `npm install -g @qwen-code/qwen-code@0.23.3` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| Claude Code | `claude-code` | `npm install -g @anthropic-ai/claude-code@latest` | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` |
| OpenClaw | `openclaw` | `npm install -g openclaw@latest` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |

### CLI Startup Arguments

| Tool | Arguments |
|------|-----------|
| Qwen Code | `qwen --print --output-format stream-json [--model MODEL]` |
| Claude Code | `claude --print --output-format stream-json [--model MODEL]` |
| OpenClaw | `openclaw --agent --json [--model MODEL]` |

### How Proxy Routing Works

CLI tools require no source changes. The Agent routes the CLI's API requests to the Open ACE proxy by setting environment variables:

1. The Agent sets the CLI's **Base URL** environment variable to point at the proxy endpoint
2. The Agent sets the CLI's **API Key** environment variable to a short-lived proxy token
3. The CLI believes it is calling the LLM API directly, but the requests are intercepted by the proxy
4. The proxy validates the token, injects the real key, forwards the request, and streams the response back

**The CLI is completely unaware it is being proxied.**

### Adding a New CLI Tool

A new adapter only needs to subclass `BaseCLIAdapter` and implement 6 methods:

```python
# remote-agent/cli_adapters/my_tool.py

from .base import BaseCLIAdapter

class MyToolAdapter(BaseCLIAdapter):
    def get_install_command(self):
        return "npm install -g my-tool@latest"

    def check_installed(self):
        import shutil
        return shutil.which("my-tool") is not None

    def get_env_vars(self, proxy_url, proxy_token):
        return {
            "MY_TOOL_API_KEY": proxy_token,
            "MY_TOOL_BASE_URL": proxy_url,
        }

    def build_start_args(self, session_id, project_path, model=None):
        args = ["my-tool", "--agent"]
        if model:
            args.extend(["--model", model])
        return args

    def get_display_name(self):
        return "My Tool"

    def get_executable_name(self):
        return "my-tool"
```

Then register it in `cli_adapters/__init__.py` in the `ADAPTERS` dict:

```python
ADAPTERS = {
    ...
    "my-tool": MyToolAdapter,
}
```

For unregistered CLI tools, the system falls back to a generic adapter (`GenericAdapter`) that tries to route requests via `OPENAI_API_KEY` / `OPENAI_BASE_URL`.

---

## Environment Variable Reference

### Server Side

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENACE_ENCRYPTION_KEY` | Dedicated encryption key for API keys / SMTP passwords (required in production) | Development-only fallback outside production |
| `SECRET_KEY` | Flask session key | `dev-secret-key` (development only) |

### Remote Agent

Configuration precedence: env vars > config file > built-in defaults.

| Variable | Config key | Default |
|----------|-----------|---------|
| `OPENACE_SERVER_URL` | `server_url` | `http://localhost:19888` |
| `OPENACE_AGENT_TOKEN` | `agent_token` | None |
| `OPENACE_MACHINE_ID` | `machine_id` | Auto-generated UUID |
| `OPENACE_HEARTBEAT_INTERVAL` | `heartbeat_interval` | `60` (seconds) |
| `OPENACE_RECONNECT_BASE_DELAY` | `reconnect_base_delay` | `1` (seconds) |
| `OPENACE_RECONNECT_MAX_DELAY` | `reconnect_max_delay` | `60` (seconds) |
| `OPENACE_MAX_SESSIONS` | `max_sessions` | `5` |
| `OPENACE_LOG_LEVEL` | `log_level` | `INFO` |

Config file path: `~/.open-ace-agent/config.json`

```json
{
    "server_url": "https://ace.example.com",
    "machine_id": "auto-generated-uuid",
    "machine_name": "My Server",
    "registration_token": "one-time-token",
    "cli_tool": "qwen-code-cli",
    "heartbeat_interval": 60,
    "reconnect_backoff_max": 60,
    "max_sessions": 5,
    "log_level": "INFO"
}
```

---

## Troubleshooting

### Install Script Returns 404

**Symptom**: `curl -fsSL http://<server>:19888/api/remote/agent/install.sh` returns 404.

**Cause**: The server is missing the install-script routes (`/api/remote/agent/install.sh` and `/api/remote/agent/files/<path>`).

**Fix**:
1. Confirm the server code is up to date
2. Restart the server
3. If still 404, use [Manual Installation](#manual-installation)

### Agent Cannot Connect to Server

**Symptom**: The Agent log shows connection failures or constant reconnects.

```bash
# View logs
tail -f ~/.open-ace-agent/agent.log

# Or via systemd
sudo journalctl -u open-ace-agent -f
```

**Troubleshooting steps**:

1. **Network connectivity**: ensure the remote machine can reach the server port
   ```bash
   curl -v http://<server>:19888/api/auth/login
   ```

2. **Registration token expired**: tokens are one-time; once used, they are invalid. If registration failed, ask the admin to generate a new one.

3. **Server URL**: check `server_url` in `config.json`; make sure it is correct and has no trailing slash.

### Agent Logs Show WebSocket Errors / Machine Stays Offline

**Symptom**: The Agent log repeatedly shows `WebSocket error: Handshake status 405 METHOD NOT ALLOWED` or `410 Gone` responses from `/api/remote/agent/ws`, and the machine stays offline.

**Cause**: The server-side WebSocket transport has been removed. `GET /api/remote/agent/ws` is deprecated and always returns `410 Gone` with a pointer to `/api/remote/agent/message`; HTTP polling is the only transport. Agents from older releases still try WebSocket first and do not fall back correctly.

**Fix**:
1. Update the Agent to a current release (HTTP polling only); reinstalling with a fresh registration token is the simplest path
2. Restart the Agent: `sudo systemctl restart open-ace-agent`
3. Check logs to confirm polling mode: `sudo journalctl -u open-ace-agent -n 20 | grep "HTTP polling"`

### Machine Shows Offline

**Symptom**: The admin UI shows the machine status as `offline`.

**Troubleshooting steps**:

1. **Agent process**: confirm the Agent process is running
   ```bash
   # Linux
   sudo systemctl status open-ace-agent
   # macOS
   ps aux | grep agent.py
   ```

2. **Heartbeat timeout**: the Agent sends a heartbeat every 60s; the server marks offline after 180s without one. Check network stability.

3. **Restart the Agent**:
   ```bash
   sudo systemctl restart open-ace-agent
   ```

### Session Creation Fails (400 Error)

**Common causes**:

1. **User not assigned to the machine**: an admin must assign user permission first
2. **Machine offline**: check whether the Agent is running and heartbeating
3. **`project_path` missing**: the working directory on the remote machine must be specified

### LLM Proxy Returns Errors

**Symptom**: In a remote session, the CLI reports an API error.

**Troubleshooting steps**:

1. **API key not stored**: the admin must first add an API key for the relevant provider in the admin page (Manage Mode → Remote Workspace → API Keys). Note: `OPENAI_API_KEY` from the environment **is not** used by Remote Workspace — the two are fully independent.
2. **Quota exhausted**: check the user's quota; the proxy checks before forwarding
3. **Proxy token expired or revoked**: general proxy tokens use a configurable TTL (4 hours by default), HA pool tokens are shorter-lived (15 minutes by default), and tokens are revoked when the backing session stops, completes, or rotates. Recreate or reattach the session to obtain a fresh token.

### CLI Tool Not Found

**Symptom**: When the Agent starts a session it reports `command not found`.

**Fix**:

```bash
# Install Qwen Code CLI
npm install -g @qwen-code/qwen-code@0.23.3

# Or install Claude Code
npm install -g @anthropic-ai/claude-code@latest

# Verify installation
which qwen
which claude
```

### Database Migration

If Remote Workspace features misbehave after an upgrade, check whether the migration has been applied:

```bash
cd /path/to/open-ace
alembic upgrade head
```

The `remote_machines`, `machine_assignments`, and `api_key_store` tables are defined in the baseline schema (`schema/schema-postgres.sql`).

### Registration Token Is One-Time-Use

A registration token can only be used once after generation. It is consumed when:

- The install script runs successfully
- The install script's registration step fails (e.g. a network timeout followed by a retry)

If the token is consumed, the admin must generate a new one. Recommendation: generate a token and immediately run the installer on the remote machine, to avoid expiration or misuse.

---

## 中文

> 让用户在浏览器中选择远程机器，启动 AI 编码会话，AI CLI 直接运行在远程机器上——无需 SSH，无需重复配置。

> 本页从**服务器视角**介绍该功能：架构、管理员配置、API 与安全设计。Agent 侧的运维内容（安装器 TLS 参数详解、启停脚本、`config.json` 配置项、`openace` 命令行工具、终端服务器细节）见 [REMOTE_AGENT.md](REMOTE_AGENT.md)，该页也链接回本页。

## 目录

- [概述](#概述)
- [架构](#架构)
- [部署检查清单](#部署检查清单)
- [快速开始](#快速开始)
- [服务端配置](#服务端配置)
- [远程 Agent 安装](#远程-agent-安装)
- [管理远程机器](#管理远程机器)
- [管理界面](#管理界面)
- [用户使用指南](#用户使用指南)
- [API 参考](#api-参考)
- [安全设计](#安全设计)
- [支持的 CLI 工具](#支持的-cli-工具)
- [环境变量参考](#环境变量参考)
- [故障排查](#故障排查)

---

## 概述

### 解决什么问题

Open ACE 工作区默认只能在服务器本机运行。用户需要操作远程机器（如开发/测试服务器）时，必须每次在会话中指示 AI 通过 SSH 连接远程机器，频繁提供凭据且容易遇到 SSH 故障。

### 如何解决

远程工作区功能让用户在创建会话时选择远程机器。AI CLI 直接运行在远程机器的 Agent 上，通过服务端代理访问 LLM API。所有 API Key 永远不会离开服务器。

### 核心特性

| 特性 | 说明 |
|------|------|
| 多 CLI 支持 | Qwen Code、Claude Code、OpenClaw 等 |
| API Key 代理 | Key 加密存储在服务器，远程 Agent 只拿到短生命周期、可回收的代理令牌 |
| 一行安装 | `curl ... \| bash` 完成远程机器部署 |
| 统一配额 | 本地和远程会话共享同一套配额体系 |
| 自动重连 | Agent 断线后指数退避自动重连 |

---

## 架构

```
[浏览器 UI] <--HTTP/WS--> [Open ACE 服务器] <--HTTP 轮询--> [远程 Agent]
                                  |                                |
                           [API Key 加密存储]               [qwen-code-cli]
                           [配额管理器]                     [claude-code]
                           [会话管理器]                     [openclaw]
```

### 消息流

1. 用户在浏览器输入消息 → `POST /api/remote/sessions/{id}/chat`
2. 服务器将命令排队，等待 Agent 通过 HTTP 轮询拉取
3. Agent 将消息喂给 CLI 子进程
4. CLI 需要 LLM 调用 → 请求发往 `POST /api/remote/llm-proxy`
5. 服务器校验配额、注入真实 API Key、转发到 LLM 提供商
6. LLM 流式响应：提供商 → 服务器 → Agent → 服务器 → 浏览器
7. 服务器记录 Token 用量

### 通信方式

Agent 与服务器之间只有一种传输方式：

| 方式 | 状态 | 适用场景 | 特点 |
|------|------|---------|------|
| HTTP 轮询 | 已实现，且是唯一传输方式 | 所有场景 | Agent 主动 POST 到 `/api/remote/agent/message`，服务器返回待执行命令，兼容性好 |
| WebSocket | 已移除 | - | 旧端点 `GET /api/remote/agent/ws` 已弃用，现在固定返回 `410 Gone`，提示改用 HTTP 轮询 |

不再存在"先试 WebSocket、失败再降级"的逻辑：当前版本的 Agent 只使用 HTTP 轮询，命令传输也不需要服务端部署任何 WebSocket worker（gevent 等）。

---

## 部署拓扑与运行态边界

Remote Workspace 可以运行在仓库提供的 Kubernetes 参考部署后面。可共享的控制面状态已经持久化，实时 socket bridge 仍保留在进程内：

- HTTP polling 命令队列、命令响应、session-to-machine 绑定、会话输出回放、远程机器、会话、消息、配额和审计记录均已持久化。
- 发送消息、暂停、恢复、停止、终止请求、切换模型和权限响应等远程会话控制 API 可以由任意 Web Pod 服务；下一次 Agent poll 会 claim 已持久化的命令。
- SSE 重连可以在浏览器断开或 Web Pod 重启后回放已持久化的会话输出。
- 实时终端 relay WebSocket 对象仍是进程内对象。活跃的浏览器到 Agent 终端 bridge 应使用粘性路由，或在故障转移后重新连接。

当前租户隔离覆盖用户、项目、工作区会话与消息、每日用量聚合、审计日志、远程机器、会话所有权、机器权限和配额。系统管理员有意保留全局运维可见性，用于支持和故障处理。

---

## 部署检查清单

从零到可用，按顺序完成以下步骤：

### 服务端（Open ACE 服务器）

```bash
# 1. 确认代码已更新到包含远程工作区模块
ls app/modules/workspace/api_key_proxy.py \
   app/modules/workspace/remote_agent_manager.py \
   app/modules/workspace/remote_session_manager.py \
   app/routes/remote.py

# 2. 运行数据库迁移（创建 remote_machines、machine_assignments、api_key_store 表）
cd /path/to/open-ace
alembic upgrade head

# 3. 设置加密密钥（生产环境强烈推荐）
export OPENACE_ENCRYPTION_KEY="<your-random-32byte-key>"
# 或使用 openssl 生成：openssl rand -hex 32

# 4. 重启服务
sudo systemctl restart open-ace  # 或你的启动方式

# 5. 验证 API 可访问
curl -s http://localhost:19888/api/remote/agent/install.sh | head -5
# 应该输出安装脚本内容
```

### 管理员操作

```bash
# 1. 管理员登录
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 存储 LLM API Key（远程会话必需）
curl -b cookies.txt -X POST http://<server>:19888/api/remote/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key_name": "production",
    "api_key": "sk-xxx...",
    "base_url": "https://api.openai.com/v1"
  }'

# 3. 生成注册令牌
curl -b cookies.txt -X POST http://<server>:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
# → {"registration_token": "abc123..."}
```

### 远程机器

```bash
# 在远程机器上执行一行安装（将 <token> 替换为上一步获取的注册令牌）
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <token>

# 如果 curl 404，说明服务器缺少安装脚本路由，请使用手动安装（见下方）
```

### 分配用户

```bash
# 获取 machine_id（从安装输出或管理界面获取）
curl -b cookies.txt http://<server>:19888/api/remote/machines

# 分配用户（user_id 从用户管理页面获取）
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

完成以上步骤后，用户即可在浏览器工作区中选择远程机器创建会话。

---

## 快速开始

### 前提条件

- Open ACE 服务器已部署并运行
- 远程机器可访问服务器的 HTTP 端口
- 远程机器已安装 Python 3.8+
- 远程机器已安装 Node.js（用于安装 CLI 工具）

### 三步完成

**第一步：管理员生成注册令牌**

在 Open ACE 管理界面（管理模式 → 远程工作区 → 远程机器 → 生成注册令牌），或通过 API：

```bash
# 管理员登录获取 session_token
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 生成注册令牌
curl -b cookies.txt -X POST http://<server>:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
```

返回：
```json
{"registration_token": "a1b2c3d4e5f6..."}
```

**第二步：在远程机器上安装 Agent**

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <registration-token>
```

> **注意**：如果此命令返回 404，说明服务器尚未部署安装脚本路由。请先确认服务器代码已更新到最新版本并重启。临时替代方案见[手动安装](#手动安装)。

安装脚本会自动：
1. 从服务器下载 Agent 文件到 `~/.open-ace-agent/`
2. 安装 Python 依赖（websocket-client、requests）
3. 安装 CLI 工具（默认 qwen-code-cli，可选 claude-code）
4. 生成 machine_id 并注册到服务器
5. 安装为系统服务（Linux: systemd，macOS: launchd）

**第三步：分配用户**

管理员在管理界面（远程机器详情弹窗 → 分配用户），或通过 API：

```bash
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

用户即可在浏览器中看到该机器并创建远程会话。

---

## 服务端配置

### 数据库迁移

远程工作区各表属于 baseline 模式（见 `schema/schema-postgres.sql` 和 `DATABASE_SCHEMA.md`）：

| 表名 | 用途 |
|------|------|
| `remote_machines` | 注册的远程机器信息 |
| `machine_assignments` | 机器与用户的分配关系 |
| `api_key_store` | 加密存储的 LLM API Key |

同时为 `agent_sessions` 表增加两列：
- `workspace_type` — `local` 或 `remote`
- `remote_machine_id` — 关联的远程机器 ID

### 环境变量

| 变量 | 必需 | 说明 | 默认值 |
|------|------|------|--------|
| `OPENACE_ENCRYPTION_KEY` | 生产环境必需 | API Key 与 SMTP 密码的独立加密密钥 | 仅开发环境提供 fallback |
| `SECRET_KEY` | 是 | Flask 会话密钥 | `dev-secret-key`（仅开发环境） |

### API Key 管理

在管理界面（管理模式 → 远程工作区 → API 密钥 → 添加 API 密钥），或通过 API：

```bash
# 存储 OpenAI API Key
curl -b cookies.txt -X POST http://<server>:19888/api/remote/api-keys \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "key_name": "production",
    "api_key": "sk-xxx...",
    "base_url": "https://api.openai.com/v1"
  }'
```

支持的 provider：
- `openai` — OpenAI / 通义千问等 OpenAI 兼容 API
- `anthropic` — Anthropic / Claude API
- `google` — Google Gemini API

### 管理页面 API 密钥 vs 环境变量 API 密钥

本系统存在**两套独立的 API 密钥机制**，分别服务不同场景，**互不共享，互不影响**：

| | 环境变量密钥（如 `auth.env.OPENAI_API_KEY`） | 管理页面 API 密钥 |
|---|---|---|
| **用途** | 本地工作区（iframe 中的 qwen-code-webui） | 远程工作区（远程机器上的 Agent） |
| **存储位置** | 服务器配置文件 / 环境变量 | 数据库 `api_key_store` 表（Fernet 加密） |
| **谁拿到真实 Key** | 本地 CLI 直接使用 | 只有服务器知道，远程 Agent 只拿短生命周期、可回收的代理令牌 |
| **管理方式** | 修改配置文件，重启服务 | 管理页面 UI 操作，无需重启 |
| **管理权限** | 服务器运维人员 | 系统管理员（Web 管理界面） |

**重要**：如果未在管理页面添加 API 密钥，远程机器将**无法调用 LLM**。远程工作区不会 fallback 使用环境变量中的 `OPENAI_API_KEY`。远程 Agent 调用 LLM 时，服务器仅从 `api_key_store` 表中查找密钥，找不到则返回错误：

```json
{"error": {"message": "No API key configured for provider 'openai'", "type": "config_error"}}
```

**因此，启用远程工作区功能后，管理员必须在管理页面为所需的 LLM 提供商分别添加 API 密钥。**

---

## 远程 Agent 安装

### 一行安装（推荐）

**Linux / macOS：**

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:19888 --token <token>
```

**Windows (PowerShell)：**

```powershell
Invoke-WebRequest -Uri "http://<server>:19888/api/remote/agent/install.ps1" | Invoke-Expression
```

### 安装参数

本表是安装器参数的权威参考（与 Agent 侧文档保持同步；TLS 策略、启停脚本和 `openace` 命令行工具见 [REMOTE_AGENT.md](REMOTE_AGENT.md)）。

| 参数 | 必需 | 说明 | 默认值 |
|------|------|------|--------|
| `--server URL` | 是 | Open ACE 服务器地址 | - |
| `--token TOKEN` | 是 | 管理员生成的注册令牌 | - |
| `--name NAME` | 否 | 机器显示名称 | hostname |
| `--install-cli TOOL` | 否 | 要安装的 CLI 工具 | `qwen-code-cli` |
| `--dir DIR` | 否 | 安装目录 | `~/.open-ace-agent` |
| `--ca-bundle PATH` | 否 | 私有 CA / 自签名服务器证书使用的 PEM CA bundle | - |
| `--insecure-skip-tls-verify` | 否 | 关闭 TLS 验证（危险，仅显式使用；会同时写入 `skip_ssl_verify` 与管理员批准项 `allow_insecure_tls`） | 关闭 |

示例 — 安装 Claude Code：

```bash
curl -fsSL http://<server>:19888/api/remote/agent/install.sh | \
  bash -s -- --server https://ace.example.com \
              --token abc123... \
              --install-cli claude-code \
              --name "Production Server"
```

### 手动安装

如果无法使用一键脚本（例如服务器缺少安装脚本路由），可以手动安装：

```bash
# 1. 复制 remote-agent/ 目录到远程机器
scp -r remote-agent/ user@remote:~/.open-ace-agent/

# 2. 安装依赖
cd ~/.open-ace-agent
pip3 install -r requirements.txt

# 3. 安装 CLI 工具
npm install -g @qwen-code/qwen-code@0.23.3
#    (requires Node.js >= 22: engines.node of @qwen-code/qwen-code@0.23.3)

# 4. 创建配置文件
cat > config.json << 'EOF'
{
    "server_url": "https://ace.example.com",
    "machine_id": "",
    "machine_name": "My Server",
    "registration_token": "<从管理员获取>",
    "cli_tool": "qwen-code-cli"
}
EOF

# 5. 注册机器（也可以直接运行 agent.py 让其自动注册）
# 手动注册：
MACHINE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
curl -X POST "${SERVER_URL}/api/remote/agent/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"registration_token\": \"<token>\",
        \"machine_id\": \"${MACHINE_ID}\",
        \"machine_name\": \"$(hostname)\",
        \"hostname\": \"$(hostname)\",
        \"os_type\": \"$(uname -s | tr '[:upper:]' '[:lower:]')\",
        \"os_version\": \"$(uname -r)\",
        \"capabilities\": {},
        \"agent_version\": \"1.0.0\"
    }"

# 6. 更新 config.json 中的 machine_id
# 7. 运行 Agent
python3 agent.py

# 8. （可选）安装为系统服务，参见下方"服务管理"
```

### Agent 目录结构

```
~/.open-ace-agent/
├── agent.py              # 主守护进程
├── config.py             # 配置管理
├── config.json           # 配置文件
├── executor.py           # CLI 子进程管理
├── system_info.py        # 系统信息收集
├── requirements.txt      # Python 依赖
├── machine_id            # 机器唯一标识（自动生成）
├── agent.log             # 运行日志
├── agent-error.log       # 错误日志
└── cli_adapters/
    ├── __init__.py       # 适配器注册中心
    ├── base.py           # 适配器基类
    ├── qwen_code.py      # Qwen Code 适配器
    ├── claude_code.py    # Claude Code 适配器
    ├── codex_cli.py      # Codex CLI 适配器
    ├── codex_jsonl_parser.py  # Codex 会话解析
    ├── usage_parser.py   # 用量提取辅助
    ├── zcode.py          # Zcode 适配器
    └── openclaw.py       # OpenClaw 适配器
```

### 服务管理

**Linux (systemd)：**

```bash
# 查看状态
sudo systemctl status open-ace-agent

# 查看日志
sudo journalctl -u open-ace-agent -f

# 重启
sudo systemctl restart open-ace-agent

# 停止
sudo systemctl stop open-ace-agent
```

**macOS (launchd)：**

```bash
# 查看日志
tail -f ~/.open-ace-agent/agent.log

# 停止
launchctl unload ~/Library/LaunchAgents/com.open-ace.agent.plist

# 启动
launchctl load ~/Library/LaunchAgents/com.open-ace.agent.plist
```

**手动运行（调试）：**

```bash
cd ~/.open-ace-agent
python3 agent.py
```

---

## 管理远程机器

### 注册新机器

在管理界面点击"生成注册令牌"按钮，或通过 API：

```bash
# 1. 管理员登录
curl -c cookies.txt -X POST http://localhost:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 生成注册令牌
curl -b cookies.txt -X POST http://localhost:19888/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
# → {"registration_token": "abc123..."}
```

### 查看所有机器

```bash
curl -b cookies.txt http://localhost:19888/api/remote/machines
```

响应示例：

```json
{
  "machines": [
    {
      "machine_id": "uuid-xxx",
      "machine_name": "Production Server",
      "hostname": "prod-01.example.com",
      "os_type": "linux",
      "os_version": "Ubuntu 22.04",
      "status": "online",
      "connected": true,
      "capabilities": {"cpu_cores": 16, "memory_gb": 64},
      "agent_version": "1.0.0"
    }
  ]
}
```

### 分配用户

```bash
# 给用户分配机器使用权
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "user"}'
```

权限级别：
- `user` — 可以使用机器创建会话
- `admin` — 可以使用机器，且可以管理本机器的用户和会话（机器级管理员）

### 权限模型

| 操作 | 系统管理员 | 机器管理员 | 普通用户 |
|------|-----------|-----------|---------|
| 创建会话 | ✅ | ✅ | ✅ |
| 管理自己的会话 | ✅ | ✅ | ✅ |
| 查看/停止他人会话（本机器） | ✅ | ✅ | ❌ |
| 浏览机器文件 | ✅ | ✅ | ✅ |
| 分配/撤销用户（本机器） | ✅ | ✅ | ❌ |
| 注销机器 | ✅ | ❌ | ❌ |
| 生成注册令牌 | ✅ | ❌ | ❌ |
| 管理 API Key | ✅ | ❌ | ❌ |

机器管理员可以：
- 分配普通用户到本机器（不能授权 admin 权限）
- 撤销普通用户的访问权限（不能撤销 admin 用户）
- 查看和停止本机器上其他用户的会话

机器管理员不能：
- 注销机器
- 生成注册令牌
- 管理 API Key
- 授权其他用户为 admin

### 撤销用户权限

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:19888/api/remote/machines/<machine_id>/assign/<user_id>
```

### 注销机器

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:19888/api/remote/machines/<machine_id>
```

---

## 管理界面

系统管理员可以通过 Open ACE Web 管理界面完成所有操作。管理页面（`/manage/*`）仅对系统管理员开放。机器管理员通过 API 管理本机器的用户和会话。

> 侧边栏 **"远程工作区"** 分组下的远程机器和 API Key 页面仅系统管理员可见。机器管理员通过 API 接口执行用户管理操作（见下方 API 参考）。

### 远程机器管理（系统管理员）

**路径**：管理模式 → 远程工作区 → 远程机器（`/manage/remote/machines`）

**功能**：

| 操作 | 说明 |
|------|------|
| 生成注册令牌 | 点击按钮生成一次性注册 token，弹窗显示 token 值、复制按钮和安装命令 |
| 查看机器列表 | 表格显示：名称、Hostname、OS、状态（在线/离线 Badge）、Agent 版本、最后心跳时间 |
| 机器详情 | 弹窗显示完整信息（capabilities JSON）+ 已分配用户列表 |
| 分配用户 | 在详情弹窗中选择用户和权限级别（user/admin），分配到机器 |
| 撤销用户 | 在详情弹窗的用户列表中撤销某用户的机器访问权限 |
| 注销机器 | 二次确认后注销，机器从列表消失 |

**统计卡片**：页面顶部显示总机器数、在线数、离线数。

### API Key 管理（系统管理员）

**路径**：管理模式 → 远程工作区 → API 密钥（`/manage/remote/api-keys`）

**功能**：

| 操作 | 说明 |
|------|------|
| 添加 API 密钥 | 弹窗中选择 Provider（OpenAI/Anthropic/Google）、输入 Key Name、API Key（密码输入框）、可选 Base URL |
| 查看密钥列表 | 表格显示：Provider（彩色 Badge）、Key Name、Base URL、状态、创建时间。**Key 值始终被遮蔽** |
| 删除 API 密钥 | 二次确认后删除 |

### 远程会话（所有用户）

**创建远程会话**：工作区 → 点击"新建会话"按钮 → 选择"远程工作区" → 从机器列表中选择在线机器 → 输入项目路径（默认使用机器的 work_dir） → 点击创建。

**会话列表**：左侧会话列表中，远程会话显示蓝色云图标（`bi-cloud-fill`）和机器名称标识。

**会话恢复**：点击任意远程会话 → 弹窗显示会话详情 → 点击"恢复会话"按钮，工作区标签页自动以远程模式打开该会话。

**会话控制**（管理模式的会话页面）：远程活跃会话显示暂停和停止按钮，已暂停会话显示恢复按钮。

**远程输出查看**：会话详情弹窗底部显示"远程输出"区域，以终端样式（等宽字体、深色背景）实时展示 CLI 输出。

### 机器管理员操作（API）

机器管理员通过 API 接口管理本机器的用户和会话，无法访问管理界面。

```bash
# 机器管理员登录获取 token
curl -c cookies.txt -X POST http://<server>:19888/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<username>","password":"<password>"}'

# 查看自己被分配的机器（含 current_user_permission 字段）
curl -b cookies.txt http://<server>:19888/api/remote/machines

# 查看机器已分配用户列表
curl -b cookies.txt http://<server>:19888/api/remote/machines/<machine_id>/users

# 分配用户到机器（权限被强制为 user，无法授权 admin）
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": <user_id>, "permission": "admin"}'
# → 实际存储的 permission 为 "user"

# 撤销普通用户（不能撤销 admin 用户，返回 403）
curl -b cookies.txt -X DELETE \
  http://<server>:19888/api/remote/machines/<machine_id>/assign/<user_id>

# 查看本机器上其他用户的会话
curl -b cookies.txt http://<server>:19888/api/remote/sessions/<session_id>

# 停止本机器上其他用户的会话
curl -b cookies.txt -X POST \
  http://<server>:19888/api/remote/sessions/<session_id>/stop
```

---

## 用户使用指南

### 在 Web 界面使用远程工作区

1. 登录 Open ACE Web 界面，进入工作模式
2. 点击左侧面板的"新建会话"按钮
3. 在弹窗中选择"远程工作区"类型
4. 从机器列表中选择一台在线的远程机器
5. 输入项目路径（默认自动填充为机器的工作目录）
6. 点击"创建"，新的工作区标签页将以远程模式打开
7. 在工作区中与 AI 交互，所有操作在远程机器上执行

### 查看和恢复远程会话

1. 在左侧会话列表中，远程会话显示蓝色云图标标识
2. 点击远程会话，弹窗显示会话详情和远程输出
3. 点击"恢复会话"按钮，工作区标签页自动以远程模式恢复该会话

### 管理远程会话（管理模式）

1. 进入管理模式 → 会话页面
2. 远程会话显示蓝色"远程"Badge 和机器名称
3. 活跃的远程会话：可暂停或停止
4. 已暂停的远程会话：可恢复
5. 点击会话卡片查看详情，包括远程输出

### 查看可用机器

```bash
# 用户登录后查看分配给自己的在线机器
curl -b cookies.txt http://localhost:19888/api/remote/machines/available
```

### 创建远程会话

```bash
curl -b cookies.txt -X POST http://localhost:19888/api/remote/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "machine_id": "<machine_id>",
    "project_path": "/home/user/my-project",
    "cli_tool": "qwen-code-cli",
    "model": "qwen3-coder-plus",
    "title": "修复登录 Bug"
  }'
```

参数说明：

| 参数 | 必需 | 说明 |
|------|------|------|
| `machine_id` | 是 | 目标机器 ID |
| `project_path` | 是 | 远程机器上的工作目录 |
| `cli_tool` | 否 | CLI 工具名称，默认 `qwen-code-cli` |
| `model` | 否 | 要使用的模型名称 |
| `title` | 否 | 会话标题 |

### 发送消息

```bash
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "请帮我审查 main.py 的代码"}'
```

### 查看会话状态

```bash
curl -b cookies.txt \
  http://localhost:19888/api/remote/sessions/<session_id>
```

响应包含会话状态和所有输出：

```json
{
  "session": {
    "session_id": "uuid-xxx",
    "status": "active",
    "machine_id": "uuid-yyy",
    "project_path": "/home/user/my-project",
    "model": "qwen3-coder-plus",
    "total_tokens": 2300,
    "message_count": 3,
    "output": [
      {"data": "{\"type\":\"thinking\",...}", "stream": "stdout", "timestamp": "..."},
      {"data": "{\"type\":\"assistant\",...}", "stream": "stdout", "timestamp": "..."}
    ]
  }
}
```

### 停止会话

```bash
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/stop
```

### 暂停/恢复会话

```bash
# 暂停
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/pause

# 恢复
curl -b cookies.txt -X POST \
  http://localhost:19888/api/remote/sessions/<session_id>/resume
```

---

## API 参考

### 机器管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/api/remote/machines/register` | 生成机器注册令牌 | 系统管理员 |
| `GET` | `/api/remote/machines` | 列出机器（管理员看所有，用户看已分配的） | 登录用户 |
| `GET` | `/api/remote/machines/<id>` | 获取机器详情 | 登录用户（需有分配） |
| `DELETE` | `/api/remote/machines/<id>` | 注销机器 | 系统管理员 |
| `POST` | `/api/remote/machines/<id>/assign` | 分配用户 | 系统管理员/机器管理员 |
| `DELETE` | `/api/remote/machines/<id>/assign/<uid>` | 撤销用户权限 | 系统管理员/机器管理员 |
| `GET` | `/api/remote/machines/<id>/users` | 获取机器已分配用户列表 | 系统管理员/机器管理员 |

### API Key 管理（管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote/api-keys` | 列出所有 API Key（key 值被遮蔽） |
| `POST` | `/api/remote/api-keys` | 存储新的 API Key（加密后入库） |
| `DELETE` | `/api/remote/api-keys/<id>` | 删除指定 API Key |

### 会话管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/api/remote/machines/available` | 获取当前用户可用的在线机器 | 登录用户 |
| `POST` | `/api/remote/sessions` | 创建远程会话 | 登录用户 |
| `GET` | `/api/remote/sessions/<id>` | 获取会话状态和输出 | 会话所有者/系统管理员/机器管理员 |
| `POST` | `/api/remote/sessions/<id>/chat` | 发送消息 | 会话所有者/系统管理员/机器管理员 |
| `POST` | `/api/remote/sessions/<id>/stop` | 停止会话 | 会话所有者/系统管理员/机器管理员 |
| `POST` | `/api/remote/sessions/<id>/pause` | 暂停会话 | 会话所有者/系统管理员/机器管理员 |
| `POST` | `/api/remote/sessions/<id>/resume` | 恢复会话 | 会话所有者/系统管理员/机器管理员 |

### Agent 安装与文件分发

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote/agent/install.sh` | 获取安装脚本 |
| `GET` | `/api/remote/agent/files/<path>` | 获取 Agent 源码文件（供安装脚本下载） |

### Agent 通信

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/remote/agent/register` | Agent 注册（使用注册令牌） |
| `GET` | `/api/remote/agent/ws` | 已弃用的旧 WebSocket 传输端点；固定返回 `410 Gone`——请改用 HTTP 轮询 `/api/remote/agent/message` |
| `POST` | `/api/remote/agent/message` | HTTP 轮询通信（推荐） |
| `POST` | `/api/remote/usage-report` | Agent 上报 Token 用量 |

### LLM 代理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/remote/llm-proxy` | 代理 LLM API 请求 |
| `*` | `/api/remote/llm-proxy/<path>` | 代理任意路径的 LLM 请求 |

### 远程文件浏览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote/machines/<id>/browse` | 浏览远程机器文件系统（需要 WebSocket 支持，当前为预留接口） |

---

## 安全设计

### 认证与授权

```
┌─────────────┐     注册令牌(一次性)     ┌─────────────┐
│   管理员     │ ───────────────────────→ │  远程 Agent  │
│  (服务器端)  │                          │ (远程机器)   │
└─────────────┘                          └─────────────┘
       │                                       │
       │ 分配用户权限                            │ 代理令牌(短时/可配置)
       ↓                                       ↓
┌─────────────┐     session_token      ┌─────────────┐
│   用户       │ ←────────────────────→ │  Open ACE   │
│  (浏览器)   │                        │   服务器     │
└─────────────┘                        └─────────────┘
```

### 安全机制

| 领域 | 机制 |
|------|------|
| **机器注册** | 管理员生成 256 位随机一次性令牌。Agent 使用后令牌立即失效。 |
| **通信加密** | 所有通信建议通过 HTTPS（TLS）。Agent 通过令牌认证。 |
| **API Key 存储** | 使用 Fernet 对称加密（基于 AES-128-CBC + HMAC-SHA256）。加密密钥从 `OPENACE_ENCRYPTION_KEY` 环境变量经 SHA-256 派生，永不存入数据库。未安装 `cryptography` 包时回退为 Base64 编码（不安全，仅限开发环境）。 |
| **API Key 访问** | 远程 Agent 获得 HMAC-SHA256 签名的代理令牌，而非真实 Key。令牌由服务端签发并按 `jti` 登记，可回收，TTL 可按会话类型配置（通用会话默认 4 小时，HA pool 令牌默认 15 分钟）。真实 Key 永远不会写入远程机器磁盘。 |
| **访问控制** | `machine_assignments` 表控制哪些用户可使用哪些机器，`permission` 字段区分 `user`/`admin`。机器管理员可委托管理本机器的用户和会话。注册、注销和令牌生成仅限系统管理员。 |
| **会话隔离** | 用户只能访问自己的会话。系统管理员和机器管理员可查看/停止本机器上其他用户的会话。 |
| **配额统一** | 本地和远程会话共享同一 `quota_usage` 表，统一计费。 |
| **审计日志** | 所有远程操作通过现有 `AuditLogger` 记录。 |

### LLM 代理安全流程

1. 远程 CLI 发送请求到 `/api/remote/llm-proxy`，携带 `Authorization: Bearer <proxy_token>`
2. 服务器验证代理令牌签名（HMAC-SHA256）、服务端签发记录、有效期以及会话生命周期状态
3. 从 `api_key_store` 解密出真实 API Key
4. 替换 Authorization 头为真实 Key，转发到 LLM 提供商
5. 流式返回响应，解析 Token 用量并记录

**关键原则：API Key 永远不会传输到远程机器。**

---

## 支持的 CLI 工具

### 内置适配器

| CLI 工具 | 标识名 | 安装命令 | 环境变量 |
|----------|--------|---------|---------|
| Qwen Code | `qwen-code-cli` | `npm install -g @qwen-code/qwen-code@0.23.3` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| Claude Code | `claude-code` | `npm install -g @anthropic-ai/claude-code@latest` | `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` |
| OpenClaw | `openclaw` | `npm install -g openclaw@latest` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |

### CLI 启动参数

| 工具 | 参数 |
|------|------|
| Qwen Code | `qwen --print --output-format stream-json [--model MODEL]` |
| Claude Code | `claude --print --output-format stream-json [--model MODEL]` |
| OpenClaw | `openclaw --agent --json [--model MODEL]` |

### 代理路由原理

CLI 工具无需修改任何源代码。Agent 通过设置环境变量将 CLI 的 API 请求路由到 Open ACE 代理：

1. Agent 设置 CLI 的 **Base URL** 环境变量指向代理端点
2. Agent 设置 CLI 的 **API Key** 环境变量为短生命周期代理令牌
3. CLI 认为自己在直接调用 LLM API，实际请求被代理拦截
4. 代理验证令牌、注入真实 Key、转发请求、流式返回响应

**CLI 完全不知道自己正在被代理。**

### 扩展新的 CLI 工具

创建新的适配器只需继承 `BaseCLIAdapter` 并实现 6 个方法：

```python
# remote-agent/cli_adapters/my_tool.py

from .base import BaseCLIAdapter

class MyToolAdapter(BaseCLIAdapter):
    def get_install_command(self):
        return "npm install -g my-tool@latest"

    def check_installed(self):
        import shutil
        return shutil.which("my-tool") is not None

    def get_env_vars(self, proxy_url, proxy_token):
        return {
            "MY_TOOL_API_KEY": proxy_token,
            "MY_TOOL_BASE_URL": proxy_url,
        }

    def build_start_args(self, session_id, project_path, model=None):
        args = ["my-tool", "--agent"]
        if model:
            args.extend(["--model", model])
        return args

    def get_display_name(self):
        return "My Tool"

    def get_executable_name(self):
        return "my-tool"
```

然后在 `cli_adapters/__init__.py` 的 `ADAPTERS` 字典中注册：

```python
ADAPTERS = {
    ...
    "my-tool": MyToolAdapter,
}
```

对于未注册的 CLI 工具，系统会自动使用通用适配器（GenericAdapter），尝试用 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 路由请求。

---

## 环境变量参考

### 服务器端

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENACE_ENCRYPTION_KEY` | API Key / SMTP 密码的独立加密密钥（生产环境必需） | 仅开发环境提供 fallback |
| `SECRET_KEY` | Flask 会话密钥 | `dev-secret-key`（仅开发环境） |

### 远程 Agent

配置优先级：环境变量 > 配置文件 > 内置默认值。

| 变量 | 对应配置项 | 默认值 |
|------|-----------|--------|
| `OPENACE_SERVER_URL` | `server_url` | `http://localhost:19888` |
| `OPENACE_AGENT_TOKEN` | `agent_token` | 无 |
| `OPENACE_MACHINE_ID` | `machine_id` | 自动生成 UUID |
| `OPENACE_HEARTBEAT_INTERVAL` | `heartbeat_interval` | `60`（秒） |
| `OPENACE_RECONNECT_BASE_DELAY` | `reconnect_base_delay` | `1`（秒） |
| `OPENACE_RECONNECT_MAX_DELAY` | `reconnect_max_delay` | `60`（秒） |
| `OPENACE_MAX_SESSIONS` | `max_sessions` | `5` |
| `OPENACE_LOG_LEVEL` | `log_level` | `INFO` |

配置文件路径：`~/.open-ace-agent/config.json`

```json
{
    "server_url": "https://ace.example.com",
    "machine_id": "auto-generated-uuid",
    "machine_name": "My Server",
    "registration_token": "one-time-token",
    "cli_tool": "qwen-code-cli",
    "heartbeat_interval": 60,
    "reconnect_backoff_max": 60,
    "max_sessions": 5,
    "log_level": "INFO"
}
```

---

## 故障排查

### 安装脚本返回 404

**症状**：`curl -fsSL http://<server>:19888/api/remote/agent/install.sh` 返回 404。

**原因**：服务器代码中缺少安装脚本路由（`/api/remote/agent/install.sh` 和 `/api/remote/agent/files/<path>`）。

**解决方法**：
1. 确认服务器代码已更新到最新版本
2. 重启服务器
3. 如果仍 404，使用[手动安装](#手动安装)方式

### Agent 无法连接服务器

**症状**：Agent 日志显示连接失败或持续重连。

```bash
# 查看日志
tail -f ~/.open-ace-agent/agent.log

# 或通过 systemd
sudo journalctl -u open-ace-agent -f
```

**排查步骤**：

1. **网络连通性**：确认远程机器可以访问服务器端口
   ```bash
   curl -v http://<server>:19888/api/auth/login
   ```

2. **注册令牌过期**：令牌为一次性使用，注册成功后即失效。如果注册失败，需要管理员重新生成。

3. **服务器地址**：检查 `config.json` 中的 `server_url` 是否正确，注意不要有末尾斜杠。

### Agent 日志出现 WebSocket 错误 / 机器一直离线

**症状**：Agent 日志反复显示 `WebSocket error: Handshake status 405 METHOD NOT ALLOWED`，或 `/api/remote/agent/ws` 返回 `410 Gone`，机器状态始终为 offline。

**原因**：服务端已移除 WebSocket 传输。`GET /api/remote/agent/ws` 已弃用，固定返回 `410 Gone` 并提示改用 `/api/remote/agent/message`；HTTP 轮询是唯一传输方式。旧版本 Agent 仍会先尝试 WebSocket，且无法正确降级。

**解决方法**：
1. 把 Agent 升级到当前版本（仅 HTTP 轮询）；最简单的方式是用新的注册令牌重新安装
2. 重启 Agent 服务：`sudo systemctl restart open-ace-agent`
3. 查看日志确认已进入轮询模式：`sudo journalctl -u open-ace-agent -n 20 | grep "HTTP polling"`

### 机器显示 offline

**症状**：管理界面中机器状态为 `offline`。

**排查步骤**：

1. **Agent 进程**：确认 Agent 进程在运行
   ```bash
   # Linux
   sudo systemctl status open-ace-agent
   # macOS
   ps aux | grep agent.py
   ```

2. **心跳超时**：Agent 每 60 秒发送心跳，服务器 180 秒无心跳则标记 offline。检查网络是否稳定。

3. **重启 Agent**：
   ```bash
   sudo systemctl restart open-ace-agent
   ```

### 会话创建失败（400 错误）

**常见原因**：

1. **用户未被分配该机器**：管理员需要先分配用户权限
2. **机器不在线**：检查 Agent 是否运行、心跳是否正常
3. **`project_path` 缺失**：必须指定远程机器上的工作目录

### LLM 代理返回错误

**症状**：远程会话中 CLI 报 API 错误。

**排查步骤**：

1. **API Key 未存储**：管理员需要先在管理页面（管理模式 → 远程工作区 → API 密钥）存储对应 provider 的 API Key。注意：环境变量中的 `OPENAI_API_KEY` **不会**被远程工作区使用，两者完全独立
2. **配额耗尽**：检查用户配额，代理在转发前会检查配额
3. **代理令牌过期或被回收**：通用代理令牌默认有效期可配置（默认 4 小时），HA pool 令牌默认 15 分钟；当底层会话停止、完成或轮换时，旧令牌也会被服务端回收。重新创建或重新附着会话即可获取新令牌。

### CLI 工具未找到

**症状**：Agent 启动会话时报 `command not found`。

**解决方法**：

```bash
# 安装 Qwen Code CLI
npm install -g @qwen-code/qwen-code@0.23.3

# 或安装 Claude Code
npm install -g @anthropic-ai/claude-code@latest

# 验证安装
which qwen
which claude
```

### 数据库迁移

如果升级后远程工作区功能异常，检查迁移是否执行：

```bash
cd /path/to/open-ace
alembic upgrade head
```

`remote_machines`、`machine_assignments`、`api_key_store` 表定义在 baseline 模式（`schema/schema-postgres.sql`）中。

### 注册令牌一次性使用说明

注册令牌生成后仅可使用一次。以下场景会消耗令牌：

- 安装脚本执行成功
- 安装脚本注册步骤失败（如网络超时后重试）

如果令牌已消耗，需要管理员重新生成。建议：生成令牌后立即在远程机器上执行安装，避免令牌过期或误用。

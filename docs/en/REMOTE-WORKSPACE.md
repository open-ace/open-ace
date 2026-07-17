# Remote Workspace

> Lets users select a remote machine in the browser and start an AI coding session — the AI CLI runs directly on the remote machine, with no SSH and no repeated setup.

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

The Agent supports two transport modes with the server:

| Mode | Status | Use Case | Notes |
|------|--------|----------|-------|
| HTTP polling | Implemented, recommended | All scenarios | Agent POSTs actively; server returns pending commands; broadly compatible |
| WebSocket | Planned | High-real-time scenarios | Requires gevent/websocket worker; currently returns 501 |

The Agent tries WebSocket first and falls back to HTTP polling on failure. For the current release, HTTP polling is recommended.

---

## Deployment Topology and Runtime State

Remote Workspace is safe to run behind the Kubernetes reference deployment, but active remote sessions are not fully stateless yet:

- Remote agent WebSocket connections, terminal relay sockets, in-flight command queues, and short-lived output buffers are held in the web process that owns the active session.
- Kubernetes deployments must keep sticky routing enabled (`ClientIP` Service affinity and nginx cookie affinity in the shipped manifests) so browser and agent traffic for an active session returns to the same pod.
- If that pod restarts, durable records such as machines, sessions, messages, quotas, and audit entries remain in the database, but live terminal relay sockets and in-memory output buffers are interrupted.
- Removing sticky routing and supporting active-session failover requires the runtime-state externalization tracked in [#1782](https://github.com/open-ace/open-ace/issues/1782).

Tenant isolation currently covers users, remote machines, session ownership, machine permissions, and quotas. System administrators intentionally have global operational visibility. Broader tenant-aware schema/query hardening for historical analytics and project tables is tracked in [#1781](https://github.com/open-ace/open-ace/issues/1781).

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

Remote Workspace uses the Alembic migration `20260417_033_add_remote_workspace_tables.py`, which creates:

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
| **Storage** | Server config file / environment variables | `api_key_store` table (AES-256-GCM encrypted) |
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

| Parameter | Required | Description | Default |
|-----------|----------|-------------|---------|
| `--server URL` | Yes | Open ACE server URL | - |
| `--token TOKEN` | Yes | Admin-generated registration token | - |
| `--name NAME` | No | Display name for the machine | hostname |
| `--install-cli TOOL` | No | CLI tool to install | `qwen-code-cli` |
| `--dir DIR` | No | Install directory | `~/.open-ace-agent` |

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
npm install -g @qwen-code/qwen-code@latest

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
| `GET` | `/api/remote/agent/ws` | WebSocket real-time transport (requires gevent; currently returns 501) |
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
| Qwen Code | `qwen-code-cli` | `npm install -g @qwen-code/qwen-code@latest` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
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

### Agent Stuck in a WebSocket Reconnect Loop

**Symptom**: The Agent log repeatedly shows `WebSocket error: Handshake status 405 METHOD NOT ALLOWED`, and the machine stays offline.

**Cause**: The Flask dev server (Werkzeug) does not support WebSocket upgrade requests, and the Agent failed to fall back from WebSocket to HTTP polling correctly.

**Fix**:
1. Ensure the Agent code is up to date (contains the WebSocket → HTTP polling fallback fix)
2. Restart the Agent: `sudo systemctl restart open-ace-agent`
3. Check logs to confirm HTTP polling mode: `sudo journalctl -u open-ace-agent -n 20 | grep "HTTP polling"`
4. Or configure the Agent to use HTTP directly: do not set the WebSocket URL in `config.json`

**Long-term**: In production, enable WebSocket support by running under gevent or gunicorn + websocket worker.

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
npm install -g @qwen-code/qwen-code@latest

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

The migration `20260417_033_add_remote_workspace_tables.py` creates the `remote_machines`, `machine_assignments`, and `api_key_store` tables.

### Registration Token Is One-Time-Use

A registration token can only be used once after generation. It is consumed when:

- The install script runs successfully
- The install script's registration step fails (e.g. a network timeout followed by a retry)

If the token is consumed, the admin must generate a new one. Recommendation: generate a token and immediately run the installer on the remote machine, to avoid expiration or misuse.

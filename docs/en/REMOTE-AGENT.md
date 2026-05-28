# Remote Agent Guide

The remote agent is a Python daemon that runs on remote machines to provide AI coding tool access via the Open ACE platform.

## Architecture

```
┌──────────┐   HTTP Polling   ┌──────────────┐
│  Agent   │ ◄──────────────► │  Flask API   │
│ (daemon) │   1s interval     │              │
└────┬─────┘                  └──────────────┘
     │
     ├── subprocess ──► CLI Tool (claude/qwen/codex/openclaw)
     │
     └── WebSocket ──► Terminal Server (PTY)
                         │
                    Browser (xterm.js)
```

## Installation

### Linux / macOS

```bash
curl -fsSL https://<server>/api/remote/agent/install.sh | bash -s -- \
  --server https://your-server.com \
  --token <agent-token> \
  --name my-machine
```

Options:
- `--server` — Open ACE server URL (required)
- `--token` — Agent registration token (required)
- `--name` — Machine display name
- `--install-cli` — Default CLI tool (default: qwen-code-cli)
- `--dir` — Installation directory (default: `~/.open-ace-agent`)

### Windows

```powershell
.\install.ps1 -ServerUrl https://your-server.com -Token <agent-token>
```

### Requirements

- Python 3.8+
- websocket-client, requests, websockets (auto-installed)

## Configuration

Config file: `~/.open-ace-agent/config.json`

| Setting | Default | Description |
|---------|---------|-------------|
| server_url | `http://localhost:5000` | Open ACE server |
| heartbeat_interval | 60s | Heartbeat frequency |
| reconnect_base_delay | 1s | Initial reconnect delay |
| reconnect_max_delay | 60s | Max reconnect delay (exponential backoff) |
| buffer_size | 4096 | Terminal output buffer |
| max_sessions | 5 | Concurrent sessions |
| log_level | INFO | Logging level |
| skip_ssl_verify | true | Skip SSL verification |

Environment variable overrides: `OPENACE_SERVER_URL`, `OPENACE_AGENT_TOKEN`, `OPENACE_MACHINE_ID`, `OPENACE_HEARTBEAT_INTERVAL`, `OPENACE_MAX_SESSIONS`, `OPENACE_LOG_LEVEL`, `OPENACE_SKIP_SSL_VERIFY`

## Supported CLI Tools

| Tool | Executable | NPM Package | Config Location |
|------|-----------|-------------|-----------------|
| Claude Code | `claude` | `@anthropic-ai/claude-code` | `~/.claude/` |
| Qwen Code | `qwen` | `@qwen-code/qwen-code` | `~/.qwen/` |
| Codex | `codex` | `@openai/codex` | `~/.codex/config.toml` |
| OpenClaw | `openclaw` | N/A | — |

Each tool has a dedicated adapter in `cli_adapters/` that handles start arguments, environment variables, permission modes, and session resume.

## openace CLI

The `openace` command-line tool is installed alongside the agent:

| Command | Description |
|---------|-------------|
| `openace login [--token TOKEN]` | Authenticate to server |
| `openace logout` | Remove stored credentials |
| `openace status` | Show server URL, machine ID, login state |
| `openace menu` | Start interactive AI tool selector |
| `openace shell` | Start shell with proxy credentials |

## Terminal Server

The terminal server provides WebSocket-based PTY access:

- **Single PTY model** — One PTY per terminal server instance
- **Authentication** — HMAC token via query parameters
- **Reconnection** — PTY persists across WebSocket disconnects; 64KB output history for screen restore
- **Resize** — JSON control messages `{"type":"resize","cols":N,"rows":N}`
- **Environment** — Auto-injects `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` from proxy tokens

## Session Sync

The agent scans session history directories every 30s and syncs to the server:

| Tool | Directory |
|------|-----------|
| Claude Code | `~/.claude/projects/` (JSONL) |
| Qwen Code | `~/.qwen/projects/` (JSONL) |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |

Sync state is tracked in `~/.open-ace-agent/session_sync_state.json`.

## Codex CLI Specifics

- Config format: **TOML** (`~/.codex/config.toml`), not JSON
- Permission modes: `plan` → `--ask-for-approval untrusted`, `auto` → `--dangerously-bypass-approvals-and-sandbox`
- Non-interactive mode: `codex exec --json --sandbox read-only`
- Session files: JSONL with event types `session_meta`, `turn_context`, `response_item`
- Content blocks: `input_text`, `output_text`, `reasoning`, `function_call`

## Daemon Commands

The agent handles these commands from the server:

| Command | Description |
|---------|-------------|
| `start_session` | Start a new CLI session |
| `send_message` | Send user message to active session |
| `stop_session` | Terminate CLI session |
| `pause_session` | SIGSTOP the CLI process |
| `resume_session` | SIGCONT the CLI process |
| `permission_response` | Forward user's permission decision |
| `update_permission_mode` | Change session permission mode |
| `update_model` | Switch AI model |
| `start_terminal` | Launch WebSocket terminal server |
| `stop_terminal` | Shutdown terminal server |

## Troubleshooting

**Agent won't connect:**
- Check `OPENACE_SERVER_URL` is reachable
- Verify agent token is valid
- Check `~/.open-ace-agent/agent.log`

**CLI tool not found:**
- Ensure the tool is installed globally (`which claude` / `which qwen` / `which codex`)
- Check PATH includes npm global bin directory

**Terminal not connecting:**
- Verify WebSocket port is not blocked by firewall
- Check terminal server process is running (`ps aux | grep terminal_server`)
- Review HMAC token in `~/.open-ace-agent/.terminal_sessions/`

**Session sync not working:**
- Check `~/.open-ace-agent/session_sync_state.json` is writable
- Verify session directories exist and contain JSONL files

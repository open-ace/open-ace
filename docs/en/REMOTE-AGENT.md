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
     └── WebSocket ──► Terminal Server (PTY / piped subprocess)
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
- `--ca-bundle PATH` — PEM CA bundle for a private or self-signed server
- `--insecure-skip-tls-verify` — Explicitly disable TLS verification (dangerous)

When the installer endpoint itself uses a private CA, bootstrap curl with the
same bundle and pass it through to the installer:

```bash
curl --cacert /path/to/ca.pem -fsSL https://<server>/api/remote/agent/install.sh | \
  bash -s -- --server https://<server> --token <agent-token> --ca-bundle /path/to/ca.pem
```

### Windows

```powershell
.\install.ps1 -ServerUrl https://your-server.com -RegistrationToken <agent-token>
```

For a private CA, add `-CaBundlePath C:\path\to\ca.pem`. The emergency
`-InsecureSkipTlsVerify` switch is intentionally explicit and should only be
used for short-lived testing.

### Requirements

- Python 3.8+
- websocket-client, requests, websockets (auto-installed)

## Configuration

Config file: `~/.open-ace-agent/config.json`

| Setting | Default | Description |
|---------|---------|-------------|
| server_url | `http://localhost:19888` | Open ACE server |
| heartbeat_interval | 60s | Heartbeat frequency |
| reconnect_base_delay | 1s | Initial reconnect delay |
| reconnect_max_delay | 60s | Max reconnect delay (exponential backoff) |
| buffer_size | 4096 | Terminal output buffer |
| max_sessions | 5 | Concurrent sessions |
| log_level | INFO | Logging level |
| skip_ssl_verify | false | Skip TLS verification; non-local HTTPS also requires an explicit CLI acknowledgement |
| allow_insecure_tls | false | Administrator policy gate for the explicit insecure switch |
| ca_bundle_path | null | PEM CA bundle for private/self-signed certificates |

Environment variable overrides: `OPENACE_SERVER_URL`, `OPENACE_AGENT_TOKEN`, `OPENACE_MACHINE_ID`, `OPENACE_HEARTBEAT_INTERVAL`, `OPENACE_MAX_SESSIONS`, `OPENACE_LOG_LEVEL`, `OPENACE_SKIP_SSL_VERIFY`, `OPENACE_ALLOW_INSECURE_TLS`, `OPENACE_CA_BUNDLE_PATH`

### TLS policy and migration

New installations verify server certificates by default. For an internal CA,
install with `--ca-bundle /path/to/ca.pem` (or `-CaBundlePath` on Windows), or
set `ca_bundle_path` in `config.json`. The same CA is used by agent HTTP calls,
terminal relay WebSockets, `openace login/menu/shell`, installer downloads, and
registration.

For a non-local HTTPS server, a legacy configuration containing
`"skip_ssl_verify": true` no longer starts silently. Prefer replacing it with a
CA bundle. If verification must be disabled temporarily, start the daemon with
`python agent.py --insecure-skip-tls-verify`; the installer equivalents persist
both `skip_ssl_verify=true` and the administrator approval
`allow_insecure_tls=true`, then add the explicit service argument. Manual use
requires the same two-step approval: policy plus CLI flag. Administrators can
disable the escape hatch by leaving `allow_insecure_tls=false`. This mode prints
a prominent warning and exposes credentials and commands to man-in-the-middle
attacks.

Use `python agent.py --ca-bundle /path/to/ca.pem` for a one-run CA override, and
`openace login|menu|shell --ca-bundle /path/to/ca.pem` for a CLI override. Run
`openace config-check` to inspect the persisted TLS configuration.

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
| `openace login [--token TOKEN] [--ca-bundle PATH]` | Authenticate to server |
| `openace logout` | Remove stored credentials |
| `openace status` | Show server URL, machine ID, login state |
| `openace menu [--ca-bundle PATH]` | Start interactive AI tool selector |
| `openace shell [--ca-bundle PATH]` | Start shell with proxy credentials |
| `openace config-check` | Validate the persisted TLS configuration |

## Terminal Server

The terminal server provides WebSocket-based terminal access:

- **Terminal process model** — Uses a persistent PTY on Linux/macOS and a persistent piped subprocess on Windows
- **Authentication** — HMAC token via query parameters
- **Reconnection** — Terminal process persists across WebSocket disconnects; 64KB output history for screen restore
- **Resize** — JSON control messages `{"type":"resize","cols":N,"rows":N}`
- **Environment** — Auto-injects `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` from proxy tokens

On Windows, `openace menu` uses a numbered text menu instead of the Unix arrow-key raw-terminal UI so the same workflow remains available in PowerShell/cmd and browser terminals.

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

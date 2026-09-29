# Open ACE Remote Agent

Python daemon that connects a remote machine to an Open ACE server over HTTP
polling, runs AI coding CLI tools as managed subprocesses, and provides
web-terminal and session-sync support. For end-to-end setup, TLS policy, and
troubleshooting see **docs/guide/REMOTE_AGENT.md** (agent side) and
**docs/guide/REMOTE_WORKSPACE.md** (server side).

## Quick start

```bash
curl -fsSL https://<server>/api/remote/agent/install.sh | bash -s -- \
  --server https://<server> --token <registration-token>
```

The installer downloads this directory to `~/.open-ace-agent/`, installs
`requirements.txt`, registers the machine, and sets up a system service. Manual
use: `pip3 install -r requirements.txt && python3 agent.py`.

## Module overview

### Core daemon

| File | Purpose |
|------|---------|
| `agent.py` | Main loop: HTTP polling for commands (`start_session`, `send_message`, `stop_terminal`, ...), heartbeats, terminal/code-server lifecycle, token rotation |
| `config.py` | Config loading (`~/.open-ace-agent/config.json`) with environment-variable overrides |
| `executor.py` | CLI subprocess lifecycle: start, feed input, non-blocking output reads, stop |
| `system_info.py` | Hardware/OS detection and installed-CLI capability report |
| `constants.py` | Credential-carrying environment keys scrubbed from agent subprocesses |

### CLI adapters (`cli_adapters/`)

| File | Tool |
|------|------|
| `base.py` | Abstract adapter: install, configure, launch, permission modes, resume |
| `claude_code.py` | Claude Code (`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`) |
| `qwen_code.py` | Qwen Code (`OPENAI_API_KEY`/`OPENAI_BASE_URL`) |
| `codex_cli.py` | Codex CLI (TOML config in `~/.codex/config.toml`) |
| `openclaw.py` | OpenClaw (OpenAI-compatible proxy routing) |
| `zcode.py` | ZCode CLI (non-stream-JSON modes) |
| `usage_parser.py` | Shared stream-json token-usage extraction (cumulative-aware) |
| `codex_jsonl_parser.py` | Codex JSONL session parser shared with server-side import |

All adapters route LLM calls through the server proxy; real API keys never
reach the remote machine (`env_security.py` enforces the scrub-and-inject
policy for short-lived proxy tokens).

### Terminal and sessions

| File | Purpose |
|------|---------|
| `terminal_server.py` | Standalone asyncio WebSocket terminal server (single persistent PTY; piped subprocess on Windows) |
| `terminal_relay.py` | Outbound WebSocket relay so the backend can reach agents on private networks |
| `websocket_proxy.py` | Browser-to-agent WebSocket proxy for terminal access |
| `terminal_menu.py` | Interactive AI-tool selector run as the initial PTY process |
| `zcode_app_server.py` | Drives ZCode in persistent `app-server` mode over stdio JSON |
| `session_sync.py` | Scans `~/.claude/projects/`, `~/.qwen/projects/`, `~/.codex/sessions/` and syncs session history to the server |
| `cli_settings.py` | Applies CLI tool settings and Codex bearer-token handling |
| `env_security.py` | Autonomous-agent environment scrubbing (Issue #2019) |
| `openace_cli.py` | `openace` command: `login`, `logout`, `status`, `menu`, `shell`, `config-check` |

### TLS and install

| File | Purpose |
|------|---------|
| `tls_config.py` | Unified TLS config for the daemon and subprocesses (CA bundle, explicit insecure switch) |
| `install.sh` / `install.ps1` | One-line installers served by `/api/remote/agent/install.*` |
| `start-agent.sh` / `start-agent.ps1` / `start-agent.cmd` | Start/stop/status and auto-start setup |
| `uninstall.sh` / `uninstall.ps1` | Remove the agent and its service |
| `configure-code-server-proxy.ps1` | Windows code-server proxy configuration helper |
| `requirements.txt` | Runtime dependencies (websocket-client, requests, websockets, tomli <3.11, psutil) |

# Remote Agent Guide — 远程代理指南

[English](#english) | [中文](#中文)

---

## English

The remote agent is a Python daemon that runs on remote machines to provide AI coding tool access via the Open ACE platform.

> This page covers the **agent perspective** — installing and running the daemon, TLS policy, CLI adapters, the terminal server, and the `openace` command. Server-side setup (registration tokens, API-key store, quotas, API reference) is documented in [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md), which links back here.

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
- websocket-client, requests, websockets, psutil; plus tomli on Python < 3.11 (auto-installed from `requirements.txt`)

## Starting and Managing the Agent

After installation, you can use the start scripts to manage the agent process:

### Linux / macOS

```bash
# Start the agent (skips if already running)
bash ~/.open-ace-agent/start-agent.sh

# Check agent status
bash ~/.open-ace-agent/start-agent.sh --status

# Stop the agent
bash ~/.open-ace-agent/start-agent.sh --stop

# Configure auto-start on boot (requires sudo to create systemd service)
bash ~/.open-ace-agent/start-agent.sh --auto-start
```

**Auto-start details:**
- Systems with systemd (Ubuntu 16.04+, CentOS 7+, RHEL 7+): Creates a systemd service that starts on boot and restarts on crash
- Environments without systemd (e.g., WSL2): Uses crontab `@reboot` for auto-start

### Windows

```powershell
# Start the agent (skips if already running)
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1"

# Check agent status
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -Status

# Stop the agent
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -Stop

# Configure auto-start on login (Windows Task Scheduler)
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -InstallAutoStart
```

**Auto-start details:**
- Uses Windows Task Scheduler to start automatically on login
- Task name: `OpenACEAgent`
- Can be managed in Task Scheduler

### Shortcut (Windows)

Windows users can also use the batch wrapper:

```cmd
%USERPROFILE%\.open-ace-agent\start-agent.cmd
```

## Configuration

Config file: `~/.open-ace-agent/config.json`

| Setting | Default | Description |
|---------|---------|-------------|
| server_url | `http://localhost:19888` | Open ACE server |
| heartbeat_interval | 60s | Heartbeat frequency |
| reconnect_base_delay | 1s | Initial reconnect delay |
| reconnect_max_delay | 60s | Max reconnect delay (exponential backoff) |
| output_buffer_size | 4096 | Terminal output buffer |
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
| ZCode | `zcode` | N/A (bundled with the ZCode desktop app) | `~/.zcode/` |

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

### Known limitations of the Windows pipe mode

On Linux/macOS the terminal server spawns the shell on a real PTY. On Windows it
uses a piped subprocess instead (stdin/stdout are anonymous pipes, not a
pseudo-terminal). Because there is no tty attached to stdin, the Windows pipe
mode has these limitations — this is expected behavior, not a regression:

- **No interactive tty semantics on stdin** — there is no echo, no line editing,
  no Tab completion, and no prompt redraw. Input is forwarded to the shell as
  raw bytes once the client submits it (typically on Enter).
- **Raw-byte CJK / wide-char input** — multi-byte input is sent as raw bytes
  into a non-tty stdin, so IME composition and wide-char readline handling are
  not available the way they are on a Unix PTY.
- **Resize does not take effect** — there is no tty to apply a window-size
  change to, so `{"type":"resize",...}` only records the requested size and the
  shell keeps wrapping output at the original width. The server logs a one-time
  notice on the first resize. Writing an ANSI size sequence is deliberately
  avoided: stdin is a pipe, so those bytes would be consumed as shell input and
  pollute the session. Real resize support requires ConPTY (`pywinpty`/winpty
  or the Win32 API) and is tracked as future work.
- **Persistence and history still work** — the piped shell still persists across
  WebSocket reconnects, and the 64KB output history is replayed for screen
  restore on reconnection.

### Process-tree cleanup (Windows)

To make shutdown reliable, the Windows terminal server binds the shell process
tree to a Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. When the
server exits (normal shutdown, hard-kill, or crash), the kernel reaps the whole
tree — including grandchildren holding the stdout write-end — which is what lets
the output relay stop cleanly. The soft-kill path (`kill_pty`) closes the Job
handle first, falling back to `taskkill /T /F` only when no Job is bound.

Narrow residual (accepted): if Job creation *and* `taskkill /T` both fail, the
shell tree may be orphaned (it then needs manual cleanup; the agent's
`proc.kill()` targets the terminal server, not the orphaned shell), and the
output relay may stay pinned until the process is force-killed. An
agent-initiated `stop_terminal` is reaped by the agent's `proc.kill()` watchdog;
agent shutdown intentionally leaves terminal servers running, so a natural-exit
under this dual-failure may wedge a terminal server until that terminal is
restarted.

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

---

## 中文

远程代理是一个运行在远程机器上的 Python 守护进程，通过 Open ACE 平台提供 AI 编码工具访问。

> 本页从 **Agent 视角**展开——守护进程的安装与运行、TLS 策略、CLI 适配器、终端服务器和 `openace` 命令。服务器侧配置（注册令牌、API Key 存储、配额、API 参考）见 [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md)，该页也链接回本页。

## 架构

```
┌──────────┐   HTTP Polling   ┌──────────────┐
│  Agent   │ ◄──────────────► │  Flask API   │
│ (daemon) │   1s interval     │              │
└────┬─────┘                  └──────────────┘
     │
     ├── subprocess ──► CLI Tool (claude/qwen/codex/openclaw)
     │
     └── WebSocket ──► Terminal Server（PTY / 管道子进程）
                         │
                    Browser (xterm.js)
```

## 安装

### Linux / macOS

```bash
curl -fsSL https://<server>/api/remote/agent/install.sh | bash -s -- \
  --server https://your-server.com \
  --token <agent-token> \
  --name my-machine
```

参数说明：
- `--server` — Open ACE 服务器 URL（必需）
- `--token` — 代理注册令牌（必需）
- `--name` — 机器显示名称
- `--install-cli` — 默认 CLI 工具（默认：qwen-code-cli）
- `--dir` — 安装目录（默认：`~/.open-ace-agent`）
- `--ca-bundle PATH` — 私有 CA 或自签名证书使用的 PEM CA bundle
- `--insecure-skip-tls-verify` — 显式关闭 TLS 验证（危险）

如果安装端点本身使用私有 CA，请让 curl 与安装器使用同一个 CA：

```bash
curl --cacert /path/to/ca.pem -fsSL https://<server>/api/remote/agent/install.sh | \
  bash -s -- --server https://<server> --token <agent-token> --ca-bundle /path/to/ca.pem
```

### Windows

```powershell
.\install.ps1 -ServerUrl https://your-server.com -RegistrationToken <agent-token>
```

私有 CA 环境请增加 `-CaBundlePath C:\path\to\ca.pem`。应急参数
`-InsecureSkipTlsVerify` 必须显式指定，只应用于短期测试。

### 系统要求

- Python 3.8+
- websocket-client、requests、websockets、psutil；Python < 3.11 还需要 tomli（通过 `requirements.txt` 自动安装）

## 启动与管理

安装完成后，可以使用启动脚本便捷地管理 Agent 进程：

### Linux / macOS

```bash
# 启动 Agent（若已在运行则跳过）
bash ~/.open-ace-agent/start-agent.sh

# 查看 Agent 运行状态
bash ~/.open-ace-agent/start-agent.sh --status

# 停止 Agent
bash ~/.open-ace-agent/start-agent.sh --stop

# 配置开机自启（需要 sudo 权限创建 systemd 服务）
bash ~/.open-ace-agent/start-agent.sh --auto-start
```

**开机自启说明：**
- 支持 systemd 的系统（Ubuntu 16.04+、CentOS 7+、RHEL 7+）：创建 systemd 服务，开机自启且崩溃自动重启
- 不支持 systemd 的环境（如 WSL2）：使用 crontab `@reboot` 实现开机自启

### Windows

```powershell
# 启动 Agent（若已在运行则跳过）
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1"

# 查看 Agent 运行状态
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -Status

# 停止 Agent
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -Stop

# 配置开机自启（Windows 计划任务，登录时自动启动）
powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.open-ace-agent\start-agent.ps1" -InstallAutoStart
```

**开机自启说明：**
- 使用 Windows 计划任务实现登录时自动启动
- 任务名称：`OpenACEAgent`
- 可在"任务计划程序"中查看和管理

### 快捷方式（Windows）

Windows 用户也可以使用批处理包装器：

```cmd
%USERPROFILE%\.open-ace-agent\start-agent.cmd
```

## 配置

配置文件：`~/.open-ace-agent/config.json`

| 设置 | 默认值 | 说明 |
|------|--------|------|
| server_url | `http://localhost:19888` | Open ACE 服务器 |
| heartbeat_interval | 60s | 心跳频率 |
| reconnect_base_delay | 1s | 初始重连延迟 |
| reconnect_max_delay | 60s | 最大重连延迟（指数退避） |
| output_buffer_size | 4096 | 终端输出缓冲 |
| max_sessions | 5 | 并发会话数 |
| log_level | INFO | 日志级别 |
| skip_ssl_verify | false | 跳过 TLS 验证；非本机 HTTPS 还必须通过 CLI 显式确认 |
| allow_insecure_tls | false | 管理员是否允许显式 insecure 开关 |
| ca_bundle_path | null | 私有 CA/自签名证书使用的 PEM CA bundle |

环境变量覆盖：`OPENACE_SERVER_URL`、`OPENACE_AGENT_TOKEN`、`OPENACE_MACHINE_ID`、`OPENACE_HEARTBEAT_INTERVAL`、`OPENACE_MAX_SESSIONS`、`OPENACE_LOG_LEVEL`、`OPENACE_SKIP_SSL_VERIFY`、`OPENACE_ALLOW_INSECURE_TLS`、`OPENACE_CA_BUNDLE_PATH`

### TLS 策略与旧配置迁移

新安装默认验证服务端证书。内网 CA 环境请使用
`--ca-bundle /path/to/ca.pem`（Windows 使用 `-CaBundlePath`），或在
`config.json` 中设置 `ca_bundle_path`。Agent HTTP、终端 Relay WebSocket、
`openace login/menu/shell`、安装文件下载和注册请求会使用同一 CA。

对于非本机 HTTPS 服务，旧配置中的 `"skip_ssl_verify": true` 不再静默
启动。首选做法是改用 CA bundle。仅在短期排障确需关闭验证时，使用
`python agent.py --insecure-skip-tls-verify`；安装脚本的同名参数会保存配置
中的 `skip_ssl_verify=true` 和管理员批准项 `allow_insecure_tls=true`，并为
系统服务增加显式参数。手工运行也必须同时具备策略批准与 CLI 参数；管理员
保持 `allow_insecure_tls=false` 即可禁用该逃生开关。该模式会显示醒目警告，
并存在中间人窃取凭据和篡改命令的风险。

单次覆盖 CA 可使用 `python agent.py --ca-bundle /path/to/ca.pem`；CLI 可用
`openace login|menu|shell --ca-bundle /path/to/ca.pem`。运行
`openace config-check` 可检查持久化的 TLS 配置。

## 支持的 CLI 工具

| 工具 | 可执行文件 | NPM 包 | 配置位置 |
|------|-----------|--------|----------|
| Claude Code | `claude` | `@anthropic-ai/claude-code` | `~/.claude/` |
| Qwen Code | `qwen` | `@qwen-code/qwen-code` | `~/.qwen/` |
| Codex | `codex` | `@openai/codex` | `~/.codex/config.toml` |
| OpenClaw | `openclaw` | N/A | — |
| ZCode | `zcode` | N/A（随 ZCode 桌面应用分发） | `~/.zcode/` |

每个工具在 `cli_adapters/` 中有专用适配器，处理启动参数、环境变量、权限模式和会话恢复。

## openace 命令行工具

`openace` 命令行工具随代理一起安装：

| 命令 | 说明 |
|------|------|
| `openace login [--token TOKEN] [--ca-bundle PATH]` | 登录到服务器 |
| `openace logout` | 删除存储的凭证 |
| `openace status` | 显示服务器 URL、机器 ID、登录状态 |
| `openace menu [--ca-bundle PATH]` | 启动交互式 AI 工具选择器 |
| `openace shell [--ca-bundle PATH]` | 启动带代理凭证的 shell |
| `openace config-check` | 校验持久化的 TLS 配置 |

## 终端服务器

终端服务器提供基于 WebSocket 的终端访问：

- **终端进程模型** — Linux/macOS 使用持久 PTY，Windows 使用持久的管道子进程
- **认证** — 通过查询参数的 HMAC token
- **重连** — 终端进程在 WebSocket 断开后保持；64KB 输出历史用于屏幕恢复
- **调整大小** — JSON 控制消息 `{"type":"resize","cols":N,"rows":N}`
- **环境** — 自动从代理 token 注入 `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`

在 Windows 上，`openace menu` 会使用编号式文本菜单，而不是 Unix 上基于原始终端的方向键菜单，这样在 PowerShell/cmd 和浏览器终端里都可以继续使用同一套流程。

### Windows pipe 模式已知限制

在 Linux/macOS 上，终端服务器在真正的 PTY 上启动 shell。Windows 上则改用管道
子进程（stdin/stdout 是匿名管道，而非伪终端）。由于 stdin 没有挂接 tty，Windows
pipe 模式存在以下限制 —— 这是预期行为，不是回归：

- **stdin 不具备交互式 tty 语义** —— 没有回显、没有行编辑、没有 Tab 补全、也没有
  提示符重绘。输入以原始字节的形式在客户端提交时（通常是按下回车）转发给 shell。
- **原始字节 CJK / 宽字符输入** —— 多字节输入以原始字节送入非 tty 的 stdin，因此
  无法像 Unix PTY 那样支持 IME 组合输入与宽字符 readline 处理。
- **resize 不生效** —— 没有 tty 可用于应用窗口尺寸变更，因此
  `{"type":"resize",...}` 仅记录请求的尺寸，shell 仍按原宽度换行。服务器会在首次
  resize 时打印一次提示。刻意不写入 ANSI 尺寸序列：stdin 是管道，这些字节会被当作
  shell 输入消费、污染会话。真正的 resize 支持需要 ConPTY（`pywinpty`/winpty 或
  Win32 API），列为后续工作。
- **持久化与历史仍可用** —— 管道 shell 仍可跨 WebSocket 重连保持，并在重连时回放
  64KB 输出历史以恢复屏幕。

### 进程树清理（Windows）

为了让关闭更可靠，Windows 终端服务器用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 把
shell 进程树绑定到 Win32 Job Object。服务器退出时（正常关闭、硬杀或崩溃），内核会
回收整棵树 —— 包括持有 stdout 写端的孙进程 —— 这正是让输出 relay 干净停止的关键。
软杀路径（`kill_pty`）优先关闭 Job 句柄，仅在没有绑定 Job 时才回退到
`taskkill /T /F`。

窄边界残留（已接受）：如果 Job 创建**且** `taskkill /T` 同时失败，shell 树可能孤儿
化（届时需要人工清理；agent 的 `proc.kill()` 针对的是 terminal server，而非孤儿
shell），输出 relay 也可能滞留直到进程被强杀。agent 主动发起的 `stop_terminal` 由
agent 的 `proc.kill()` 看门狗兜底；agent 关闭按设计会保留 terminal server 运行，因此
在此双失败下自然退出可能让某个 terminal server 卡住，直到该 terminal 被重启。

## 会话同步

代理每 30 秒扫描会话历史目录并同步到服务器：

| 工具 | 目录 |
|------|------|
| Claude Code | `~/.claude/projects/`（JSONL） |
| Qwen Code | `~/.qwen/projects/`（JSONL） |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |

同步状态追踪文件：`~/.open-ace-agent/session_sync_state.json`。

## Codex CLI 特殊说明

- 配置格式：**TOML**（`~/.codex/config.toml`），非 JSON
- 权限模式：`plan` → `--ask-for-approval untrusted`，`auto` → `--dangerously-bypass-approvals-and-sandbox`
- 非交互模式：`codex exec --json --sandbox read-only`
- 会话文件：JSONL，包含事件类型 `session_meta`、`turn_context`、`response_item`
- 内容块：`input_text`、`output_text`、`reasoning`、`function_call`

## 守护进程命令

代理处理来自服务器的以下命令：

| 命令 | 说明 |
|------|------|
| `start_session` | 启动新的 CLI 会话 |
| `send_message` | 向活动会话发送用户消息 |
| `stop_session` | 终止 CLI 会话 |
| `pause_session` | SIGSTOP CLI 进程 |
| `resume_session` | SIGCONT CLI 进程 |
| `permission_response` | 转发用户的权限决定 |
| `update_permission_mode` | 更改会话权限模式 |
| `update_model` | 切换 AI 模型 |
| `start_terminal` | 启动 WebSocket 终端服务器 |
| `stop_terminal` | 关闭终端服务器 |

## 故障排查

**代理无法连接：**
- 检查 `OPENACE_SERVER_URL` 是否可达
- 验证代理 token 是否有效
- 检查 `~/.open-ace-agent/agent.log`

**CLI 工具未找到：**
- 确保工具已全局安装（`which claude` / `which qwen` / `which codex`）
- 检查 PATH 是否包含 npm 全局 bin 目录

**终端无法连接：**
- 验证 WebSocket 端口未被防火墙阻止
- 检查终端服务器进程是否运行（`ps aux | grep terminal_server`）
- 查看 `~/.open-ace-agent/.terminal_sessions/` 中的 HMAC token

**会话同步不工作：**
- 检查 `~/.open-ace-agent/session_sync_state.json` 是否可写
- 验证会话目录是否存在并包含 JSONL 文件

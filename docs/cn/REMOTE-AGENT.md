# 远程代理指南

远程代理是一个运行在远程机器上的 Python 守护进程，通过 Open ACE 平台提供 AI 编码工具访问。

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
- websocket-client、requests、websockets（自动安装）

## 配置

配置文件：`~/.open-ace-agent/config.json`

| 设置 | 默认值 | 说明 |
|------|--------|------|
| server_url | `http://localhost:19888` | Open ACE 服务器 |
| heartbeat_interval | 60s | 心跳频率 |
| reconnect_base_delay | 1s | 初始重连延迟 |
| reconnect_max_delay | 60s | 最大重连延迟（指数退避） |
| buffer_size | 4096 | 终端输出缓冲 |
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

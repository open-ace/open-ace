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
- `--insecure-skip-tls-verify` — 跳过 TLS 证书验证（不推荐用于生产环境）
- `--ca-bundle PATH` — 自定义 CA bundle 文件路径

### Windows

```powershell
.\install.ps1 -ServerUrl https://your-server.com -RegistrationToken <agent-token>
```

参数说明：
- `-InsecureSkipTlsVerify` — 跳过 TLS 证书验证（不推荐用于生产环境）
- `-CaBundlePath PATH` — 自定义 CA bundle 文件路径

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
| skip_ssl_verify | **false** | 跳过 SSL 验证（默认关闭，保持安全） |
| ca_bundle_path | null | 自定义 CA bundle 路径 |
| insecure_mode_allowed | true | 是否允许 insecure 模式 |

环境变量覆盖：`OPENACE_SERVER_URL`、`OPENACE_AGENT_TOKEN`、`OPENACE_MACHINE_ID`、`OPENACE_HEARTBEAT_INTERVAL`、`OPENACE_MAX_SESSIONS`、`OPENACE_LOG_LEVEL`、`OPENACE_SKIP_SSL_VERIFY`、`OPENACE_CA_BUNDLE_PATH`、`OPENACE_INSECURE_MODE_ALLOWED`

## SSL/TLS 安全配置

### 安全最佳实践

**默认行为：** 远程代理默认验证 TLS 证书，确保与控制面的通信安全。

**推荐配置：**
- 使用受信任 CA 签发的证书（如 Let's Encrypt）
- 私有化部署使用内网 CA 时，配置 `ca_bundle_path`

**不推荐：**
- 在生产环境使用 `skip_ssl_verify: true`
- 在非 localhost URL 下跳过 TLS 验证

### 自签名证书配置

如果服务器使用自签名证书或内网 CA，请按以下步骤配置：

**1. 准备 CA bundle 文件**

```bash
# 导出内网 CA 证书
openssl x509 -in /path/to/ca.crt -out >> ~/.open-ace-agent/ca-bundle.crt

# 或合并系统 CA + 企业 CA
cat /etc/ssl/certs/ca-certificates.crt /path/to/enterprise-ca.crt > ~/.open-ace-agent/ca-bundle.crt
```

**2. 配置方式（任选其一）**

```bash
# 方式一：配置文件
echo '{"ca_bundle_path": "~/.open-ace-agent/ca-bundle.crt"}' >> ~/.open-ace-agent/config.json

# 方式二：环境变量
export OPENACE_CA_BUNDLE_PATH=~/.open-ace-agent/ca-bundle.crt

# 方式三：命令行参数
--ca-bundle ~/.open-ace-agent/ca-bundle.crt
```

### 开发/测试环境

对于使用自签名证书的开发环境，可以临时跳过 TLS 验证：

```bash
# 安装时
curl -fsSL https://<server>/api/remote/agent/install.sh | bash -s -- \
  --server https://your-server.com \
  --token <token> \
  --insecure-skip-tls-verify

# 或通过环境变量
export OPENACE_SKIP_SSL_VERIFY=true
```

**注意：** 启用此模式时，日志会显示 `[SECURITY WARNING]` 告警。

### 生产环境强化

在生产环境中，可以完全禁止 insecure 模式：

```json
{
  "insecure_mode_allowed": false
}
```

配置后，如果 `skip_ssl_verify=true`，代理将拒绝启动并输出错误。

## 从旧版本升级

### 从 `skip_ssl_verify: true` 升级

如果您之前的配置包含 `"skip_ssl_verify": true`：

**场景 A：使用公网 CA 证书**
1. 直接升级即可
2. 代理将自动验证 TLS

**场景 B：使用自签名证书**
1. 获取内网 CA 的 CA bundle 文件
2. 配置 `ca_bundle_path`
3. 重启代理

**场景 C：临时跳过验证**
1. 设置 `OPENACE_SKIP_SSL_VERIFY=true` 环境变量
2. 检查日志中的 `[SECURITY WARNING]`
3. 尽快配置 CA bundle

### 回滚方案

如果升级后连接失败：

```bash
# 快速回滚
export OPENACE_SKIP_SSL_VERIFY=true

# 或恢复旧版本配置
```

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
| `openace login [--token TOKEN]` | 登录到服务器 |
| `openace logout` | 删除存储的凭证 |
| `openace status` | 显示服务器 URL、机器 ID、登录状态 |
| `openace menu` | 启动交互式 AI 工具选择器 |
| `openace shell` | 启动带代理凭证的 shell |

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
- 如果出现 SSL 错误，检查日志中的诊断信息

**SSL/TLS 错误：**
- 证书链不完整：确保服务器证书包含中间 CA
- 主机名不匹配：检查证书的 SAN/CN
- 自签名证书：配置 `ca_bundle_path` 或临时使用 `OPENACE_SKIP_SSL_VERIFY=true`

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
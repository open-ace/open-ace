# Remote Workspace 远程工作区

> 让用户在浏览器中选择远程机器，启动 AI 编码会话，AI CLI 直接运行在远程机器上——无需 SSH，无需重复配置。

## 目录

- [概述](#概述)
- [架构](#架构)
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
| API Key 代理 | Key 加密存储在服务器，远程 Agent 只拿到短期代理令牌 |
| 一行安装 | `curl ... \| bash` 完成远程机器部署 |
| 统一配额 | 本地和远程会话共享同一套配额体系 |
| 自动重连 | Agent 断线后指数退避自动重连 |

---

## 架构

```
[浏览器 UI] <--HTTP/WS--> [Open ACE 服务器] <--WebSocket/HTTP--> [远程 Agent]
                                  |                                |
                           [API Key 加密存储]               [qwen-code-cli]
                           [配额管理器]                     [claude-code]
                           [会话管理器]                     [openclaw]
```

### 消息流

1. 用户在浏览器输入消息 → `POST /api/remote/sessions/{id}/chat`
2. 服务器转发命令到远程 Agent（WebSocket 或 HTTP 轮询）
3. Agent 将消息喂给 CLI 子进程
4. CLI 需要 LLM 调用 → 请求发往 `POST /api/remote/llm-proxy`
5. 服务器校验配额、注入真实 API Key、转发到 LLM 提供商
6. LLM 流式响应：提供商 → 服务器 → Agent → 服务器 → 浏览器
7. 服务器记录 Token 用量

### 通信方式

Agent 支持两种与服务器的通信方式：

| 方式 | 适用场景 | 特点 |
|------|---------|------|
| WebSocket | 实时性要求高 | 双向通信，服务器可即时推送命令 |
| HTTP 轮询 | 防火墙限制严格 | Agent 主动 POST，服务器返回待执行命令 |

Agent 优先尝试 WebSocket 连接，失败时自动降级为 HTTP 轮询。

---

## 快速开始

### 前提条件

- Open ACE 服务器已部署并运行
- 远程机器可访问服务器的 HTTP/WebSocket 端口
- 远程机器已安装 Python 3.8+

### 三步完成

**第一步：管理员生成注册令牌**

在 Open ACE 管理界面（管理模式 → 远程工作区 → 远程机器 → 生成注册令牌），或通过 API：

```bash
# 管理员登录获取 session_token
curl -c cookies.txt -X POST http://<server>:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 生成注册令牌
curl -b cookies.txt -X POST http://<server>:5001/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
```

返回：
```json
{"registration_token": "a1b2c3d4e5f6..."}
```

**第二步：在远程机器上安装 Agent**

```bash
curl -fsSL http://<server>:5001/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:5001 --token <registration-token>
```

安装脚本会自动：
1. 下载 Agent 文件到 `~/.open-ace-agent/`
2. 安装 Python 依赖（websocket-client、requests）
3. 安装 CLI 工具（默认 qwen-code-cli，可选 claude-code）
4. 生成 machine_id 并注册到服务器
5. 安装为系统服务（Linux: systemd，macOS: launchd）

**第三步：分配用户**

管理员在管理界面（远程机器详情弹窗 → 分配用户），或通过 API：

```bash
curl -b cookies.txt -X POST \
  http://<server>:5001/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": 89, "permission": "admin"}'
```

用户即可在浏览器中看到该机器并创建远程会话。

---

## 服务端配置

### 数据库迁移

远程工作区使用 Alembic 迁移 `20260417_033_add_remote_workspace_tables.py`，会自动创建以下表：

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
| `OPENACE_ENCRYPTION_KEY` | 推荐 | API Key 加密密钥。未设置时从 `SECRET_KEY` 派生 | `SECRET_KEY` 的 SHA-256 |
| `SECRET_KEY` | 是 | Flask 会话密钥，也影响 API Key 加密 | `dev-secret-key` |

### API Key 管理

在管理界面（管理模式 → 远程工作区 → API Keys → 添加 API Key），或通过 API：

```bash
# 存储 OpenAI API Key
curl -b cookies.txt -X POST http://<server>:5001/api/remote/api-keys \
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

---

## 远程 Agent 安装

### 一行安装（推荐）

**Linux / macOS：**

```bash
curl -fsSL http://<server>:5001/api/remote/agent/install.sh | \
  bash -s -- --server http://<server>:5001 --token <token>
```

**Windows (PowerShell)：**

```powershell
Invoke-WebRequest -Uri "http://<server>:5001/api/remote/agent/install.ps1" | Invoke-Expression
```

### 安装参数

| 参数 | 必需 | 说明 | 默认值 |
|------|------|------|--------|
| `--server URL` | 是 | Open ACE 服务器地址 | - |
| `--token TOKEN` | 是 | 管理员生成的注册令牌 | - |
| `--name NAME` | 否 | 机器显示名称 | hostname |
| `--install-cli TOOL` | 否 | 要安装的 CLI 工具 | `qwen-code-cli` |
| `--dir DIR` | 否 | 安装目录 | `~/.open-ace-agent` |

示例 — 安装 Claude Code：

```bash
curl -fsSL http://<server>:5001/api/remote/agent/install.sh | \
  bash -s -- --server https://ace.example.com \
              --token abc123... \
              --install-cli claude-code \
              --name "Production Server"
```

### 手动安装

如果无法使用一键脚本，可以手动安装：

```bash
# 1. 复制 remote-agent/ 目录到远程机器
scp -r remote-agent/ user@remote:~/.open-ace-agent/

# 2. 安装依赖
cd ~/.open-ace-agent
pip3 install -r requirements.txt

# 3. 安装 CLI 工具
npm install -g @qwen-code/qwen-code@latest

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

# 5. 运行 Agent
python3 agent.py
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
    ├── base.py           # 适配器基类
    ├── qwen_code.py      # Qwen Code 适配器
    ├── claude_code.py    # Claude Code 适配器
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
curl -c cookies.txt -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 生成注册令牌
curl -b cookies.txt -X POST http://localhost:5001/api/remote/machines/register \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": 1}'
# → {"registration_token": "abc123..."}
```

### 查看所有机器

```bash
curl -b cookies.txt http://localhost:5001/api/remote/machines
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
  http://localhost:5001/api/remote/machines/<machine_id>/assign \
  -H "Content-Type: application/json" \
  -d '{"user_id": 89, "permission": "use"}'
```

权限级别：
- `use` — 可以使用机器创建会话
- `admin` — 可以使用机器，且具有管理权限

### 撤销用户权限

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:5001/api/remote/machines/<machine_id>/assign/<user_id>
```

### 注销机器

```bash
curl -b cookies.txt -X DELETE \
  http://localhost:5001/api/remote/machines/<machine_id>
```

---

## 管理界面

管理员可以通过 Open ACE Web 界面完成除远程 Agent 安装外的所有操作。侧边栏 **"远程工作区"** 分组下提供两个页面：

### 远程机器管理

**路径**：管理模式 → 远程工作区 → 远程机器（`/manage/remote/machines`）

**功能**：

| 操作 | 说明 |
|------|------|
| 生成注册令牌 | 点击按钮生成一次性注册 token，弹窗显示 token 值、复制按钮和安装命令 |
| 查看机器列表 | 表格显示：名称、Hostname、OS、状态（在线/离线 Badge）、Agent 版本、最后心跳时间 |
| 机器详情 | 弹窗显示完整信息（capabilities JSON）+ 已分配用户列表 |
| 分配用户 | 在详情弹窗中选择用户和权限级别（use/admin），分配到机器 |
| 撤销用户 | 在详情弹窗的用户列表中撤销某用户的机器访问权限 |
| 注销机器 | 二次确认后注销，机器从列表消失 |

**统计卡片**：页面顶部显示总机器数、在线数、离线数。

### API Key 管理

**路径**：管理模式 → 远程工作区 → API Keys（`/manage/remote/api-keys`）

**功能**：

| 操作 | 说明 |
|------|------|
| 添加 API Key | 弹窗中选择 Provider（OpenAI/Anthropic/Google）、输入 Key Name、API Key（密码输入框）、可选 Base URL |
| 查看密钥列表 | 表格显示：Provider（彩色 Badge）、Key Name、Base URL、状态、创建时间。**Key 值始终被遮蔽** |
| 删除 API Key | 二次确认后删除 |

> 两个页面仅管理员可见。普通用户的侧边栏不显示这些菜单项。

---

## 用户使用指南

### 查看可用机器

```bash
# 用户登录后查看分配给自己的在线机器
curl -b cookies.txt http://localhost:5001/api/remote/machines/available
```

### 创建远程会话

```bash
curl -b cookies.txt -X POST http://localhost:5001/api/remote/sessions \
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
  http://localhost:5001/api/remote/sessions/<session_id>/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "请帮我审查 main.py 的代码"}'
```

### 查看会话状态

```bash
curl -b cookies.txt \
  http://localhost:5001/api/remote/sessions/<session_id>
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
  http://localhost:5001/api/remote/sessions/<session_id>/stop
```

### 暂停/恢复会话

```bash
# 暂停
curl -b cookies.txt -X POST \
  http://localhost:5001/api/remote/sessions/<session_id>/pause

# 恢复
curl -b cookies.txt -X POST \
  http://localhost:5001/api/remote/sessions/<session_id>/resume
```

---

## API 参考

### 机器管理（管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/remote/machines/register` | 生成机器注册令牌 |
| `GET` | `/api/remote/machines` | 列出所有机器 |
| `GET` | `/api/remote/machines/<id>` | 获取机器详情 |
| `DELETE` | `/api/remote/machines/<id>` | 注销机器 |
| `POST` | `/api/remote/machines/<id>/assign` | 分配用户 |
| `DELETE` | `/api/remote/machines/<id>/assign/<uid>` | 撤销用户权限 |
| `GET` | `/api/remote/machines/<id>/users` | 获取机器已分配用户列表 |

### API Key 管理（管理员）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote/api-keys` | 列出所有 API Key（key 值被遮蔽） |
| `POST` | `/api/remote/api-keys` | 存储新的 API Key（加密后入库） |
| `DELETE` | `/api/remote/api-keys/<id>` | 删除指定 API Key |

### 会话管理（用户）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/remote/machines/available` | 获取当前用户可用的在线机器 |
| `POST` | `/api/remote/sessions` | 创建远程会话 |
| `GET` | `/api/remote/sessions/<id>` | 获取会话状态和输出 |
| `POST` | `/api/remote/sessions/<id>/chat` | 发送消息 |
| `POST` | `/api/remote/sessions/<id>/stop` | 停止会话 |
| `POST` | `/api/remote/sessions/<id>/pause` | 暂停会话 |
| `POST` | `/api/remote/sessions/<id>/resume` | 恢复会话 |

### Agent 通信

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/remote/agent/register` | Agent 注册（使用注册令牌） |
| `WS` | `/api/remote/agent/ws` | WebSocket 实时通信 |
| `POST` | `/api/remote/agent/message` | HTTP 轮询通信 |

### LLM 代理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/remote/llm-proxy` | 代理 LLM API 请求 |
| `*` | `/api/remote/llm-proxy/<path>` | 代理任意路径的 LLM 请求 |

---

## 安全设计

### 认证与授权

```
┌─────────────┐     注册令牌(一次性)     ┌─────────────┐
│   管理员     │ ───────────────────────→ │  远程 Agent  │
│  (服务器端)  │                          │ (远程机器)   │
└─────────────┘                          └─────────────┘
       │                                       │
       │ 分配用户权限                            │ 代理令牌(5分钟)
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
| **通信加密** | 所有通信建议通过 HTTPS/WSS（TLS）。Agent 通过令牌认证。 |
| **API Key 存储** | AES-256-GCM 加密。加密密钥来自 `OPENACE_ENCRYPTION_KEY` 环境变量，永不存入数据库。 |
| **API Key 访问** | 远程 Agent 获得短期代理令牌（5 分钟有效），而非真实 Key。Key 永远不会写入远程机器磁盘。 |
| **访问控制** | `machine_assignments` 表控制哪些用户可使用哪些机器。注册仅限管理员。 |
| **配额统一** | 本地和远程会话共享同一 `quota_usage` 表，统一计费。 |
| **审计日志** | 所有远程操作通过现有 `AuditLogger` 记录。 |

### LLM 代理安全流程

1. 远程 CLI 发送请求到 `/api/remote/llm-proxy`，携带 `Authorization: Bearer <proxy_token>`
2. 服务器验证代理令牌签名（HMAC-SHA256）和有效期
3. 从 `api_key_store` 解密出真实 API Key
4. 替换 Authorization 头为真实 Key，转发到 LLM 提供商
5. 流式返回响应，解析 Token 用量并记录

**关键原则：API Key 永远不会传输到远程机器。**

---

## 支持的 CLI 工具

### 内置适配器

| CLI 工具 | 标识名 | 安装命令 | 环境变量 |
|----------|--------|---------|---------|
| Qwen Code | `qwen-code-cli` | `npm install -g @qwen-code/qwen-code@latest` | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
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
2. Agent 设置 CLI 的 **API Key** 环境变量为短期代理令牌
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
| `OPENACE_ENCRYPTION_KEY` | API Key 加密密钥（推荐设置） | 从 `SECRET_KEY` 派生 |
| `SECRET_KEY` | Flask 会话密钥 | `dev-secret-key` |

### 远程 Agent

配置优先级：环境变量 > 配置文件 > 内置默认值。

| 变量 | 对应配置项 | 默认值 |
|------|-----------|--------|
| `OPENACE_SERVER_URL` | `server_url` | `http://localhost:5000` |
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

### Agent 无法连接服务器

**症状**：Agent 日志显示连接失败或持续重连。

```bash
# 查看日志
tail -f ~/.open-ace-agent/agent.log
```

**排查步骤**：

1. **网络连通性**：确认远程机器可以访问服务器端口
   ```bash
   curl -v http://<server>:5001/api/auth/login
   ```

2. **注册令牌过期**：令牌为一次性使用，注册成功后即失效。如果注册失败，需要管理员重新生成。

3. **服务器地址**：检查 `config.json` 中的 `server_url` 是否正确，注意不要有末尾斜杠。

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

1. **API Key 未存储**：管理员需要先存储对应 provider 的 API Key
2. **配额耗尽**：检查用户配额，代理在转发前会检查配额
3. **代理令牌过期**：令牌有效期 5 分钟，正常情况下 Agent 每次创建会话获取新令牌

### CLI 工具未找到

**症状**：Agent 启动会话时报 `command not found`。

**解决方法**：

```bash
# 安装 Qwen Code CLI
npm install -g @qwen-code/qwen-code@latest

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

迁移文件 `20260417_033_add_remote_workspace_tables.py` 会创建 `remote_machines`、`machine_assignments`、`api_key_store` 表。

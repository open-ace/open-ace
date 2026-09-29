# config.json Reference — config.json 配置参考

[English](#english) | [中文](#中文)

---

## English

This is the reference for `~/.open-ace/config.json`, the application-configuration file of the Open ACE server. It covers every field of the current sample (`config/config.json.sample`) and the behavior notes from `config/CONFIG_GUIDE.md`.

## Location and Precedence

- Default path: `~/.open-ace/config.json`. The directory can be moved with the `OPENACE_CONFIG_DIR` environment variable (the Kubernetes manifests set it to `/home/open-ace/.open-ace`, a mounted PVC).
- **Configuration priority: environment variables > config.json > built-in defaults.** Verified consumers:
  - `DATABASE_URL` (or the split set `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`) overrides any `database.*` setting in config.json — the docker entrypoint and `scripts/shared/config.py` both resolve the database URL in that order.
  - `SECRET_KEY` set in the environment wins over the `secret_key` field.
  - `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` override the `data_fetch` section.
- In Docker, the entrypoint generates config.json on first start from the environment (`PORT` becomes `server.web_port`, `SERVER_IP` builds `server.server_url`, secrets are auto-generated and persisted). The container itself always listens on 19888; `PORT` only maps the host port.
- Division of labor with [ENV_REFERENCE.md](ENV_REFERENCE.md): **environment variables own deployment-level concerns** (ports, database, security mode, secrets — see `.env.example` for Docker Compose), while **config.json owns application behavior** (workspace, alerts, integrations, feature switches). This document is the authoritative field table for config.json.
- Application readers cache config.json for up to 60 seconds (`app/utils/config.py`), so most edits take effect within a minute without restart. Exceptions that require a restart: the autonomous scheduler is started once at boot (`autonomous.enabled`).

## Top-Level Fields

| Field | Description | Example / default |
|-------|-------------|-------------------|
| `host_name` | Host identity used to attribute data from different machines. This is the **only** hostname field — all tools' data is attributed to it; do not duplicate hostnames under `tools.*`. | `localhost`, `server-01` |
| `secret_key` | Flask session-signing key. The `SECRET_KEY` environment variable takes precedence. Rotate with care — rotation invalidates all logged-in sessions. | random string |

## `server.*`

| Field | Description | Default |
|-------|-------------|---------|
| `server.upload_auth_key` | Upload authentication key, used to authenticate data uploaded by remote machines (the `X-Auth-Key` header of `/api/upload/batch`) | random string |
| `server.server_url` | Server address; remote machines must be configured with it | `http://192.168.1.100:19888` |
| `server.web_port` | Web service port | `19888` |
| `server.web_host` | Web bind address | `0.0.0.0` |
| `server.events_ingest_key` | Shared secret for the scheduler → web SSE event ingest (autonomous activity panel). Must be at least 32 characters; shorter values are ignored | `""` |
| `server.events_ingest_url` | Ingest URL the scheduler forwards autonomous SSE events to | `""` |
| `server.events_ingest_trusted_sources` | List of trusted source specifications allowed to post ingest events | `[]` |

## `database.*`

| Field | Description | Default |
|-------|-------------|---------|
| `database.type` | Database type: `sqlite` or `postgresql` | `sqlite` |
| `database.path` | SQLite database path (SQLite only) | `~/.open-ace/ace.db` |
| `database.url` | PostgreSQL connection URL (PostgreSQL only) | `null` |

**Note:** the `DATABASE_URL` environment variable takes precedence over the config file. If `DATABASE_URL` is set, the database settings in config.json are ignored.

**SQLite example:**
```json
{
  "database": {
    "type": "sqlite",
    "path": "~/.open-ace/ace.db"
  }
}
```

**PostgreSQL example:**
```json
{
  "database": {
    "type": "postgresql",
    "url": "postgresql://user:password@localhost:5432/ace"
  }
}
```

## `workspace.*`

| Field | Description | Default |
|-------|-------------|---------|
| `workspace.enabled` | Enable the workspace feature | `false` |
| `workspace.url` | Workspace service address | `http://localhost:8080` |
| `workspace.multi_user_mode` | Enable multi-user mode (one dedicated process per user) | `false` |
| `workspace.required_isolation_level` | Isolation floor for multi-user installs (the entrypoint pins `os_user` when multi-user mode is on) | — |
| `workspace.port_range_start` | First port of the per-user webui port pool | `3100` |
| `workspace.port_range_end` | Last port of the per-user webui port pool | `3200` |
| `workspace.max_instances` | Maximum concurrently running webui instances | `30` |
| `workspace.idle_timeout_minutes` | Auto-shutdown time for idle instances (minutes) | `30` |
| `workspace.cleanup_interval_minutes` | Cleanup sweep interval for idle instances | `5` |
| `workspace.token_secret` | Token-signing secret (use a strong random string) | `""` |
| `workspace.webui_path` | Explicit path to the qwen-code-webui installation (auto-detected when empty) | `""` |

In multi-user mode, Open ACE starts an independent `qwen-code-webui` process for each user, running as that user's `system_account`. This ensures:

- Each user only sees their own qwen projects and conversation history
- User actions are recorded in the qwen logs under the correct identity
- Data isolation and audit traceability in multi-user environments

**Single-user example:**
```json
{
  "workspace": {
    "enabled": true,
    "url": "http://localhost:8080",
    "multi_user_mode": false
  }
}
```

**Multi-user example:**
```json
{
  "workspace": {
    "enabled": true,
    "url": "http://localhost",
    "multi_user_mode": true,
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30,
    "idle_timeout_minutes": 30,
    "token_secret": "generate-a-strong-random-secret-key-here"
  }
}
```

## Other Sections

| Field | Description | Default |
|-------|-------------|---------|
| `data_fetch.interval` | Data fetch interval in seconds (minimum 60) | `300` |
| `data_fetch.enabled` | Enable the in-app data fetch scheduler | `true` |
| `quota_enforcement.interval` | Quota enforcement sweep interval in seconds | `60` |
| `quota_enforcement.enabled` | Enable quota enforcement | `true` |
| `autonomous.enabled` | Enable the AI autonomous development feature (restart required) | `true` |
| `autonomous.acceptance_verification_enabled` | Enable post-merge acceptance verification in the autonomous workflow | `true` |
| `run_timeline.enabled` | Persist remote-session run timelines | `false` |
| `model_gateway.enabled` | Enable the LiteLLM-compatible model gateway | `false` |
| `policy.enabled` | Enable the central policy & approval feature | `false` |
| `policy.approval_ttl_seconds` | Default lifetime of a policy approval (a rule may override it) | `3600` |
| `insights.temperature` / `insights.max_tokens` | Sampling parameters for insight generation | `0.3` / `4096` |

`data_fetch.*` and `quota_enforcement.*` can be overridden by the `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` and `QUOTA_ENFORCEMENT_INTERVAL` / `QUOTA_ENFORCEMENT_ENABLED` environment variables, following the precedence above.

## `alerts.*`

| Field | Description | Default |
|-------|-------------|--------|
| `alerts.allow_private_webhook_urls` | Whether alert webhooks may target private-network / loopback / link-local addresses. Off by default, blocking SSRF risk. | `false` |
| `alerts.dingtalk_webhook_secret` | Global signing secret for DingTalk custom robots. Empty sends as a plain webhook; when set, `timestamp` / `sign` are appended before sending. Users may also set their own secret in "alert preferences" (stored encrypted in the webhook URL as `openace_dingtalk_secret=<secret>`), which takes precedence over this global value. | `""` |
| `alerts.webhook_secret` | HMAC-SHA256 shared secret for generic (non-Feishu, non-DingTalk) webhooks. When set, request bodies are signed and the signature is sent in the `X-OpenACE-Signature` header so receivers can verify origin; empty means no signing. | `""` |

> By default, alert webhooks only allow publicly reachable `http(s)` URLs. Feishu / Lark and DingTalk group-robot webhooks work directly; if you really need to push to an intranet address, explicitly turn on `alerts.allow_private_webhook_urls` and control access at the network layer.

## `tools.*`

| Field | Description | Default |
|-------|-------------|---------|
| `tools.openclaw.enabled` | Enable OpenClaw | `true` |
| `tools.openclaw.token_env` | Environment variable name for the OpenClaw API token | `OPENCLAW_TOKEN` |
| `tools.openclaw.gateway_url` | OpenClaw Gateway address | `http://localhost:18789` |
| `tools.claude.enabled` | Enable Claude | `true` |
| `tools.qwen.enabled` | Enable Qwen | `true` |

## `feishu.*` and `dingtalk.*`

Both integrations support user/group name resolution in imported sessions, manual or scheduled organization-structure sync, and (DingTalk) alert-center robot pushes. Neither includes Feishu SSO login. The full field tables, setup steps, and troubleshooting live in the dedicated guides:

- **Feishu**: `feishu.app_id`, `feishu.app_secret`, `feishu.org_sync_enabled` (default `false`), `feishu.org_sync_tenant_id` (default `1`), `feishu.org_sync_interval_minutes` (default `60`) — see [FEISHU_CONFIG.md](FEISHU_CONFIG.md)
- **DingTalk**: `dingtalk.app_key`, `dingtalk.app_secret`, `dingtalk.org_sync_enabled` (default `false`), `dingtalk.org_sync_tenant_id` (default `1`), `dingtalk.org_sync_interval_minutes` (default `60`), `dingtalk.org_sync_root_dept_id` (default `"1"`) — see [DINGTALK_CONFIG.md](DINGTALK_CONFIG.md)

## Getting Started

1. Copy the sample configuration file:
   ```bash
   cp config/config.json.sample ~/.open-ace/config.json
   ```

2. Edit `~/.open-ace/config.json`:
   - Set `host_name` to the machine's hostname
   - Generate a random `upload_auth_key`
   - Configure other optional parameters as needed

3. Start the service:
   ```bash
   python3 server.py
   ```

---

## 中文

本文是 `~/.open-ace/config.json`（Open ACE 服务端应用配置文件）的参考文档，覆盖当前示例文件（`config/config.json.sample`）的全部字段，并整理了 `config/CONFIG_GUIDE.md` 中的行为说明。

## 位置与优先级

- 默认路径：`~/.open-ace/config.json`。目录可通过 `OPENACE_CONFIG_DIR` 环境变量重定位（Kubernetes 清单中设为 `/home/open-ace/.open-ace`，挂载 PVC）。
- **配置优先级：环境变量 > config.json > 内置默认值。** 已核实的消费方：
  - `DATABASE_URL`（或拆分形式的 `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`）优先于 config.json 中的 `database.*` —— docker entrypoint 与 `scripts/shared/config.py` 都按该顺序解析数据库连接。
  - 环境中设置了 `SECRET_KEY` 时优先于 `secret_key` 字段。
  - `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` 优先于 `data_fetch` 段。
- Docker 下，entrypoint 首次启动时根据环境变量生成 config.json（`PORT` 写入 `server.web_port`，`SERVER_IP` 拼出 `server.server_url`，缺失密钥自动生成并持久化）。容器自身固定监听 19888；`PORT` 只映射宿主机端口。
- 与 [ENV_REFERENCE.md](ENV_REFERENCE.md) 的分工：**环境变量负责部署层关注点**（端口、数据库、安全模式、密钥 —— Docker Compose 见 `.env.example`），**config.json 负责应用行为**（工作区、告警、集成、功能开关）。本文是 config.json 的权威字段表。
- 应用读取方对 config.json 有最长 60 秒的缓存（`app/utils/config.py`），多数修改在一分钟内免重启生效。需要重启的例外：自主开发调度器只在启动时拉起（`autonomous.enabled`）。

## 顶层字段

| 字段 | 说明 | 示例 / 默认值 |
|------|------|---------------|
| `host_name` | 主机名标识，用于区分不同机器的数据。这是**唯一**的主机名字段，所有工具的数据都以它归属；请勿在 `tools.*` 下重复配置主机名。 | `localhost`, `server-01` |
| `secret_key` | Flask 会话签名密钥。`SECRET_KEY` 环境变量优先。谨慎轮换 —— 轮换会使所有已登录会话失效。 | 随机字符串 |

## `server.*`

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `server.upload_auth_key` | 上传认证密钥，用于验证远程机器上传的数据（`/api/upload/batch` 的 `X-Auth-Key` 头） | 随机字符串 |
| `server.server_url` | 服务器地址，远程机器需要配置此地址 | `http://192.168.1.100:19888` |
| `server.web_port` | Web 服务端口 | `19888` |
| `server.web_host` | Web 绑定地址 | `0.0.0.0` |
| `server.events_ingest_key` | 调度器 → Web 的 SSE 事件摄取共享密钥（自主开发活动面板）。至少 32 字符，过短将被忽略 | `""` |
| `server.events_ingest_url` | 调度器转发自主开发 SSE 事件的摄取 URL | `""` |
| `server.events_ingest_trusted_sources` | 允许提交摄取事件的受信来源列表 | `[]` |

## `database.*`

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `database.type` | 数据库类型，可选 `sqlite` 或 `postgresql` | `sqlite` |
| `database.path` | SQLite 数据库路径（仅 SQLite 有效） | `~/.open-ace/ace.db` |
| `database.url` | PostgreSQL 连接 URL（仅 PostgreSQL 有效） | `null` |

> **注意：** 环境变量 `DATABASE_URL` 优先级高于配置文件。如果设置了 `DATABASE_URL`，将忽略配置文件中的数据库配置。

**SQLite 配置示例：**
```json
{
  "database": {
    "type": "sqlite",
    "path": "~/.open-ace/ace.db"
  }
}
```

**PostgreSQL 配置示例：**
```json
{
  "database": {
    "type": "postgresql",
    "url": "postgresql://user:password@localhost:5432/ace"
  }
}
```

## `workspace.*`

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `workspace.enabled` | 是否启用 Workspace 功能 | `false` |
| `workspace.url` | Workspace 服务地址 | `http://localhost:8080` |
| `workspace.multi_user_mode` | 是否启用多用户模式（为每个用户启动独立进程） | `false` |
| `workspace.required_isolation_level` | 多用户安装的隔离级别下限（多用户模式开启时 entrypoint 固定为 `os_user`） | — |
| `workspace.port_range_start` | 多用户模式下端口池起始端口 | `3100` |
| `workspace.port_range_end` | 多用户模式下端口池结束端口 | `3200` |
| `workspace.max_instances` | 最大同时运行的 webui 实例数 | `30` |
| `workspace.idle_timeout_minutes` | 空闲实例自动关闭时间（分钟） | `30` |
| `workspace.cleanup_interval_minutes` | 空闲实例清理扫描间隔（分钟） | `5` |
| `workspace.token_secret` | Token 签名密钥（建议使用强随机字符串） | `""` |
| `workspace.webui_path` | qwen-code-webui 安装路径（留空自动探测） | `""` |

多用户模式下，Open ACE 会为每个用户启动独立的 `qwen-code-webui` 进程，以该用户的 `system_account` 身份运行。这确保了：

- 每个用户只能看到自己的 qwen 项目和对话历史
- 用户操作会以正确的身份记录到 qwen 日志中
- 多用户环境下的数据隔离和审计追溯

**单用户模式配置示例：**
```json
{
  "workspace": {
    "enabled": true,
    "url": "http://localhost:8080",
    "multi_user_mode": false
  }
}
```

**多用户模式配置示例：**
```json
{
  "workspace": {
    "enabled": true,
    "url": "http://localhost",
    "multi_user_mode": true,
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30,
    "idle_timeout_minutes": 30,
    "token_secret": "generate-a-strong-random-secret-key-here"
  }
}
```

## 其他段落

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `data_fetch.interval` | 数据采集间隔（秒，最小 60） | `300` |
| `data_fetch.enabled` | 是否启用应用内数据采集调度器 | `true` |
| `quota_enforcement.interval` | 配额执行扫描间隔（秒） | `60` |
| `quota_enforcement.enabled` | 是否启用配额执行 | `true` |
| `autonomous.enabled` | 是否启用 AI 自主开发功能（需重启生效） | `true` |
| `autonomous.acceptance_verification_enabled` | 自主开发流程合并后是否执行验收校验 | `true` |
| `run_timeline.enabled` | 是否持久化远程会话运行时间线 | `false` |
| `model_gateway.enabled` | 是否启用 LiteLLM 兼容模型网关 | `false` |
| `policy.enabled` | 是否启用中央策略与审批功能 | `false` |
| `policy.approval_ttl_seconds` | 策略审批的默认有效期（单条规则可覆盖） | `3600` |
| `insights.temperature` / `insights.max_tokens` | 洞察生成的采样参数 | `0.3` / `4096` |

`data_fetch.*` 与 `quota_enforcement.*` 可被 `DATA_FETCH_INTERVAL` / `DATA_FETCH_ENABLED` 与 `QUOTA_ENFORCEMENT_INTERVAL` / `QUOTA_ENFORCEMENT_ENABLED` 环境变量覆盖，优先级见上文。

## `alerts.*`

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `alerts.allow_private_webhook_urls` | 是否允许告警 webhook 指向私网 / 回环 / 链路本地地址。默认关闭，用于阻断 SSRF 风险。 | `false` |
| `alerts.dingtalk_webhook_secret` | 钉钉自定义机器人"加签"密钥（全局，所有用户共用）。留空时按普通 webhook 发送；配置后发送前自动附加 `timestamp` / `sign`。也可在「告警偏好」里按用户设置各自的密钥（写入 webhook URL 的 `openace_dingtalk_secret=<secret>`，加密存储），其优先级高于此全局值。 | `""` |
| `alerts.webhook_secret` | 通用 webhook（非飞书 / 非钉钉）的 HMAC-SHA256 共享密钥。配置后对发送的请求体签名并把签名写入 `X-OpenACE-Signature` 头，接收方可据此校验请求来源；留空则不签名。 | `""` |

> 默认情况下，告警 webhook 仅允许公开可达的 `http(s)` 地址。飞书 / Lark 和钉钉群机器人 webhook 可直接使用；如果你确实需要向内网地址推送，请显式打开 `alerts.allow_private_webhook_urls`，并在网络层自行控制访问范围。

## `tools.*`

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `tools.openclaw.enabled` | 是否启用 OpenClaw | `true` |
| `tools.openclaw.token_env` | OpenClaw API Token 环境变量名 | `OPENCLAW_TOKEN` |
| `tools.openclaw.gateway_url` | OpenClaw Gateway 地址 | `http://localhost:18789` |
| `tools.claude.enabled` | 是否启用 Claude | `true` |
| `tools.qwen.enabled` | 是否启用 Qwen | `true` |

## `feishu.*` 与 `dingtalk.*`

两个集成都覆盖导入会话中的用户/群名解析、手动或定时的组织架构同步，以及（钉钉）告警中心机器人推送；均不包含飞书 SSO 登录。完整字段表、配置步骤与排障见专属指南：

- **飞书**：`feishu.app_id`、`feishu.app_secret`、`feishu.org_sync_enabled`（默认 `false`）、`feishu.org_sync_tenant_id`（默认 `1`）、`feishu.org_sync_interval_minutes`（默认 `60`）—— 见 [FEISHU_CONFIG.md](FEISHU_CONFIG.md)
- **钉钉**：`dingtalk.app_key`、`dingtalk.app_secret`、`dingtalk.org_sync_enabled`（默认 `false`）、`dingtalk.org_sync_tenant_id`（默认 `1`）、`dingtalk.org_sync_interval_minutes`（默认 `60`）、`dingtalk.org_sync_root_dept_id`（默认 `"1"`）—— 见 [DINGTALK_CONFIG.md](DINGTALK_CONFIG.md)

## 配置步骤

1. 复制示例配置文件：
   ```bash
   cp config/config.json.sample ~/.open-ace/config.json
   ```

2. 编辑 `~/.open-ace/config.json`：
   - 设置 `host_name` 为主机名
   - 生成随机的 `upload_auth_key`
   - 根据需要配置其他可选参数

3. 启动服务：
   ```bash
   python3 server.py
   ```

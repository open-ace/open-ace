# 配置文件说明

## config.json（主服务器配置）

### 必需配置

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `host_name` | 主机名标识，用于区分不同机器的数据 | `localhost`, `server-01` |
| `server.upload_auth_key` | 上传认证密钥，用于验证远程机器上传的数据 | 随机字符串 |
| `server.server_url` | 服务器地址，远程机器需要配置此地址 | `http://192.168.1.100:19888` |
| `server.web_port` | Web 服务端口 | `19888` |

### 可选配置

#### 数据库配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `database.type` | 数据库类型，可选 `sqlite` 或 `postgresql` | `sqlite` |
| `database.path` | SQLite 数据库路径（仅 SQLite 有效） | `~/.open-ace/ace.db` |
| `database.url` | PostgreSQL 连接 URL（仅 PostgreSQL 有效） | `null` |

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

> **注意：** 环境变量 `DATABASE_URL` 优先级高于配置文件。如果设置了 `DATABASE_URL`，将忽略配置文件中的数据库配置。

#### Workspace
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `workspace.enabled` | 是否启用 Workspace 功能 | `false` |
| `workspace.url` | Workspace 服务地址 | `http://localhost:8080` |
| `workspace.multi_user_mode` | 是否启用多用户模式（为每个用户启动独立进程） | `false` |
| `workspace.port_range_start` | 多用户模式下端口池起始端口 | `3100` |
| `workspace.port_range_end` | 多用户模式下端口池结束端口 | `3200` |
| `workspace.max_instances` | 最大同时运行的 webui 实例数 | `30` |
| `workspace.idle_timeout_minutes` | 空闲实例自动关闭时间（分钟） | `30` |
| `workspace.token_secret` | Token 签名密钥（建议使用强随机字符串） | `your-secret-key` |

### Autonomous（AI 自主开发）

> ⚠️ 修改此配置后需要重启服务器才能生效。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `autonomous.enabled` | 是否启用 AI 自主开发功能 | `true` |

### Alerts（告警推送）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `alerts.allow_private_webhook_urls` | 是否允许告警 webhook 指向私网 / 回环 / 链路本地地址。默认关闭，用于阻断 SSRF 风险。 | `false` |
| `alerts.dingtalk_webhook_secret` | 钉钉自定义机器人“加签”密钥。留空时按普通 webhook 发送；配置后发送前自动附加 `timestamp` / `sign`。 | `""` |
| `alerts.webhook_secret` | 通用 webhook（非飞书 / 非钉钉）的 HMAC-SHA256 共享密钥。配置后对发送的请求体签名并把签名写入 `X-OpenACE-Signature` 头，接收方可据此校验请求来源；留空则不签名。 | `""` |

> 默认情况下，告警 webhook 仅允许公开可达的 `http(s)` 地址。飞书 / Lark 和钉钉群机器人 webhook 可直接使用；如果你确实需要向内网地址推送，请显式打开 `alerts.allow_private_webhook_urls`，并在网络层自行控制访问范围。

**多用户模式说明：**

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

#### 工具配置
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `tools.openclaw.enabled` | 是否启用 OpenClaw | `true` |
| `tools.openclaw.token_env` | OpenClaw API Token 环境变量名 | `OPENCLAW_TOKEN` |
| `tools.openclaw.gateway_url` | OpenClaw Gateway 地址 | `http://localhost:18789` |
| `tools.claude.enabled` | 是否启用 Claude | `true` |
| `tools.qwen.enabled` | 是否启用 Qwen | `true` |

#### 定时任务
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `cron.enabled` | 是否启用定时任务 | `true` |
| `cron.run_time` | 每日运行时间（HH:MM 格式） | `00:30` |

#### 飞书集成（可选）

当前飞书能力覆盖导入会话中的用户/群名解析，以及手动或定时的飞书组织架构同步；不包含飞书 SSO 登录流程。

| 参数 | 说明 | 默认值 / 示例值 |
|------|------|----------------|
| `feishu.app_id` | 飞书应用 App ID | `cli_xxxxxxxxxxxxx` |
| `feishu.app_secret` | 飞书应用 Secret | `xxxxxxxxxxxxxxxxx` |
| `feishu.org_sync_enabled` | 是否启用飞书组织架构自动同步 | `false` |
| `feishu.org_sync_tenant_id` | 同步写入的租户 ID | `1` |
| `feishu.org_sync_interval_minutes` | 自动同步间隔（分钟） | `60` |

#### 钉钉集成（可选）

当前钉钉能力覆盖 OpenClaw 导入链路中的用户/群名解析、手动或定时的钉钉组织架构同步，以及告警中心向钉钉自定义机器人 webhook 推送告警。

| 参数 | 说明 | 默认值 / 示例值 |
|------|------|----------------|
| `dingtalk.app_key` | 钉钉内部应用 AppKey | `dingxxxxxxxxxxxxxx` |
| `dingtalk.app_secret` | 钉钉内部应用 AppSecret | `xxxxxxxxxxxxxxxxx` |
| `dingtalk.org_sync_enabled` | 是否启用钉钉组织架构自动同步 | `false` |
| `dingtalk.org_sync_tenant_id` | 同步写入的租户 ID | `1` |
| `dingtalk.org_sync_interval_minutes` | 自动同步间隔（分钟） | `60` |
| `dingtalk.org_sync_root_dept_id` | 同步起始部门 ID。钉钉根部门通常为 `1`。 | `"1"` |

#### ROI 分析假设（可选）

ROI 分析页（`/manage/analysis/roi`）展示的成本节省与生产力收益是**可配置的规划估算**，而非已实现的节省。底层假设通过以下环境变量全局配置；前端“Apply”按钮发起的按请求覆盖仅作用于当次查询，不会修改这些默认值。

| 环境变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENACE_ROI_HOURLY_LABOR_COST` | 每小时人工成本（用于估算节省）。 | `50.0` |
| `OPENACE_ROI_PRODUCTIVITY_MULTIPLIER` | 生产力乘数（如 `10` 表示 10 倍提升）。 | `10.0` |
| `OPENACE_ROI_AVG_TIME_SAVED_PER_REQUEST` | 每次请求平均节省的分钟数。 | `5.0` |
| `OPENACE_ROI_CURRENCY` | 展示货币代码（ISO 4217 风格，最多 8 字符）。 | `USD` |

> 注意：非法值（负数、零、`inf`/`Infinity`、非数字）会被忽略并回退到上表默认值。各变量必须为有限正数。

---

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

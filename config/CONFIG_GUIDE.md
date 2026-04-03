# 配置文件说明

## config.json（主服务器配置）

### 必需配置

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `host_name` | 主机名标识，用于区分不同机器的数据 | `localhost`, `server-01` |
| `server.upload_auth_key` | 上传认证密钥，用于验证远程机器上传的数据 | 随机字符串 |
| `server.server_url` | 服务器地址，远程机器需要配置此地址 | `http://192.168.1.100:5000` |
| `server.web_port` | Web 服务端口 | `5000` |

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
| `workspace.max_instances` | 最大同时运行的 webui 实例数 | `20` |
| `workspace.idle_timeout_minutes` | 空闲实例自动关闭时间（分钟） | `30` |
| `workspace.token_secret` | Token 签名密钥（建议使用强随机字符串） | `your-secret-key` |

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
    "max_instances": 20,
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

#### 飞书通知（可选）
| 参数 | 说明 | 示例值 |
|------|------|--------|
| `feishu.app_id` | 飞书应用 App ID | `cli_xxxxxxxxxxxxx` |
| `feishu.app_secret` | 飞书应用 Secret | `xxxxxxxxxxxxxxxxx` |

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
   python3 web.py
   ```

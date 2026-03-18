# 配置文件说明

## config.json（主服务器配置）

### 必需配置

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `host_name` | 主机名标识，用于区分不同机器的数据 | `localhost`, `server-01` |
| `server.upload_auth_key` | 上传认证密钥，用于验证远程机器上传的数据 | 随机字符串 |
| `server.server_url` | 服务器地址，远程机器需要配置此地址 | `http://192.168.1.100:5001` |
| `server.web_port` | Web 服务端口 | `5001` |

### 可选配置

#### Workspace
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `workspace.enabled` | 是否启用 Workspace 功能 | `false` |
| `workspace.url` | Workspace 服务地址 | `http://localhost:8080` |

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

#### 远程机器配置（可选）
| 参数 | 说明 | 示例值 |
|------|------|--------|
| `remote.enabled` | 是否启用远程机器数据采集 | `false` |
| `remote.hosts` | 远程机器列表 | `["host1", "host2"]` |

---

## remote_config.json（远程机器配置）

远程机器只需要配置最基本的参数：

### 必需配置

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `host_name` | 主机名标识 | `ai-lab`, `server-02` |
| `server.upload_auth_key` | 上传认证密钥（必须与主服务器一致） | 随机字符串 |

### 工具配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `tools.openclaw.enabled` | 是否启用 OpenClaw | `true` |
| `tools.openclaw.token_env` | OpenClaw API Token 环境变量名 | `OPENCLAW_TOKEN` |
| `tools.openclaw.gateway_url` | OpenClaw Gateway 地址 | `http://localhost:18789` |
| `tools.claude.enabled` | 是否启用 Claude | `true` |
| `tools.qwen.enabled` | 是否启用 Qwen | `true` |

### 注意事项

远程机器**不需要**配置以下参数（由主服务器统一管理）：
- `cron` - 定时任务配置
- `workspace` - Workspace 配置
- `feishu` - 飞书通知配置
- `remote` - 远程机器配置
- `server.server_url` - 服务器地址
- `server.web_port` - Web 端口
- `server.web_host` - Web 监听地址

---

## 配置步骤

### 主服务器配置

1. 复制示例配置文件：
   ```bash
   cp config/config.json.sample config/config.json
   ```

2. 编辑 `config/config.json`：
   - 设置 `host_name` 为主机名
   - 生成随机的 `upload_auth_key`
   - 根据需要配置其他可选参数

3. 启动服务：
   ```bash
   python3 web.py
   ```

### 远程机器配置

1. 复制示例配置文件：
   ```bash
   cp config/remote_config.json.sample config/remote_config.json
   ```

2. 编辑 `config/remote_config.json`：
   - 设置 `host_name` 为主机名
   - 设置 `upload_auth_key` 与主服务器一致

3. 启动服务：
   ```bash
   python3 web.py
   ```

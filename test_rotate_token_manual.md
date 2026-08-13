# Issue #2499 手动测试验证步骤

## 测试目标

验证轮换 token 后，Agent 多久会显示离线。

## 预期时间

- Agent 心跳间隔：60 秒
- 服务端心跳超时：180 秒（3分钟）
- 理论离线时间：轮换后 180 秒内

## 测试环境准备

### 1. 确认 Agent 在线

```bash
# 在管理页面确认目标机器状态为 "connected"
# 或通过 API 检查
curl -H "Authorization: Bearer <admin-token>" \
  http://localhost:19888/api/remote/machines
```

### 2. 准备日志监控

**终端 1 - 服务端日志**：
```bash
# 监控服务端日志
tail -f /path/to/open-ace/logs/server.log | grep -E "heartbeat|offline|rotate.*token"
```

**终端 2 - Agent 日志**：
```bash
# 在远程机器上监控 Agent 日志
ssh user@remote-machine
tail -f ~/.open-ace-agent/agent.log | grep -E "heartbeat|401|Authentication"
```

## 测试步骤

### 步骤 1：记录开始时间

```bash
date +"%Y-%m-%d %H:%M:%S"
# 记录时间：2026-08-12 XX:XX:XX
```

### 步骤 2：执行 Rotate Token

在管理页面点击 "Rotate Token" 按钮，或通过 API：

```bash
curl -X POST \
  -H "Authorization: Bearer <admin-token>" \
  http://localhost:19888/api/remote/machines/<machine_id>/token/rotate
```

### 步骤 3：观察 Agent 日志

Agent 应该立即开始报错：

```
[ERROR] Authentication failed (401) - Invalid or revoked Bearer token
[ERROR] Heartbeat failed: 401
[ERROR] Retrying in 1s...
[ERROR] Retrying in 2s...
[ERROR] Retrying in 4s...
```

### 步骤 4：记录机器离线时间

在管理页面或通过 API 持续检查机器状态：

```bash
# 每 10 秒检查一次状态
watch -n 10 'curl -s -H "Authorization: Bearer <admin-token>" \
  http://localhost:19888/api/remote/machines/<machine_id> | jq ".status"'

# 或者在服务端日志中查找离线标记
# "Marked 1 machines offline due to heartbeat timeout"
```

记录机器状态变为 `offline` 的时间。

### 步骤 5：计算实际离线时间

```
离线时间 = 离线时间点 - Rotate Token 时间点
```

**预期结果**：
- 理论值：180 秒（3 分钟）
- 实际值：由于检测间隔为 60 秒，可能在 180-240 秒之间

## 测试数据记录表

| 时间点 | 事件 | 时间戳 |
|--------|------|--------|
| T0 | 执行 Rotate Token | ____:____:____ |
| T1 | Agent 首次收到 401 | ____:____:____ |
| T2 | Agent 最后一次心跳成功 | ____:____:____ |
| T3 | 服务端标记机器 offline | ____:____:____ |
| T3 - T0 | 实际离线时间 | ____ 秒 |

## 验证点

### ✅ 预期行为（Bug）

1. Agent 立即收到 401 错误（秒级）
2. Agent 无法发送心跳
3. 服务端在 180-240 秒后标记机器离线

### ❌ 如果不符合预期

1. Agent 没有收到 401 → 检查 token 是否真的被吊销
2. Agent 仍然能发送心跳 → 检查是否有缓存 token
3. 离线时间远超 240 秒 → 检查心跳检测任务是否正常运行

## 补充验证：查看数据库状态

```bash
# 检查 agent_tokens 表
sqlite3 /path/to/open-ace/openace.db \
  "SELECT id, machine_id, is_revoked, revoked_at FROM agent_tokens WHERE machine_id='<machine_id>'"

# 检查 remote_machines 表的 last_heartbeat
sqlite3 /path/to/open-ace/openace.db \
  "SELECT machine_id, status, last_heartbeat FROM remote_machines WHERE machine_id='<machine_id>'"
```

## 恢复测试环境

测试完成后，手动更新 Agent 配置：

```bash
# 在远程机器上
ssh user@remote-machine
cd ~/.open-ace-agent
# 编辑 config.json，更新 agent_token 字段为新 token
vi config.json
# 重启 Agent
sudo systemctl restart open-ace-agent
```
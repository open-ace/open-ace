# Web Terminal Session 持久化计划

## 问题

用户刷新浏览器时，WebSocket 断开 → 60秒后 terminal_server.py 退出 → PTY 进程终止 → Claude Code 聊天历史丢失。

## 目标

WebSocket 断开后 PTY 进程继续运行，用户刷新后可以重新连接到同一个 terminal session，恢复之前的聊天历史。

## 方案设计

### 核心思路：WebSocket 临时，PTY 持久

```
当前架构（刷新丢失）:
WebSocket 断开 → PTY 被 SIGTERM → terminal_server 退出 → 全部丢失

新架构（刷新恢复）:
WebSocket 断开 → PTY 保持运行 → 新 WebSocket 连接 → 恢复同一个 terminal
```

### 方案 A：Single-PTY Terminal Server（推荐）

terminal_server.py 只管理一个 PTY，支持 WebSocket 断开重连：

```
┌─────────────────────────────────────────────────────┐
│ terminal_server.py                                  │
│                                                     │
│  ┌─────────────┐     ┌──────────────────────────┐ │
│  │ WebSocket 1 │────►│                          │ │
│  │ (连接中)    │     │    Single PTY Process    │ │
│  └─────────────┘     │    ┌─────────────────┐   │ │
│                      │    │ bash + claude   │   │ │
│  ┌─────────────┐     │    │ (聊天历史保持)  │   │ │
│  │ WebSocket 2 │────►│    └─────────────────┘   │ │
│  │ (等待连接)  │     │                          │ │
│  └─────────────┘     └──────────────────────────┘ │
│                                                     │
│  WebSocket 可断开重连，PTY 持续运行直到显式停止     │
└─────────────────────────────────────────────────────┘
```

**修改文件：`remote-agent/terminal_server.py`**

1. PTY 进程在 server 启动时就创建（而不是每个连接创建）
2. WebSocket 连接只是"附加"到已存在的 PTY
3. WebSocket 断开时 PTY 继续运行
4. 新 WebSocket 连接时恢复同一个 PTY 的输出
5. 只有收到 `stop_terminal` 命令才终止 PTY

### 方案 B：PTY 进程独立管理（备选）

将 PTY 进程管理从 terminal_server 分离：

```
┌──────────────┐     HTTP API      ┌──────────────┐
│ agent.py     │──────────────────►│ pty_manager  │
│              │   start_pty       │ (独立进程)   │
│              │   attach_pty      │              │
│              │   stop_pty        │ 管理 PTY 生命周期
└──────────────┘                   └──────────────┘
                                         │
                                         │ stdout/stdin
                                         ▼
                                   ┌──────────────┐
                                   │ PTY (bash +  │
                                   │ claude)      │
                                   └──────────────┘
                                         │
                                         │ WebSocket proxy
                                         ▼
                                   ┌──────────────┐
                                   │ terminal_    │
                                   │ server       │
                                   │ (只做IO转发) │
                                   └──────────────┘
```

复杂度较高，不推荐。

## 实现步骤（方案 A）

### Step 1: 修改 terminal_server.py 为 Single-PTY 模式

```python
class TerminalServer:
    """Single-PTY terminal server with WebSocket reconnection support."""

    def __init__(self):
        self.master_fd: int | None = None
        self.pty_pid: int | None = None
        self._pty_output_buffer: bytearray = bytearray()  # 保存输出历史
        self._active_websockets: set = set()

    def spawn_pty(self):
        """Spawn PTY once at startup."""
        if self.master_fd is None:
            self.master_fd, self.pty_pid = _spawn_pty(...)

    async def handle_connection(self, websocket):
        """Handle WebSocket connection - attach to existing PTY."""
        self._active_websockets.add(websocket)

        # 1. 发送历史输出给新连接（恢复屏幕）
        if self._pty_output_buffer:
            await websocket.send(bytes(self._pty_output_buffer[-65536:]))  # 最近64KB

        # 2. 开始转发 PTY I/O
        try:
            await asyncio.gather(
                self.relay_output_to_websocket(websocket),
                self.relay_input_from_websocket(websocket),
            )
        finally:
            self._active_websockets.discard(websocket)
            # WebSocket 断开，PTY 保持运行

    async def relay_output_to_websocket(self, websocket):
        """Read PTY output, buffer it, and send to websocket."""
        while True:
            data = os.read(self.master_fd, 65536)
            if data:
                self._pty_output_buffer.extend(data)  # 缓存历史
                # 发送给所有活跃连接
                for ws in self._active_websockets:
                    await ws.send(data)

    def stop_pty(self):
        """Only called when stop_terminal command received."""
        os.kill(self.pty_pid, signal.SIGTERM)
```

### Step 2: 修改 agent.py 增加 attach_terminal 命令

```python
# agent.py 新增
def _cmd_attach_terminal(self, data: dict[str, Any]) -> None:
    """Attach to existing terminal session."""
    terminal_id = data.get("terminal_id", "")

    if terminal_id in self._terminal_processes:
        # terminal_server 还在运行，返回现有 ws_url
        port = self._terminal_ports[terminal_id]
        term_token = self._terminal_tokens[terminal_id]
        hostname = self._get_reachable_hostname()
        ws_url = f"ws://{hostname}:{port}"

        self._http_send({
            "type": "terminal_status",
            "terminal_id": terminal_id,
            "status": "running",
            "ws_url": ws_url,
            "token": term_token,
        })
    else:
        # terminal_server 已退出，需要重新启动
        self._cmd_start_terminal(data)
```

### Step 3: 修改后端 API 支持 attach

```python
# app/routes/remote.py
@remote_bp.route("/terminal/<terminal_id>/attach", methods=["POST"])
def attach_terminal(terminal_id):
    """Attach to existing terminal session (after browser refresh)."""
    # 查找 terminal_id 对应的 machine_id
    # 发送 attach_terminal 命令给 agent
    # 返回 ws_url 和 token
```

### Step 4: 修改前端支持自动 attach

```typescript
// TerminalTab.tsx 或 Workspace.tsx
useEffect(() => {
  if (terminalId && machineId) {
    // 先尝试 attach，如果失败再 start
    remoteApi.attachTerminal({ terminal_id, machine_id })
      .then(res => {
        if (res.success) {
          setWsUrl(res.terminal.ws_url);
          setToken(res.terminal.token);
        } else {
          // attach 失败，重新 start
          remoteApi.startTerminal({ machine_id });
        }
      });
  }
}, [terminalId, machineId]);
```

### Step 5: 修改 idle timeout 逻辑

```python
# terminal_server.py
# 移除 idle timeout 退出逻辑，改为：
# - 只在收到 stop_terminal 命令时退出
# - 或 agent shutdown 时退出
# - 或 PTY 进程自然退出时退出
```

### Step 6: 添加 terminal session 持久化记录

```python
# agent.py
# 在 terminal session 启动后，保存到本地文件
def _save_terminal_session(self, terminal_id, data):
    session_file = os.path.join(self._session_dir, f"{terminal_id}.json")
    with open(session_file, "w") as f:
        json.dump({
            "terminal_id": terminal_id,
            "port": self._terminal_ports[terminal_id],
            "token": self._terminal_tokens[terminal_id],
            "created_at": datetime.utcnow().isoformat(),
        }, f)

# agent 启动时恢复 terminal sessions
def _restore_terminal_sessions(self):
    # 读取 session 文件，检查 terminal_server 是否还在运行
    # 如果在运行，恢复 _terminal_processes 映射
```

## 文件修改清单

| 文件 | 修改内容 |
|------|----------|
| `remote-agent/terminal_server.py` | Single-PTY 模式，WebSocket 断开不退出，输出历史缓存 |
| `remote-agent/agent.py` | 新增 `attach_terminal` 命令，terminal session 持久化 |
| `app/routes/remote.py` | 新增 `/terminal/<id>/attach` API |
| `frontend/src/api/remote.ts` | 新增 `attachTerminal` 方法 |
| `frontend/src/components/features/TerminalTab.tsx` | 自动 attach 逻辑 |

## 注意事项

1. **输出历史限制**：只缓存最近 64KB 或 128KB，避免内存无限增长
2. **PTY 自然退出**：如果用户在 terminal 中输入 `exit`，PTY 会退出，需要处理
3. **多连接支持**：理论上可以支持多个 WebSocket 连接同一个 PTY（多用户观看）
4. **安全性**：attach 时需要验证用户权限和 terminal_id ownership

## 测试场景

1. 创建 terminal → 运行 claude → 刷新浏览器 → 自动恢复连接
2. 创建 terminal → 运行 claude → 关闭 tab → 重新打开 terminal → 恢复聊天历史
3. 创建 terminal → 在 terminal 中输入 exit → terminal 自然结束
4. 显式 stop terminal → terminal 确实终止

## 预估工作量

- Step 1-2: 修改 terminal_server.py 和 agent.py（2-3小时）
- Step 3-4: 后端 API 和前端（1-2小时）
- Step 5-6: 持久化和恢复（1-2小时）
- 测试验证：1小时

总计：5-8小时

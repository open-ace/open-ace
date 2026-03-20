# AI Token Analyzer - 核心概念定义

本文档澄清项目中使用的核心概念：Request、Message、Conversation、Session。

---

## 概念定义

### 1. Request（API 请求）

**定义**：每次对云端大模型 API 的调用计为 1 次 Request。在原始日志中通常有 `auth_type` 这样的字段。

**示例**：
- 用户发送一条消息 → 1 次 API 调用 → 1 Request
- AI 调用一个工具 → 1 次 API 调用 → 1 Request
- AI 返回最终回答 → 1 次 API 调用 → 1 Request

**说明**：一个 Conversation 可能包含多次 Request。

---

### 2. Message（消息）

**定义**：所有角色的消息数，按角色细分：

| 指标名 | 角色 | 说明 |
|--------|------|------|
| message_user | user | 用户发送的消息 |
| message_assistant | assistant | AI 生成的回复 |
| message_toolresult | toolResult | 工具执行结果 |
| message_error | error | 错误消息 |

**示例**：
```
user: "帮我查一下天气"           → message_user +1
assistant: "好的，我来查..."     → message_assistant +1
toolResult: {weather data}       → message_toolresult +1
assistant: "今天天气晴朗..."     → message_assistant +1
```

---

### 3. Session（会话）

**定义**：工具层面（qwen code、claude code、openclaw）的一次会话。

**边界**：
- **开始**：用户启动工具进程
- **结束**：退出进程或用户手动执行 `/clear` 命令

**说明**：一个 Session 包含多个 Conversation。

---

### 4. Conversation（一轮对话）

**定义**：从用户发送一条消息开始，到 AI 完成回答结束（包括 AI 调用 tool 处理）。

**边界**：
- **开始**：用户发送一条消息
- **结束**：AI 完成最终回答

**示例**：
```
Conversation 1:
  user: "帮我查一下天气"
  assistant: "好的，我来查..."
  toolResult: {weather data}
  assistant: "今天天气晴朗..."  ← Conversation 结束

Conversation 2:
  user: "明天呢？"
  assistant: "明天..."
```

---

## 概念关系图

```
Session (工具会话)
├── Conversation 1 (一轮对话)
│   ├── Request 1 (API调用: 用户消息)
│   ├── Request 2 (API调用: AI调用工具)
│   ├── Request 3 (API调用: AI返回结果)
│   ├── message_user: 1
│   ├── message_assistant: 2
│   └── message_toolresult: 1
├── Conversation 2
│   └── ...
└── Conversation N
```

---

## 对话示例详解

以下是一个完整的对话流程，展示了 Request、Message、Conversation 的关系：

```
Conversation 1:
  user: "帮我查一下天气"
  │
  ├── Request 1 (API调用) ──> assistant: "好的，我来查..."  (message_assistant +1)
  │
  ├── Request 2 (API调用) ──> toolResult: {weather data}  (message_toolresult +1)
  │
  └── Request 3 (API调用) ──> assistant: "今天天气晴朗..."  (message_assistant +1)

Conversation 2:
  user: "明天呢？"
  │
  └── Request 4 (API调用) ──> assistant: "明天..."
```

**统计结果**：
- **Requests**: 4 次 API 调用
- **Messages**: 6 条（2 user + 3 assistant + 1 toolResult）
- **Conversations**: 2 轮对话

---

## 当前实现与定义的差异

| 概念 | 当前实现 | 正确定义 | 差异 |
|------|----------|----------|------|
| Request | `role='user'` 消息数 | API 调用次数（日志中有 `auth_type` 字段） | ❌ 不准确 |
| Message | 所有消息数 | 所有消息数（按角色细分） | ⚠️ 需细分 |
| Session | 按 `(sender, date, tool_name)` 分组 | 工具进程会话 | ❌ 不准确 |
| Conversation | `COUNT(DISTINCT parent_id)` | 一轮对话 | ❌ 不准确 |

---

## 修复建议

1. **Request**：需要从 API 层面统计，或通过消息链推断（日志中有 `auth_type` 字段）
2. **Message**：增加按角色细分的统计
3. **Session**：需要从工具层面获取会话标识
4. **Conversation**：需要根据消息链正确识别一轮对话的边界

---

## 修复完成 ✅

### 修复内容

已根据 Issue #94 完成概念定义和实现的修正：

| 概念 | 修复前 | 修复后 |
|------|--------|--------|
| **Request** | `role='user'` 消息数 | API 调用次数（日志中有 `auth_type` 字段） |
| **Message** | 所有消息数 | 所有消息数（按角色细分：user, assistant, toolResult, error） |
| **Session** | 按 `(sender, date, tool_name)` 分组 | 工具进程会话（`agent_session_id` 字段） |
| **Conversation** | `COUNT(DISTINCT parent_id)` | 一轮对话（`conversation_id` 字段） |

### 数据库修改

1. **daily_messages 表新增字段**：
   - `agent_session_id` TEXT - 工具会话标识（进程级别）
   - `conversation_id` TEXT - 一轮对话标识
   - `feishu_conversation_id` TEXT - 飞书会话标识符（重命名自 `conversation_label`）

2. **字段重命名**：
   - `conversation_label` → `feishu_conversation_id`（避免与 Conversation 概念混淆）

### 代码修改

1. **fetch_claude.py**：新增 `get_agent_session_id_from_path()` 函数，从项目路径提取 `agent_session_id`
2. **fetch_qwen.py**：新增 `get_agent_session_id_from_path()` 函数，从项目路径提取 `agent_session_id`
3. **fetch_openclaw.py**：新增 `get_agent_session_id_from_path()` 函数，从项目路径提取 `agent_session_id`
4. **web.py**：更新 API 端点，支持新字段 `agent_session_id` 和 `conversation_id`
5. **templates/index.html**：更新 UI 文本，将 "Session Statistics" 改为 "Agent Session Statistics"

### 迁移脚本

运行以下脚本迁移现有数据：
```bash
python3 scripts/migrate_concepts.py
```

该脚本会：
- 重命名 `conversation_label` 字段为 `feishu_conversation_id`
- 添加 `agent_session_id` 和 `conversation_id` 字段
- 为现有数据计算 `agent_session_id` 和 `conversation_id`
- 创建新字段的索引

### 相关 Issue

- #94 [重构] 澄清 Request、Message、Conversation、Session 概念的定义和区别，修正代码实现
- #91 My Usage Report 页面 Token Usage 和 Request Chart 数据验证

---

*文档版本：2.0.0*
*最后更新：2026-03-20*
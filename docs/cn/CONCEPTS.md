# 核心概念

> **ACE** = **AI Computing Explorer**

本文档定义 Open ACE 中的核心概念：**请求（Request）**、**消息（Message）**、**对话（Conversation）** 和 **会话（Session）**。

## 概念定义

### 1. 请求（Request / API Call）

**定义**：每次对云端 LLM 的 API 调用计为 1 个 Request。在日志中通常通过 `auth_type` 字段标识。

**示例**：
- 用户发送消息 → 1 次 API 调用 → 1 个 Request
- AI 调用工具 → 1 次 API 调用 → 1 个 Request
- AI 返回最终响应 → 1 次 API 调用 → 1 个 Request

**注意**：一个对话（Conversation）可能包含多个 Request。

---

### 2. 消息（Message）

**定义**：按角色分类的所有消息：

| 类型 | 角色 | 说明 |
|------|------|------|
| `message_user` | user | 用户发送的消息 |
| `message_assistant` | assistant | AI 生成的响应 |
| `message_toolresult` | toolResult | 工具执行结果 |
| `message_error` | error | 错误消息 |

**示例**：
```
user: "查看天气"                      → message_user +1
assistant: "让我查一下..."            → message_assistant +1
toolResult: {天气数据}                → message_toolresult +1
assistant: "今天是晴天..."            → message_assistant +1
```

---

### 3. 会话（Session）

**定义**：工具级别的会话（qwen code、claude code、openclaw）。

**边界**：
- **开始**：用户启动工具进程
- **结束**：进程退出或用户执行 `/clear` 命令

**注意**：一个 Session 包含多个 Conversation。

---

### 4. 对话（Conversation）

**定义**：一轮对话，从用户发送消息到 AI 完成响应（包括工具调用）。

**边界**：
- **开始**：用户发送消息
- **结束**：AI 完成最终响应

**示例**：
```
Conversation 1:
  user: "查看天气"
  assistant: "让我查一下..."
  toolResult: {天气数据}
  assistant: "今天是晴天..."  ← 对话结束

Conversation 2:
  user: "明天呢？"
  assistant: "明天..."
```

---

## 概念关系

```
Session (工具会话)
├── Conversation 1 (一轮对话)
│   ├── Request 1 (API 调用：用户消息)
│   ├── Request 2 (API 调用：AI 调用工具)
│   ├── Request 3 (API 调用：AI 返回结果)
│   ├── message_user: 1
│   ├── message_assistant: 2
│   └── message_toolresult: 1
├── Conversation 2
│   └── ...
└── Conversation N
```

---

## 统计示例

```
Conversation 1:
  user: "查看天气"
  │
  ├── Request 1 ──> assistant: "让我查一下..."  (message_assistant +1)
  │
  ├── Request 2 ──> toolResult: {天气数据}      (message_toolresult +1)
  │
  └── Request 3 ──> assistant: "今天是晴天..."   (message_assistant +1)

Conversation 2:
  user: "明天呢？"
  │
  └── Request 4 ──> assistant: "明天..."
```

**结果**：
- **请求（Requests）**：4 次 API 调用
- **消息（Messages）**：6 条（2 user + 3 assistant + 1 toolResult）
- **对话（Conversations）**：2 轮

---

## 数据库字段

| 字段 | 说明 |
|------|------|
| `agent_session_id` | 工具会话标识符（进程级别） |
| `conversation_id` | 对话标识符 |
| `feishu_conversation_id` | 飞书对话标识符 |
| `auth_type` | API 调用指示符（用于 Request 计数） |

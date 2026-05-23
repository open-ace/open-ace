# Core Concepts

> **ACE** = **AI Computing Explorer**

This document defines the core concepts used in Open ACE: **Request**, **Message**, **Conversation**, and **Session**.

## Concept Definitions

### 1. Request (API Call)

**Definition**: Each API call to the cloud LLM counts as 1 Request. In logs, this is typically identified by the `auth_type` field.

**Example**:
- User sends a message → 1 API call → 1 Request
- AI calls a tool → 1 API call → 1 Request
- AI returns final response → 1 API call → 1 Request

**Note**: One Conversation may contain multiple Requests.

---

### 2. Message

**Definition**: All messages by role:

| Type | Role | Description |
|------|------|-------------|
| `message_user` | user | Messages sent by user |
| `message_assistant` | assistant | AI-generated responses |
| `message_toolresult` | toolResult | Tool execution results |
| `message_error` | error | Error messages |

**Example**:
```
user: "Check the weather"           → message_user +1
assistant: "Let me check..."        → message_assistant +1
toolResult: {weather data}          → message_toolresult +1
assistant: "It's sunny today..."    → message_assistant +1
```

---

### 3. Session

**Definition**: A tool-level session (qwen code, claude code, openclaw).

**Boundaries**:
- **Start**: User launches the tool process
- **End**: Process exits or user runs `/clear` command

**Note**: One Session contains multiple Conversations.

---

### 4. Conversation

**Definition**: One round of dialogue, from user sending a message to AI completing the response (including tool calls).

**Boundaries**:
- **Start**: User sends a message
- **End**: AI completes the final response

**Example**:
```
Conversation 1:
  user: "Check the weather"
  assistant: "Let me check..."
  toolResult: {weather data}
  assistant: "It's sunny today..."  ← Conversation ends

Conversation 2:
  user: "What about tomorrow?"
  assistant: "Tomorrow..."
```

---

## Concept Relationship

```
Session (Tool Session)
├── Conversation 1 (One Round)
│   ├── Request 1 (API call: user message)
│   ├── Request 2 (API call: AI calls tool)
│   ├── Request 3 (API call: AI returns result)
│   ├── message_user: 1
│   ├── message_assistant: 2
│   └── message_toolresult: 1
├── Conversation 2
│   └── ...
└── Conversation N
```

---

## Statistics Example

```
Conversation 1:
  user: "Check the weather"
  │
  ├── Request 1 ──> assistant: "Let me check..."  (message_assistant +1)
  │
  ├── Request 2 ──> toolResult: {weather data}    (message_toolresult +1)
  │
  └── Request 3 ──> assistant: "It's sunny..."    (message_assistant +1)

Conversation 2:
  user: "What about tomorrow?"
  │
  └── Request 4 ──> assistant: "Tomorrow..."
```

**Results**:
- **Requests**: 4 API calls
- **Messages**: 6 (2 user + 3 assistant + 1 toolResult)
- **Conversations**: 2 rounds

---

## Database Fields

| Field | Description |
|-------|-------------|
| `agent_session_id` | Tool session identifier (process level) |
| `conversation_id` | Conversation identifier |
| `feishu_conversation_id` | Feishu conversation identifier |
| `auth_type` | API call indicator (for Request counting) |

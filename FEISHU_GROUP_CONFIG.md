# 飞书群聊名称映射配置指南

## 概述

为了在 Messages 页面显示飞书群聊的实际名称（而不是群聊 ID），需要配置飞书开放平台应用并启用群聊信息查询功能。

## 当前状态

**已配置应用信息：**
- App ID: `cli_xxxxxxxxxxxxxxxx`
- App Secret: `your_feishu_app_secret_here`

**API 测试结果：**
- ✅ 应用凭证有效（可以获取 access_token）
- ✅ 用户信息 API 可用
- ⚠️ 群聊名称需要通过 conversation_label 查询

**当前问题：**
- 配置中 `conversation_label` 字段的值为 `oc_76de7975e9c3543658a7c13a80b0251e` (oc_前缀)
- 这是 OpenClaw 内部生成的群聊标识符，不是飞书的 chat_id 格式
- 飞书 API 的 conversation_label 格式应该是 `chat_xxxxx_xxxxx`

## 解决方案

### 方案一：使用 OpenClaw 内部群聊标识符（当前）

**优点：**
- 无需额外配置
- `oc_` 标识符在 OpenClaw 系统内是唯一的

**缺点：**
- 标识符不可读，无法直接看出是哪个群聊
- 无法通过飞书 API 获取实际群名称

**当前处理方式：**
- 前端直接显示完整的 `oc_76de7975e9c3543658a7c13a80b0251e`
- 在群聊信息列显示该标识符

### 方案二：配置飞书群聊 API 权限（推荐）

如果希望显示实际的群聊名称，需要进行以下配置。

#### 1. 检查应用权限

1. 访问飞书开放平台：https://open.feishu.cn/app
2. 找到应用 `cli_xxxxxxxxxxxxxxxx`
3. 点击"权限管理"
4. 确保已添加以下权限：
   - `chat:chat:readonly` - 读取会话信息
   - 如果没有，点击"申请权限"并提交审核
5. 发布应用版本

#### 2. 获取群聊名称的 API

飞书提供了以下 API 来获取群聊信息：

**获取会话详情：**
```
GET https://open.feishu.cn/open-apis/chat/v4/chat/{chat_id}
Authorization: Bearer {token}
```

**返回示例：**
```json
{
  "code": 0,
  "data": {
    "chat_id": "chat_xxxxx",
    "name": "群聊名称",
    "description": "群描述",
    "owner": "ou_xxxxx",
    "member_count": 5
  }
}
```

#### 3. 配置本地配置文件

编辑 `~/.ai-token-analyzer/config.json`：

```json
{
  "host_name": "your-machine-name",
  "server": {
    "upload_auth_key": "your-auth-key",
    "server_url": "http://server-ip:5001"
  },
  "tools": {
    "openclaw": {
      "enabled": true,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "http://localhost:18789",
      "hostname": "your-machine-name"
    }
  },
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxx"
  }
}
```

#### 4. 使用群聊缓存功能

脚本已包含 `feishu_group_cache.py` 模块：

```bash
# 测试获取群聊名称
cd /Users/rhuang/workspace/ai-token-analyzer
python3 scripts/feishu_group_cache.py test chat_xxxxx <your_app_id> <your_app_secret>

# 查看缓存的群聊
python3 scripts/feishu_group_cache.py list

# 清除缓存
python3 scripts/feishu_group_cache.py clear
```

### 方案三：修改 OpenClaw 采集逻辑（高级）

如果 OpenClaw 记录的 feishu_conversation_id 是 `oc_` 前缀而不是飞书的 `chat_` 格式，可能需要：

1. 检查 OpenClaw 是否正确解析了飞书消息元数据
2. 修改 `scripts/fetch_openclaw.py` 中的元数据解析逻辑
3. 确保提取正确的飞书 chat_id

**注意**：`feishu_conversation_id` 字段替代了旧的 `conversation_label` 字段（Issue #94）

## 当前问题分析

### 为什么 feishu_conversation_id 是 `oc_` 开头？

从数据库中看到的示例：
```
sender_id: ou_c3163dee8efb941dcb735e0d2bbb4623
feishu_conversation_id: oc_76de7975e9c3543658a7c13a80b0251e
is_group_chat: true
```

`oc_` 开头的值是 OpenClaw 内部生成的群聊标识符，它不是飞书的 chat_id。这可能导致无法通过飞书 API 查询群聊名称。

**注意**：`feishu_conversation_id` 字段替代了旧的 `conversation_label` 字段（Issue #94）

### 如何获取真实的飞书 chat_id？

需要检查 OpenClaw 记录的消息元数据中是否包含 `chat_id` 字段：

```json
{
  "message_id": "om_xxxxx",
  "sender_id": "ou_xxxxx",
  "chat_id": "chat_xxxxx",  // 这才是飞书的群聊 ID
  "feishu_conversation_id": "oc_xxxxx",  // 这是 OpenClaw 的内部 ID（替代 conversation_label）
  "is_group_chat": true
}
```

如果元数据中没有 `chat_id` 字段，可能需要：

1. 更新 OpenClaw 到最新版本
2. 检查 OpenClaw 的飞书消息处理器
3. 修改 OpenClaw 的采集逻辑

**注意**：`feishu_conversation_id` 字段替代了旧的 `conversation_label` 字段（Issue #94）

## 推荐的解决方案

### 短期方案

1. 接受 `oc_` 开头的群聊标识符作为当前显示名称
2. 群聊名称列显示完整的 `oc_76de7975e9c3543658a7c13a80b0251e`
3. 在未来 OpenClaw 修复元数据问题后再升级

### 长期方案

1. 与 OpenClaw 团队沟通，确保正确记录飞书的 chat_id
2. 配置飞书群聊 API 权限
3. 实现更完善的群聊名称映射

## 测试配置

### 测试群聊信息查询

运行以下命令测试群聊名称获取：

```bash
cd /Users/rhuang/workspace/ai-token-analyzer
python3 scripts/feishu_group_cache.py test <chat_id> <app_id> <app_secret>
```

### 查看数据库中的群聊信息

```bash
python3 -c "
import sys
sys.path.insert(0, 'scripts')
sys.path.insert(0, 'scripts/shared')
import db
conn = db.get_connection()
cursor = conn.cursor()
cursor.execute('SELECT sender_id, feishu_conversation_id, group_subject, is_group_chat FROM daily_messages WHERE is_group_chat = 1 LIMIT 5')
for row in cursor.fetchall():
    print(f'sender_id: {row[0]}, feishu_conversation_id: {row[1]}, group_subject: {row[2]}, is_group_chat: {row[3]}')
conn.close()
"
```

## 更新日志

- 2026-03-07: 创建配置指南
- 添加 `feishu_group_cache.py` 模块
- 前端支持显示 `oc_` 开头的群聊标识符
- 2026-03-20: 更新字段名 `conversation_label` → `feishu_conversation_id`（Issue #94）

## 参考资料

- 飞书开放平台文档：https://open.feishu.cn/document
- 会话 API 文档：https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN
- 群聊管理 API：https://open.feishu.cn/document/orgfm/org-client-api/im-chat/manage-chat

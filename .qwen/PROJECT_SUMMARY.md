# AI Token Analyzer - 项目总结

## 项目概述

AI Token 用量追踪和分析系统，支持 OpenClaw、Claude、Qwen 等 AI 工具，提供 Web 仪表板和消息分析功能。

## 架构

- **中央服务器**: 192.168.31.181:5001 (Flask Web 应用)
- **远程机器**: 192.168.31.159 (hostname: ai-lab, user: openclaw)
- **数据流**: 远程机器收集 OpenClaw 日志 → 通过 HTTP API 上传到中央服务器 → 存入 SQLite 数据库
- **数据库**: `~/.ai-token-analyzer/usage.db`，包含表：`daily_usage`、`daily_messages`

## 技术栈

- **后端**: Python 3.9+, Flask, SQLite
- **前端**: HTML 模板，Chart.js, Bootstrap
- **数据收集**: 自定义脚本 (`fetch_openclaw.py`, `fetch_claude.py`, `fetch_qwen.py`)
- **远程同步**: HTTP POST 到 `/api/upload/batch`，使用 auth key 认证

## 重要文件

| 文件 | 用途 | 部署位置 |
|------|------|----------|
| `web.py` | Flask Web 服务器，提供 API 和 Dashboard 界面 | 中央服务器 |
| `cli.py` | 命令行工具，支持查询、报告、邮件发送等 | 中央服务器 |
| `templates/index.html` | Dashboard 和 Messages 页面 UI | 中央服务器 |
| `scripts/fetch_openclaw.py` | OpenClaw 数据收集（token 用量 + 消息内容） | 全部 |
| `scripts/fetch_claude.py` | Claude 数据收集 | 中央服务器 |
| `scripts/fetch_qwen.py` | Qwen 数据收集 | 中央服务器 |
| `scripts/upload_to_server.py` | 数据上传脚本 | 远程机器 |
| `scripts/manage.py` | 统一部署和管理脚本 | 全部 |
| `scripts/shared/db.py` | 数据库操作 | 全部 |
| `scripts/shared/feishu_user_cache.py` | 飞书用户信息查询和缓存 | 全部 |
| `scripts/clean_message_content.py` | 消息内容清洗脚本 | 全部 |
| `~/.ai-token-analyzer/config.json` | 本地配置 | 全部 |

## 部署目录

- **开发目录**: `/Users/rhuang/workspace/ai-token-analyzer/` - 源代码和开发使用
- **部署目录**: `~/ai-token-analyzer/` - 实际运行和部署使用
- **远程部署**: `/home/openclaw/ai-token-analyzer/` - 远程机器部署

## 部署和管理命令

```bash
# 本地部署（中央服务器）
python3 scripts/manage.py local deploy    # 部署到 ~/ai-token-analyzer/
python3 scripts/manage.py local start     # 启动 Web 服务
python3 scripts/manage.py local stop      # 停止 Web 服务
python3 scripts/manage.py local status    # 查看服务状态

# 远程部署（ai-lab）
python3 scripts/manage.py remote deploy   # 完整部署到远程机器
python3 scripts/manage.py remote sync     # 快速同步文件到远程
python3 scripts/manage.py remote status   # 查看远程状态
```

## ⚠️ 高优先级问题：正确提取用户消息

### 问题描述

当前从 OpenClaw 原始日志提取用户消息时，存在以下问题：

1. **消息内容包含元数据**：提取的消息包含 JSON 元数据块（```json```）、Conversation info、Sender info 等
2. **发送者姓名未正确解析**：飞书用户的 `ou_xxxxx` ID 没有解析为真实姓名
3. **群聊信息丢失**：`group_subject` 和 `conversation_label` 字段没有正确提取

### 影响范围

- 数据库中已有约 400+ 条飞书消息受到污染
- Web 界面显示的消息包含大量元数据，影响可读性
- 消息分析和搜索功能受到影响

### 解决方案

#### 1. 修复提取逻辑（进行中）

在 `scripts/fetch_openclaw.py` 中：

```python
def clean_message_content(text: str) -> str:
    """Clean message content by removing all metadata."""
    # 处理 System 格式
    # 移除 ```json``` 代码块
    # 移除 JSON 元数据行
    # 移除 "Sender: " 前缀
    # 返回纯文本内容
```

#### 2. 飞书用户名解析

已实现飞书用户 ID 到真实姓名的解析：
- App ID: `cli_a92be94ec4395cc2`
- App Secret: `6pvXz79b6gqadmEGKWIuVdTEjkf1DkSf`
- 缓存位置：`~/.ai-token-analyzer/feishu_users.json`
- 缓存有效期：1 小时

#### 3. 数据清洗脚本

已创建 `scripts/clean_message_content.py` 用于清洗已有数据：

```bash
cd ~/ai-token-analyzer
python3 scripts/clean_message_content.py
```

### 数据修复流程（解决后执行）

**⚠️ 重要：在提取逻辑完全修复后，必须执行以下步骤：**

```bash
# 1. 清除中央服务器数据库中的飞书消息
sqlite3 ~/.ai-token-analyzer/usage.db "DELETE FROM daily_messages WHERE message_source='feishu';"

# 2. 清除远程机器数据库中的飞书消息
ssh openclaw@192.168.31.159 "python3 -c \"import sqlite3; conn=sqlite3.connect('/home/openclaw/.ai-token-analyzer/usage.db'); c=conn.cursor(); c.execute(\\\"DELETE FROM daily_messages WHERE message_source='feishu'\\\"); conn.commit(); conn.close()\""

# 3. 清除上传标记
ssh openclaw@192.168.31.159 "rm -f ~openclaw/.ai-token-analyzer/upload_marker.json"

# 4. 重新从原始日志提取
ssh openclaw@192.168.31.159 "cd /home/openclaw/ai-token-analyzer && python3 scripts/fetch_openclaw.py --days 30"

# 5. 上传到中央服务器
ssh openclaw@192.168.31.159 "cd /home/openclaw/ai-token-analyzer && python3 scripts/upload_to_server.py --server http://192.168.31.181:5001 --auth-key deploy-remote-machine-key-2026 --hostname ai-lab --days 30"

# 6. 验证数据
sqlite3 ~/.ai-token-analyzer/usage.db "SELECT sender_name, group_subject, substr(content, 1, 50) FROM daily_messages WHERE message_source='feishu' LIMIT 10;"
```

### 验收标准

修复后的消息应满足：

1. ✅ **纯文本内容**：不包含 ```json```、Conversation info、Sender info 等元数据
2. ✅ **发送者姓名**：飞书用户显示真实姓名（如"韩成凤"），而不是 `ou_xxxxx` ID
3. ✅ **群聊信息**：`group_subject` 字段包含群聊 ID，`is_group_chat` 正确标识群聊/私聊
4. ✅ **消息来源**：正确标识 `feishu`、`slack`、`openclaw`

## 当前状态

### 已完成的功能

- ✅ 飞书用户姓名解析（韩成凤、黄迎春、吴丹）
- ✅ 消息来源检测（Slack/Feishu/OpenClaw）
- ✅ 群聊信息提取（conversation_label, group_subject, is_group_chat）
- ✅ Web 界面显示发送者、来源、群聊、主机、工具信息
- ✅ 远程机器数据收集和上传
- ✅ 统一管理和部署脚本

### 待解决的问题

- ⚠️ **高优先级**: 消息内容清洗逻辑需要完善
- ⚠️ **高优先级**: 需要从原始日志重新提取所有飞书消息
- 🔧 部分消息仍包含元数据残留

## 测试清单

- [ ] 所有飞书消息显示纯文本内容
- [ ] 所有飞书消息显示发送者真实姓名
- [ ] 群聊消息正确标识群聊信息
- [ ] Web 界面正确显示所有元信息
- [ ] 远程机器数据正常上传

## 更新历史

**2026-03-06**:
- 创建统一管理和部署脚本 `manage.py`
- 分离开发和部署目录
- 添加消息内容清洗功能
- 实现飞书用户姓名解析
- 添加群聊信息提取

**2026-03-05**:
- 修复远程机器数据收集
- 增强消息内容提取
- 添加消息来源检测

---

**更新**: 2026-03-06T17:00:00+08:00

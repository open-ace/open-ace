# 远程机器部署指南

## 部署位置

远程机器：**<REMOTE_HOST> (ai-lab)**
用户：**openclaw**
部署目录：**/home/openclaw/ai-token-analyzer/**

## 目录结构

```
/home/openclaw/ai-token-analyzer/
├── cli.py                          # CLI 工具
├── web.py                          # Web 服务器（如果需要）
├── README.md                       # 项目说明
├── requirements.txt                # Python 依赖
├── config/                         # 配置文件目录
│   └── settings.json.sample        # 配置示例
├── contrib/                        # systemd 服务配置
│   ├── fetch-openclaw.service      # OpenClaw 数据收集服务
│   └── fetch-openclaw.timer        # OpenClaw 数据收集定时器
├── cron/                           # cron 脚本
│   └── daily_run.sh                # 每日运行脚本
├── logs/                           # 日志目录
├── scripts/                        # 核心脚本
│   ├── fetch_openclaw.py           # OpenClaw 数据收集（消息+token）
│   ├── upload_to_server.py         # 数据上传脚本
│   ├── create_db.py                # 数据库创建工具
│   ├── init_db.py                  # 数据库初始化工具
│   ├── setup.py                    # 设置工具
│   ├── clean_message_content.py    # 消息内容清洗脚本
│   └── shared/                     # 共享模块
│       ├── __init__.py
│       ├── config.py               # 配置加载
│       ├── db.py                   # 数据库操作
│       ├── feishu_user_cache.py    # 飞书用户缓存
│       └── utils.py                # 工具函数
├── static/                         # 静态资源（Web UI）
└── templates/                      # HTML 模板（Web UI）
```

**注意：** 远程机器（ai-lab）不需要的文件：
- `email_notifier.py` - 邮件从中央服务器发送
- `fetch_claude.py`, `fetch_qwen.py` - 中央服务器使用
- `cli.py`, `web.py` - 中央服务器使用

## ⚠️ 高优先级：数据修复流程

**在 `fetch_openclaw.py` 的消息提取逻辑完全修复后，必须执行以下步骤重新提取所有消息：**

```bash
# 1. 从中央服务器清除飞书消息
sqlite3 ~/.ai-token-analyzer/usage.db "DELETE FROM daily_messages WHERE message_source='feishu';"

# 2. 从远程机器清除飞书消息
ssh openclaw@<REMOTE_HOST> "python3 -c \"import sqlite3; conn=sqlite3.connect('/home/openclaw/.ai-token-analyzer/usage.db'); c=conn.cursor(); c.execute(\\\"DELETE FROM daily_messages WHERE message_source='feishu'\\\"); conn.commit(); conn.close()\""

# 3. 清除上传标记
ssh openclaw@<REMOTE_HOST> "rm -f ~openclaw/.ai-token-analyzer/upload_marker.json"

# 4. 重新从原始日志提取（建议提取 30 天）
ssh openclaw@<REMOTE_HOST> "cd /home/openclaw/ai-token-analyzer && python3 scripts/fetch_openclaw.py --days 30"

# 5. 上传到中央服务器
ssh openclaw@<REMOTE_HOST> "cd /home/openclaw/ai-token-analyzer && python3 scripts/upload_to_server.py --server http://<SERVER_IP>:5001 --auth-key <UPLOAD_AUTH_KEY> --hostname ai-lab --days 30"

# 6. 验证数据
sqlite3 ~/.ai-token-analyzer/usage.db "SELECT sender_name, group_subject, substr(content, 1, 50) FROM daily_messages WHERE message_source='feishu' LIMIT 10;"
```

### 验收标准

修复后的数据应满足：

1. ✅ 消息内容为纯文本，不包含 ```json```、Conversation info 等元数据
2. ✅ 飞书用户显示真实姓名（如"韩成凤"），而不是 `ou_xxxxx` ID
3. ✅ 群聊消息的 `group_subject` 字段包含群聊 ID
4. ✅ `is_group_chat` 正确标识群聊/私聊

## 配置文件

### 本地配置 (~/.ai-token-analyzer/config.json)

```json
{
  "host_name": "ai-lab",
  "server": {
    "upload_auth_key": "<UPLOAD_AUTH_KEY>",
    "server_url": "http://<SERVER_IP>:5001"
  },
  "tools": {
    "openclaw": {
      "enabled": true,
      "token_env": "<OPENCLAW_TOKEN>",
      "gateway_url": "http://localhost:18789",
      "hostname": "ai-lab"
    }
  },
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_feishu_app_secret_here"
  }
}
```

## 部署步骤

### 使用统一管理脚本（推荐）

```bash
# 完整部署到远程机器
cd /Users/rhuang/workspace/ai-token-analyzer
python3 scripts/manage.py remote deploy

# 快速同步（不执行清理）
python3 scripts/manage.py remote sync
```

### 手动部署步骤

**1. 清理旧部署**

```bash
# 创建部署目录
ssh openclaw@<REMOTE_HOST> "mkdir -p /home/openclaw/ai-token-analyzer"
```

### 3. 同步文件

```bash
# 从本地同步文件
rsync -avz \
    --exclude='.git' \
    --exclude='.qwen' \
    --exclude='logs/*' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    ./ openclaw@<REMOTE_HOST>:/home/openclaw/ai-token-analyzer/
```

### 4. 清理不必要的脚本

```bash
# 在远程机器上清理
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer/scripts
rm -f check_*.py db_info.py test_*.py
rm -f deploy_remote.py fetch_remote.py upload_to_server.py
rm -f fetch_all_tools.py fetch_claude.py fetch_qwen.py fetch_openclaw.py
rm -f install_web_service.sh start_web.sh stop_web.sh
rm -f com.ai-token-analyzer.web.plist
"
```

### 5. 设置权限

```bash
# 确保所有文件属于 openclaw 用户
ssh root@<REMOTE_HOST> "chown -R openclaw:openclaw /home/openclaw/ai-token-analyzer"

# 设置可执行权限
ssh openclaw@<REMOTE_HOST> "
chmod +x /home/openclaw/ai-token-analyzer/scripts/fetch_openclaw.py
chmod +x /home/openclaw/ai-token-analyzer/web.py
chmod +x /home/openclaw/ai-token-analyzer/cli.py
"
```

### 6. 配置 systemd 服务（可选）

```bash
# 复制服务文件
scp contrib/fetch-openclaw.service contrib/fetch-openclaw.timer root@<REMOTE_HOST>:/etc/systemd/system/

# 重新加载并启用服务
ssh root@<REMOTE_HOST> "
systemctl daemon-reload
systemctl enable fetch-openclaw.timer
systemctl start fetch-openclaw.timer
systemctl status fetch-openclaw.timer
"
```

## 测试部署

```bash
# 测试数据收集
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer
python3 scripts/fetch_openclaw.py --days 1
"

# 检查数据库
ssh openclaw@<REMOTE_HOST> "
sqlite3 ~/.ai-token-analyzer/usage.db 'SELECT date, host_name, tokens_used FROM daily_usage ORDER BY date DESC LIMIT 5;'
"
```

## 常用命令

### 手动运行数据收集

```bash
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer
python3 scripts/fetch_openclaw.py --days 7
"
```

### 查看日志

```bash
ssh openclaw@<REMOTE_HOST> "
tail -f /home/openclaw/ai-token-analyzer/logs/*.log
"
```

### 检查 systemd 服务状态

```bash
ssh root@<REMOTE_HOST> "
systemctl status fetch-openclaw.service
systemctl status fetch-openclaw.timer
systemctl list-timers
"
```

### 更新部署

```bash
# 运行自动部署脚本
cd /Users/rhuang/workspace/ai-token-analyzer
bash scripts/clean_deploy_remote.sh
```

## 飞书用户名解析

飞书用户缓存位于：`~openclaw/.ai-token-analyzer/feishu_users.json`

### 查看缓存用户

```bash
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer
python3 scripts/shared/feishu_user_cache.py list
"
```

### 清除缓存

```bash
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer
python3 scripts/shared/feishu_user_cache.py clear
"
```

## 故障排查

### 检查 Python 依赖

```bash
ssh openclaw@<REMOTE_HOST> "
cd /home/openclaw/ai-token-analyzer
pip3 list | grep -E 'requests|flask|sqlite'
"
```

### 检查数据库

```bash
ssh openclaw@<REMOTE_HOST> "
sqlite3 ~/.ai-token-analyzer/usage.db '.tables'
sqlite3 ~/.ai-token-analyzer/usage.db 'SELECT COUNT(*) FROM daily_usage;'
"
```

### 检查文件权限

```bash
ssh openclaw@<REMOTE_HOST> "
ls -la /home/openclaw/ai-token-analyzer/
ls -la /home/openclaw/ai-token-analyzer/scripts/
"
```

## 更新历史

- **2026-03-06**: 清理部署，统一使用 `/home/openclaw/ai-token-analyzer/` 目录
- **2026-03-05**: 初始部署到 `/opt/ai-token-analyzer/`

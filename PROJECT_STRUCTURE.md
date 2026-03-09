# AI Token Analyzer - 项目结构与部署脚本设计文档

本文档记录项目结构设计、发布脚本和安装脚本的设计思路，供将来优化参考。

---

## 一、项目结构

### 1.1 目录结构概览

```
ai-token-analyzer/
├── cli.py                    # 命令行工具入口
├── web.py                    # Web 服务入口
├── requirements.txt          # Python 依赖
├── VERSION                   # 版本号文件
├── README.md                 # 项目说明
│
├── config/                   # 配置文件模板
│   ├── config.json.sample        # 本地配置模板
│   └── remote_config.json.sample # 远程配置模板
│
├── contrib/                  # 系统服务配置
│   ├── fetch-openclaw.service    # systemd 服务
│   └── fetch-openclaw.timer      # systemd 定时器
│
├── cron/                     # 定时任务脚本
│   └── daily_run.sh              # 每日运行脚本
│
├── scripts/                  # 核心脚本
│   ├── install.sh                # 安装脚本
│   ├── setup.py                  # 配置初始化
│   ├── fetch_claude.py           # Claude 数据收集
│   ├── fetch_openclaw.py         # OpenClaw 数据收集
│   ├── fetch_qwen.py             # Qwen 数据收集
│   ├── upload_to_server.py       # 数据上传到中央服务器
│   ├── create_db.py              # 数据库创建
│   ├── init_db.py                # 数据库初始化
│   ├── init_auth_db.py           # 认证数据库初始化
│   ├── com.ai-token-analyzer.web.plist  # macOS launchd 配置
│   └── shared/                   # 共享模块
│       ├── __init__.py
│       ├── config.py             # 配置加载
│       ├── db.py                 # 数据库操作
│       ├── utils.py              # 工具函数
│       ├── email_notifier.py     # 邮件通知
│       ├── feishu_user_cache.py  # 飞书用户缓存
│       └── feishu_group_cache.py # 飞书群聊缓存
│
├── static/                   # 静态资源 (Web UI)
├── templates/                # HTML 模板
├── logs/                     # 日志目录 (运行时生成)
└── dist/                     # 发布包目录 (构建时生成)
```

### 1.2 核心模块说明

| 模块 | 位置 | 用途 |
|------|------|------|
| `shared/config.py` | shared | 配置文件加载，支持本地和远程环境 |
| `shared/db.py` | shared | 数据库 CRUD 操作，连接池管理 |
| `shared/utils.py` | shared | 通用工具函数 |
| `shared/email_notifier.py` | shared | 邮件报告发送 |
| `shared/feishu_user_cache.py` | shared | 飞书用户信息缓存，避免频繁 API 调用 |
| `shared/feishu_group_cache.py` | shared | 飞书群聊信息缓存 |

### 1.3 数据流向

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据收集层                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  每台机器根据实际运行的 AI 工具调用对应的 fetch 脚本：           │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ fetch_claude.py    - 收集 Claude 日志                    │   │
│  │ fetch_qwen.py      - 收集 Qwen 日志                      │   │
│  │ fetch_openclaw.py  - 收集 OpenClaw 日志                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  典型部署场景：                                                 │
│                                                                 │
│  场景A：单机部署                                           │
│  ┌─────────────────────┐                                       │
│  │ 本地机器 (macOS)     │                                       │
│  │ - fetch_claude.py   │ ← 收集本地 Claude 日志                │
│  │ - fetch_qwen.py     │ ← 收集本地 Qwen 日志                  │
│  │ - fetch_openclaw.py │ ← 收集本地 OpenClaw 日志（如有）      │
│  │ - web.py            │ ← 提供 Web 服务                       │
│  │ - cron job          │ ← 定时调度                            │
│  └──────────┬──────────┘                                       │
│             │                                                   │
│             ▼                                                   │
│  ┌─────────────────────┐                                       │
│  │ 本地 SQLite 数据库   │ ← 所有数据汇总于此                    │
│  └─────────────────────┘                                       │
│                                                                 │
│  场景B：本地 + 远程机器分布式部署                                │
│  ┌─────────────────────┐     ┌─────────────────────┐          │
│  │ 本地机器 (中央服务器) │     │ 远程机器 (ai-lab)    │          │
│  │ - fetch_claude.py   │     │ - fetch_openclaw.py │          │
│  │ - fetch_qwen.py     │     │ - fetch_claude.py   │ (如需要) │
│  │ - fetch_openclaw.py │     │ - fetch_qwen.py     │ (如需要) │
│  │ - web.py            │     │ - systemd timer     │          │
│  │ - cron job          │     └──────────┬──────────┘          │
│  └──────────┬──────────┘                │                      │
│             │                           ▼                      │
│             │                 ┌─────────────────────┐          │
│             │                 │ 远程 SQLite 数据库  │          │
│             │                 └──────────┬──────────┘          │
│             │                            │                      │
│             │                            ▼                      │
│             │                 ┌─────────────────────┐          │
│             │◄────────────────│ upload_to_server.py │          │
│             │                 └─────────────────────┘          │
│             │                                                   │
│             ▼                                                   │
│  ┌─────────────────────┐                                       │
│  │ 中央 SQLite 数据库   │                                       │
│  └─────────────────────┘                                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        数据展示层                                │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────┐          ┌─────────────────────┐      │
│  │ web.py (Flask)      │◄─────────│ 中央 SQLite 数据库   │      │
│  │ cli.py              │          └─────────────────────┘      │
│  └─────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
```

### 1.4 调度配置说明

| 配置文件 | 适用系统 | 用途 |
|----------|----------|------|
| `contrib/fetch-openclaw.service` | Linux (systemd) | systemd 服务定义 |
| `contrib/fetch-openclaw.timer` | Linux (systemd) | systemd 定时器 |
| `scripts/com.ai-token-analyzer.web.plist` | macOS | launchd 服务配置 |
| `cron/daily_run.sh` | 通用 | cron 定时脚本模板 |

**注意：** `fetch-openclaw.service/timer` 只是示例配置，实际应根据机器上运行的 AI 工具调整：
- 如果机器运行 Claude，需要定时调用 `fetch_claude.py`
- 如果机器运行 Qwen，需要定时调用 `fetch_qwen.py`
- 如果机器运行 OpenClaw，需要定时调用 `fetch_openclaw.py`

---

## 二、发布脚本 (release.sh) 设计

### 2.1 设计目标

1. **版本管理**：支持从 VERSION 文件读取版本号，也支持命令行指定
2. **文件筛选**：只打包必要的运行文件，排除开发专用文件
3. **体积优化**：排除 node_modules 等大体积目录
4. **可追溯性**：文件名包含版本号和日期，生成 SHA256 校验和

### 2.2 排除文件策略

| 类别 | 文件 | 排除原因 |
|------|------|----------|
| 开发工具 | `release.sh`, `manage.py` | 用户不需要发布和开发管理脚本 |
| 一次性脚本 | `clean_message_content.py`, `migrate_messages.py`, `restore_queued_messages.py` | 数据修复脚本，新安装不需要 |
| 前端依赖 | `node_modules/` | 体积巨大（100M+），运行时不需要 |
| 缓存文件 | `__pycache__/`, `*.pyc` | Python 缓存，运行时自动生成 |
| IDE 配置 | `.idea/`, `.vscode/`, `.DS_Store` | 开发环境配置 |
| 版本控制 | `.git/`, `.qwen/` | 版本控制目录 |

### 2.3 关键实现

```bash
# 版本号来源优先级
1. 命令行参数 --version
2. VERSION 文件
3. 默认值 "1.0.0"

# 打包流程
1. 创建临时目录
2. 复制必要文件
3. 清理 scripts 目录（删除排除文件）
4. 清理 static 目录（删除 node_modules）
5. 创建 tar.gz 压缩包
6. 计算 SHA256 校验和
7. 清理临时目录
```

### 2.4 输出格式

```
dist/ai-token-analyzer-v{VERSION}-{DATE}.tar.gz
```

示例：`ai-token-analyzer-v1.0.0-20260309.tar.gz`

---

## 三、安装脚本 (install.sh) 设计

### 3.1 设计目标

1. **双模式支持**：本地部署和远程部署
2. **交互友好**：无配置文件时提供交互式引导
3. **升级保护**：检测已安装情况，保护数据文件
4. **调度灵活**：支持 systemd timer 和 cron 两种调度方式

### 3.2 部署模式

#### 本地部署 (local)
- 目标：当前机器
- 用途：中央服务器，运行 Web 服务，汇总所有数据
- 调度：cron job

#### 远程部署 (remote)
- 目标：远程 Linux 服务器
- 用途：数据收集节点，定时收集并上传数据
- 调度：systemd timer（推荐）或 cron job

### 3.3 数据保护策略

升级时保护的文件：

| 文件/目录 | 位置 | 说明 |
|-----------|------|------|
| `config.json` | `~/.ai-token-analyzer/` | 用户配置 |
| `usage.db` | `~/.ai-token-analyzer/` 或部署目录 | 数据库 |
| `feishu_users.json` | `~/.ai-token-analyzer/` | 飞书用户缓存 |
| `upload_marker.json` | `~/.ai-token-analyzer/` | 上传标记 |
| `logs/` | 部署目录 | 日志文件 |

### 3.4 调度方式对比

| 特性 | systemd timer | cron job |
|------|---------------|----------|
| 日志管理 | journalctl 集成 | 需手动配置日志文件 |
| 失败重试 | 支持 | 不支持 |
| 开机自启 | 支持 | 需要额外配置 |
| 精确时间 | 支持 | 支持 |
| 适用系统 | Linux with systemd | 所有 Unix |
| 推荐场景 | Linux 服务器 | 简单部署、macOS |

### 3.5 配置文件格式

```bash
# install.conf
INSTALL_MODE=local|remote

# 本地配置
LOCAL_USER=${USER}
LOCAL_PATH=$HOME/ai-token-analyzer
LOCAL_INTERVAL=30

# 远程配置
REMOTE_HOST=192.168.31.159
REMOTE_USER=openclaw
REMOTE_PATH=/home/openclaw/ai-token-analyzer
REMOTE_INTERVAL=30
REMOTE_SCHEDULER=systemd  # 或 cron
```

### 3.6 安装流程

```
┌─────────────────────────────────────────────────────────────┐
│                      安装流程                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 解析配置（配置文件 或 交互式输入）                        │
│                    │                                        │
│                    ▼                                        │
│  2. 检测已安装情况                                          │
│          ┌────────┴────────┐                               │
│          ▼                 ▼                               │
│     已安装              未安装                              │
│          │                 │                               │
│          ▼                 ▼                               │
│     备份数据          创建目录                              │
│          │                 │                               │
│          ▼                 ▼                               │
│     更新文件          复制文件                              │
│          │                 │                               │
│          └────────┬────────┘                               │
│                   ▼                                         │
│  3. 设置权限                                                 │
│                   │                                         │
│                   ▼                                         │
│  4. 配置调度器 (systemd/cron)                               │
│                   │                                         │
│                   ▼                                         │
│  5. 完成                                                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、未来优化建议

### 4.1 项目结构优化

1. **统一配置目录**
   - 当前：配置文件分散在 `~/.ai-token-analyzer/` 和部署目录
   - 建议：统一使用 `~/.ai-token-analyzer/` 存放所有配置和数据

2. **日志管理**
   - 当前：日志直接写入 `logs/` 目录
   - 建议：支持日志轮转，按日期分割日志文件

3. **模块化改进**
   - 当前：`fetch_*.py` 脚本有大量重复代码
   - 建议：抽取公共基类，减少代码重复

### 4.2 发布脚本优化

1. **增量发布**
   - 当前：每次发布完整包
   - 建议：支持增量更新包，减少传输量

2. **签名验证**
   - 当前：只有 SHA256 校验
   - 建议：添加 GPG 签名，提高安全性

3. **多平台支持**
   - 当前：主要针对 Linux/macOS
   - 建议：考虑 Windows 支持

### 4.3 安装脚本优化

1. **依赖检查**
   - 当前：不检查 Python 依赖
   - 建议：自动检查并安装 `requirements.txt` 中的依赖

2. **服务健康检查**
   - 当前：安装后不验证
   - 建议：安装后自动运行健康检查，验证服务可用性

3. **回滚机制**
   - 当前：只有备份，无自动回滚
   - 建议：升级失败时自动回滚到之前版本

4. **配置迁移**
   - 当前：配置格式变更需手动处理
   - 建议：支持配置文件版本迁移

### 4.4 其他建议

1. **版本号管理**
   - 使用语义化版本 (SemVer)
   - 在代码中嵌入版本号，避免硬编码

2. **文档完善**
   - 添加 CHANGELOG.md 记录版本变更
   - 添加 CONTRIBUTING.md 贡献指南

3. **测试覆盖**
   - 添加安装脚本测试用例
   - 模拟不同环境进行测试

---

## 五、附录

### 5.1 文件清单

**发布包包含的文件：**

```
ai-token-analyzer-v1.0.0-20260309/
├── cli.py
├── web.py
├── README.md
├── requirements.txt
├── VERSION
├── FEISHU_GROUP_CONFIG.md
├── FEISHU_USER_CONFIG.md
├── REMOTE_DEPLOY.md
├── config/
│   ├── config.json.sample
│   └── remote_config.json.sample
├── contrib/
│   ├── fetch-openclaw.service
│   └── fetch-openclaw.timer
├── cron/
│   └── daily_run.sh
├── scripts/
│   ├── install.sh
│   ├── setup.py
│   ├── fetch_claude.py
│   ├── fetch_openclaw.py
│   ├── fetch_qwen.py
│   ├── upload_to_server.py
│   ├── create_db.py
│   ├── init_db.py
│   ├── init_auth_db.py
│   ├── com.ai-token-analyzer.web.plist
│   └── shared/
│       ├── __init__.py
│       ├── config.py
│       ├── db.py
│       ├── utils.py
│       ├── email_notifier.py
│       ├── feishu_user_cache.py
│       └── feishu_group_cache.py
├── static/
├── templates/
├── web/
└── logs/
```

### 5.2 常用命令

```bash
# 发布
./scripts/release.sh
./scripts/release.sh --version 1.1.0

# 安装（交互式）
./scripts/install.sh

# 安装（配置文件）
./scripts/install.sh --config install.conf

# 本地管理
python3 scripts/manage.py local start
python3 scripts/manage.py local stop
python3 scripts/manage.py local status

# 远程管理
python3 scripts/manage.py remote deploy
python3 scripts/manage.py remote sync
```

---

*文档版本：1.0.0*
*最后更新：2026-03-09*
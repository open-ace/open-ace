# AI Token Analyzer

[English](#english) | [中文](#中文)

<a name="中文"></a>
## 中文

一个 AI 工具 token 用量追踪和分析项目，支持 OpenClaw、Claude 和 Qwen。

### 功能特点

- **用量追踪**：追踪 OpenClaw、Claude、Qwen 等 AI 工具的 token 使用量
- **数据分析**：查看每日用量、历史趋势、工具对比
- **Messages 分析**：查看单条消息详情（仅 Claude 和 Qwen，支持按角色、搜索筛选）
- **Web 可视化**：基于 Flask 的 Web 界面，使用 Chart.js 展示数据趋势
- **命令行工具**：通过 CLI 快速查询每日用量、历史数据和统计摘要
- **自动收集**：定时从本地日志文件提取 token 使用数据
- **邮件报告**：每日通过邮件发送用量报告

### 项目结构

```
ai-token-analyzer/
├── scripts/
│   ├── shared/           # 共享模块 (db, utils, config, email_notifier, feishu_user_cache)
│   ├── manage.py         # 统一部署和管理脚本
│   ├── fetch_claude.py   # Claude 日志收集
│   ├── fetch_openclaw.py # OpenClaw 日志收集（消息+token）
│   ├── fetch_qwen.py     # Qwen 日志收集
│   ├── create_db.py      # 数据库创建
│   ├── init_db.py        # 数据库初始化
│   └── setup.py          # 安装设置
├── web/                  # Web 应用目录
├── templates/            # HTML 模板
├── static/               # 静态资源
├── web.py                # Flask Web 服务器
├── cli.py                # 命令行工具
├── requirements.txt      # Python 依赖
└── config/
    ├── config.json.sample        # 本地配置文件模板
    └── remote_config.json.sample # 远程机器配置模板
```

### 安装

```bash
pip install -r requirements.txt
```

### 部署和使用

#### 开发目录 vs 部署目录

- **开发目录**：`/Users/rhuang/workspace/ai-token-analyzer/` - 源代码和开发使用
- **部署目录**：`~/ai-token-analyzer/` - 实际运行和部署使用

#### 部署到本地（中央服务器）

```bash
# 从开发目录部署到 ~/ai-token-analyzer/
cd /Users/rhuang/workspace/ai-token-analyzer
python3 scripts/manage.py local deploy
```

#### 使用部署版本

```bash
# 切换到部署目录
cd ~/ai-token-analyzer

# 启动 Web 服务
python3 web.py

# 或使用管理脚本（从开发目录）
python3 /Users/rhuang/workspace/ai-token-analyzer/scripts/manage.py local start
```

#### 初始化配置

```bash
python3 cli.py config init
```

编辑配置文件 `~/.ai-token-analyzer/config.json`，设置：

- 各 AI 工具的日志文件路径
- 邮件服务器设置（用于每日报告）

#### 收集数据

```bash
# 运行收集脚本
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py
```

#### 查看用量

```bash
# 查看今天用量
python3 cli.py today

# 查看特定日期用量
python3 cli.py query 2025-03-01

# 查看最近7天用量
python3 cli.py top

# 查看总量摘要
python3 cli.py summary
```

#### 运行 Web 界面

```bash
# 使用管理脚本（推荐）
python3 scripts/manage.py local start

# 或直接运行
python3 web.py
```

访问 http://localhost:5001 查看可视化数据。

**Web 界面功能：**
- **Summary 页面**：查看各工具的用量摘要和趋势图
- **Messages 页面**：查看单条消息详情（支持按日期、工具、角色、搜索词筛选）

### 部署和管理

使用统一的管理脚本 `scripts/manage.py`：

```bash
# 本地部署（中央服务器）
python3 scripts/manage.py local setup    # 初始化配置
python3 scripts/manage.py local install  # 安装系统服务
python3 scripts/manage.py local start    # 启动 Web 服务
python3 scripts/manage.py local stop     # 停止 Web 服务
python3 scripts/manage.py local status   # 查看服务状态

# 远程部署（ai-lab）
python3 scripts/manage.py remote deploy  # 完整部署到远程机器
python3 scripts/manage.py remote sync    # 快速同步文件到远程
python3 scripts/manage.py remote status  # 查看远程状态
```

#### 生成邮件报告

```bash
python3 cli.py report
```

### API 端点

- `GET /api/summary` - 获取所有工具的统计摘要
- `GET /api/today` - 获取今天的用量
- `GET /api/<tool_name>/<days>` - 获取指定工具 N 天内的用量
- `GET /api/date/<date>` - 获取指定日期的用量
- `GET /api/messages` - 获取消息列表（支持 filters）

### Messages API

```
GET /api/messages?date=2025-03-01&tool=claude&roles=user,assistant&search=test&page=1&limit=50
```

参数：
- `date` - 日期（YYYY-MM-DD）
- `tool` - 工具名称（claude, qwen, openclaw）
- `roles` - 角色筛选（user, assistant, system），逗号分隔
- `search` - 搜索内容
- `page` - 页码（默认 1）
- `limit` - 每页数量（默认 50）

### cron 自动化

在 crontab 中添加任务：

```bash
# 每天 00:30 运行数据收集和报告
30 0 * * * cd /path/to/ai-token-analyzer && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py && python3 scripts/fetch_openclaw.py && python3 cli.py report >> /path/to/logs/cron.log 2>&1
```

---

<a name="English"></a>
## English

An AI tool token usage tracking and analysis project, supporting OpenClaw, Claude, and Qwen.

### Features

- **Usage Tracking**: Track token usage from OpenClaw, Claude, Qwen, and other AI tools
- **Data Analysis**: View daily usage, historical trends, and tool comparisons
- **Messages Analysis**: View individual message details (Claude and Qwen only, filter by role and search)
- **Web Visualization**: Flask-based web interface with Chart.js for data visualization
- **CLI Tool**: Query daily usage, historical data, and summary statistics via command line
- **Automatic Collection**: Scheduled extraction of token usage from local log files
- **Email Reports**: Daily usage reports sent via email

### Project Structure

```
ai-token-analyzer/
├── scripts/
│   ├── shared/          # Shared modules (db, utils, email)
│   ├── fetch_claude.py  # Claude log fetcher
│   ├── fetch_openclaw.py # OpenClaw log fetcher
│   ├── fetch_qwen.py    # Qwen log fetcher
│   └── check_requirements.py
├── web/                 # Web application directory
├── templates/           # HTML templates
├── static/              # Static resources
├── web.py               # Flask web server
├── cli.py               # Command-line interface
├── requirements.txt     # Python dependencies
└── config/
    └── settings.json.sample  # Configuration template
```

### Installation

```bash
pip install -r requirements.txt
```

### Usage

#### Setup Configuration

```bash
python3 cli.py config init
```

Edit the config file at `~/.ai-token-analyzer/config.json` to configure:

- Log file paths for each AI tool
- SMTP settings for email reports

#### Collect Data

```bash
# Run fetcher scripts
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py
```

#### Query Usage

```bash
# Show today's usage
python3 cli.py today

# Query usage by date
python3 cli.py query 2025-03-01

# Show top usage for last 7 days
python3 cli.py top

# Show total summary
python3 cli.py summary
```

#### Run Web Interface

```bash
python3 web.py
```

Visit http://localhost:5001 to view the visualization.

**Web Interface Features:**
- **Summary Page**: View usage summaries and trend charts for each tool
- **Messages Page**: View individual message details (filter by date, tool, role, and search term)

#### Email Reports

```bash
python3 cli.py report
```

### API Endpoints

- `GET /api/summary` - Get summary statistics for all tools
- `GET /api/today` - Get today's usage
- `GET /api/<tool_name>/<days>` - Get usage for a tool over N days
- `GET /api/date/<date>` - Get usage for a specific date
- `GET /api/messages` - Get messages list (with filters)

### Messages API

```
GET /api/messages?date=2025-03-01&tool=claude&roles=user,assistant&search=test&page=1&limit=50
```

Parameters:
- `date` - Date in YYYY-MM-DD format
- `tool` - Tool name (claude, qwen, openclaw)
- `roles` - Role filters (user, assistant, system), comma-separated
- `search` - Search term for content
- `page` - Page number (default 1)
- `limit` - Items per page (default 50)

### Cron Automation

Add to crontab:

```bash
# Run data collection and report daily at 00:30
30 0 * * * cd /path/to/ai-token-analyzer && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py && python3 scripts/fetch_openclaw.py && python3 cli.py report >> /path/to/logs/cron.log 2>&1
```

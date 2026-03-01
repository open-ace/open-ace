# AI Token Analyzer

[English](#english) | [中文](#中文)

<a name="中文"></a>
## 中文

一个统一的 AI 工具 token 用量追踪项目，支持 OpenClaw、Claude 和 Qwen。

### 功能特点

- **多工具支持**：追踪 OpenClaw、Claude、Qwen 等 AI 工具的 token 使用量
- **Web 可视化**：基于 Flask 的 Web 界面，使用 Chart.js 展示数据趋势
- **命令行工具**：通过 CLI 快速查询每日用量、历史数据和统计摘要
- **自动收集**：定时从本地日志文件提取 token 使用数据
- **邮件报告**：每日通过邮件发送用量报告

### 项目结构

```
ai-token-analyzer/
├── scripts/
│   ├── shared/          # 共享模块 (db, utils, email)
│   ├── fetch_claude.py  # Claude 日志收集
│   ├── fetch_openclaw.py # OpenClaw 日志收集
│   ├── fetch_qwen.py    # Qwen 日志收集
│   └── check_requirements.py
├── web/                 # Web 应用目录
├── templates/           # HTML 模板
├── static/              # 静态资源
├── web.py               # Flask Web 服务器
├── cli.py               # 命令行工具
├── requirements.txt     # Python 依赖
└── config/
    └── settings.json.sample  # 配置文件模板
```

### 安装

```bash
pip install -r requirements.txt
```

### 使用

#### 初始化配置

```bash
python3 cli.py config init
```

编辑配置文件 `~/.ai_token_usage/config.json`，设置：

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
python3 web.py
```

访问 http://localhost:5000 查看可视化数据。

#### 生成邮件报告

```bash
python3 cli.py report
```

### API 端点

- `GET /api/summary` - 获取所有工具的统计摘要
- `GET /api/today` - 获取今天的用量
- `GET /api/<tool_name>/<days>` - 获取指定工具 N 天内的用量
- `GET /api/date/<date>` - 获取指定日期的用量

### cron 自动化

在 crontab 中添加任务：

```bash
# 每天 00:30 运行数据收集和报告
30 0 * * * cd /path/to/ai-token-analyzer && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py && python3 scripts/fetch_openclaw.py && python3 cli.py report >> /path/to/logs/cron.log 2>&1
```

---

<a name="English"></a>
## English

A unified AI tool token usage tracking project, supporting OpenClaw, Claude, and Qwen.

### Features

- **Multi-tool support**: Track token usage from OpenClaw, Claude, Qwen, and other AI tools
- **Web visualization**: Flask-based web interface with Chart.js for data visualization
- **CLI tool**: Query daily usage, historical data, and summary statistics via command line
- **Automatic collection**: Scheduled extraction of token usage from local log files
- **Email reports**: Daily usage reports sent via email

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

Edit the config file at `~/.ai_token_usage/config.json` to configure:

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

Visit http://localhost:5000 to view the visualization.

#### Email Reports

```bash
python3 cli.py report
```

### API Endpoints

- `GET /api/summary` - Get summary statistics for all tools
- `GET /api/today` - Get today's usage
- `GET /api/<tool_name>/<days>` - Get usage for a tool over N days
- `GET /api/date/<date>` - Get usage for a specific date

### Cron Automation

Add to crontab:

```bash
# Run data collection and report daily at 00:30
30 0 * * * cd /path/to/ai-token-analyzer && python3 scripts/fetch_claude.py && python3 scripts/fetch_qwen.py && python3 scripts/fetch_openclaw.py && python3 cli.py report >> /path/to/logs/cron.log 2>&1
```

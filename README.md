# Open ACE

> **ACE** = **AI Computing Explorer**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-orange.svg)](https://flask.palletsprojects.com/)
[![CI](https://github.com/your-org/open-ace/workflows/CI/badge.svg)](https://github.com/your-org/open-ace/actions)
[![codecov](https://codecov.io/gh/your-org/open-ace/branch/main/graph/badge.svg)](https://codecov.io/gh/your-org/open-ace)

**AI Token Usage Tracker & Analyzer** - Track, analyze, and visualize token usage across multiple AI tools.

[English](#english) | [中文](#中文)

---

<a name="中文"></a>

## 概述

Open ACE 是一个开源的 AI 工具 Token 用量追踪和分析平台，帮助团队和个人了解 AI 工具的使用情况，优化成本和效率。

### ✨ 核心功能

| 功能 | 描述 |
|------|------|
| 📊 **多工具支持** | 支持 Claude、Qwen、OpenClaw 等主流 AI 工具 |
| 📈 **可视化分析** | Web 界面展示用量趋势、热力图、对比分析 |
| 💬 **消息追踪** | 查看每条消息详情，支持按角色、内容筛选 |
| 🖥️ **CLI 工具** | 命令行快速查询用量统计 |
| 📧 **邮件报告** | 自动发送每日用量报告 |
| 🔄 **自动收集** | 定时从日志文件提取用量数据 |

### 📸 Screenshots

| Dashboard | Messages |
|:---------:|:--------:|
| ![Dashboard](docs/images/dashboard.png) | ![Messages](docs/images/messages.png) |

| Analysis | Conversation History |
|:--------:|:--------------------:|
| ![Analysis](docs/images/analysis.png) | ![Conversation](docs/images/conversation.png) |

## 🚀 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-org/open-ace.git
cd open-ace

# 安装依赖
pip install -r requirements.txt
```

### 配置

```bash
# 初始化配置
python3 cli.py config init

# 编辑配置文件
vim ~/.open-ace/config.json
```

### 启动

```bash
# 启动 Web 服务
python3 web.py

# 访问 http://localhost:5001
```

### 收集数据

```bash
# 收集各工具数据
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py
```

## 📖 文档

| 文档 | 说明 |
|------|------|
| [产品介绍](docs/INTRO.md) | **快速了解项目定位和功能** |
| [架构说明](docs/ARCHITECTURE.md) | 系统架构和核心概念 |
| [部署指南](docs/DEPLOYMENT.md) | 本地和远程部署 |
| [飞书配置](docs/FEISHU_CONFIG.md) | 飞书集成配置 |
| [开发指南](docs/DEVELOPMENT.md) | 参与开发 |

## 🛠️ CLI 命令

```bash
python3 cli.py today      # 查看今日用量
python3 cli.py top        # 查看最近7天用量
python3 cli.py summary    # 查看总量摘要
python3 cli.py report     # 生成邮件报告
```

## 🔌 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/summary` | 获取统计摘要 |
| `GET /api/today` | 获取今日用量 |
| `GET /api/messages` | 获取消息列表 |
| `GET /api/data-status` | 获取数据状态 |

详细 API 文档请参考 [API Reference](docs/API.md)。

## 📁 项目结构

```
open-ace/
├── cli.py              # CLI 入口
├── web.py              # Web 服务入口
├── scripts/            # 核心脚本
│   ├── fetch_*.py      # 数据收集脚本
│   ├── shared/         # 共享模块
│   └── migrations/     # 数据迁移脚本
├── templates/          # HTML 模板
├── static/             # 静态资源
├── tests/              # 测试文件
└── docs/               # 文档
```

## 🤝 贡献

欢迎贡献代码、报告问题或提出建议！请阅读 [贡献指南](CONTRIBUTING.md)。

## 📄 许可证

本项目采用 [Apache 2.0](LICENSE) 许可证。

---

<a name="english"></a>

## Overview

Open ACE is an open-source AI token usage tracking and analysis platform that helps teams and individuals understand their AI tool usage, optimize costs, and improve efficiency.

### ✨ Features

| Feature | Description |
|---------|-------------|
| 📊 **Multi-tool Support** | Supports Claude, Qwen, OpenClaw and more |
| 📈 **Visual Analytics** | Web dashboard with trends, heatmaps, comparisons |
| 💬 **Message Tracking** | View individual messages with role/content filters |
| 🖥️ **CLI Tool** | Quick command-line queries |
| 📧 **Email Reports** | Automated daily usage reports |
| 🔄 **Auto Collection** | Scheduled data extraction from logs |

### 📸 Screenshots

| Dashboard | Messages |
|:---------:|:--------:|
| ![Dashboard](docs/images/dashboard.png) | ![Messages](docs/images/messages.png) |

| Analysis | Conversation History |
|:--------:|:--------------------:|
| ![Analysis](docs/images/analysis.png) | ![Conversation](docs/images/conversation.png) |

## 🚀 Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/your-org/open-ace.git
cd open-ace

# Install dependencies
pip install -r requirements.txt
```

### Configuration

```bash
# Initialize config
python3 cli.py config init

# Edit config file
vim ~/.open-ace/config.json
```

### Start Server

```bash
# Start web server
python3 web.py

# Visit http://localhost:5001
```

### Collect Data

```bash
python3 scripts/fetch_claude.py
python3 scripts/fetch_qwen.py
python3 scripts/fetch_openclaw.py
```

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | System architecture and concepts |
| [Deployment](docs/DEPLOYMENT.md) | Local and remote deployment |
| [Feishu Config](docs/FEISHU_CONFIG.md) | Feishu integration |
| [Development](docs/DEVELOPMENT.md) | Contributing guide |

## 🛠️ CLI Commands

```bash
python3 cli.py today      # Today's usage
python3 cli.py top        # Last 7 days usage
python3 cli.py summary    # Total summary
python3 cli.py report     # Generate email report
```

## 🤝 Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md).

## 📄 License

This project is licensed under the [Apache 2.0 License](LICENSE).
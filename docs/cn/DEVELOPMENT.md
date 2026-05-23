# 开发指南

> **ACE** = **AI Computing Explorer**

本指南涵盖如何搭建开发环境以及如何为 Open ACE 贡献代码。

## 开发环境搭建

### 前提条件

- Python 3.9+
- Git
- 代码编辑器（VS Code、PyCharm 等）

### 搭建步骤

```bash
# 克隆仓库
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 上使用: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 安装开发依赖
pip install pytest pytest-cov playwright

# 初始化配置
python3 cli.py config init
```

## 项目结构

```
open-ace/
├── web.py              # Web 服务器入口
├── requirements.txt    # Python 依赖
│
├── app/                # Flask 应用
│   ├── __init__.py     # create_app() 工厂函数
│   ├── routes/         # 23 个 Blueprint 路由模块
│   ├── services/       # 14 个业务逻辑服务
│   ├── repositories/   # 11 个数据访问仓储
│   ├── modules/        # 领域逻辑包
│   │   ├── analytics/  # 使用分析、ROI、成本优化
│   │   ├── compliance/ # 审计分析、报告、数据保留
│   │   ├── governance/ # 审计日志、告警、配额、内容过滤
│   │   ├── sso/        # OAuth2/OIDC SSO
│   │   └── workspace/  # 远程代理、会话、协作
│   ├── models/         # 数据模型（User、Message、Session、Tenant 等）
│   ├── auth/           # 认证装饰器
│   └── utils/          # 辅助工具、验证器、格式化器
│
├── frontend/           # React + TypeScript SPA
│   └── src/
│       ├── api/        # API 客户端模块
│       ├── hooks/      # React Query hooks
│       ├── components/ # UI 组件（common、features、work、layout）
│       ├── store/      # Zustand 状态管理
│       ├── i18n/       # 国际化（en/zh/ja/ko）
│       ├── types/      # TypeScript 接口
│       └── utils/      # 格式化器、辅助工具
│
├── remote-agent/       # 远程代理守护进程
│   ├── agent.py        # 主守护进程循环
│   ├── executor.py     # CLI 子进程管理
│   ├── cli_adapters/   # 工具适配器（Claude、Qwen、Codex、OpenClaw）
│   ├── terminal_server.py  # WebSocket PTY 服务器
│   └── session_sync.py # 会话历史同步
│
├── scripts/            # 数据采集脚本
│   ├── fetch_*.py      # 各工具数据采集器
│   ├── shared/         # 共享模块（config、db、utils）
│   └── migrations/     # Alembic 数据库迁移
│
├── k8s/                # Kubernetes 清单
├── schema/             # 数据库模式文件
├── static/             # 构建后的前端资源
├── templates/          # HTML 模板
├── tests/              # 测试文件
│   ├── unit/           # 单元测试
│   ├── e2e/            # 端到端测试
│   └── issues/         # Issue 相关测试
└── docs/               # 文档（en/ + cn/）
```

## 前端开发

### 搭建

```bash
cd frontend
npm install
```

### 开发服务器

```bash
# 在 3000 端口启动开发服务器（API 代理到 localhost:5000）
npm run dev
```

前端需要后端在 5000 端口运行才能正常工作。

### 构建

```bash
# 生产构建（输出到 ../static/js/dist/）
npm run build
```

### 测试

```bash
# 单元测试
npm run test

# 使用 Playwright 运行 E2E 测试
npx playwright test
```

完整前端参考请参阅 [FRONTEND-GUIDE.md](FRONTEND-GUIDE.md)。

## 代码风格

我们遵循 [PEP 8](https://pep8.org/) 风格指南：

- 使用 4 个空格缩进
- 最大行长度：100 个字符
- 使用有意义的变量名和函数名
- 为函数和类添加文档字符串

### 示例

```python
def get_daily_usage(date: str, tool_name: str = None) -> dict:
    """
    获取指定日期的 token 使用量。

    Args:
        date: 日期，格式为 YYYY-MM-DD
        tool_name: 可选的工具筛选条件

    Returns:
        包含使用统计的字典
    """
    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute(
            "SELECT * FROM daily_usage WHERE date = ? AND tool_name = ?",
            (date, tool_name)
        )
    else:
        cursor.execute(
            "SELECT * FROM daily_usage WHERE date = ?",
            (date,)
        )

    return cursor.fetchall()
```

## 测试

### 运行测试

```bash
# 运行所有测试
pytest

# 详细输出
pytest -v

# 运行指定测试文件
pytest tests/test_db.py

# 带覆盖率
pytest --cov=scripts/shared tests/
```

### 测试组织

```
tests/
├── unit/               # 单元测试
│   ├── test_message_service.py
│   ├── test_usage_service.py
│   └── ...
├── e2e/                # 端到端测试
│   ├── manage/         # 管理 UI 测试
│   ├── remote/         # 远程工作区测试
│   └── terminal/       # 终端测试
├── issues/             # Issue 相关测试
│   ├── 164/
│   ├── 517/
│   └── ...
└── conftest.py         # 共享 fixtures
```

### 编写测试

```python
import pytest
from scripts.shared import db

def test_get_connection():
    """测试数据库连接。"""
    conn = db.get_connection()
    assert conn is not None
    conn.close()

def test_get_daily_usage():
    """测试每日使用量查询。"""
    result = db.get_daily_usage("2026-03-21")
    assert isinstance(result, list)
```

## 使用 Playwright 进行 UI 测试

### 搭建

```bash
# 安装 Playwright
pip install playwright

# 安装浏览器
playwright install chromium
```

### 运行 UI 测试

```bash
# 运行 UI 测试
pytest tests/ui/

# 运行指定测试
pytest tests/ui/test_screenshot.py
```

### UI 测试示例

```python
import asyncio
from playwright.async_api import async_playwright

async def test_login():
    """测试登录功能。"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 导航到登录页
        await page.goto('http://localhost:5000/login')

        # 填写表单
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')

        # 等待重定向
        await page.wait_for_url('http://localhost:5000/')

        await browser.close()
```

## 数据库迁移

数据库模式迁移使用 Alembic。迁移文件在 `migrations/` 目录中。

```bash
# 运行迁移
alembic upgrade head

# 创建新迁移
alembic revision --autogenerate -m "description"
```

### 数据迁移

数据迁移脚本（如 SQLite 到 PostgreSQL），请参阅 `scripts/utils/`：

```bash
# 从 SQLite 迁移到 PostgreSQL
python3 scripts/utils/migrate_to_postgres.py
```

## 添加新数据源

添加新的 AI 工具支持：

1. 创建 `scripts/fetch_newtool.py`
2. 实现日志解析逻辑
3. 添加到配置模板
4. 添加测试

### 模板

```python
#!/usr/bin/env python3
"""从 NewTool 获取使用数据。"""

import os
import sys
from pathlib import Path

# 添加共享模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shared'))

import db
import utils

def fetch_newtool(days: int = 7):
    """获取 NewTool 使用数据。"""
    log_path = Path.home() / '.newtool' / 'logs'

    for log_file in log_path.glob('*.jsonl'):
        # 解析日志文件
        # 提取 token 使用量
        # 保存到数据库
        pass

if __name__ == '__main__':
    fetch_newtool()
```

## 调试

### 启用调试日志

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### 数据库检查

```bash
# 打开数据库
sqlite3 ~/.open-ace/usage.db

# 查询表
.tables
.schema daily_usage
SELECT * FROM daily_usage LIMIT 10;
```

## 发布流程

1. 更新 `VERSION` 文件
2. 更新 `CHANGELOG.md`
3. 创建 git tag
4. 构建发布包

```bash
# 构建发布
./scripts/release.sh --version 1.1.0
```

## 获取帮助

- 查看 `docs/` 中的现有文档
- 在 GitHub 上搜索已有 Issue
- 为 Bug 或功能需求创建新 Issue

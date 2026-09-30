# Development Guide — 开发指南

[English](#english) | [中文](#中文)

---

## English

> **ACE** = **AI Computing Explorer**

This guide covers setting up a development environment and contributing to Open ACE.

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- A code editor (VS Code, PyCharm, etc.)

### Setup Steps

```bash
# Clone the repository
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install the exact dependency set used by GitHub CI
pip install -r requirements-ci.lock

# Only needed for browser E2E tests
playwright install chromium

# Initialize configuration
python3 cli.py config init
```

### Starting the Backend and First Login

```bash
# Start the Flask backend (API + built frontend) on port 19888
python3 server.py
```

Open http://localhost:19888 and log in with the default account `admin` / `admin123`. Change this password immediately after the first login — see [DEPLOYMENT.md](../guide/DEPLOYMENT.md) for credentials handling and production hardening.

## Project Structure

```
open-ace/
├── server.py              # Web server entry point
├── requirements.txt       # Production dependency policy
├── requirements-ci.in     # Development/CI tools plus production dependencies
├── requirements-ci.lock   # Resolved local/GitHub CI dependency set
│
├── app/                # Flask application
│   ├── __init__.py     # create_app() factory
│   ├── routes/         # 39 Blueprint route modules
│   ├── services/       # 42 business logic services
│   ├── repositories/   # 26 data access repositories
│   ├── modules/        # Domain logic packages
│   │   ├── analytics/  # Usage analytics, ROI, cost optimization
│   │   ├── compliance/ # Audit analysis, reports, retention
│   │   ├── governance/ # Audit logging, alerts, quotas, content filter
│   │   ├── sso/        # OAuth2/OIDC/SAML SSO
│   │   └── workspace/  # Remote agents, sessions, collaboration
│   ├── models/         # Data models (User, Message, Session, Tenant, etc.)
│   ├── auth/           # Authentication decorators
│   └── utils/          # Helpers, validators, formatters
│
├── frontend/           # React + TypeScript SPA
│   └── src/
│       ├── api/        # API client modules
│       ├── hooks/      # React Query hooks
│       ├── components/ # UI components (common, features, work, layout)
│       ├── store/      # Zustand state management
│       ├── i18n/       # Internationalization (en/zh/ja/ko)
│       ├── types/      # TypeScript interfaces
│       └── utils/      # Formatters, helpers
│
├── remote-agent/       # Remote agent daemon
│   ├── agent.py        # Main daemon loop
│   ├── executor.py     # CLI subprocess management
│   ├── cli_adapters/   # Tool adapters (Claude, Qwen, Codex, OpenClaw)
│   ├── terminal_server.py  # WebSocket terminal server (PTY on Linux/macOS, piped subprocess on Windows)
│   └── session_sync.py # Session history sync
│
├── scripts/            # Data collection scripts
│   ├── fetch_*.py      # Per-tool data fetchers
│   ├── shared/         # Shared modules (config, db, utils)
│   └── migrations/     # Alembic database migrations
│
├── k8s/                # Kubernetes manifests
├── schema/             # Database schema files
├── static/             # Built frontend assets
├── tests/              # Test files
│   ├── unit/           # Unit tests
│   ├── integration/    # Integration tests
│   └── e2e/            # End-to-end tests
└── docs/               # Documentation (en/ + cn/)
```

## Frontend Development

### Setup

```bash
cd frontend
npm install
```

### Development Server

```bash
# Start dev server on port 3000 (proxies API to localhost:19888)
npm run dev
```

The backend must be running on port 19888 for the frontend to work.

### Build

```bash
# Build for production (outputs to ../static/js/dist/)
npm run build
```

### Testing

```bash
# Unit tests
npm run test

# E2E tests with Playwright
npx playwright test

# Server-dependent extended tests used by CI PR/release gates
cd frontend && npm ci && npm run build && cd ..
python scripts/run_extended_tests.py --category critical --isolated-home

# Full E2E or issue regression shards
python scripts/run_extended_tests.py --category e2e --isolated-home
python scripts/run_extended_tests.py --category issues --split-total 4 --split-group 1 --isolated-home
```

See [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) for the complete frontend reference.

## Code Style

We follow [PEP 8](https://pep8.org/) style guidelines:

- Use 4 spaces for indentation
- Maximum line length: 100 characters
- Use meaningful variable and function names
- Add docstrings to functions and classes

### Example

```python
def get_daily_usage(date: str, tool_name: str = None) -> dict:
    """
    Get token usage for a specific date.

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool filter

    Returns:
        Dictionary with usage statistics
    """
    from app.repositories.database import get_connection, adapt_sql

    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute(
            adapt_sql("SELECT * FROM daily_usage WHERE date = ? AND tool_name = ?"),
            (date, tool_name)
        )
    else:
        cursor.execute(
            adapt_sql("SELECT * FROM daily_usage WHERE date = ?"),
            (date,)
        )

    return cursor.fetchall()
```

## Testing

### Run Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run unit tests only
pytest tests/unit/

# Run integration tests (SQLite + PostgreSQL)
pytest tests/integration/

# Run integration tests with PostgreSQL only
pytest tests/integration/ -k "_pg"

# Run specific test file
pytest tests/unit/test_db.py

# Run with coverage
pytest --cov=app tests/
```

### Test Organization

```
tests/
├── unit/               # Unit tests
│   ├── test_message_service.py
│   ├── test_usage_service.py
│   └── ...
├── integration/        # Integration tests (SQLite + PostgreSQL)
│   ├── test_auth_service_pg.py
│   ├── test_auth_service_sqlite.py
│   ├── test_governance_repo_pg.py
│   ├── test_governance_repo_sqlite.py
│   └── ...
├── e2e/                # End-to-end tests
│   ├── browser/        # Browser behavior (regression is a marker)
│   ├── manage/         # Admin UI tests
│   ├── performance/    # E2E performance tests
│   ├── remote/         # Remote workspace tests
│   ├── terminal/       # Terminal tests
│   ├── ui/             # UI/screenshot tests
│   └── work/           # Work-mode UI tests
├── performance/        # Timing/resource tests (scheduled lane)
└── conftest.py         # Shared fixtures
```

Regression and issue provenance are pytest markers, not additional copies or
top-level directories. Put a new bug test in its single runtime layer and use
`pytest.mark.regression` plus `pytest.mark.issue(number)`. See
`docs/dev/TEST_LAYERS.md` for the migration and CI policy.

### Writing Tests

```python
import pytest
from scripts.shared import db

def test_get_connection():
    """Test database connection."""
    conn = db.get_connection()
    assert conn is not None
    conn.close()

def test_get_daily_usage():
    """Test daily usage query."""
    result = db.get_daily_usage("2026-03-21")
    assert isinstance(result, list)
```

## UI Testing with Playwright

### Setup

```bash
# Install Playwright
pip install playwright

# Install browsers
playwright install chromium
```

### Running UI Tests

```bash
# Run UI tests
pytest tests/e2e/ui/

# Run specific test
pytest tests/e2e/ui/test_screenshot.py
```

### Example UI Test

```python
import asyncio
from playwright.async_api import async_playwright

async def test_login():
    """Test login functionality."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Navigate to login
        await page.goto('http://localhost:19888/login')

        # Fill form
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')

        # Wait for redirect
        await page.wait_for_url('http://localhost:19888/')

        await browser.close()
```

## Database Migrations

Database schema migrations use Alembic. See `migrations/` directory for migration files.

```bash
# Run migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"
```

### Data Migration

For data migration scripts (e.g., SQLite to PostgreSQL), see `scripts/utils/`:

```bash
# Migrate from SQLite to PostgreSQL
python3 scripts/utils/migrate_to_postgres.py
```

## Adding a New Data Source

To add support for a new AI tool:

1. Create `scripts/fetch_newtool.py`
2. Implement log parsing logic
3. Add to configuration template
4. Add tests

### Template

```python
#!/usr/bin/env python3
"""Fetch usage data from NewTool."""

import os
import sys
from pathlib import Path

# Add shared modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'shared'))

import db
import utils

def fetch_newtool(days: int = 7):
    """Fetch NewTool usage data."""
    log_path = Path.home() / '.newtool' / 'logs'

    for log_file in log_path.glob('*.jsonl'):
        # Parse log file
        # Extract token usage
        # Save to database
        pass

if __name__ == '__main__':
    fetch_newtool()
```

## Debugging

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Database Inspection

```bash
# Open SQLite database
sqlite3 ~/.open-ace/ace.db

# Query tables
.tables
.schema daily_usage
SELECT * FROM daily_usage LIMIT 10;

# PostgreSQL (if configured)
psql $DATABASE_URL
\dt
\d daily_usage
SELECT * FROM daily_usage LIMIT 10;
```

## Release Process

Direct commits to `main` are blocked by the `no-commit-to-branch` hook, so
releases go through a `release/vX.Y.Z` branch that is merged back by PR.

1. Cut the branch: `git checkout -b release/vX.Y.Z origin/main`
2. On that branch, curate and commit the `[Unreleased]` section of `CHANGELOG.md`
   (it becomes the release notes; the release script requires a clean tracked tree)
3. Run `./scripts/release.sh --version X.Y.Z` to update `pyproject.toml`, the
   frontend package and lockfile, and `CHANGELOG.md`; run
   `python3 scripts/check_release_version.py --tag vX.Y.Z`.
4. Commit the prepared files, open a PR from `release/vX.Y.Z` to `main`, and
   merge it after CI passes. Tag the merged main commit as `vX.Y.Z` and push
   that tag. Build the deployment tarball from the tag with
   `bash scripts/install-central/package-method/package.sh --version X.Y.Z`.
5. Publish the GitHub Release with `dist/open-ace-X.Y.Z.tar.gz` attached;
   `.github/workflows/release.yml` then builds the sdist/wheel, attaches them, and
   publishes to PyPI as `open-ace-server` via Trusted Publishing (enabled by the `PYPI_PUBLISH=true`
   repository variable; no API token). `docker-publish.yml` pushes the GHCR image
6. Refresh the docs site (`open-ace/open-ace-docs`): update the release
   summary in `src/pages/project/releases.js`, then redeploy so it re-syncs
   the docs from `main`

```bash
# Preview what the release script would change
./scripts/release.sh --version X.Y.Z --dry-run
```

## Getting Help

- Check existing documentation in `docs/`
- Search existing issues on GitHub
- Open a new issue for bugs or feature requests

---

## 中文

> **ACE** = **AI Computing Explorer**

本指南涵盖如何搭建开发环境以及如何为 Open ACE 贡献代码。

## 开发环境搭建

### 前提条件

- Python 3.10+
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

# 安装与 GitHub CI 完全一致的依赖集合
pip install -r requirements-ci.lock

# 仅浏览器 E2E 测试需要
playwright install chromium

# 初始化配置
python3 cli.py config init
```

### 启动后端与首次登录

```bash
# 在 19888 端口启动 Flask 后端（API + 已构建的前端）
python3 server.py
```

打开 `http://localhost:19888`，使用默认账号 `admin` / `admin123` 登录。首次登录后请立即修改该密码 —— 凭据处理与生产加固见 [DEPLOYMENT.md](../guide/DEPLOYMENT.md)。

## 项目结构

```
open-ace/
├── server.py              # Web 服务器入口
├── requirements.txt       # 生产依赖策略
├── requirements-ci.in     # 开发/CI 工具及生产依赖入口
├── requirements-ci.lock   # 本地/GitHub CI 统一的解析结果
│
├── app/                # Flask 应用
│   ├── __init__.py     # create_app() 工厂函数
│   ├── routes/         # 39 个 Blueprint 路由模块
│   ├── services/       # 42 个业务逻辑服务
│   ├── repositories/   # 26 个数据访问仓储
│   ├── modules/        # 领域逻辑包
│   │   ├── analytics/  # 使用分析、ROI、成本优化
│   │   ├── compliance/ # 审计分析、报告、数据保留
│   │   ├── governance/ # 审计日志、告警、配额、内容过滤
│   │   ├── sso/        # OAuth2/OIDC/SAML SSO
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
│   ├── terminal_server.py  # WebSocket 终端服务器（Linux/macOS 使用 PTY，Windows 使用管道子进程）
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
├── tests/              # 测试文件
│   ├── unit/           # 单元测试
│   ├── integration/    # 集成测试
│   └── e2e/            # 端到端测试
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
# 在 3000 端口启动开发服务器（API 代理到 localhost:19888）
npm run dev
```

前端需要后端在 19888 端口运行才能正常工作。

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

# CI PR / 发布门禁使用的服务依赖扩展测试
cd frontend && npm ci && npm run build && cd ..
python scripts/run_extended_tests.py --category critical --isolated-home

# 完整 E2E 或 issue 回归分片
python scripts/run_extended_tests.py --category e2e --isolated-home
python scripts/run_extended_tests.py --category e2e --split-total 4 --split-group 1 --isolated-home
```

完整前端参考请参阅 [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md)。

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
    from app.repositories.database import get_connection, adapt_sql

    conn = get_connection()
    cursor = conn.cursor()

    if tool_name:
        cursor.execute(
            adapt_sql("SELECT * FROM daily_usage WHERE date = ? AND tool_name = ?"),
            (date, tool_name)
        )
    else:
        cursor.execute(
            adapt_sql("SELECT * FROM daily_usage WHERE date = ?"),
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

# 仅运行单元测试
pytest tests/unit/

# 运行集成测试（SQLite + PostgreSQL）
pytest tests/integration/

# 仅运行 PostgreSQL 集成测试
pytest tests/integration/ -k "_pg"

# 运行指定测试文件
pytest tests/unit/test_db.py

# 带覆盖率
pytest --cov=app tests/
```

### 测试组织

```
tests/
├── unit/               # 单元测试
│   ├── test_message_service.py
│   ├── test_usage_service.py
│   └── ...
├── integration/        # 集成测试（SQLite + PostgreSQL）
│   ├── test_auth_service_pg.py
│   ├── test_auth_service_sqlite.py
│   ├── test_governance_repo_pg.py
│   ├── test_governance_repo_sqlite.py
│   └── ...
├── e2e/                # 端到端测试
│   ├── browser/        # 浏览器行为（regression 使用 marker）
│   ├── manage/         # 管理 UI 测试
│   ├── performance/    # E2E 性能测试
│   ├── remote/         # 远程工作区测试
│   ├── terminal/       # 终端测试
│   ├── ui/             # UI/截图测试
│   └── work/           # Work 模式 UI 测试
├── performance/        # 时间/资源测试（定时 lane）
├── issues/             # 历史隔离区；禁止新增测试
│   ├── 164/
│   ├── 517/
│   └── ...
└── conftest.py         # 共享 fixtures
```

回归和 issue 来源使用 pytest marker 表达，不再创建顶层目录或复制测试。新的缺陷
测试只放在一个运行层级，并添加 `pytest.mark.regression` 与
`pytest.mark.issue(number)`；迁移和 CI 规则见 `docs/dev/TEST_LAYERS.md`。

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
pytest tests/e2e/ui/

# 运行指定测试
pytest tests/e2e/ui/test_screenshot.py
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
        await page.goto('http://localhost:19888/login')

        # 填写表单
        await page.fill('#username', 'admin')
        await page.fill('#password', 'admin123')
        await page.click('button[type="submit"]')

        # 等待重定向
        await page.wait_for_url('http://localhost:19888/')

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
# 打开 SQLite 数据库
sqlite3 ~/.open-ace/ace.db

# 查询表
.tables
.schema daily_usage
SELECT * FROM daily_usage LIMIT 10;

# PostgreSQL（如果已配置）
psql $DATABASE_URL
\dt
\d daily_usage
SELECT * FROM daily_usage LIMIT 10;
```

## 发布流程

`no-commit-to-branch` hook 禁止直接 commit 到 `main`，因此发版走 `release/vX.Y.Z` 分支，再用 PR 合回 main。

1. 切分支：`git checkout -b release/vX.Y.Z origin/main`
2. 在该分支上整理并提交 `CHANGELOG.md` 的 `[Unreleased]` 段落（它就是发布说明；发布脚本要求已跟踪文件无未提交更改）
3. 运行 `./scripts/release.sh --version X.Y.Z`，同步更新 `pyproject.toml`、前端 package、锁文件和
   `CHANGELOG.md`；运行 `python3 scripts/check_release_version.py --tag vX.Y.Z`。
4. 提交变更，从 `release/vX.Y.Z` 向 `main` 创建 PR；CI 通过并合并后，给合并后的 main 提交打
   `vX.Y.Z` 标签并推送。然后从该标签运行
   `bash scripts/install-central/package-method/package.sh --version X.Y.Z` 构建部署包。
5. 发布 GitHub Release 并附带 `dist/open-ace-X.Y.Z.tar.gz`；
   `.github/workflows/release.yml` 随后构建 sdist/wheel 并附到 Release，再通过 Trusted Publishing 以 `open-ace-server` 名称发布到 PyPI（由仓库变量 `PYPI_PUBLISH=true` 开启，无需 API token）；`docker-publish.yml` 推送 GHCR 镜像
6. 刷新文档站（`open-ace/open-ace-docs`）：更新 `src/pages/project/releases.js` 中的版本摘要，然后重新部署，使其从 `main` 重新同步文档

```bash
# 预览发布脚本将做的改动
./scripts/release.sh --version X.Y.Z --dry-run
```

## 获取帮助

- 查看 `docs/` 中的现有文档
- 在 GitHub 上搜索已有 Issue
- 为 Bug 或功能需求创建新 Issue

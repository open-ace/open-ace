# Open ACE 项目分析与优化方案

> **ACE** = **AI Computing Explorer**
> 文档创建时间：2026-03-21
> 文档目的：全面分析项目现状，提出架构、设计、代码实现方面的优化建议

---

## 目录

1. [项目定位](#1-项目定位)
2. [架构问题分析](#2-架构问题分析)
3. [代码质量问题](#3-代码质量问题)
4. [功能完善建议](#4-功能完善建议)
5. [企业级特性补充](#5-企业级特性补充)
6. [开源项目必备要素](#6-开源项目必备要素)
7. [优化实施路线图](#7-优化实施路线图)
8. [项目健康度评估](#8-项目健康度评估)

---

## 1. 项目定位

### 1.1 核心定位

**Open ACE = 企业级AI工作平台**

```
┌─────────────────────────────────────────────────────────────────┐
│                        Open ACE 平台                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────────────┐     ┌─────────────────────┐          │
│   │        "用"         │     │        "管"         │          │
│   │     Workspace       │     │   Management Hub    │          │
│   ├─────────────────────┤     ├─────────────────────┤          │
│   │ • AI 对话           │     │ • 用量监视 Dashboard │          │
│   │ • 任务执行          │     │ • 消息追踪 Messages  │          │
│   │ • 工具调用          │     │ • 深度分析 Analysis  │          │
│   │ • 多工具支持        │     │ • 安全管控           │          │
│   └─────────────────────┘     │ • 成本优化           │          │
│                               └─────────────────────┘          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 核心价值主张

| 维度 | 目标 | 说明 |
|------|------|------|
| **"用"** | 方便用 | 让用户在 Workspace 上方便地与 AI 对话、完成各类任务 |
| **"管"** | 方便管 | 监视、分析、持续优化 AI 使用，保障安全可控 |

### 1.3 目标用户

| 用户角色 | 主要需求 | 使用场景 |
|----------|----------|----------|
| 普通用户 | AI 对话、任务执行 | Workspace |
| 团队管理者 | 用量监控、成本控制 | Dashboard, Analysis |
| 系统管理员 | 用户管理、安全管控 | Management |
| 企业决策者 | ROI 分析、合规报告 | Analysis |

---

## 2. 架构问题分析

### 2.1 当前架构现状

```
当前架构（单体应用）：
┌─────────────────────────────────────────────────────────────────┐
│  web.py (1955行)                                                │
│  ├── 所有 API 路由                                              │
│  ├── 业务逻辑                                                   │
│  └── 模板渲染                                                   │
├─────────────────────────────────────────────────────────────────┤
│  db.py (3131行)                                                 │
│  ├── 所有数据库操作                                              │
│  └── 数据聚合逻辑                                               │
├─────────────────────────────────────────────────────────────────┤
│  index.html (6762行)                                            │
│  ├── 所有前端页面                                               │
│  ├── JavaScript 逻辑                                            │
│  └── CSS 样式                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 问题分析

| 问题 | 影响 | 严重程度 |
|------|------|----------|
| 单文件过大 | 难以维护、难以测试、难以协作 | 🔴 高 |
| 缺少分层架构 | 业务逻辑与数据访问耦合 | 🔴 高 |
| 模块导入不规范 | 难以扩展、难以复用 | 🟡 中 |
| 配置管理分散 | 部署复杂、环境切换困难 | 🟡 中 |

### 2.3 建议的目标架构

```
目标架构：企业级分层架构
┌─────────────────────────────────────────────────────────────────┐
│                        Presentation Layer                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Web UI     │  │  CLI        │  │  REST API   │              │
│  │  (React/Vue)│  │  (cli.py)   │  │  (v1/v2)    │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
├─────────────────────────────────────────────────────────────────┤
│                        Application Layer                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Workspace   │  │ Analytics   │  │ Governance  │              │
│  │ Service     │  │ Service     │  │ Service     │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
├─────────────────────────────────────────────────────────────────┤
│                        Domain Layer                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Session     │  │ Message     │  │ Usage       │              │
│  │ Aggregate   │  │ Aggregate   │  │ Aggregate   │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
├─────────────────────────────────────────────────────────────────┤
│                        Infrastructure Layer                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Database    │  │ Cache       │  │ External    │              │
│  │ (SQLite/PG) │  │ (Redis)     │  │ APIs        │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

### 2.4 建议的目录结构

```
open-ace/
├── app/                          # 应用主目录
│   ├── __init__.py               # Flask app factory
│   │
│   ├── routes/                   # 路由层（API 端点）
│   │   ├── __init__.py
│   │   ├── usage.py              # 用量相关 API
│   │   ├── messages.py           # 消息相关 API
│   │   ├── analysis.py           # 分析相关 API
│   │   ├── auth.py               # 认证相关 API
│   │   └── workspace.py          # Workspace 相关 API
│   │
│   ├── services/                 # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── usage_service.py      # 用量统计服务
│   │   ├── message_service.py    # 消息处理服务
│   │   ├── analysis_service.py   # 分析服务
│   │   ├── auth_service.py       # 认证服务
│   │   └── workspace_service.py  # Workspace 服务
│   │
│   ├── repositories/             # 数据访问层
│   │   ├── __init__.py
│   │   ├── usage_repo.py         # 用量数据访问
│   │   ├── message_repo.py       # 消息数据访问
│   │   └── user_repo.py          # 用户数据访问
│   │
│   ├── models/                   # 数据模型
│   │   ├── __init__.py
│   │   ├── usage.py              # 用量模型
│   │   ├── message.py            # 消息模型
│   │   ├── session.py            # 会话模型
│   │   └── user.py               # 用户模型
│   │
│   ├── modules/                  # 功能模块
│   │   ├── workspace/            # "用" 相关模块
│   │   │   ├── session_manager.py
│   │   │   ├── prompt_library.py
│   │   │   └── tool_connector.py
│   │   │
│   │   ├── governance/           # "管" 相关模块
│   │   │   ├── quota_manager.py
│   │   │   ├── audit_logger.py
│   │   │   ├── content_filter.py
│   │   │   └── compliance.py
│   │   │
│   │   └── analytics/            # 分析模块
│   │       ├── usage_analytics.py
│   │       ├── cost_optimizer.py
│   │       └── roi_calculator.py
│   │
│   └── utils/                    # 工具函数
│       ├── __init__.py
│       ├── formatters.py
│       ├── validators.py
│       └── helpers.py
│
├── cli.py                        # CLI 入口
├── web.py                        # Web 服务入口（简化）
│
├── config/                       # 配置文件
│   ├── settings.py               # 配置类
│   ├── config.json.sample
│   └── remote_config.json.sample
│
├── templates/                    # HTML 模板（拆分）
│   ├── base.html
│   ├── components/
│   │   ├── sidebar.html
│   │   ├── navbar.html
│   │   └── footer.html
│   ├── pages/
│   │   ├── dashboard.html
│   │   ├── messages.html
│   │   ├── analysis.html
│   │   ├── management.html
│   │   └── workspace.html
│   └── auth/
│       ├── login.html
│       └── logout.html
│
├── static/                       # 静态资源
│   ├── js/
│   │   ├── app.js
│   │   ├── components/
│   │   ├── services/
│   │   └── utils/
│   └── css/
│       ├── main.css
│       ├── components.css
│       └── themes.css
│
├── tests/                        # 测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── docs/                         # 文档
├── scripts/                      # 脚本
└── migrations/                   # 数据库迁移
```

### 2.5 Workspace 架构优化

当前 Workspace 通过 iframe 嵌入，这是合理的解耦设计。建议增强：

| 功能 | 当前状态 | 建议 |
|------|----------|------|
| 身份认证 | 独立登录 | 单点登录 (SSO) |
| 状态同步 | 无 | 实时活动同步到管理后台 |
| 权限控制 | 基础 | 基于角色的访问控制 (RBAC) |
| 审计日志 | 无 | 记录所有 AI 操作 |

---

## 3. 代码质量问题

### 3.1 类型注解不完整

**当前代码：**
```python
def get_usage_by_date(date, tool_name=None, host_name=None):
    ...
```

**建议改进：**
```python
from typing import Optional, List, Dict, Any

def get_usage_by_date(
    date: str,
    tool_name: Optional[str] = None,
    host_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    ...
```

### 3.2 错误处理不一致

**当前代码：**
```python
def save_usage(...):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(...)  # 可能抛出异常但未处理
    conn.commit()
    conn.close()  # 异常时连接不会关闭
```

**建议改进：**
```python
from contextlib import contextmanager

@contextmanager
def get_db_connection():
    """数据库连接上下文管理器"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def save_usage(...):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(...)
        conn.commit()
```

### 3.3 数据库迁移管理不规范

**当前方式：**
```python
# 难以追踪变更历史
if 'host_name' not in columns:
    cursor.execute("ALTER TABLE daily_usage ADD COLUMN host_name TEXT")
```

**建议改进：** 使用 Alembic 进行迁移管理

```python
# migrations/versions/001_initial.py
def upgrade():
    op.create_table(
        'daily_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('tool_name', sa.String(), nullable=False),
        ...
    )

def downgrade():
    op.drop_table('daily_usage')
```

### 3.4 缺少日志系统

**当前代码：**
```python
print(f"Database initialized at {DB_PATH}")
```

**建议改进：**
```python
import logging

logger = logging.getLogger(__name__)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger.info(f"Database initialized at {DB_PATH}")
```

### 3.5 模块导入问题

**当前方式：**
```python
# 不够优雅
spec_db = importlib.util.spec_from_file_location('db', db_path)
db = importlib.util.module_from_spec(spec_db)
spec_db.loader.exec_module(db)
```

**建议改进：** 使用标准的 Python 包结构

```python
# 推荐方式
from openace.services import UsageService
from openace.repositories import UsageRepository
```

---

## 4. 功能完善建议

### 4.1 "用" - Workspace 功能增强

| 功能 | 当前状态 | 优先级 | 建议 |
|------|----------|--------|------|
| AI 对话 | ✅ 通过 iframe | - | 考虑原生集成或深度 API 对接 |
| 多工具支持 | ✅ Claude/Qwen/OpenClaw | - | 统一工具接入层 |
| 会话管理 | ⚠️ 基础 | 高 | 增强会话持久化和恢复 |
| 快捷指令 | ❌ 缺少 | 高 | 添加 Prompt 模板库 |
| 协作功能 | ❌ 缺少 | 中 | 团队共享会话、知识库 |
| 移动端支持 | ❌ 缺少 | 中 | 响应式设计优化 |

### 4.2 "管" - 管理功能增强

| 功能 | 当前状态 | 优先级 | 建议 |
|------|----------|--------|------|
| 用量监视 | ✅ Dashboard | - | 增加实时告警 |
| 消息追踪 | ✅ Messages | - | 增加敏感内容检测 |
| 深度分析 | ✅ Analysis | - | 增加 ROI 分析 |
| 用户管理 | ⚠️ 基础 | 高 | 完善 RBAC |
| 配额管理 | ⚠️ 基础 | 高 | 增加预算控制、超额预警 |
| 安全管控 | ❌ 缺少 | 高 | 敏感词过滤、操作审计 |
| 合规报告 | ❌ 缺少 | 中 | 自动生成合规报告 |

### 4.3 建议新增的核心模块

```
modules/
├── workspace/                   # "用" 相关
│   ├── session_manager.py       # 会话管理
│   │   - 会话持久化
│   │   - 会话恢复
│   │   - 会话归档
│   │
│   ├── prompt_library.py        # Prompt 模板库
│   │   - 模板管理
│   │   - 模板分类
│   │   - 模板共享
│   │
│   └── tool_connector.py        # 工具连接器
│       - 统一工具接口
│       - 工具注册
│       - 工具路由
│
├── governance/                  # "管" 相关
│   ├── quota_manager.py         # 配额管理
│   │   - 用户配额
│   │   - 团队配额
│   │   - 超额预警
│   │
│   ├── audit_logger.py          # 审计日志
│   │   - 操作记录
│   │   - 访问日志
│   │   - 变更追踪
│   │
│   ├── content_filter.py        # 内容安全过滤
│   │   - 敏感词检测
│   │   - PII 识别
│   │   - 内容脱敏
│   │
│   └── compliance.py            # 合规报告
│       - 使用报告
│       - 合规检查
│       - 审计报告
│
└── analytics/                   # 分析相关
    ├── usage_analytics.py       # 用量分析
    │   - 趋势分析
    │   - 异常检测
    │   - 预测分析
    │
    ├── cost_optimizer.py        # 成本优化建议
    │   - 成本分析
    │   - 优化建议
    │   - 节省报告
    │
    └── roi_calculator.py        # ROI 计算
        - 效率提升
        - 成本节省
        - 价值评估
```

---

## 5. 企业级特性补充

### 5.1 安全与合规

```python
# modules/governance/content_filter.py

from dataclasses import dataclass
from typing import List, Optional
import re

@dataclass
class FilterResult:
    """过滤结果"""
    passed: bool
    risk_level: str  # low, medium, high
    matched_rules: List[str]
    suggestion: Optional[str]

class ContentFilter:
    """敏感内容过滤"""
    
    def __init__(self, config: dict):
        self.sensitive_patterns = config.get('sensitive_patterns', [])
        self.pii_patterns = self._load_pii_patterns()
    
    def check_message(self, content: str) -> FilterResult:
        """检查消息内容"""
        matched = []
        risk_level = 'low'
        
        # 检查敏感词
        for pattern in self.sensitive_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                matched.append(f"sensitive:{pattern}")
                risk_level = 'high'
        
        # 检查 PII
        for pii_type, pattern in self.pii_patterns.items():
            if re.search(pattern, content):
                matched.append(f"pii:{pii_type}")
                risk_level = max(risk_level, 'medium')
        
        return FilterResult(
            passed=len(matched) == 0,
            risk_level=risk_level,
            matched_rules=matched,
            suggestion=self._get_suggestion(risk_level) if matched else None
        )
    
    def _load_pii_patterns(self) -> dict:
        """加载 PII 识别模式"""
        return {
            'email': r'[\w\.-]+@[\w\.-]+\.\w+',
            'phone': r'\d{3}-\d{4}-\d{4}',
            'credit_card': r'\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}',
        }
```

```python
# modules/governance/audit_logger.py

from datetime import datetime
from typing import Optional
from dataclasses import dataclass
import json

@dataclass
class AuditLog:
    """审计日志"""
    timestamp: datetime
    user_id: str
    action: str
    resource_type: str
    resource_id: str
    details: dict
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

class AuditLogger:
    """操作审计日志"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def log_action(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict = None,
        ip_address: str = None,
        user_agent: str = None
    ):
        """记录操作日志"""
        log = AuditLog(
            timestamp=datetime.utcnow(),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent
        )
        self._save_log(log)
    
    def query_logs(
        self,
        user_id: str = None,
        action: str = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = 100
    ) -> list:
        """查询审计日志"""
        ...
```

### 5.2 多租户支持

```python
# models/tenant.py

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass
class QuotaConfig:
    """配额配置"""
    daily_token_limit: int = 1000000
    monthly_token_limit: int = 30000000
    daily_request_limit: int = 1000
    monthly_request_limit: int = 30000

@dataclass
class TenantSettings:
    """租户设置"""
    allowed_tools: List[str] = field(default_factory=lambda: ['claude', 'qwen', 'openclaw'])
    content_filter_enabled: bool = True
    audit_log_enabled: bool = True
    data_retention_days: int = 90

@dataclass
class Tenant:
    """租户"""
    id: str
    name: str
    slug: str
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    settings: TenantSettings = field(default_factory=TenantSettings)
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_active: bool = True
```

```python
# models/user.py

from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from enum import Enum

class UserRole(Enum):
    """用户角色"""
    ADMIN = 'admin'
    MANAGER = 'manager'
    USER = 'user'

@dataclass
class Permission:
    """权限"""
    resource: str
    action: str  # read, write, delete, admin

@dataclass
class User:
    """用户"""
    id: str
    tenant_id: str
    username: str
    email: str
    role: UserRole = UserRole.USER
    permissions: List[Permission] = field(default_factory=list)
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    
    def has_permission(self, resource: str, action: str) -> bool:
        """检查权限"""
        if self.role == UserRole.ADMIN:
            return True
        return any(
            p.resource == resource and p.action in [action, 'admin']
            for p in self.permissions
        )
```

### 5.3 高可用部署

**Docker Compose 部署：**

```yaml
# docker-compose.yml
version: '3.8'

services:
  open-ace-web:
    image: open-ace:latest
    build: .
    ports:
      - "5001:5001"
    environment:
      - DATABASE_URL=postgresql://openace:password@postgres:5432/openace
      - REDIS_URL=redis://redis:6379/0
      - SECRET_KEY=${SECRET_KEY}
      - FLASK_ENV=production
    depends_on:
      - postgres
      - redis
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=openace
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=openace
    volumes:
      - pgdata:/var/lib/postgresql/data
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    restart: unless-stopped

  # 可选：Nginx 反向代理
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - open-ace-web
    restart: unless-stopped

volumes:
  pgdata:
  redisdata:
```

**Dockerfile：**

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非 root 用户
RUN useradd -m -u 1000 openace && \
    chown -R openace:openace /app
USER openace

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/health')" || exit 1

EXPOSE 5001

CMD ["python", "web.py"]
```

---

## 6. 开源项目必备要素

### 6.1 当前状态检查

| 项目 | 状态 | 优先级 | 说明 |
|------|------|--------|------|
| CI/CD 配置 | ⚠️ 有 .github 但需完善 | 高 | 需要添加更多检查 |
| 代码格式化工具 | ❌ 缺少 | 高 | 需要添加 black, isort |
| 类型检查 | ❌ 缺少 | 高 | 需要添加 mypy 配置 |
| Linting | ❌ 缺少 | 高 | 需要添加 ruff/flake8 |
| pre-commit hooks | ❌ 缺少 | 中 | 提交前自动检查 |
| API 文档 | ⚠️ 有但需完善 | 中 | 添加 OpenAPI/Swagger |
| 示例配置 | ✅ 有 sample | - | - |
| Docker 支持 | ❌ 缺少 | 中 | 添加 Dockerfile |
| 贡献者指南 | ✅ 有 CONTRIBUTING.md | - | - |
| 行为准则 | ❌ 缺少 | 低 | 添加 CODE_OF_CONDUCT.md |
| 更新日志 | ✅ 有 CHANGELOG.md | - | - |
| 安全政策 | ❌ 缺少 | 中 | 添加 SECURITY.md |

### 6.2 建议添加的配置文件

**pyproject.toml（统一项目配置）：**

```toml
[project]
name = "open-ace"
version = "1.0.0"
description = "Enterprise AI Workspace Platform - Track, analyze, and optimize AI usage"
readme = "README.md"
license = {text = "Apache-2.0"}
requires-python = ">=3.9"
authors = [
    {name = "Open ACE Team"}
]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: System Administrators",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]
dependencies = [
    "flask>=2.0.0",
    "aiohttp>=1.0.0",
    "websockets>=10.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "black>=23.0.0",
    "isort>=5.12.0",
    "ruff>=0.0.270",
    "mypy>=1.0.0",
    "pre-commit>=3.0.0",
]

[project.scripts]
openace = "openace.cli:main"

[tool.black]
line-length = 100
target-version = ['py39', 'py310', 'py311', 'py312']
include = '\.pyi?$'
exclude = '''
/(
    \.git
    | \.hg
    | \.mypy_cache
    | \.tox
    | \.venv
    | _build
    | buck-out
    | build
    | dist
)/
'''

[tool.isort]
profile = "black"
line_length = 100
known_first_party = ["openace"]

[tool.ruff]
line-length = 100
target-version = "py39"
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # Pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]
ignore = [
    "E501",  # line too long (handled by black)
    "B008",  # do not perform function calls in argument defaults
]

[tool.mypy]
python_version = "3.9"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --cov=app --cov-report=html --cov-report=term-missing"
filterwarnings = [
    "ignore::DeprecationWarning",
]

[tool.coverage.run]
source = ["app"]
omit = ["tests/*", "*/__pycache__/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

**.pre-commit-config.yaml：**

```yaml
# .pre-commit-config.yaml
repos:
  # 代码格式化
  - repo: https://github.com/psf/black
    rev: 23.12.1
    hooks:
      - id: black
        language_version: python3.11

  # import 排序
  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
        args: ["--profile", "black"]

  # Linting
  - repo: https://github.com/charliermarsh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]

  # 类型检查
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.8.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
        args: [--ignore-missing-imports]

  # 通用检查
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-json
      - id: check-added-large-files
        args: ['--maxkb=1000']
      - id: check-merge-conflict
      - id: debug-statements

  # 安全检查
  - repo: https://github.com/PyCQA/bandit
    rev: 1.7.6
    hooks:
      - id: bandit
        args: ["-c", "pyproject.toml"]
        additional_dependencies: ["bandit[toml]"]
```

**.github/workflows/ci.yml（完善 CI）：**

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install black isort ruff mypy
      
      - name: Run Black
        run: black --check .
      
      - name: Run isort
        run: isort --check-only --diff .
      
      - name: Run Ruff
        run: ruff check .
      
      - name: Run MyPy
        run: mypy app/

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.9', '3.10', '3.11', '3.12']
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      
      - name: Run tests
        run: pytest --cov=app --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml

  build:
    runs-on: ubuntu-latest
    needs: [lint, test]
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Build Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: false
          tags: open-ace:${{ github.sha }}
```

**SECURITY.md：**

```markdown
# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take security seriously. If you discover a vulnerability, please:

1. **Do not** open a public issue
2. Email us at security@open-ace.dev
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will respond within 48 hours and provide a timeline for the fix.

## Security Best Practices

When deploying Open ACE:

- Use HTTPS in production
- Set strong `SECRET_KEY` and `upload_auth_key`
- Enable content filtering for sensitive data
- Regularly audit access logs
- Keep dependencies updated
```

**CODE_OF_CONDUCT.md：**

```markdown
# Contributor Covenant Code of Conduct

## Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone, regardless of age, body
size, visible or invisible disability, ethnicity, sex characteristics, gender
identity and expression, level of experience, education, socio-economic status,
nationality, personal appearance, race, religion, or sexual identity and orientation.

## Our Standards

Examples of behavior that contributes to a positive environment:

* Demonstrating empathy and kindness toward other people
* Being respectful of differing opinions, viewpoints, and experiences
* Giving and gracefully accepting constructive feedback
* Accepting responsibility and apologizing to those affected by our mistakes

Examples of unacceptable behavior:

* The use of sexualized language or imagery
* Trolling, insulting or derogatory comments
* Public or private harassment
* Publishing others' private information without their permission

## Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be
reported to the community leaders at conduct@open-ace.dev.

## Attribution

This Code of Conduct is adapted from the [Contributor Covenant][homepage],
version 2.0.

[homepage]: https://www.contributor-covenant.org
```

---

## 7. 优化实施路线图

### Phase 1: 基础设施 (1-2 周)

**目标：建立项目基础设施，提升代码质量保障**

| 任务 | 预计时间 | 产出 |
|------|----------|------|
| 添加 `pyproject.toml` | 0.5 天 | 统一配置文件 |
| 配置 black, isort | 0.5 天 | 代码格式化 |
| 配置 ruff | 0.5 天 | Linting 检查 |
| 配置 mypy | 1 天 | 类型检查 |
| 添加 pre-commit hooks | 0.5 天 | 提交前检查 |
| 完善 CI/CD | 1 天 | 自动化测试 |
| 添加 Dockerfile | 0.5 天 | 容器化部署 |
| 添加 docker-compose.yml | 0.5 天 | 编排部署 |

### Phase 2: 架构重构 (3-4 周)

**目标：拆分大文件，建立分层架构**

| 任务 | 预计时间 | 产出 |
|------|----------|------|
| 创建 app 目录结构 | 1 天 | 模块化目录 |
| 拆分 web.py 为路由模块 | 3 天 | routes/ 模块 |
| 引入 Service Layer | 3 天 | services/ 模块 |
| 引入 Repository Pattern | 3 天 | repositories/ 模块 |
| 定义数据模型 | 2 天 | models/ 模块 |
| 统一错误处理 | 2 天 | 错误处理中间件 |
| 添加日志系统 | 1 天 | logging 配置 |
| 更新测试 | 3 天 | 测试覆盖 |

### Phase 3: "管" 功能增强 (2-3 周)

**目标：完善管理功能，提升企业级能力**

| 任务 | 预计时间 | 产出 |
|------|----------|------|
| 完善用户权限管理 (RBAC) | 3 天 | 权限系统 |
| 添加配额管理和预警 | 3 天 | quota_manager |
| 添加内容安全过滤 | 2 天 | content_filter |
| 添加操作审计日志 | 2 天 | audit_logger |
| 完善数据导出功能 | 2 天 | 导出 API |

### Phase 4: "用" 功能增强 (2-3 周)

**目标：提升用户体验，增强协作能力**

| 任务 | 预计时间 | 产出 |
|------|----------|------|
| Workspace 状态同步机制 | 3 天 | 状态同步 |
| Prompt 模板库 | 3 天 | prompt_library |
| 会话持久化和恢复 | 2 天 | session_manager |
| 团队协作功能 | 3 天 | 协作模块 |

### Phase 5: 企业级特性 (2 周)

**目标：支持企业级部署和运维**

| 任务 | 预计时间 | 产出 |
|------|----------|------|
| 多租户支持 | 3 天 | 租户系统 |
| SSO 集成 | 3 天 | SSO 模块 |
| 合规报告生成 | 2 天 | compliance 模块 |
| 高可用部署方案 | 2 天 | K8s 配置 |
| 性能优化 | 2 天 | 缓存、索引 |

---

## 8. 项目健康度评估

### 8.1 评分标准

| 维度 | 权重 | 评分标准 |
|------|------|----------|
| 功能完整性 | 20% | 核心功能是否完善，企业级特性是否具备 |
| 架构设计 | 20% | 是否分层清晰，是否易于扩展 |
| 代码质量 | 15% | 是否规范，是否有测试覆盖 |
| 可扩展性 | 15% | 是否易于添加新功能 |
| 安全合规 | 15% | 是否具备企业级安全特性 |
| 文档完善 | 15% | 文档是否齐全，是否易于上手 |

### 8.2 当前评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完整性 | ⭐⭐⭐☆☆ (3/5) | 核心功能完善，企业级特性待完善 |
| 架构设计 | ⭐⭐☆☆☆ (2/5) | 单体架构，需要分层重构 |
| 代码质量 | ⭐⭐⭐☆☆ (3/5) | 需要规范化，测试覆盖不足 |
| 可扩展性 | ⭐⭐☆☆☆ (2/5) | 大文件影响扩展 |
| 安全合规 | ⭐⭐☆☆☆ (2/5) | 缺少企业级安全特性 |
| 文档完善 | ⭐⭐⭐☆☆ (3/5) | 有基础文档，需完善 |

**总体评分：2.5/5.0**（作为企业级平台）

### 8.3 目标评分

完成所有优化后，预期达到：

| 维度 | 当前 | 目标 | 提升 |
|------|------|------|------|
| 功能完整性 | 3/5 | 4.5/5 | +1.5 |
| 架构设计 | 2/5 | 4/5 | +2 |
| 代码质量 | 3/5 | 4/5 | +1 |
| 可扩展性 | 2/5 | 4.5/5 | +2.5 |
| 安全合规 | 2/5 | 4/5 | +2 |
| 文档完善 | 3/5 | 4/5 | +1 |

**目标总体评分：4.2/5.0**

---

## 附录

### A. 参考资料

- [Flask 最佳实践](https://flask.palletsprojects.com/en/latest/patterns/)
- [Python 代码风格指南 (PEP 8)](https://pep8.org/)
- [Clean Architecture in Python](https://github.com/cosmic-python/code)
- [OWASP 安全指南](https://owasp.org/www-project-web-security-testing-guide/)

### B. 相关文档

- [架构说明](ARCHITECTURE.md)
- [部署指南](DEPLOYMENT.md)
- [开发指南](DEVELOPMENT.md)
- [核心概念](CONCEPTS.md)

---

*文档版本：1.0*
*最后更新：2026-03-21*
# Open ACE 功能实现情况分析报告

> **ACE** = **AI Computing Explorer**
> 文档创建时间：2026-03-21
> 文档目的：分析当前后端和前端功能实现情况，提出未实现功能的实现方案

---

## 目录

1. [后端功能实现情况](#1-后端功能实现情况)
2. [前端功能实现情况](#2-前端功能实现情况)
3. [尚未实现功能的实现方案](#3-尚未实现功能的实现方案)
4. [实施优先级建议](#4-实施优先级建议)
5. [总结](#5-总结)

---

## 1. 后端功能实现情况

### 1.1 已完整实现的模块

| 模块 | 文件位置 | 功能描述 |
|------|----------|----------|
| **核心路由** | `app/routes/` | usage, messages, analysis, auth, admin, upload, pages, fetch, report |
| **Governance** | `app/modules/governance/` | audit_logger（审计日志）、content_filter（内容过滤）、quota_manager（配额管理） |
| **Analytics** | `app/modules/analytics/` | usage_analytics（用量分析） |
| **Workspace** | `app/modules/workspace/` | prompt_library（模板库）、session_manager（会话管理）、tool_connector（工具连接）、state_sync（状态同步）、collaboration（协作） |
| **SSO** | `app/modules/sso/` | provider, oauth2, oidc, manager |
| **Compliance** | `app/modules/compliance/` | report（合规报告）、audit（审计分析）、retention（数据保留） |
| **Tenant** | `app/models/tenant.py` | 多租户模型、租户服务、租户仓库 |
| **Services 层** | `app/services/` | usage, message, auth, analysis, permission, workspace, tenant |
| **Repositories 层** | `app/repositories/` | usage, message, user, tenant, database |

### 1.2 模块详细说明

#### Governance 模块

```
app/modules/governance/
├── __init__.py
├── audit_logger.py      # 审计日志记录
├── content_filter.py    # 敏感内容过滤
└── quota_manager.py     # 配额管理和告警
```

**已实现功能**：
- ✅ 操作审计日志记录
- ✅ 敏感词检测和 PII 识别
- ✅ 用户配额追踪
- ✅ 配额告警生成（80%、95%、100%）

#### Workspace 模块

```
app/modules/workspace/
├── __init__.py
├── prompt_library.py    # Prompt 模板库
├── session_manager.py   # 会话持久化管理
├── tool_connector.py    # AI 工具连接器
├── state_sync.py        # 状态同步管理
└── collaboration.py     # 团队协作功能
```

**已实现功能**：
- ✅ Prompt 模板 CRUD 操作
- ✅ 模板分类和标签管理
- ✅ 模板变量渲染
- ✅ 会话创建、恢复、归档
- ✅ 会话消息管理
- ✅ 工具健康检查
- ✅ 实时状态同步
- ✅ 团队管理和会话共享

#### SSO 模块

```
app/modules/sso/
├── __init__.py
├── provider.py          # SSO 提供者基类
├── oauth2.py            # OAuth2 实现
├── oidc.py              # OIDC 实现
└── manager.py           # SSO 管理器
```

**已实现功能**：
- ✅ OAuth2 认证流程
- ✅ OIDC 认证流程
- ✅ 多提供者支持

#### Compliance 模块

```
app/modules/compliance/
├── __init__.py
├── report.py            # 合规报告生成
├── audit.py             # 审计分析
└── retention.py         # 数据保留管理
```

**已实现功能**：
- ✅ 合规报告生成
- ✅ 审计数据分析
- ✅ 数据保留策略

### 1.3 部分实现/待完善的模块

| 模块 | 当前状态 | 缺失部分 |
|------|----------|----------|
| **ROI Calculator** | 未实现 | 成本效益分析、价值评估 |
| **Cost Optimizer** | 未实现 | 成本优化建议、节省报告 |
| **实时告警** | 框架存在 | WebSocket 推送、邮件/消息通知 |
| **移动端 API** | 未实现 | 响应式 API 优化 |

---

## 2. 前端功能实现情况

### 2.1 页面与用户角色对应关系

根据 `static/js/auth.js` 中的 `updateNavMenu` 函数，页面按用户角色区分显示：

| 用户角色 | 可见页面 |
|----------|----------|
| **Admin 用户** | Dashboard、Messages、Analysis、Management |
| **普通用户** | Workspace、Report |

### 2.2 已完整实现的功能

| 页面 | 功能 | 可见用户 | 状态 |
|------|------|----------|------|
| **Dashboard** | 用量监视、图表展示、数据概览 | Admin | ✅ 完整 |
| **Messages** | 消息追踪、搜索、过滤、详情查看 | Admin | ✅ 完整 |
| **Analysis** | 深度分析、趋势图表、对比分析、对话历史（Conversation History tab） | Admin | ✅ 完整 |
| **Management** | 用户管理、配额管理、审计日志、内容过滤、安全设置 | Admin | ✅ 完整 |
| **Workspace** | AI 工作空间（iframe 嵌入外部工具） | 普通用户 | ✅ 完整 |
| **Report** | 个人用量报告、Token 使用图表、请求统计 | 普通用户 | ✅ 完整 |

### 2.3 Workspace 页面实现详情

Workspace 页面通过 iframe 嵌入外部 AI 工具，配置方式：

```json
// config.json
{
    "workspace": {
        "enabled": true,
        "url": "http://your-workspace-url"
    }
}
```

**前端代码**（`templates/index.html`）：
```html
<div id="workspace-section" class="content-section" style="display: none;">
    {% if workspace_enabled %}
    <iframe src="{{ workspace_url }}/" id="workspace-frame"
            style="width: 100%; height: calc(100vh - 160px); border: none;">
    </iframe>
    {% else %}
    <div>Workspace Not Configured</div>
    {% endif %}
</div>
```

### 2.4 Report 页面实现详情

Report 页面为普通用户提供个人用量报告：

- Token 使用统计
- 请求次数统计
- 按工具分类的使用图表
- 趋势图表展示

### 2.5 待完善的前端功能

| 功能 | 说明 | 优先级 |
|------|------|--------|
| **Prompt 模板库 UI** | 模板管理、分类、搜索、使用界面（后端 API 已实现） | 中 |
| **会话管理 UI** | 会话列表、恢复、归档界面（后端 API 已实现） | 中 |
| **团队协作 UI** | 团队管理、会话共享、知识库界面（后端 API 已实现） | 低 |
| **实时告警通知** | 前端通知组件、告警中心 | 中 |
| **SSO 登录适配** | 前端登录页面适配 SSO 流程 | 低 |

---

## 3. 尚未实现功能的实现方案

### 3.1 Prompt 模板库 UI 实现方案

**目标**：为用户提供 Prompt 模板管理界面

**当前状态**：后端 API 已完整实现（`app/modules/workspace/prompt_library.py`），前端 UI 待开发

#### 实施步骤

```
Phase 1: 模板列表页面 (1-2 天)
├── 模板列表表格/卡片视图
├── 分类筛选器
├── 标签筛选器
├── 搜索功能
└── 分页组件

Phase 2: 模板创建/编辑 (1-2 天)
├── 模板创建表单
├── 模板编辑表单
├── 变量定义界面
└── 模板预览

Phase 3: 模板使用 (1 天)
├── 变量填充界面
├── 模板渲染预览
└── 一键复制/发送到 Workspace
```

#### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/prompts` | GET | 获取模板列表 |
| `/api/workspace/prompts` | POST | 创建模板 |
| `/api/workspace/prompts/<id>` | GET | 获取模板详情 |
| `/api/workspace/prompts/<id>` | PUT | 更新模板 |
| `/api/workspace/prompts/<id>` | DELETE | 删除模板 |
| `/api/workspace/prompts/<id>/render` | POST | 渲染模板 |
| `/api/workspace/prompts/categories` | GET | 获取分类列表 |
| `/api/workspace/prompts/featured` | GET | 获取精选模板 |

#### 前端代码示例

```html
<!-- Prompt 模板库面板 -->
<div id="prompt-library-panel" class="card">
    <div class="card-header d-flex justify-content-between">
        <h5>Prompt Templates</h5>
        <button class="btn btn-primary btn-sm" onclick="showCreateTemplateModal()">
            <i class="bi bi-plus"></i> New Template
        </button>
    </div>
    <div class="card-body">
        <!-- 筛选器 -->
        <div class="row mb-3">
            <div class="col-md-4">
                <select id="category-filter" class="form-select" onchange="loadTemplates()">
                    <option value="">All Categories</option>
                </select>
            </div>
            <div class="col-md-4">
                <input type="text" id="search-input" class="form-control" 
                       placeholder="Search templates..." onkeyup="debounce(loadTemplates, 300)">
            </div>
        </div>
        <!-- 模板列表 -->
        <div id="template-list" class="row"></div>
    </div>
</div>
```

```javascript
// 加载模板列表
async function loadTemplates() {
    const category = document.getElementById('category-filter').value;
    const search = document.getElementById('search-input').value;
    
    const params = new URLSearchParams();
    if (category) params.append('category', category);
    if (search) params.append('search', search);
    
    const response = await fetch(`/api/workspace/prompts?${params}`);
    const data = await response.json();
    
    if (data.success) {
        renderTemplateList(data.data.templates);
    }
}

// 渲染模板列表
function renderTemplateList(templates) {
    const container = document.getElementById('template-list');
    container.innerHTML = templates.map(t => `
        <div class="col-md-4 mb-3">
            <div class="card h-100">
                <div class="card-body">
                    <h6 class="card-title">${t.name}</h6>
                    <p class="card-text small text-muted">${t.description}</p>
                    <div class="d-flex gap-1">
                        ${t.tags.map(tag => `<span class="badge bg-secondary">${tag}</span>`).join('')}
                    </div>
                </div>
                <div class="card-footer bg-transparent">
                    <button class="btn btn-sm btn-outline-primary" onclick="useTemplate(${t.id})">
                        Use
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="editTemplate(${t.id})">
                        Edit
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}
```

---

### 3.2 会话管理 UI 实现方案

**目标**：提供会话持久化管理界面

**当前状态**：后端 API 已完整实现（`app/modules/workspace/session_manager.py`），前端 UI 待开发

#### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/workspace/sessions` | GET | 获取会话列表 |
| `/api/workspace/sessions` | POST | 创建会话 |
| `/api/workspace/sessions/<id>` | GET | 获取会话详情 |
| `/api/workspace/sessions/<id>/messages` | POST | 添加消息 |
| `/api/workspace/sessions/<id>/complete` | POST | 完成会话 |
| `/api/workspace/sessions/<id>` | DELETE | 删除会话 |

---

### 3.3 实时告警系统实现方案

**目标**：实时推送配额告警、系统通知

#### 后端实现

```python
# app/modules/governance/alert_notifier.py

import asyncio
import logging
from typing import List, Callable, Set
from dataclasses import dataclass, asdict
from datetime import datetime
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """告警数据结构"""
    alert_id: str
    alert_type: str  # quota, system, security
    severity: str    # info, warning, critical
    title: str
    message: str
    user_id: int
    created_at: datetime
    
    def to_dict(self):
        return {
            **asdict(self),
            'created_at': self.created_at.isoformat()
        }


class AlertNotifier:
    """实时告警通知器"""
    
    def __init__(self):
        self._subscribers: List[Callable] = []
        self._websocket_clients: Set = set()
        self._email_config = {}
        self._webhooks = {}
    
    def subscribe(self, callback: Callable):
        """订阅告警"""
        self._subscribers.append(callback)
    
    def register_websocket(self, ws):
        """注册 WebSocket 客户端"""
        self._websocket_clients.add(ws)
    
    def unregister_websocket(self, ws):
        """注销 WebSocket 客户端"""
        self._websocket_clients.discard(ws)
    
    async def broadcast(self, alert: Alert):
        """广播告警到所有订阅者"""
        # 1. 调用订阅者回调
        for callback in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"Error in alert callback: {e}")
        
        # 2. WebSocket 推送
        alert_dict = alert.to_dict()
        for ws in list(self._websocket_clients):
            try:
                await ws.send_json(alert_dict)
            except Exception as e:
                logger.error(f"Error sending to WebSocket: {e}")
                self._websocket_clients.discard(ws)
    
    async def send_email_alert(self, alert: Alert, user_email: str):
        """发送邮件告警"""
        # TODO: 实现邮件发送逻辑
        logger.info(f"Sending email alert to {user_email}: {alert.title}")
    
    async def send_webhook_alert(self, alert: Alert, webhook_url: str):
        """发送 Webhook 告警"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=alert.to_dict(),
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status >= 400:
                        logger.error(f"Webhook failed: {response.status}")
        except Exception as e:
            logger.error(f"Error sending webhook: {e}")
    
    async def notify_user(self, user_id: int, alert: Alert):
        """通知特定用户"""
        # 获取用户通知偏好
        preferences = await self._get_user_preferences(user_id)
        
        # WebSocket 推送
        # (通过用户关联的 WebSocket 连接)
        
        # 邮件通知
        if preferences.get('email_enabled') and alert.severity in ['warning', 'critical']:
            await self.send_email_alert(alert, preferences.get('email'))
        
        # Webhook 通知
        if preferences.get('webhook_url'):
            await self.send_webhook_alert(alert, preferences['webhook_url'])
    
    async def _get_user_preferences(self, user_id: int) -> dict:
        """获取用户通知偏好"""
        # TODO: 从数据库获取
        return {
            'email_enabled': True,
            'email': f'user{user_id}@example.com',
            'webhook_url': None
        }


# 全局告警通知器实例
_alert_notifier: AlertNotifier = None


def get_alert_notifier() -> AlertNotifier:
    """获取全局告警通知器"""
    global _alert_notifier
    if _alert_notifier is None:
        _alert_notifier = AlertNotifier()
    return _alert_notifier
```

#### WebSocket 路由

```python
# app/routes/alerts.py

from flask import Blueprint, request
from flask_socketio import emit, join_room, leave_room
import logging

logger = logging.getLogger(__name__)

alerts_bp = Blueprint('alerts', __name__)


# Socket.IO 事件处理
def register_socket_events(socketio):
    @socketio.on('connect', namespace='/alerts')
    def handle_connect():
        logger.info(f"Client connected to alerts: {request.sid}")
    
    @socketio.on('disconnect', namespace='/alerts')
    def handle_disconnect():
        logger.info(f"Client disconnected from alerts: {request.sid}")
    
    @socketio.on('subscribe', namespace='/alerts')
    def handle_subscribe(data):
        user_id = data.get('user_id')
        if user_id:
            join_room(f'user_{user_id}')
            logger.info(f"User {user_id} subscribed to alerts")
    
    @socketio.on('unsubscribe', namespace='/alerts')
    def handle_unsubscribe(data):
        user_id = data.get('user_id')
        if user_id:
            leave_room(f'user_{user_id}')
```

#### 前端实现

```javascript
// WebSocket 连接管理
class AlertManager {
    constructor() {
        this.socket = null;
        this.connected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
    }
    
    connect(userId) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/alerts/ws`;
        
        this.socket = new WebSocket(wsUrl);
        
        this.socket.onopen = () => {
            console.log('Alert WebSocket connected');
            this.connected = true;
            this.reconnectAttempts = 0;
            
            // 订阅用户告警
            this.socket.send(JSON.stringify({
                type: 'subscribe',
                user_id: userId
            }));
        };
        
        this.socket.onmessage = (event) => {
            const alert = JSON.parse(event.data);
            this.handleAlert(alert);
        };
        
        this.socket.onclose = () => {
            console.log('Alert WebSocket disconnected');
            this.connected = false;
            this.attemptReconnect(userId);
        };
        
        this.socket.onerror = (error) => {
            console.error('Alert WebSocket error:', error);
        };
    }
    
    attemptReconnect(userId) {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
            setTimeout(() => this.connect(userId), delay);
        }
    }
    
    handleAlert(alert) {
        console.log('Received alert:', alert);
        
        // 显示通知
        this.showNotification(alert);
        
        // 更新告警计数
        this.updateAlertBadge();
        
        // 如果是严重告警，显示模态框
        if (alert.severity === 'critical') {
            this.showCriticalAlertModal(alert);
        }
    }
    
    showNotification(alert) {
        // 使用 Bootstrap Toast
        const toastContainer = document.getElementById('alert-toast-container');
        if (!toastContainer) return;
        
        const severityClass = {
            'info': 'bg-info',
            'warning': 'bg-warning',
            'critical': 'bg-danger'
        }[alert.severity] || 'bg-secondary';
        
        const toastHtml = `
            <div class="toast ${severityClass} text-white" role="alert">
                <div class="toast-header">
                    <strong class="me-auto">${alert.title}</strong>
                    <small>${new Date(alert.created_at).toLocaleTimeString()}</small>
                    <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
                </div>
                <div class="toast-body">
                    ${alert.message}
                </div>
            </div>
        `;
        
        toastContainer.insertAdjacentHTML('beforeend', toastHtml);
        const toastEl = toastContainer.lastElementChild;
        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();
        
        toastEl.addEventListener('hidden.bs.toast', () => {
            toastEl.remove();
        });
    }
    
    updateAlertBadge() {
        const badge = document.getElementById('alert-badge');
        if (badge) {
            const count = parseInt(badge.textContent || '0') + 1;
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline' : 'none';
        }
    }
    
    showCriticalAlertModal(alert) {
        // 显示严重告警模态框
        const modal = new bootstrap.Modal(document.getElementById('critical-alert-modal'));
        document.getElementById('critical-alert-title').textContent = alert.title;
        document.getElementById('critical-alert-message').textContent = alert.message;
        modal.show();
    }
}

// 初始化
const alertManager = new AlertManager();
```

---

### 3.4 ROI 分析模块实现方案

**目标**：计算和展示 AI 使用的投资回报率

#### 后端实现

```python
# app/modules/analytics/roi_calculator.py

"""
Open ACE - ROI Calculator Module

Calculates Return on Investment for AI usage.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ROIMetrics:
    """ROI 指标数据结构"""
    period: str
    start_date: str
    end_date: str
    total_cost: float = 0.0
    tokens_used: int = 0
    requests_made: int = 0
    estimated_hours_saved: float = 0.0
    estimated_savings: float = 0.0
    productivity_gain: float = 0.0
    roi_percentage: float = 0.0
    cost_per_request: float = 0.0
    cost_per_token: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            'period': self.period,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'total_cost': round(self.total_cost, 4),
            'tokens_used': self.tokens_used,
            'requests_made': self.requests_made,
            'estimated_hours_saved': round(self.estimated_hours_saved, 2),
            'estimated_savings': round(self.estimated_savings, 2),
            'productivity_gain': round(self.productivity_gain, 2),
            'roi_percentage': round(self.roi_percentage, 2),
            'cost_per_request': round(self.cost_per_request, 6),
            'cost_per_token': round(self.cost_per_token, 8),
        }


@dataclass
class ModelPricing:
    """模型定价"""
    input_price: float   # per 1K tokens
    output_price: float  # per 1K tokens


class ROICalculator:
    """ROI 计算器"""
    
    # 模型定价（每 1K tokens，美元）
    MODEL_PRICING = {
        'claude-3-opus': ModelPricing(input_price=0.015, output_price=0.075),
        'claude-3-sonnet': ModelPricing(input_price=0.003, output_price=0.015),
        'claude-3-haiku': ModelPricing(input_price=0.00025, output_price=0.00125),
        'claude-3-5-sonnet': ModelPricing(input_price=0.003, output_price=0.015),
        'qwen-max': ModelPricing(input_price=0.02, output_price=0.06),
        'qwen-plus': ModelPricing(input_price=0.004, output_price=0.012),
        'qwen-turbo': ModelPricing(input_price=0.002, output_price=0.006),
        'gpt-4': ModelPricing(input_price=0.03, output_price=0.06),
        'gpt-4-turbo': ModelPricing(input_price=0.01, output_price=0.03),
        'gpt-3.5-turbo': ModelPricing(input_price=0.0005, output_price=0.0015),
    }
    
    # 默认定价（未知模型）
    DEFAULT_PRICING = ModelPricing(input_price=0.01, output_price=0.03)
    
    # 假设人工处理成本
    HOURLY_LABOR_COST = 50.0  # 美元/小时
    
    # AI 效率提升倍数
    PRODUCTIVITY_MULTIPLIER = 10.0
    
    # 平均每个请求节省的时间（分钟）
    AVG_TIME_SAVED_PER_REQUEST = 5.0
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def calculate_roi(
        self,
        start_date: str,
        end_date: str,
        user_id: Optional[int] = None,
        tool_name: Optional[str] = None
    ) -> ROIMetrics:
        """
        计算指定时间段的 ROI
        
        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            user_id: 可选的用户 ID 筛选
            tool_name: 可选的工具名称筛选
        
        Returns:
            ROIMetrics: ROI 指标
        """
        import sqlite3
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 构建查询
        query = '''
            SELECT 
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(input_tokens + output_tokens) as total_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        '''
        params = [start_date, end_date]
        
        if user_id:
            query += ' AND user_id = ?'
            params.append(user_id)
        
        if tool_name:
            query += ' AND tool_name = ?'
            params.append(tool_name)
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        # 获取模型使用详情
        model_query = '''
            SELECT tool_name, model, 
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        '''
        model_params = [start_date, end_date]
        
        if user_id:
            model_query += ' AND user_id = ?'
            model_params.append(user_id)
        
        if tool_name:
            model_query += ' AND tool_name = ?'
            model_params.append(tool_name)
        
        model_query += ' GROUP BY tool_name, model'
        
        cursor.execute(model_query, model_params)
        model_rows = cursor.fetchall()
        conn.close()
        
        # 计算成本
        total_cost = 0.0
        for model_row in model_rows:
            model_name = model_row['model'] or 'default'
            pricing = self.MODEL_PRICING.get(model_name, self.DEFAULT_PRICING)
            
            input_cost = (model_row['input_tokens'] or 0) / 1000 * pricing.input_price
            output_cost = (model_row['output_tokens'] or 0) / 1000 * pricing.output_price
            total_cost += input_cost + output_cost
        
        # 获取统计数据
        requests = row['request_count'] or 0
        tokens = row['total_tokens'] or 0
        
        # 计算节省
        estimated_hours_saved = requests * self.AVG_TIME_SAVED_PER_REQUEST / 60
        estimated_savings = estimated_hours_saved * self.HOURLY_LABOR_COST
        
        # 计算 ROI
        if total_cost > 0:
            roi_percentage = ((estimated_savings - total_cost) / total_cost) * 100
        else:
            roi_percentage = 0.0
        
        # 效率提升
        productivity_gain = (self.PRODUCTIVITY_MULTIPLIER - 1) * 100
        
        # 单位成本
        cost_per_request = total_cost / requests if requests > 0 else 0
        cost_per_token = total_cost / tokens if tokens > 0 else 0
        
        return ROIMetrics(
            period=f"{start_date} to {end_date}",
            start_date=start_date,
            end_date=end_date,
            total_cost=total_cost,
            tokens_used=tokens,
            requests_made=requests,
            estimated_hours_saved=estimated_hours_saved,
            estimated_savings=estimated_savings,
            productivity_gain=productivity_gain,
            roi_percentage=roi_percentage,
            cost_per_request=cost_per_request,
            cost_per_token=cost_per_token,
        )
    
    def get_roi_trend(
        self,
        months: int = 6,
        user_id: Optional[int] = None
    ) -> List[ROIMetrics]:
        """
        获取 ROI 趋势
        
        Args:
            months: 月数
            user_id: 可选的用户 ID
        
        Returns:
            List[ROIMetrics]: ROI 趋势列表
        """
        trends = []
        today = datetime.utcnow()
        
        for i in range(months):
            end_date = today - timedelta(days=i*30)
            start_date = end_date - timedelta(days=30)
            
            roi = self.calculate_roi(
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d'),
                user_id
            )
            trends.append(roi)
        
        return list(reversed(trends))
    
    def get_roi_by_tool(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, ROIMetrics]:
        """
        按工具获取 ROI
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            Dict[str, ROIMetrics]: 工具名称到 ROI 的映射
        """
        import sqlite3
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT tool_name FROM daily_usage
            WHERE date >= ? AND date <= ?
        ''', (start_date, end_date))
        
        tools = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        result = {}
        for tool in tools:
            if tool:
                result[tool] = self.calculate_roi(start_date, end_date, tool_name=tool)
        
        return result
    
    def get_roi_by_user(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[int, ROIMetrics]:
        """
        按用户获取 ROI
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            Dict[int, ROIMetrics]: 用户 ID 到 ROI 的映射
        """
        import sqlite3
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT user_id FROM daily_usage
            WHERE date >= ? AND date <= ? AND user_id IS NOT NULL
        ''', (start_date, end_date))
        
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        result = {}
        for user_id in users:
            result[user_id] = self.calculate_roi(start_date, end_date, user_id=user_id)
        
        return result
```

#### API 路由

```python
# app/routes/roi.py

from flask import Blueprint, jsonify, request
from app.modules.analytics.roi_calculator import ROICalculator
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

roi_bp = Blueprint('roi', __name__)

CONFIG_DIR = Path.home() / ".open-ace"
DB_PATH = CONFIG_DIR / "usage.db"


@roi_bp.route('/roi', methods=['GET'])
def get_roi():
    """获取 ROI 指标"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        user_id = request.args.get('user_id', type=int)
        tool_name = request.args.get('tool_name')
        
        if not start_date or not end_date:
            return jsonify({
                'success': False,
                'error': 'start_date and end_date are required'
            }), 400
        
        calculator = ROICalculator(str(DB_PATH))
        roi = calculator.calculate_roi(start_date, end_date, user_id, tool_name)
        
        return jsonify({
            'success': True,
            'data': roi.to_dict()
        })
    except Exception as e:
        logger.error(f"Error calculating ROI: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/trend', methods=['GET'])
def get_roi_trend():
    """获取 ROI 趋势"""
    try:
        months = request.args.get('months', default=6, type=int)
        user_id = request.args.get('user_id', type=int)
        
        calculator = ROICalculator(str(DB_PATH))
        trends = calculator.get_roi_trend(months, user_id)
        
        return jsonify({
            'success': True,
            'data': [t.to_dict() for t in trends]
        })
    except Exception as e:
        logger.error(f"Error getting ROI trend: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@roi_bp.route('/roi/by-tool', methods=['GET'])
def get_roi_by_tool():
    """按工具获取 ROI"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if not start_date or not end_date:
            return jsonify({
                'success': False,
                'error': 'start_date and end_date are required'
            }), 400
        
        calculator = ROICalculator(str(DB_PATH))
        roi_by_tool = calculator.get_roi_by_tool(start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': {k: v.to_dict() for k, v in roi_by_tool.items()}
        })
    except Exception as e:
        logger.error(f"Error getting ROI by tool: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
```

---

### 3.5 成本优化建议模块实现方案

```python
# app/modules/analytics/cost_optimizer.py

"""
Open ACE - Cost Optimizer Module

Analyzes usage patterns and provides cost optimization suggestions.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from enum import Enum
import sqlite3

logger = logging.getLogger(__name__)


class OptimizationType(Enum):
    """优化类型"""
    MODEL_SWITCH = 'model_switch'
    USAGE_PATTERN = 'usage_pattern'
    QUOTA_ADJUSTMENT = 'quota_adjustment'
    TOOL_CONSOLIDATION = 'tool_consolidation'
    TIME_OPTIMIZATION = 'time_optimization'


class Priority(Enum):
    """优先级"""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


@dataclass
class OptimizationSuggestion:
    """优化建议"""
    suggestion_id: str
    suggestion_type: str
    title: str
    description: str
    potential_savings: float
    priority: str
    action_items: List[str] = field(default_factory=list)
    affected_users: List[int] = field(default_factory=list)
    affected_tools: List[str] = field(default_factory=list)
    implementation_effort: str = 'medium'  # low, medium, high
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> dict:
        return {
            'suggestion_id': self.suggestion_id,
            'suggestion_type': self.suggestion_type,
            'title': self.title,
            'description': self.description,
            'potential_savings': round(self.potential_savings, 2),
            'priority': self.priority,
            'action_items': self.action_items,
            'affected_users': self.affected_users,
            'affected_tools': self.affected_tools,
            'implementation_effort': self.implementation_effort,
            'created_at': self.created_at.isoformat(),
        }


class CostOptimizer:
    """成本优化分析器"""
    
    # 模型定价（每 1K tokens）
    MODEL_PRICING = {
        'claude-3-opus': {'input': 0.015, 'output': 0.075},
        'claude-3-sonnet': {'input': 0.003, 'output': 0.015},
        'claude-3-haiku': {'input': 0.00025, 'output': 0.00125},
        'claude-3-5-sonnet': {'input': 0.003, 'output': 0.015},
        'qwen-max': {'input': 0.02, 'output': 0.06},
        'qwen-plus': {'input': 0.004, 'output': 0.012},
        'qwen-turbo': {'input': 0.002, 'output': 0.006},
    }
    
    # 模型层级（从贵到便宜）
    MODEL_HIERARCHY = {
        'claude': ['claude-3-opus', 'claude-3-sonnet', 'claude-3-haiku'],
        'qwen': ['qwen-max', 'qwen-plus', 'qwen-turbo'],
    }
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def analyze(self, days: int = 30) -> List[OptimizationSuggestion]:
        """
        分析使用数据并生成优化建议
        
        Args:
            days: 分析最近多少天的数据
        
        Returns:
            List[OptimizationSuggestion]: 优化建议列表
        """
        suggestions = []
        
        end_date = datetime.utcnow().strftime('%Y-%m-%d')
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # 获取使用数据
        usage_data = self._get_usage_data(start_date, end_date)
        
        # 1. 分析模型使用
        model_suggestions = self._analyze_model_usage(usage_data)
        suggestions.extend(model_suggestions)
        
        # 2. 分析使用模式
        pattern_suggestions = self._analyze_usage_patterns(usage_data)
        suggestions.extend(pattern_suggestions)
        
        # 3. 分析配额效率
        quota_suggestions = self._analyze_quota_efficiency(usage_data)
        suggestions.extend(quota_suggestions)
        
        # 4. 分析工具使用
        tool_suggestions = self._analyze_tool_usage(usage_data)
        suggestions.extend(tool_suggestions)
        
        # 按潜在节省排序
        suggestions.sort(key=lambda x: x.potential_savings, reverse=True)
        
        return suggestions
    
    def _get_usage_data(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """获取使用数据"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 总体统计
        cursor.execute('''
            SELECT 
                COUNT(*) as total_requests,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
        ''', (start_date, end_date))
        overall = cursor.fetchone()
        
        # 按模型统计
        cursor.execute('''
            SELECT tool_name, model,
                   COUNT(*) as requests,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_request
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY tool_name, model
        ''', (start_date, end_date))
        by_model = cursor.fetchall()
        
        # 按用户统计
        cursor.execute('''
            SELECT user_id,
                   COUNT(*) as requests,
                   SUM(input_tokens + output_tokens) as total_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ? AND user_id IS NOT NULL
            GROUP BY user_id
        ''', (start_date, end_date))
        by_user = cursor.fetchall()
        
        # 按时间段统计
        cursor.execute('''
            SELECT strftime('%H', timestamp) as hour,
                   COUNT(*) as requests
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY hour
            ORDER BY requests DESC
        ''', (start_date, end_date))
        by_hour = cursor.fetchall()
        
        conn.close()
        
        return {
            'overall': overall,
            'by_model': by_model,
            'by_user': by_user,
            'by_hour': by_hour,
        }
    
    def _analyze_model_usage(self, data: Dict) -> List[OptimizationSuggestion]:
        """分析模型使用情况"""
        suggestions = []
        
        for row in data['by_model']:
            model = row['model'] or 'unknown'
            avg_tokens = row['avg_tokens_per_request'] or 0
            requests = row['requests'] or 0
            
            # 检查是否使用昂贵模型处理简单任务
            if model in ['claude-3-opus', 'qwen-max']:
                if avg_tokens < 500:  # 短对话
                    # 找到更便宜的替代模型
                    tool_prefix = 'claude' if 'claude' in model else 'qwen'
                    hierarchy = self.MODEL_HIERARCHY.get(tool_prefix, [])
                    current_idx = hierarchy.index(model) if model in hierarchy else 0
                    
                    if current_idx < len(hierarchy) - 1:
                        cheaper_model = hierarchy[current_idx + 1]
                        savings = self._calculate_model_savings(
                            model, cheaper_model, 
                            row['input_tokens'], row['output_tokens']
                        )
                        
                        suggestions.append(OptimizationSuggestion(
                            suggestion_id=f'model_switch_{model}_{cheaper_model}',
                            suggestion_type=OptimizationType.MODEL_SWITCH.value,
                            title=f"考虑使用 {cheaper_model} 处理简单任务",
                            description=f"当前 {model} 平均每请求仅 {avg_tokens:.0f} tokens，"
                                      f"切换到 {cheaper_model} 可节省成本",
                            potential_savings=savings,
                            priority=Priority.HIGH.value,
                            action_items=[
                                f"将短对话（<500 tokens）路由到 {cheaper_model}",
                                "保留复杂任务使用当前模型",
                                "实现自动模型选择逻辑",
                            ],
                            affected_tools=[row['tool_name']],
                        ))
        
        return suggestions
    
    def _analyze_usage_patterns(self, data: Dict) -> List[OptimizationSuggestion]:
        """分析使用模式"""
        suggestions = []
        
        # 分析高峰时段
        if data['by_hour']:
            peak_hours = sorted(data['by_hour'], key=lambda x: x['requests'], reverse=True)[:3]
            peak_hours_list = [h['hour'] for h in peak_hours]
            
            # 如果高峰时段请求集中，建议错峰使用
            total_requests = data['overall']['total_requests'] or 1
            peak_requests = sum(h['requests'] for h in peak_hours)
            peak_percentage = peak_requests / total_requests * 100
            
            if peak_percentage > 50:
                suggestions.append(OptimizationSuggestion(
                    suggestion_id='time_optimization_peak',
                    suggestion_type=OptimizationType.TIME_OPTIMIZATION.value,
                    title="优化使用时段分布",
                    description=f"高峰时段（{', '.join(peak_hours_list)}时）集中了 {peak_percentage:.1f}% 的请求，"
                              "建议错峰使用以提高响应速度",
                    potential_savings=0,  # 时间优化不直接节省成本
                    priority=Priority.MEDIUM.value,
                    action_items=[
                        "将批量任务安排在非高峰时段",
                        "实现请求队列和调度",
                        "监控响应时间并动态调整",
                    ],
                ))
        
        return suggestions
    
    def _analyze_quota_efficiency(self, data: Dict) -> List[OptimizationSuggestion]:
        """分析配额效率"""
        suggestions = []
        
        # 找出配额使用不均衡的用户
        if data['by_user']:
            total_tokens = sum(u['total_tokens'] or 0 for u in data['by_user'])
            user_count = len(data['by_user'])
            avg_tokens = total_tokens / user_count if user_count > 0 else 0
            
            # 找出使用量远低于平均的用户
            low_usage_users = [
                u['user_id'] for u in data['by_user']
                if (u['total_tokens'] or 0) < avg_tokens * 0.2
            ]
            
            if len(low_usage_users) > user_count * 0.3:
                suggestions.append(OptimizationSuggestion(
                    suggestion_id='quota_adjustment_low_usage',
                    suggestion_type=OptimizationType.QUOTA_ADJUSTMENT.value,
                    title="优化配额分配",
                    description=f"发现 {len(low_usage_users)} 个用户使用量远低于平均，"
                              "建议重新评估配额分配",
                    potential_savings=0,
                    priority=Priority.LOW.value,
                    action_items=[
                        "审查低使用量用户的配额设置",
                        "考虑将未使用配额重新分配",
                        "设置配额过期和回收机制",
                    ],
                    affected_users=low_usage_users[:10],  # 只显示前10个
                ))
        
        return suggestions
    
    def _analyze_tool_usage(self, data: Dict) -> List[OptimizationSuggestion]:
        """分析工具使用"""
        suggestions = []
        
        # 检查是否有多个工具提供类似功能
        tools = set(row['tool_name'] for row in data['by_model'] if row['tool_name'])
        
        if len(tools) > 2:
            suggestions.append(OptimizationSuggestion(
                suggestion_id='tool_consolidation',
                suggestion_type=OptimizationType.TOOL_CONSOLIDATION.value,
                title="考虑整合 AI 工具",
                description=f"当前使用 {len(tools)} 个不同的 AI 工具，"
                          "整合可能带来更好的价格谈判空间",
                potential_savings=0,
                priority=Priority.LOW.value,
                action_items=[
                    "评估各工具的使用频率和成本",
                    "与供应商谈判批量折扣",
                    "考虑统一使用主要工具",
                ],
                affected_tools=list(tools),
            ))
        
        return suggestions
    
    def _calculate_model_savings(
        self,
        current_model: str,
        cheaper_model: str,
        input_tokens: int,
        output_tokens: int
    ) -> float:
        """计算切换模型的节省"""
        current_pricing = self.MODEL_PRICING.get(current_model, {'input': 0.01, 'output': 0.03})
        cheaper_pricing = self.MODEL_PRICING.get(cheaper_model, {'input': 0.01, 'output': 0.03})
        
        current_cost = (input_tokens / 1000 * current_pricing['input'] +
                       output_tokens / 1000 * current_pricing['output'])
        
        cheaper_cost = (input_tokens / 1000 * cheaper_pricing['input'] +
                       output_tokens / 1000 * cheaper_pricing['output'])
        
        return current_cost - cheaper_cost
    
    def get_cost_breakdown(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """获取成本分解"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 按模型分解
        cursor.execute('''
            SELECT tool_name, model,
                   COUNT(*) as requests,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens
            FROM daily_usage
            WHERE date >= ? AND date <= ?
            GROUP BY tool_name, model
        ''', (start_date, end_date))
        
        breakdown = []
        for row in cursor.fetchall():
            model = row['model'] or 'unknown'
            pricing = self.MODEL_PRICING.get(model, {'input': 0.01, 'output': 0.03})
            
            input_cost = (row['input_tokens'] or 0) / 1000 * pricing['input']
            output_cost = (row['output_tokens'] or 0) / 1000 * pricing['output']
            total_cost = input_cost + output_cost
            
            breakdown.append({
                'tool_name': row['tool_name'],
                'model': model,
                'requests': row['requests'],
                'input_tokens': row['input_tokens'],
                'output_tokens': row['output_tokens'],
                'input_cost': round(input_cost, 4),
                'output_cost': round(output_cost, 4),
                'total_cost': round(total_cost, 4),
            })
        
        conn.close()
        
        return {
            'breakdown': breakdown,
            'total_cost': round(sum(b['total_cost'] for b in breakdown), 4),
        }
```

---

## 4. 实施优先级建议

### 4.1 优先级矩阵

| 优先级 | 功能 | 预计工时 | 业务价值 | 依赖关系 |
|--------|------|----------|----------|----------|
| 🟠 P1 | 实时告警系统 | 3-4 天 | 运维保障，及时响应问题 | 无 |
| 🟠 P1 | Prompt 模板库 UI | 3-4 天 | 提升用户使用效率 | 无（后端已实现） |
| 🟡 P2 | ROI 分析模块 | 2-3 天 | 管理决策支持 | 无 |
| 🟡 P2 | 成本优化建议 | 2-3 天 | 成本控制 | ROI 模块 |
| 🟡 P2 | 会话管理 UI | 2-3 天 | 会话持久化管理 | 无（后端已实现） |
| 🟢 P3 | 团队协作 UI | 4-5 天 | 协作增强 | 无（后端已实现） |
| 🟢 P3 | SSO 登录适配 | 1-2 天 | 企业级认证 | 无（后端已实现） |

### 4.2 实施路线图

```
Week 1-2: P1 功能
├── 实时告警系统
│   ├── WebSocket 推送
│   ├── 前端通知组件
│   └── 邮件/Webhook 通知
└── Prompt 模板库 UI
    ├── 模板列表页面
    ├── 模板创建/编辑表单
    └── 模板渲染预览

Week 3: P2 功能
├── ROI 分析模块
│   ├── 后端计算逻辑
│   └── 前端图表展示
├── 成本优化建议
│   ├── 分析算法
│   └── 建议展示界面
└── 会话管理 UI
    ├── 会话历史列表
    └── 会话恢复功能

Week 4+: P3 功能
├── 团队协作 UI
│   ├── 团队管理界面
│   └── 会话共享设置
└── SSO 登录适配
    └── 前端登录页面适配
```

---

## 5. 总结

### 5.1 当前项目状态

**后端架构**：
- ✅ 已完成模块化重构
- ✅ 分层架构清晰（Routes → Services → Repositories → Models）
- ✅ 企业级特性（SSO、多租户、合规）后端已就绪
- ✅ Workspace 模块（Prompt 模板库、会话管理、工具连接、协作）后端已完整实现

**前端实现**：
- ✅ 核心管理功能（Dashboard、Messages、Analysis、Management）已完整实现（Admin 用户）
- ✅ Workspace 页面已实现（普通用户，iframe 嵌入外部工具）
- ✅ Report 页面已实现（普通用户，个人用量报告）
- ⚠️ 部分高级功能 UI 待实现（Prompt 模板库、会话管理、团队协作等）

### 5.2 主要差距

| 类别 | 差距 | 影响 |
|------|------|------|
| 高级分析 | ROI 分析未实现 | 管理层无法评估投资回报 |
| 高级分析 | 成本优化未实现 | 无法优化成本 |
| 实时功能 | 告警推送未实现 | 无法及时响应问题 |
| 前端 UI | Prompt 模板库 UI 未实现 | 后端 API 已就绪，缺少前端界面 |
| 前端 UI | 会话管理 UI 未实现 | 后端 API 已就绪，缺少前端界面 |
| 前端 UI | 团队协作 UI 未实现 | 后端 API 已就绪，缺少前端界面 |

### 5.3 建议下一步行动

1. **优先实现**：实时告警系统
   - 提升运维响应能力
   - WebSocket 推送 + 前端通知组件

2. **并行开发**：Prompt 模板库 UI
   - 后端 API 已完整实现
   - 只需前端界面开发

3. **短期规划**：ROI 分析和成本优化
   - 为管理层提供决策支持
   - 可逐步完善

4. **中期规划**：会话管理和团队协作 UI
   - 后端 API 已就绪
   - 按需实现前端界面

### 5.4 项目完成度评估

| 维度 | 完成度 | 说明 |
|------|--------|------|
| 后端核心功能 | 95% | 主要功能已实现，ROI/成本优化待补充 |
| 前端核心页面 | 100% | 所有核心页面已实现 |
| 企业级特性 | 90% | SSO、多租户、合规已实现，告警待完善 |
| 高级分析功能 | 60% | ROI、成本优化待实现 |
| 前端高级 UI | 50% | 模板库、会话管理、协作 UI 待实现 |

**总体完成度：约 85%**

---

*文档版本：1.0*
*最后更新：2026-03-21*
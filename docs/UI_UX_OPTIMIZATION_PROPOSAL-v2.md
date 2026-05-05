# Open ACE 企业级AI工作平台 UI/UX 优化方案 v2

> 文档版本: 2.0
> 创建日期: 2026-03-23
> 目标: 打造"方便用、方便管"的企业级AI工作平台
> 更新说明: 根据 6.2.1 工作模式布局和 6.2.2 管理模式布局，详细设计各页面功能、元素和布局

---

## 目录

1. [方案概述](#一方案概述)
2. [工作模式页面设计](#二工作模式页面设计)
3. [管理模式页面设计](#三管理模式页面设计)
4. [实施计划](#四实施计划)
5. [附录](#五附录)

---

## 一、方案概述

### 1.1 设计理念

**核心理念**: "用"与"管"分离，双入口设计

```
用户进入平台:
├── 🚀 工作模式 (用)
│   └── 专注AI对话和任务完成
└── 📊 管理模式 (管)
    └── 专注监控、分析、治理
```

### 1.2 现有代码分析

| 模块 | 现有文件 | 状态 |
|------|----------|------|
| **前端布局** | `frontend/src/components/layout/WorkLayout.tsx` | ✅ 已实现基础结构 |
| **前端布局** | `frontend/src/components/layout/ManageLayout.tsx` | ✅ 已实现基础结构 |
| **前端页面** | `frontend/src/components/features/*.tsx` | ✅ 已实现基础功能 |
| **后端路由** | `app/routes/*.py` | ✅ 已实现 API |
| **数据服务** | `app/services/*.py` | ✅ 已实现业务逻辑 |

### 1.3 需要完善的内容

1. **工作模式**: 需要完善三栏布局的具体功能实现
2. **管理模式**: 需要按照导航菜单设计各子页面
3. **页面细节**: 需要详细设计每个页面的图表、元素、交互

---

## 二、工作模式页面设计

### 2.1 工作模式布局结构

```
┌─────────────────────────────────────────────────────────────┐
│  Header: Logo | 模式切换 | 搜索 | 通知 | 用户                │
├───────────┬─────────────────────────────────┬───────────────┤
│           │                                 │               │
│  左侧面板  │         主内容区域               │   右侧面板    │
│  (可折叠)  │                                 │   (可折叠)    │
│           │                                 │               │
│  ┌─────┐  │   ┌─────────────────────┐      │   ┌─────┐    │
│  │导航 │  │   │                     │      │   │提示词│    │
│  ├─────┤  │   │    AI 对话区域       │      │   ├─────┤    │
│  │会话 │  │   │                     │      │   │工具  │    │
│  │列表 │  │   └─────────────────────┘      │   ├─────┤    │
│  └─────┘  │                                 │   │文档  │    │
│           │                                 │   └─────┘    │
├───────────┴─────────────────────────────────┴───────────────┤
│  Status Bar: 模型 | Token用量 | 延迟                          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 左侧面板设计

#### 2.2.1 导航菜单

**现有代码**: `frontend/src/components/layout/WorkLayout.tsx`

```tsx
// 现有导航项
const workNavItems: NavItem[] = [
  { id: 'workspace', label: 'workspace', icon: 'bi-grid', path: '/work' },
  { id: 'sessions', label: 'sessions', icon: 'bi-collection', path: '/work/sessions' },
  { id: 'prompts', label: 'prompts', icon: 'bi-file-text', path: '/work/prompts' },
];
```

**需要改动**: 无，现有导航已满足需求

#### 2.2.2 会话列表

**功能描述**:
- 显示用户的 AI 会话历史
- 支持按日期分组（今日、昨日、本周、更早）
- 支持搜索和筛选
- 点击会话可在主区域打开对话

**页面元素**:

| 元素 | 类型 | 说明 |
|------|------|------|
| 搜索框 | Input | 搜索会话标题 |
| 日期分组 | Group | 今日/昨日/本周/更早 |
| 会话卡片 | Card | 标题、工具图标、时间、消息数 |
| 新建按钮 | Button | 创建新会话 |

**数据来源**:
- 现有 API: `GET /api/sessions`
- 现有服务: `app/services/message_service.py`

**需要改动**:
1. 前端: 在 `WorkLayout.tsx` 中实现会话列表组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/work/SessionList.tsx`
- 修改文件: `frontend/src/components/layout/WorkLayout.tsx`

### 2.3 主内容区域设计

#### 2.3.1 Workspace 页面（AI对话主界面）

**现有代码**: `frontend/src/components/features/Workspace.tsx`

**功能描述**:
- iframe 嵌入外部 AI 工具界面
- 支持配置的 AI 工具 URL

**页面元素**:

| 元素 | 类型 | 说明 |
|------|------|------|
| iframe | iframe | 嵌入 AI 工具界面 |
| 加载状态 | Loading | 加载中显示 |
| 错误提示 | Error | 配置错误时显示 |

**数据来源**:
- 现有 API: `GET /api/workspace/config`
- 现有服务: `app/services/workspace_service.py`

**需要改动**: 无，现有实现已满足需求

#### 2.3.2 Sessions 页面（会话管理）

**现有代码**: `frontend/src/components/features/Sessions.tsx`

**功能描述**:
- 显示所有 Agent 会话列表
- 支持按工具、状态、类型筛选
- 支持查看会话详情和消息
- 支持删除和完成会话

**页面元素**:

| 元素 | 类型 | 说明 |
|------|------|------|
| 统计卡片 | Card x4 | 总会话/活跃会话/消息/Token |
| 筛选器 | Filter | 工具/状态/类型/搜索 |
| 会话卡片 | Card | 标题、状态、类型、工具、消息数、Token |
| 消息展开 | Collapse | 展开查看会话消息 |
| 分页 | Pagination | 分页导航 |

**数据来源**:
- 现有 API: `GET /api/sessions`, `GET /api/sessions/stats`
- 现有服务: `app/services/message_service.py`

**需要改动**: 无，现有实现已满足需求

#### 2.3.3 Prompts 页面（提示词库）

**现有代码**: `frontend/src/components/features/Prompts.tsx`

**功能描述**:
- 显示提示词模板列表
- 支持按分类筛选和搜索
- 支持创建、编辑、删除提示词
- 支持变量渲染和复制

**页面元素**:

| 元素 | 类型 | 说明 |
|------|------|------|
| 筛选器 | Filter | 分类/搜索 |
| 提示词卡片 | Card x N | 名称、分类、标签、描述、使用次数 |
| 创建按钮 | Button | 打开创建模态框 |
| 编辑模态框 | Modal | 名称、描述、分类、内容、标签、变量 |
| 渲染模态框 | Modal | 变量输入、结果预览、复制 |
| 分页 | Pagination | 分页导航 |

**数据来源**:
- 现有 API: `GET /api/prompts`, `POST /api/prompts`, `PUT /api/prompts/:id`, `DELETE /api/prompts/:id`
- 现有服务: `app/modules/prompts/`

**需要改动**: 无，现有实现已满足需求

### 2.4 右侧面板设计

#### 2.4.1 辅助面板结构

**功能描述**:
- 提供快捷访问提示词、工具、文档
- 支持折叠展开

**页面元素**:

| Tab | 内容 |
|-----|------|
| 提示词 | 快捷访问常用提示词，点击可复制 |
| 工具 | AI 工具快捷入口 |
| 文档 | 帮助文档和指南 |

**需要改动**:
1. 前端: 在 `WorkLayout.tsx` 中实现辅助面板内容
2. 后端: 无需改动

**代码位置**:
- 新增组件: `frontend/src/components/work/AssistPanel.tsx`
- 修改文件: `frontend/src/components/layout/WorkLayout.tsx`

### 2.5 状态栏设计

**功能描述**:
- 显示当前模型信息
- 显示 Token 使用量
- 显示响应延迟

**页面元素**:

| 元素 | 位置 | 说明 |
|------|------|------|
| 模型名称 | 左侧 | 当前使用的 AI 模型 |
| Token 用量 | 中间 | 已用/总量 |
| 延迟 | 右侧 | API 响应延迟 |

**需要改动**:
1. 前端: 实现状态栏组件
2. 后端: 需要新增 API 获取实时状态

**代码位置**:
- 新增组件: `frontend/src/components/work/StatusBar.tsx`
- 新增 API: `GET /api/workspace/status`

---

## 三、管理模式页面设计

### 3.1 管理模式布局结构

```
┌─────────────────────────────────────────────────────────────┐
│  Header: Logo | 模式切换 | 搜索 | 通知 | 用户                │
├───────────┬─────────────────────────────────────────────────┤
│           │                                                 │
│  侧边导航  │              主内容区域                          │
│  (可折叠)  │                                                 │
│           │   ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  概览     │   │ 卡片1    │ │ 卡片2    │ │ 卡片3    │       │
│  └仪表盘  │   └──────────┘ └──────────┘ └──────────┘       │
│  分析     │                                                 │
│  └趋势    │   ┌──────────────────────────────────────┐     │
│  └异常    │   │           图表区域                    │     │
│  └会话历史 │   └──────────────────────────────────────┘     │
│  └消息查询 │                                                 │
│  治理     │   ┌─────────────────┐ ┌─────────────────┐      │
│  └审计    │   │    表格/列表    │ │    详情面板     │      │
│  └配额    │   └─────────────────┘ └─────────────────┘      │
│  └内容过滤 │                                                 │
│  └安全    │                                                 │
│  用户     │                                                 │
│  └管理    │                                                 │
│           │                                                 │
└───────────┴─────────────────────────────────────────────────┘
```

### 3.2 侧边导航设计

**现有代码**: `frontend/src/components/layout/ManageLayout.tsx`

```tsx
// 现有导航结构
const navSections: NavSection[] = [
  {
    id: 'overview',
    title: 'overview',
    items: [{ id: 'dashboard', label: 'dashboard', icon: 'bi-speedometer2', path: '/manage/dashboard' }],
  },
  {
    id: 'analysis',
    title: 'analysis',
    items: [
      { id: 'trend', label: 'tokenTrend', icon: 'bi-graph-up', path: '/manage/analysis' },
      { id: 'messages', label: 'messages', icon: 'bi-chat-dots', path: '/manage/messages' },
    ],
  },
  {
    id: 'governance',
    title: 'governance',
    items: [
      { id: 'audit', label: 'auditLog', icon: 'bi-journal-text', path: '/manage/audit' },
      { id: 'quota', label: 'quotaManagement', icon: 'bi-sliders', path: '/manage/quota' },
      { id: 'security', label: 'securitySettings', icon: 'bi-shield', path: '/manage/security' },
    ],
  },
  {
    id: 'users',
    title: 'user',
    items: [{ id: 'users', label: 'userManagement', icon: 'bi-people', path: '/manage/users' }],
  },
];
```

**需要改动**:
1. 添加"异常"菜单项到分析分组
2. 调整路由映射

**修改后导航结构**:

```tsx
const navSections: NavSection[] = [
  {
    id: 'overview',
    title: '概览',
    items: [
      { id: 'dashboard', label: '仪表盘', icon: 'bi-speedometer2', path: '/manage/dashboard' }
    ],
  },
  {
    id: 'analysis',
    title: '分析',
    items: [
      { id: 'trend', label: '趋势分析', icon: 'bi-graph-up', path: '/manage/analysis/trend' },
      { id: 'anomaly', label: '异常检测', icon: 'bi-exclamation-triangle', path: '/manage/analysis/anomaly' },
      { id: 'roi', label: 'ROI 分析', icon: 'bi-currency-dollar', path: '/manage/analysis/roi' },
      { id: 'conversation-history', label: '会话历史', icon: 'bi-chat-history', path: '/manage/analysis/conversation-history' },
      { id: 'messages', label: '消息查询', icon: 'bi-chat-dots', path: '/manage/messages' },
    ],
  },
  {
    id: 'governance',
    title: '治理',
    items: [
      { id: 'audit', label: '审计日志', icon: 'bi-journal-text', path: '/manage/audit' },
      { id: 'audit-analysis', label: '审计分析', icon: 'bi-search', path: '/manage/compliance/audit' },
      { id: 'quota', label: '配额管理', icon: 'bi-sliders', path: '/manage/quota' },
      { id: 'alerts', label: '告警管理', icon: 'bi-bell', path: '/manage/alerts' },
      { id: 'content-filter', label: '内容过滤', icon: 'bi-shield-check', path: '/manage/governance/content-filter' },
      { id: 'compliance', label: '合规报告', icon: 'bi-file-earmark-text', path: '/manage/compliance/reports' },
      { id: 'retention', label: '数据保留', icon: 'bi-database', path: '/manage/compliance/retention' },
      { id: 'security', label: '安全设置', icon: 'bi-shield', path: '/manage/security' },
    ],
  },
  {
    id: 'users',
    title: '用户',
    items: [
      { id: 'users', label: '用户管理', icon: 'bi-people', path: '/manage/users' },
      { id: 'tenants', label: '租户管理', icon: 'bi-building', path: '/manage/tenants' },
    ],
  },
  {
    id: 'settings',
    title: '设置',
    items: [
      { id: 'sso', label: 'SSO 设置', icon: 'bi-key', path: '/manage/settings/sso' },
    ],
  },
];
```

### 3.3 概览分组页面设计

#### 3.3.1 仪表盘页面 (Dashboard)

**路由**: `/manage/dashboard`
**现有代码**: `frontend/src/components/features/Dashboard.tsx`

**功能描述**:
- 显示系统整体使用概览
- 今日使用统计
- 累计统计
- 趋势图表
- 工具分布

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 主机选择 | Select | `GET /api/hosts` |
| | 工具选择 | Select | 固定选项 |
| | 自动刷新 | Switch | - |
| | 刷新按钮 | Button | - |
| **今日使用** | OpenClaw 卡片 | Card | `GET /api/usage/today` |
| | Claude 卡片 | Card | `GET /api/usage/today` |
| | Qwen 卡片 | Card | `GET /api/usage/today` |
| **总览** | OpenClaw 累计 | Card | `GET /api/summary` |
| | Claude 累计 | Card | `GET /api/summary` |
| | Qwen 累计 | Card | `GET /api/summary` |
| **图表** | Token 趋势图 | LineChart | `GET /api/usage/trend` |
| | Token 分布图 | PieChart | 计算得出 |
| **表格** | 工具详情表 | Table | `GET /api/summary` |

**数据来源**:
- 现有 API: `app/routes/usage.py`
- 现有服务: `app/services/usage_service.py`

**需要改动**: 无，现有实现已满足需求

### 3.4 分析分组页面设计

#### 3.4.1 趋势分析页面 (Trend Analysis)

**路由**: `/manage/analysis/trend`
**现有代码**: `frontend/src/components/features/Analysis.tsx` (需拆分)

**功能描述**:
- 显示 Token 使用趋势
- 支持按日期范围、工具、主机筛选
- 显示关键指标卡片
- 显示使用热力图
- 显示工具对比
- 显示峰值时段
- 显示活跃用户排名

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 快捷日期 | ButtonGroup | 7天/30天/90天/全部 |
| | 开始日期 | DatePicker | - |
| | 结束日期 | DatePicker | - |
| | 工具选择 | Select | 固定选项 |
| | 主机选择 | Select | `GET /api/hosts` |
| **指标卡片** | 总 Token | StatCard | `GET /api/analysis/key-metrics` |
| | 总请求 | StatCard | `GET /api/analysis/key-metrics` |
| | 活跃用户 | StatCard | `GET /api/analysis/user-ranking` |
| | 活跃工具 | StatCard | `GET /api/analysis/tool-comparison` |
| **图表** | 使用热力图 | Heatmap | `GET /api/analysis/daily-hourly-usage` |
| | Token 趋势 | LineChart | `GET /api/analysis/daily-hourly-usage` |
| | 工具对比 | BarChart | `GET /api/analysis/tool-comparison` |
| **表格** | 峰值时段 | Table | `GET /api/analysis/peak-usage` |
| | 活跃用户 | Table | `GET /api/analysis/user-ranking` |
| | 会话统计 | Table | `GET /api/analysis/conversation-stats` |
| | 用户分层 | DoughnutChart | `GET /api/analysis/user-segmentation` |

**数据来源**:
- 现有 API: `app/routes/analysis.py`
- 现有服务: `app/services/analysis_service.py`

**需要改动**:
1. 前端: 从 `Analysis.tsx` 拆分出趋势分析部分
2. 后端: 无需改动

**代码位置**:
- 新增组件: `frontend/src/components/features/analysis/TrendAnalysis.tsx`
- 修改文件: `frontend/src/components/features/Analysis.tsx`
- 修改路由: `frontend/src/App.tsx`

#### 3.4.2 异常检测页面 (Anomaly Detection)

**路由**: `/manage/analysis/anomaly`
**现有代码**: 无，需新建

**功能描述**:
- 显示异常检测结果
- 支持按日期范围筛选
- 显示异常类型、严重程度
- 提供优化建议

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 日期范围 | DatePicker | - |
| | 异常类型 | Select | 用量异常/延迟异常/错误率异常 |
| | 严重程度 | Select | 高/中/低 |
| **统计卡片** | 异常总数 | StatCard | 计算得出 |
| | 高危异常 | StatCard | 计算得出 |
| | 已处理 | StatCard | 计算得出 |
| **图表** | 异常趋势 | LineChart | `GET /api/analysis/anomaly-detection` |
| | 异常分布 | PieChart | 按类型统计 |
| **列表** | 异常列表 | Table | `GET /api/analysis/anomaly-detection` |
| | 优化建议 | List | `GET /api/analysis/recommendations` |

**数据来源**:
- 现有 API: `GET /api/analysis/anomaly-detection` (返回空数组，需实现)
- 现有 API: `GET /api/analysis/recommendations`
- 现有服务: `app/services/analysis_service.py`

**需要改动**:
1. 前端: 新建异常检测页面组件
2. 后端: 实现异常检测逻辑

**代码位置**:
- 新增组件: `frontend/src/components/features/analysis/AnomalyDetection.tsx`
- 修改服务: `app/services/analysis_service.py`
- 新增 API: `app/routes/analysis.py` (完善异常检测)

**异常检测逻辑设计**:

```python
# app/services/analysis_service.py 新增方法

def detect_anomalies(
    self,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    host_name: Optional[str] = None
) -> List[Dict]:
    """
    检测使用异常

    异常类型:
    1. 用量突增: 日用量超过平均值 2 倍
    2. 用量骤降: 日用量低于平均值 50%
    3. 延迟异常: 响应时间超过阈值
    4. 错误率异常: 错误率超过阈值

    返回:
        List[Dict]: 异常列表
    """
    anomalies = []

    # 获取每日用量数据
    daily_data = self.message_repo.get_daily_token_totals(start_date, end_date, host_name)

    if not daily_data:
        return anomalies

    # 计算平均值和标准差
    tokens = [d.get('total_tokens', 0) for d in daily_data]
    avg_tokens = sum(tokens) / len(tokens)
    std_tokens = (sum((t - avg_tokens) ** 2 for t in tokens) / len(tokens)) ** 0.5

    # 检测异常
    for d in daily_data:
        token = d.get('total_tokens', 0)
        date = d.get('date')

        # 用量突增
        if token > avg_tokens + 2 * std_tokens:
            anomalies.append({
                'type': 'usage_spike',
                'date': date,
                'actual': token,
                'expected': avg_tokens,
                'deviation': (token - avg_tokens) / avg_tokens * 100,
                'severity': 'high' if token > avg_tokens * 3 else 'medium'
            })

        # 用量骤降
        if token < avg_tokens * 0.5:
            anomalies.append({
                'type': 'usage_drop',
                'date': date,
                'actual': token,
                'expected': avg_tokens,
                'deviation': (avg_tokens - token) / avg_tokens * 100,
                'severity': 'low'
            })

    return anomalies
```

#### 3.4.3 会话历史页面 (Conversation History)

**路由**: `/manage/analysis/conversation-history`
**现有代码**: `frontend/src/components/features/ConversationHistory.tsx`

**功能描述**:
- 显示会话历史表格
- 支持按日期、工具、主机、发送者筛选
- 支持列显示/隐藏、排序
- 支持全屏模式查看
- 点击会话可查看详情（时间线、延迟曲线）

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 日期选择 | DatePicker | - |
| | 工具选择 | Select | OpenClaw/Claude/Qwen/全部 |
| | 主机选择 | Select | `GET /api/hosts` |
| | 发送者搜索 | Input | - |
| | 重置按钮 | Button | - |
| | 刷新按钮 | Button | - |
| **表格** | 会话表格 | Table | `GET /api/conversation-history` |
| | 日期 | Column | 可排序 |
| | 工具 | Column | Badge 显示 |
| | 主机 | Column | - |
| | 发送者 | Column | - |
| | 消息数 | Column | 数字显示 |
| | Token 数 | Column | 格式化显示 |
| | 最后消息时间 | Column | 时间格式化 |
| | 操作 | Column | 查看详情按钮 |
| **工具栏** | 列选择器 | Dropdown | 显示/隐藏列 |
| | 全屏按钮 | Button | 切换全屏模式 |
| **详情模态框** | 时间线 Tab | Tab | 显示会话消息时间线 |
| | 延迟曲线 Tab | Tab | 显示响应延迟图表 |
| | 消息列表 | List | 角色/内容/时间/Token |
| | 延迟图表 | LineChart | 消息延迟曲线 |
| **分页** | 分页导航 | Pagination | 每页 20 条 |

**数据来源**:
- 现有 API: `GET /api/conversation-history`
- 现有 API: `GET /api/conversation-timeline/:sessionId`
- 现有服务: `app/services/message_service.py`

**需要改动**:
1. 前端: 从 `Analysis.tsx` 中拆分为独立页面
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 现有组件: `frontend/src/components/features/ConversationHistory.tsx`
- 修改文件: `frontend/src/components/features/Analysis.tsx` (移除 Tab)
- 修改路由: `frontend/src/App.tsx`

#### 3.4.4 消息查询页面 (Messages)

**路由**: `/manage/messages`
**现有代码**: `frontend/src/components/features/Messages.tsx`

**功能描述**:
- 显示消息列表
- 支持多条件筛选
- 支持搜索消息内容
- 支持查看消息详情

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 日期范围 | DatePicker | - |
| | 主机选择 | Select | `GET /api/hosts` |
| | 工具选择 | Select | 固定选项 |
| | 发送者选择 | SearchableSelect | `GET /api/senders` |
| | 角色选择 | Checkbox | user/assistant/system |
| | 搜索框 | Input | - |
| | 自动刷新 | Switch | - |
| **统计** | 消息总数 | Text | `GET /api/messages/count` |
| **列表** | 消息卡片 | Card | `GET /api/messages` |
| | 角色标识 | Badge | - |
| | 内容预览 | Text | - |
| | 展开详情 | Collapse | - |
| **分页** | 分页导航 | Pagination | - |

**数据来源**:
- 现有 API: `app/routes/messages.py`
- 现有服务: `app/services/message_service.py`

**需要改动**: 无，现有实现已满足需求

#### 3.4.5 ROI 分析页面 (ROI Analysis)

**路由**: `/manage/analysis/roi`
**现有代码**: 无，需新建

**功能描述**:
- 显示 ROI（投资回报）指标
- 支持按日期范围、用户、工具筛选
- 显示成本分解和趋势
- 提供成本优化建议
- 显示效率分析报告

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 日期范围 | DatePicker | - |
| | 用户选择 | Select | 用户列表 |
| | 工具选择 | Select | 固定选项 |
| **指标卡片** | 总 ROI | StatCard | `GET /api/roi` |
| | 总成本 | StatCard | `GET /api/roi/cost-breakdown` |
| | 节省成本 | StatCard | 计算得出 |
| | 效率提升 | StatCard | `GET /api/optimization/efficiency` |
| **图表** | ROI 趋势 | LineChart | `GET /api/roi/trend` |
| | 成本分解 | PieChart | `GET /api/roi/cost-breakdown` |
| | 按工具对比 | BarChart | `GET /api/roi/by-tool` |
| | 每日成本 | AreaChart | `GET /api/roi/daily-costs` |
| **表格** | 优化建议 | Table | `GET /api/optimization/suggestions` |
| | 用户 ROI 排名 | Table | `GET /api/roi/by-user` |

**数据来源**:
- 现有 API: `app/routes/roi.py`
- 现有服务: `app/modules/analytics/roi_calculator.py`, `app/modules/analytics/cost_optimizer.py`

**需要改动**:
1. 前端: 新建 ROI 分析页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/analysis/ROIAnalysis.tsx`
- 修改路由: `frontend/src/App.tsx`

### 3.5 治理分组页面设计

#### 3.5.1 审计日志页面 (Audit Log)

**路由**: `/manage/audit`
**现有代码**: `frontend/src/components/features/management/AuditLog.tsx`

**功能描述**:
- 显示系统操作审计日志
- 支持按用户、操作类型、时间筛选
- 支持导出日志

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **过滤器** | 用户名 | Input | - |
| | 操作类型 | Select | 登录/登出/数据查看/数据导出/配置变更等 |
| | 资源类型 | Select | 用户/配额/安全设置/过滤规则等 |
| | 严重程度 | Select | 高/中/低 |
| | 时间范围 | DatePicker | - |
| | 搜索 | Input | - |
| **统计** | 日志总数 | Text | API 返回 |
| **表格** | 日志表格 | Table | `GET /api/audit/logs` |
| | 时间 | Column | - |
| | 用户 | Column | - |
| | 操作 | Column | - |
| | 资源 | Column | - |
| | 详情 | Column | - |
| | IP 地址 | Column | - |
| **操作** | 导出 | Button | `GET /api/audit/logs/export` |
| **分页** | 分页导航 | Pagination | - |

**数据来源**:
- 现有 API: `app/routes/governance.py`
- 现有服务: `app/modules/governance/audit_logger.py`

**需要改动**: 无，现有实现已满足需求

#### 3.5.2 配额管理页面 (Quota Management)

**路由**: `/manage/quota`
**现有代码**: `frontend/src/components/features/management/QuotaManagement.tsx`

**功能描述**:
- 显示用户配额状态
- 支持设置用户配额
- 显示配额告警

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **统计卡片** | 总用户数 | StatCard | 计算得出 |
| | 超额用户 | StatCard | `GET /api/quota/status/all` |
| | 告警数 | StatCard | `GET /api/quota/alerts` |
| **表格** | 用户配额表 | Table | `GET /api/quota/status/all` |
| | 用户名 | Column | - |
| | 日配额 | Column | Token/请求 |
| | 月配额 | Column | Token/请求 |
| | 已用 | Column | - |
| | 使用率 | Column | 进度条 |
| | 状态 | Column | Badge |
| **操作** | 编辑配额 | Button | 打开模态框 |
| | 配额模态框 | Modal | 日Token/月Token/日请求/月请求 |
| **告警** | 告警列表 | List | `GET /api/quota/alerts` |
| | 确认告警 | Button | `POST /api/quota/alerts/:id/acknowledge` |

**数据来源**:
- 现有 API: `app/routes/governance.py`
- 现有服务: `app/modules/governance/quota_manager.py`

**需要改动**: 无，现有实现已满足需求

#### 3.5.3 内容过滤页面 (Content Filter)

**路由**: `/manage/governance/content-filter`
**现有代码**: `frontend/src/components/features/management/ContentFilter.tsx`

**功能描述**:
- 管理内容过滤规则
- 支持创建、编辑、删除规则
- 支持启用/禁用规则
- 规则类型：关键词、正则表达式、PII（个人身份信息）
- 严重程度：低、中、高
- 操作类型：警告、阻止、脱敏

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **操作栏** | 添加规则 | Button | 打开创建模态框 |
| **表格** | 规则表格 | Table | `GET /api/filter-rules` |
| | 匹配模式 | Column | 代码格式显示 |
| | 描述 | Column | 小字显示 |
| | 类型 | Column | Badge: keyword/regex/pii |
| | 严重程度 | Column | Badge: danger/warning/info |
| | 操作类型 | Column | Badge: danger/warning/primary |
| | 状态 | Column | 开关切换 |
| | 操作 | Column | 编辑/删除按钮 |
| **模态框** | 创建/编辑规则 | Modal | - |
| | 匹配模式 | Input | 必填 |
| | 类型选择 | Select | keyword/regex/pii |
| | 严重程度 | Select | low/medium/high |
| | 操作类型 | Select | warn/block/redact |
| | 描述 | Input | 可选 |
| | 启用状态 | Checkbox | 默认启用 |

**规则类型说明**:

| 类型 | 说明 | 示例 |
|------|------|------|
| keyword | 关键词匹配 | 敏感词汇列表 |
| regex | 正则表达式匹配 | 邮箱、手机号模式 |
| pii | 个人身份信息检测 | 身份证、银行卡号 |

**操作类型说明**:

| 操作 | 说明 |
|------|------|
| warn | 警告：记录日志但不阻止 |
| block | 阻止：拒绝请求并返回错误 |
| redact | 脱敏：替换敏感内容为占位符 |

**数据来源**:
- 现有 API: `GET /api/filter-rules`, `POST /api/filter-rules`, `PUT /api/filter-rules/:id`, `DELETE /api/filter-rules/:id`
- 现有服务: `app/modules/governance/content_filter.py`

**需要改动**:
1. 前端: 从 `Management.tsx` 中拆分为独立页面
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 现有组件: `frontend/src/components/features/management/ContentFilter.tsx`
- 修改文件: `frontend/src/components/features/Management.tsx` (移除 Tab)
- 修改路由: `frontend/src/App.tsx`

#### 3.5.4 安全设置页面 (Security Settings)

**路由**: `/manage/security`
**现有代码**: `frontend/src/components/features/management/SecuritySettings.tsx`

**功能描述**:
- 配置系统安全设置
- 配置密码策略
- 配置登录安全策略
- 配置会话管理

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **密码策略** | 密码策略表单 | Form | `GET /api/security-settings` |
| | 最小长度 | Input | 数字，默认 8 |
| | 复杂度要求 | Checkbox | 大写/小写/数字/特殊字符 |
| | 过期时间 | Input | 天数，0 表示永不过期 |
| **登录策略** | 登录失败锁定表单 | Form | - |
| | 最大失败次数 | Input | 数字，默认 5 |
| | 锁定时间 | Input | 分钟，默认 30 |
| | 会话超时 | Input | 分钟，默认 60 |
| **操作** | 保存设置 | Button | `PUT /api/security-settings` |
| | 重置默认 | Button | 恢复默认设置 |

**数据来源**:
- 现有 API: `GET /api/security-settings`, `PUT /api/security-settings`
- 现有服务: `app/modules/governance/`

**需要改动**:
1. 前端: 从 `Management.tsx` 中拆分为独立页面
2. 后端: 无需改动

**代码位置**:
- 现有组件: `frontend/src/components/features/management/SecuritySettings.tsx`
- 修改文件: `frontend/src/components/features/Management.tsx` (移除 Tab)
- 修改路由: `frontend/src/App.tsx`

#### 3.5.5 告警管理页面 (Alert Management)

**路由**: `/manage/alerts`
**现有代码**: 无，需新建

**功能描述**:
- 显示系统告警列表
- 支持按类型、严重程度、已读状态筛选
- 支持标记已读/全部已读
- 支持删除告警
- 配置通知偏好（邮件、推送、Webhook）
- 支持 SSE 实时告警推送

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **统计卡片** | 总告警数 | StatCard | 计算得出 |
| | 未读告警 | StatCard | `GET /api/alerts/unread-count` |
| | 高危告警 | StatCard | 计算得出 |
| **过滤器** | 告警类型 | Select | quota/system/security |
| | 严重程度 | Select | info/warning/critical |
| | 已读状态 | Select | 全部/已读/未读 |
| **列表** | 告警表格 | Table | `GET /api/alerts` |
| | 标题 | Column | - |
| | 消息 | Column | 内容预览 |
| | 类型 | Column | Badge |
| | 严重程度 | Column | Badge (danger/warning/info) |
| | 时间 | Column | 格式化显示 |
| | 操作 | Column | 标记已读/删除 |
| **设置** | 通知偏好模态框 | Modal | - |
| | 邮件通知 | Switch | - |
| | 推送通知 | Switch | - |
| | Webhook URL | Input | - |
| | 告警类型订阅 | Checkbox | quota/system/security |
| | 最低严重程度 | Select | info/warning/critical |

**数据来源**:
- 现有 API: `app/routes/alerts.py`
- 现有服务: `app/modules/governance/alert_notifier.py`

**需要改动**:
1. 前端: 新建告警管理页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/management/AlertManagement.tsx`
- 修改路由: `frontend/src/App.tsx`

#### 3.5.6 合规报告页面 (Compliance Report)

**路由**: `/manage/compliance/reports`
**现有代码**: 无，需新建

**功能描述**:
- 选择报告类型生成合规报告
- 支持日期范围选择
- 支持多种报告格式（JSON/CSV）
- 查看已保存报告列表
- 下载历史报告

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **报告类型** | 类型选择 | Card Group | 固定选项 |
| | Usage Summary | Card | 使用摘要报告 |
| | User Activity | Card | 用户活动报告 |
| | Audit Trail | Card | 审计追踪报告 |
| | Security Report | Card | 安全报告 |
| | Comprehensive | Card | 综合报告 |
| **生成报告** | 日期范围 | DatePicker | - |
| | 报告格式 | Select | JSON/CSV |
| | 生成按钮 | Button | `POST /api/compliance/reports` |
| **已保存报告** | 报告列表 | Table | `GET /api/compliance/reports/saved` |
| | 报告名称 | Column | - |
| | 类型 | Column | Badge |
| | 生成时间 | Column | - |
| | 操作 | Column | 查看/下载/删除 |

**报告类型说明**:

| 类型 | 说明 |
|------|------|
| Usage Summary | AI 使用摘要统计 |
| User Activity | 用户活动和参与度指标 |
| Audit Trail | 完整审计日志追踪 |
| Data Access | 数据访问和导出日志 |
| Security Report | 安全相关事件分析 |
| Quota Usage | 配额使用和告警 |
| Comprehensive | 包含所有章节的完整报告 |

**数据来源**:
- 现有 API: `app/routes/compliance.py`
- 现有服务: `app/modules/compliance/report.py`

**需要改动**:
1. 前端: 新建合规报告页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/compliance/ComplianceReport.tsx`
- 修改路由: `frontend/src/App.tsx`

#### 3.5.7 数据保留页面 (Data Retention)

**路由**: `/manage/compliance/retention`
**现有代码**: 无，需新建

**功能描述**:
- 查看和设置数据保留规则
- 执行数据清理（支持预览模式）
- 查看清理历史
- 存储使用估算
- 合规状态检查

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **合规状态** | 合规状态卡片 | StatCard | `GET /api/compliance/retention/status` |
| | 存储使用 | StatCard | `GET /api/compliance/retention/storage` |
| **保留规则** | 规则表格 | Table | `GET /api/compliance/retention/rules` |
| | 数据类型 | Column | messages/sessions/audit_logs 等 |
| | 保留天数 | Column | 可编辑 |
| | 操作类型 | Column | delete/archive |
| | 操作 | Column | 编辑按钮 |
| **清理操作** | 预览按钮 | Button | `POST /api/compliance/retention/cleanup?dry_run=true` |
| | 执行清理 | Button | `POST /api/compliance/retention/cleanup` |
| | 清理预览 | Modal | 显示将要删除的数据 |
| **清理历史** | 历史表格 | Table | `GET /api/compliance/retention/history` |
| | 执行时间 | Column | - |
| | 清理类型 | Column | - |
| | 删除记录数 | Column | - |
| | 状态 | Column | Badge |

**数据类型保留规则**:

| 数据类型 | 默认保留天数 | 说明 |
|----------|-------------|------|
| messages | 90 | 消息记录 |
| sessions | 180 | 会话记录 |
| audit_logs | 365 | 审计日志 |
| usage_stats | 365 | 使用统计 |
| alerts | 30 | 告警记录 |

**数据来源**:
- 现有 API: `app/routes/compliance.py`
- 现有服务: `app/modules/compliance/retention.py`

**需要改动**:
1. 前端: 新建数据保留页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/compliance/DataRetention.tsx`
- 修改路由: `frontend/src/App.tsx`

#### 3.5.8 审计分析页面 (Audit Analysis)

**路由**: `/manage/compliance/audit`
**现有代码**: 无，需新建

**功能描述**:
- 审计模式分析
- 异常行为检测
- 用户行为画像
- 安全评分展示

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **安全评分** | 安全评分卡片 | GaugeChart | `GET /api/compliance/audit/security-score` |
| | 评分详情 | List | 各项评分 |
| **模式分析** | 模式图表 | Charts | `GET /api/compliance/audit/patterns` |
| | 登录模式 | BarChart | 按时间段分布 |
| | 操作模式 | PieChart | 操作类型分布 |
| | 资源访问 | Heatmap | 访问热力图 |
| **异常检测** | 异常列表 | Table | `GET /api/compliance/audit/anomalies` |
| | 异常类型 | Column | Badge |
| | 描述 | Column | - |
| | 时间 | Column | - |
| | 严重程度 | Column | Badge |
| **用户画像** | 用户选择 | Select | 用户列表 |
| | 行为画像 | Card | `GET /api/compliance/audit/user/:id/profile` |
| | 活跃时间 | Chart | 活跃时段分布 |
| | 常用操作 | List | 操作频率 |

**数据来源**:
- 现有 API: `app/routes/compliance.py`
- 现有服务: `app/modules/compliance/audit.py`

**需要改动**:
1. 前端: 新建审计分析页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/compliance/AuditAnalysis.tsx`
- 修改路由: `frontend/src/App.tsx`

### 3.6 用户分组页面设计

#### 3.6.1 用户管理页面 (User Management)

**路由**: `/manage/users`
**现有代码**: `frontend/src/components/features/management/UserManagement.tsx`

**功能描述**:
- 显示用户列表
- 支持创建、编辑、删除用户
- 支持重置密码
- 支持角色分配

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **操作栏** | 添加用户 | Button | 打开模态框 |
| | 刷新 | Button | - |
| **表格** | 用户表格 | Table | `GET /api/admin/users` |
| | 用户名 | Column | - |
| | 邮箱 | Column | - |
| | 角色 | Column | Badge |
| | 状态 | Column | Badge |
| | 创建时间 | Column | - |
| | 操作 | Column | 编辑/删除/重置密码 |
| **模态框** | 用户表单 | Modal | 用户名/邮箱/密码/角色 |
| | 编辑用户 | Modal | 用户名/邮箱/角色/状态 |
| | 重置密码 | Modal | 新密码 |

**数据来源**:
- 现有 API: `app/routes/admin.py`
- 现有服务: `app/services/auth_service.py`

**需要改动**: 无，现有实现已满足需求

#### 3.6.2 租户管理页面 (Tenant Management)

**路由**: `/manage/tenants`
**现有代码**: 无，需新建

**功能描述**:
- 显示租户列表
- 支持创建、编辑、删除租户
- 配额管理
- 暂停/激活租户
- 查看使用统计

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **统计卡片** | 总租户数 | StatCard | 计算得出 |
| | 活跃租户 | StatCard | 计算得出 |
| | 试用租户 | StatCard | 计算得出 |
| **过滤器** | 状态选择 | Select | active/suspended/trial |
| | 套餐选择 | Select | standard/premium/enterprise |
| **表格** | 租户表格 | Table | `GET /api/tenants` |
| | 租户名称 | Column | - |
| | Slug | Column | - |
| | 套餐 | Column | Badge |
| | 状态 | Column | Badge (success/warning/danger) |
| | 配额使用 | Column | 进度条 |
| | 创建时间 | Column | - |
| | 操作 | Column | 编辑/暂停/删除 |
| **模态框** | 创建租户 | Modal | 名称/Slug/套餐/联系人 |
| | 编辑租户 | Modal | 名称/联系人/设置 |
| | 配额设置 | Modal | Token 配额/请求配额 |
| **操作** | 暂停租户 | Button | `POST /api/tenants/:id/suspend` |
| | 激活租户 | Button | `POST /api/tenants/:id/activate` |

**套餐类型说明**:

| 套餐 | 月 Token 配额 | 月请求配额 | 说明 |
|------|-------------|-----------|------|
| Standard | 100,000 | 10,000 | 标准版 |
| Premium | 500,000 | 50,000 | 高级版 |
| Enterprise | Unlimited | Unlimited | 企业版 |

**数据来源**:
- 现有 API: `app/routes/tenant.py`
- 现有服务: `app/services/tenant_service.py`

**需要改动**:
1. 前端: 新建租户管理页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/management/TenantManagement.tsx`
- 修改路由: `frontend/src/App.tsx`

### 3.7 设置分组页面设计

#### 3.7.1 SSO 设置页面 (SSO Settings)

**路由**: `/manage/settings/sso`
**现有代码**: 无，需新建

**功能描述**:
- 管理 SSO 提供者
- 注册新的 SSO 提供者
- 配置 OAuth2/OIDC 参数
- 启用/禁用提供者
- 查看用户绑定身份

**页面元素**:

| 区域 | 元素 | 类型 | 数据来源 |
|------|------|------|----------|
| **提供者列表** | 提供者表格 | Table | `GET /api/sso/providers` |
| | 提供者名称 | Column | Google/Microsoft/GitHub 等 |
| | 类型 | Column | OAuth2/OIDC |
| | 状态 | Column | Badge (enabled/disabled) |
| | 绑定用户数 | Column | - |
| | 操作 | Column | 编辑/禁用 |
| **注册提供者** | 添加按钮 | Button | 打开模态框 |
| | 提供者选择 | Select | 预定义提供者/自定义 |
| | Client ID | Input | 必填 |
| | Client Secret | Input | 必填 |
| | Redirect URI | Input | 自动生成 |
| | Scope | Input | 可选 |
| **自定义提供者** | Authorization URL | Input | OAuth2 授权端点 |
| | Token URL | Input | Token 端点 |
| | UserInfo URL | Input | 用户信息端点 |
| | Issuer URL | Input | OIDC Issuer |
| **用户身份** | 用户列表 | Table | `GET /api/sso/identities/:user_id` |
| | 用户名 | Column | - |
| | 绑定提供者 | Column | Badge 列表 |
| | 操作 | Column | 解绑 |

**预定义 SSO 提供者**:

| 提供者 | 类型 | 说明 |
|--------|------|------|
| Google | OAuth2 | Google 账号登录 |
| Microsoft | OAuth2 | Microsoft 账号登录 |
| GitHub | OAuth2 | GitHub 账号登录 |
| Okta | OIDC | Okta 企业身份 |
| Custom | OAuth2/OIDC | 自定义提供者 |

**数据来源**:
- 现有 API: `app/routes/sso.py`
- 现有服务: `app/modules/sso/manager.py`

**需要改动**:
1. 前端: 新建 SSO 设置页面组件
2. 后端: 无需改动，现有 API 已支持

**代码位置**:
- 新增组件: `frontend/src/components/features/settings/SSOSettings.tsx`
- 修改路由: `frontend/src/App.tsx`

---

## 四、实施计划

### 4.1 任务清单

| 优先级 | 任务 | 类型 | 文件 | 预计工时 |
|--------|------|------|------|----------|
| P0 | 添加所有新页面菜单项 | 前端 | `ManageLayout.tsx` | 1h |
| P0 | 更新路由配置 | 前端 | `App.tsx` | 1h |
| P1 | 实现异常检测页面 | 前端 | `AnomalyDetection.tsx` (新建) | 4h |
| P1 | 实现异常检测逻辑 | 后端 | `analysis_service.py` | 4h |
| P1 | 拆分会话历史为独立页面 | 前端 | `ConversationHistory.tsx` | 2h |
| P1 | 拆分内容过滤为独立页面 | 前端 | `ContentFilter.tsx` | 2h |
| P1 | 实现告警管理页面 | 前端 | `AlertManagement.tsx` (新建) | 4h |
| P1 | 实现合规报告页面 | 前端 | `ComplianceReport.tsx` (新建) | 4h |
| P1 | 实现数据保留页面 | 前端 | `DataRetention.tsx` (新建) | 4h |
| P1 | 实现审计分析页面 | 前端 | `AuditAnalysis.tsx` (新建) | 4h |
| P2 | 实现 ROI 分析页面 | 前端 | `ROIAnalysis.tsx` (新建) | 6h |
| P2 | 实现租户管理页面 | 前端 | `TenantManagement.tsx` (新建) | 6h |
| P2 | 实现会话列表组件 | 前端 | `SessionList.tsx` (新建) | 4h |
| P2 | 实现辅助面板组件 | 前端 | `AssistPanel.tsx` (新建) | 4h |
| P2 | 实现状态栏组件 | 前端 | `StatusBar.tsx` (新建) | 2h |
| P2 | 新增状态 API | 后端 | `workspace.py` | 2h |
| P3 | 拆分趋势分析页面 | 前端 | `TrendAnalysis.tsx` (新建) | 4h |
| P3 | 实现 SSO 设置页面 | 前端 | `SSOSettings.tsx` (新建) | 4h |

### 4.2 实施步骤

#### 第一阶段：导航完善 (1天)

1. 修改 `ManageLayout.tsx`，添加所有新页面菜单项
2. 更新 `App.tsx` 路由配置
3. 测试导航跳转

#### 第二阶段：页面拆分 (2天)

1. 从 `Analysis.tsx` 拆分会话历史为独立页面
2. 从 `Management.tsx` 拆分内容过滤为独立页面
3. 更新相关路由和导航
4. 测试页面功能

#### 第三阶段：P1 新页面实现 (3天)

1. 实现告警管理页面
2. 实现合规报告页面
3. 实现数据保留页面
4. 实现审计分析页面
5. 实现异常检测页面和后端逻辑
6. 集成测试

#### 第四阶段：P2 功能实现 (3天)

1. 实现 ROI 分析页面
2. 实现租户管理页面
3. 实现会话列表组件
4. 实现辅助面板组件
5. 实现状态栏组件
6. 后端新增状态 API

#### 第五阶段：P3 功能完善 (2天)

1. 拆分趋势分析页面
2. 实现 SSO 设置页面
3. 优化页面布局
4. 整体测试

### 4.3 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 异常检测算法不准确 | 误报/漏报 | 设置可调阈值，支持人工确认 |
| 会话列表性能问题 | 加载慢 | 分页加载，虚拟滚动 |
| 状态栏实时性 | 数据延迟 | WebSocket 推送 |
| 告警通知延迟 | 响应不及时 | SSE 实时推送，多渠道通知 |
| 数据清理误删 | 数据丢失 | 预览模式，备份机制 |
| SSO 配置错误 | 登录失败 | 配置验证，回滚机制 |

---

## 五、附录

### 5.1 API 端点清单

| 模块 | 端点 | 方法 | 说明 |
|------|------|------|------|
| **Usage** | `/api/summary` | GET | 获取使用摘要 |
| | `/api/usage/today` | GET | 获取今日使用 |
| | `/api/usage/trend` | GET | 获取趋势数据 |
| | `/api/hosts` | GET | 获取主机列表 |
| **Analysis** | `/api/analysis/key-metrics` | GET | 获取关键指标 |
| | `/api/analysis/daily-hourly-usage` | GET | 获取每日/每小时使用 |
| | `/api/analysis/peak-usage` | GET | 获取峰值使用 |
| | `/api/analysis/user-ranking` | GET | 获取用户排名 |
| | `/api/analysis/tool-comparison` | GET | 获取工具对比 |
| | `/api/analysis/anomaly-detection` | GET | 获取异常检测 |
| | `/api/analysis/recommendations` | GET | 获取优化建议 |
| **ROI** | `/api/roi` | GET | 获取 ROI 指标 |
| | `/api/roi/trend` | GET | ROI 趋势 |
| | `/api/roi/by-tool` | GET | 按工具 ROI |
| | `/api/roi/by-user` | GET | 按用户 ROI |
| | `/api/roi/cost-breakdown` | GET | 成本分解 |
| | `/api/optimization/suggestions` | GET | 成本优化建议 |
| | `/api/optimization/efficiency` | GET | 效率报告 |
| **Conversation** | `/api/conversation-history` | GET | 获取会话历史列表 |
| | `/api/conversation-timeline/:sessionId` | GET | 获取会话时间线 |
| **Messages** | `/api/messages` | GET | 获取消息列表 |
| | `/api/messages/count` | GET | 获取消息数量 |
| | `/api/senders` | GET | 获取发送者列表 |
| **Alerts** | `/api/alerts` | GET | 获取告警列表 |
| | `/api/alerts/unread-count` | GET | 获取未读告警数 |
| | `/api/alerts/:id/read` | POST | 标记告警已读 |
| | `/api/alerts/read-all` | POST | 标记全部已读 |
| | `/api/alerts/:id` | DELETE | 删除告警 |
| | `/api/alerts/preferences` | GET/PUT | 通知偏好 |
| | `/api/alerts/stream` | GET | SSE 实时告警流 |
| **Compliance** | `/api/compliance/reports` | GET/POST | 报告类型/生成报告 |
| | `/api/compliance/reports/saved` | GET | 已保存报告 |
| | `/api/compliance/reports/:id` | GET | 报告详情 |
| | `/api/compliance/audit/patterns` | GET | 审计模式分析 |
| | `/api/compliance/audit/anomalies` | GET | 审计异常检测 |
| | `/api/compliance/audit/user/:id/profile` | GET | 用户行为画像 |
| | `/api/compliance/audit/security-score` | GET | 安全评分 |
| | `/api/compliance/retention/rules` | GET/PUT | 数据保留规则 |
| | `/api/compliance/retention/cleanup` | POST | 执行数据清理 |
| | `/api/compliance/retention/history` | GET | 清理历史 |
| | `/api/compliance/retention/storage` | GET | 存储估算 |
| | `/api/compliance/retention/status` | GET | 合规状态 |
| **Governance** | `/api/audit/logs` | GET | 获取审计日志 |
| | `/api/quota/status/all` | GET | 获取所有用户配额 |
| | `/api/quota/alerts` | GET | 获取配额告警 |
| | `/api/filter-rules` | GET/POST | 过滤规则列表/创建 |
| | `/api/filter-rules/:id` | PUT/DELETE | 更新/删除过滤规则 |
| | `/api/security-settings` | GET/PUT | 安全设置 |
| **Tenant** | `/api/tenants` | GET/POST | 租户列表/创建 |
| | `/api/tenants/:id` | GET/PUT/DELETE | 租户 CRUD |
| | `/api/tenants/:id/quota` | PUT | 更新配额 |
| | `/api/tenants/:id/suspend` | POST | 暂停租户 |
| | `/api/tenants/:id/activate` | POST | 激活租户 |
| | `/api/tenants/:id/usage` | GET | 使用历史 |
| | `/api/tenants/plans` | GET | 套餐配额 |
| **SSO** | `/api/sso/providers` | GET/POST | SSO 提供者列表/注册 |
| | `/api/sso/providers/:name` | DELETE | 禁用提供者 |
| | `/api/sso/session` | GET/DELETE | 会话管理 |
| | `/api/sso/identities/:user_id` | GET | 用户身份 |
| **Admin** | `/api/admin/users` | GET/POST | 用户管理 |
| | `/api/admin/users/:id` | PUT/DELETE | 用户操作 |
| | `/api/admin/users/:id/password` | PUT | 重置密码 |
| **Workspace** | `/api/workspace/config` | GET | 获取工作区配置 |
| | `/api/workspace/status` | GET | 获取工作区状态 (新增) |

### 5.2 组件清单

| 组件 | 路径 | 状态 |
|------|------|------|
| WorkLayout | `components/layout/WorkLayout.tsx` | ✅ 已实现 |
| ManageLayout | `components/layout/ManageLayout.tsx` | ✅ 已实现 |
| Dashboard | `components/features/Dashboard.tsx` | ✅ 已实现 |
| Analysis | `components/features/Analysis.tsx` | ✅ 已实现 |
| ConversationHistory | `components/features/ConversationHistory.tsx` | ✅ 已实现 |
| Messages | `components/features/Messages.tsx` | ✅ 已实现 |
| Sessions | `components/features/Sessions.tsx` | ✅ 已实现 |
| Prompts | `components/features/Prompts.tsx` | ✅ 已实现 |
| Workspace | `components/features/Workspace.tsx` | ✅ 已实现 |
| UserManagement | `components/features/management/UserManagement.tsx` | ✅ 已实现 |
| QuotaManagement | `components/features/management/QuotaManagement.tsx` | ✅ 已实现 |
| AuditLog | `components/features/management/AuditLog.tsx` | ✅ 已实现 |
| ContentFilter | `components/features/management/ContentFilter.tsx` | ✅ 已实现 |
| SecuritySettings | `components/features/management/SecuritySettings.tsx` | ✅ 已实现 |
| AlertManagement | `components/features/management/AlertManagement.tsx` | 🆕 待实现 |
| TenantManagement | `components/features/management/TenantManagement.tsx` | 🆕 待实现 |
| TrendAnalysis | `components/features/analysis/TrendAnalysis.tsx` | 🆕 待实现 |
| AnomalyDetection | `components/features/analysis/AnomalyDetection.tsx` | 🆕 待实现 |
| ROIAnalysis | `components/features/analysis/ROIAnalysis.tsx` | 🆕 待实现 |
| ComplianceReport | `components/features/compliance/ComplianceReport.tsx` | 🆕 待实现 |
| DataRetention | `components/features/compliance/DataRetention.tsx` | 🆕 待实现 |
| AuditAnalysis | `components/features/compliance/AuditAnalysis.tsx` | 🆕 待实现 |
| SSOSettings | `components/features/settings/SSOSettings.tsx` | 🆕 待实现 |
| SessionList | `components/work/SessionList.tsx` | 🆕 待实现 |
| AssistPanel | `components/work/AssistPanel.tsx` | 🆕 待实现 |
| StatusBar | `components/work/StatusBar.tsx` | 🆕 待实现 |

### 5.3 路由配置

```tsx
// App.tsx 路由配置

// 工作模式路由
const WorkRoutes = [
  { path: '/work', element: <Workspace /> },
  { path: '/work/sessions', element: <Sessions /> },
  { path: '/work/prompts', element: <Prompts /> },
];

// 管理模式路由
const ManageRoutes = [
  // 概览
  { path: '/manage/dashboard', element: <Dashboard /> },

  // 分析
  { path: '/manage/analysis/trend', element: <TrendAnalysis /> },
  { path: '/manage/analysis/anomaly', element: <AnomalyDetection /> },
  { path: '/manage/analysis/roi', element: <ROIAnalysis /> },
  { path: '/manage/analysis/conversation-history', element: <ConversationHistory /> },
  { path: '/manage/messages', element: <Messages /> },

  // 治理
  { path: '/manage/audit', element: <AuditLog /> },
  { path: '/manage/compliance/audit', element: <AuditAnalysis /> },
  { path: '/manage/quota', element: <QuotaManagement /> },
  { path: '/manage/alerts', element: <AlertManagement /> },
  { path: '/manage/governance/content-filter', element: <ContentFilter /> },
  { path: '/manage/compliance/reports', element: <ComplianceReport /> },
  { path: '/manage/compliance/retention', element: <DataRetention /> },
  { path: '/manage/security', element: <SecuritySettings /> },

  // 用户
  { path: '/manage/users', element: <UserManagement /> },
  { path: '/manage/tenants', element: <TenantManagement /> },

  // 设置
  { path: '/manage/settings/sso', element: <SSOSettings /> },
];
```

---

> 文档维护: Open ACE 开发团队
> 最后更新: 2026-03-23

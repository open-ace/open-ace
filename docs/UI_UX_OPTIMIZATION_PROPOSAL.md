# Open ACE 企业级AI工作平台 UI/UX 优化方案

> 文档版本: 1.0  
> 创建日期: 2026-03-23  
> 目标: 打造"方便用、方便管"的企业级AI工作平台

---

## 目录

1. [现有项目功能分析](#一现有项目功能分析)
2. [现有页面功能详解](#二现有页面功能详解)
3. [现有UI/UX问题分析](#三现有uiux问题分析)
4. [优化方案概述](#四优化方案概述)
5. [方案A: 渐进式优化方案](#五方案a渐进式优化方案保留现有页面功能)
6. [方案B: 双轨制重构方案](#六方案b双轨制重构方案)
7. [方案C: 全新设计系统方案](#七方案c全新设计系统方案)
8. [方案对比与建议](#八方案对比与建议)

---

## 一、现有项目功能分析

### 1.1 技术栈概览

| 层级 | 技术栈 |
|------|--------|
| **前端** | React 18 + TypeScript + Vite + React Router 7 |
| **状态管理** | Zustand + TanStack Query |
| **UI框架** | Bootstrap 5 + Chart.js |
| **后端** | Python Flask |
| **数据库** | SQLite (开发) / PostgreSQL (生产) |
| **认证** | Session-based + SSO (OAuth2/OIDC) |

### 1.2 后端API模块

| 模块 | API端点数 | 核心功能 |
|------|-----------|----------|
| **认证模块** | 5 | 登录/登出/用户资料/SSO |
| **使用量统计** | 8 | 摘要/今日/工具/日期范围/趋势 |
| **消息管理** | 6 | 消息列表/发送者/对话历史/时间线 |
| **分析模块** | 10 | 关键指标/小时使用/峰值/用户排名/异常检测 |
| **管理模块** | 6 | 用户CRUD/配额管理 |
| **治理模块** | 8 | 审计日志/配额/内容安全/告警 |
| **工作区模块** | 10 | 提示词/会话/工具/团队/共享 |
| **租户模块** | 8 | 租户CRUD/配额/暂停激活 |
| **ROI分析** | 6 | ROI指标/趋势/成本分解 |
| **告警模块** | 4 | 告警列表/SSE实时流 |
| **合规模块** | 4 | 合规报告/数据保留 |

**总计: 75+ API端点**

### 1.3 数据模型

```
核心数据模型:
├── User (用户) - 认证/角色/配额
├── Tenant (租户) - 多租户隔离
├── Message (消息) - AI对话记录
├── Session (会话) - 用户会话
├── DailyUsage (每日使用量) - 统计数据
├── AuditLog (审计日志) - 操作追踪
├── PromptTemplate (提示词模板) - 提示词库
├── ContentFilterRule (内容过滤规则) - 安全控制
└── SecuritySettings (安全设置) - 安全配置
```

### 1.4 支持的AI工具

- **OpenClaw** - 自研AI工具
- **Claude** - Anthropic Claude
- **Qwen** - 阿里通义千问

### 1.5 国际化支持

- 英语 (en)
- 中文 (zh)
- 日语 (ja)
- 韩语 (ko)

---

## 二、现有页面功能详解

### 2.1 页面路由与功能矩阵

| 路由 | 页面 | 核心功能 | 数据展示 |
|------|------|----------|----------|
| `/` | Dashboard | 使用概览 | 今日卡片/总览卡片/趋势图/分布图/工具表 |
| `/messages` | Messages | 消息列表 | 过滤器/消息卡片/分页 |
| `/analysis` | Analysis | 数据分析 | 指标卡片/热力图/趋势/异常/建议 |
| `/management` | Management | 管理中心 | Tab切换(用户/配额/审计/过滤/安全) |
| `/sessions` | Sessions | 会话管理 | 状态卡片/会话列表/详情展开 |
| `/prompts` | Prompts | 提示词库 | 卡片网格/CRUD/变量渲染 |
| `/report` | Report | 个人报告 | 统计卡片/趋势图/日使用表 |
| `/workspace` | Workspace | AI工作区 | iframe嵌入外部AI工具 |
| `/login` | Login | 登录 | 表单认证 |
| `/security` | SecuritySettings | 安全设置 | 安全配置 |

### 2.2 各页面功能详解

#### Dashboard (仪表盘)
```
功能组件:
├── 过滤器 - 主机/工具选择/自动刷新
├── 今日使用 - 3个工具卡片(OpenClaw/Claude/Qwen)
├── 总览 - 3个累计统计卡片
├── 趋势图 - 30天Token趋势折线图
├── 分布图 - Token分布饼图
└── 工具表 - 可排序的详细数据表
```

#### Messages (消息列表)
```
功能组件:
├── 过滤器 - 日期/主机/工具/发送者/角色/搜索
├── 统计 - 总消息数
├── 消息卡片 - 角色标识/内容预览/展开详情
└── 分页 - 20条/页
```

#### Analysis (数据分析)
```
功能组件:
├── 过滤器 - 快捷日期/自定义日期/工具/主机
├── 指标卡片 - 6个关键指标(Token/请求/用户/工具/异常/健康分)
├── 使用热力图 - 24小时使用分布
├── 趋势图 - Token趋势折线图
├── 工具对比 - 水平条形图
├── 峰值时段 - Top5日期表
├── 活跃用户 - Top10用户表
├── 异常检测 - 异常列表
├── 优化建议 - 建议列表
├── 会话统计 - 详细统计表
├── 用户分层 - 环形图
└── 对话历史 - Tab切换
```

#### Management (管理中心)
```
Tab页面:
├── 用户管理 - 用户CRUD/角色分配/密码重置
├── 配额管理 - 用户配额设置/使用情况
├── 审计日志 - 操作日志查询/导出
├── 内容过滤 - 过滤规则CRUD
└── 安全设置 - 全局安全配置
```

#### Sessions (会话管理)
```
功能组件:
├── 统计卡片 - 总会话/活跃会话/消息/Token
├── 过滤器 - 工具/状态/类型/搜索
├── 会话卡片 - 标题/状态/类型/元信息/消息展开
└── 分页 - 20条/页
```

#### Prompts (提示词库)
```
功能组件:
├── 过滤器 - 分类/搜索
├── 提示词卡片 - 名称/分类/标签/描述/使用次数
├── 创建/编辑模态框 - 完整表单/变量定义
├── 渲染模态框 - 变量输入/结果预览/复制
└── 分页 - 20条/页
```

#### Report (个人报告)
```
功能组件:
├── 日期选择 - 开始/结束日期
├── 统计卡片 - Token/输入/输出/请求
├── 趋势图 - Token趋势
├── 分布图 - 工具分布
└── 日使用表 - 详细数据
```

#### Workspace (工作区)
```
功能组件:
└── iframe嵌入 - 外部AI工具界面
```

---

## 三、现有UI/UX问题分析

### 3.1 布局问题

#### 问题1: 侧边栏设计不够灵活
```
现状:
- 固定250px宽度，折叠后60px
- 所有页面平铺在一级导航
- 移动端体验差(侧边栏隐藏，无替代导航)

影响:
- 8个导航项平铺，缺乏层次感
- "用"与"管"场景混杂
- 移动端用户无法便捷导航
```

#### 问题2: 页面结构单一
```
现状:
- 所有页面采用相同布局: Header + Content
- 无针对不同场景的布局优化
- 大屏空间利用率低

影响:
- 信息密度不足
- 用户需要频繁切换页面
- 无法同时查看多个数据源
```

#### 问题3: 响应式设计不完善
```
现状:
- 移动端侧边栏直接隐藏
- 表格在小屏幕上水平滚动
- 卡片布局固定3列

影响:
- 移动端用户体验差
- 关键信息在移动端难以查看
```

### 3.2 信息架构问题

#### 问题4: 导航分类不清晰
```
现状导航项:
Dashboard | Messages | Analysis | Management | Sessions | Prompts | Report | Workspace

问题:
- 8个平铺项，认知负担重
- 功能边界模糊(Analysis vs Report vs Dashboard)
- "用"(Workspace/Prompts)与"管"(Management/Analysis)混杂

建议分类:
├── 工作区 (用)
│   ├── Workspace
│   ├── Prompts
│   └── Sessions
├── 分析 (管)
│   ├── Dashboard
│   ├── Analysis
│   └── Messages
└── 管理 (管)
    ├── 用户管理
    ├── 配额管理
    └── 安全设置
```

#### 问题5: 功能入口分散
```
问题:
- 相关功能分布在不同页面
- 用户需要记住多个入口
- 无全局搜索功能

示例:
- 用户配额: Management > Quota
- 用户使用报告: Report
- 用户消息: Messages
→ 同一用户的信息分散在3个页面
```

### 3.3 交互体验问题

#### 问题6: 过滤器体验不一致
```
现状:
- Dashboard: 下拉选择 + 开关
- Messages: 日期输入 + 下拉 + 复选框 + 搜索框
- Analysis: 快捷按钮 + 日期输入 + 下拉
- Sessions: 下拉 + 搜索框

问题:
- 样式不统一
- 位置不固定
- 交互模式不一致
```

#### 问题7: 数据表格功能单一
```
现状:
- 基础排序
- 无列选择
- 无数据导出
- 无批量操作
- 无自定义视图

影响:
- 用户无法定制数据展示
- 数据分析效率低
```

#### 问题8: 卡片交互不足
```
现状:
- Dashboard卡片: 仅展示数据
- Messages卡片: 点击展开
- Sessions卡片: 点击展开

缺失:
- 快捷操作按钮
- 悬停预览
- 拖拽排序
- 收藏/标记
```

### 3.4 视觉设计问题

#### 问题9: 视觉层次不分明
```
现状:
- 所有卡片样式相同
- 无重点突出
- 信息优先级不清晰

影响:
- 用户难以快速定位关键信息
- 重要告警/异常容易被忽略
```

#### 问题10: 空间利用不合理
```
现状:
- 大屏: 内容居中，两侧空白
- 卡片间距固定
- 无紧凑/舒适模式切换

影响:
- 大屏用户无法获取更多信息
- 信息密度无法根据需求调整
```

### 3.5 功能缺失

| 缺失功能 | 影响 | 优先级 |
|----------|------|--------|
| 全局搜索 | 用户无法快速定位功能/数据 | 高 |
| 快捷操作面板 | 高频操作需要多次点击 | 高 |
| 个性化仪表盘 | 用户无法定制关注指标 | 中 |
| 实时通知中心 | 用户无法及时获知重要事件 | 高 |
| 数据导出 | 用户无法导出分析结果 | 中 |
| 批量操作 | 管理效率低 | 中 |
| 操作历史 | 无法追溯用户操作 | 低 |
| 帮助系统 | 新用户学习成本高 | 中 |

---

## 四、优化方案概述

基于以上分析，提出三种优化方案：

| 方案 | 特点 | 改动程度 | 开发周期 | 风险 |
|------|------|----------|----------|------|
| **A: 渐进式优化** | 保留现有页面功能，逐步优化 | 小 | 2-3周 | 低 |
| **B: 双轨制重构** | "用"与"管"分离，重新设计布局 | 中 | 4-6周 | 中 |
| **C: 全新设计系统** | 全面重构，引入现代设计系统 | 大 | 8-12周 | 高 |

---

## 五、方案A: 渐进式优化方案（保留现有页面功能）

### 5.1 设计原则

- **最小改动**: 保留所有现有页面和功能
- **渐进增强**: 在现有基础上添加优化
- **向后兼容**: 不破坏现有用户习惯

### 5.2 优化内容

#### 5.2.1 导航优化

**现状**: 8个平铺导航项

**优化**: 分组导航 + 收纳

```
优化后导航:
├── 📊 概览
│   └── Dashboard
├── 💬 数据
│   ├── Messages
│   ├── Analysis
│   └── Report
├── 🛠️ 工具
│   ├── Workspace
│   ├── Prompts
│   └── Sessions
└── ⚙️ 管理
    └── Management
```

**实现方式**:
```tsx
// Sidebar.tsx 修改
const navGroups = [
  {
    id: 'overview',
    title: '概览',
    items: [{ id: 'dashboard', label: '仪表盘', icon: 'bi-speedometer2' }]
  },
  {
    id: 'data',
    title: '数据',
    items: [
      { id: 'messages', label: '消息', icon: 'bi-chat-dots' },
      { id: 'analysis', label: '分析', icon: 'bi-graph-up' },
      { id: 'report', label: '报告', icon: 'bi-file-earmark-bar-graph' }
    ]
  },
  // ...
];
```

#### 5.2.2 过滤器统一

**现状**: 各页面过滤器样式不一致

**优化**: 统一过滤器组件

```tsx
// 新增: components/common/FilterBar.tsx
interface FilterBarProps {
  filters: FilterConfig[];
  onFilterChange: (key: string, value: any) => void;
  onReset: () => void;
  autoRefresh?: boolean;
  onAutoRefreshChange?: (enabled: boolean) => void;
}

// 统一使用
<FilterBar
  filters={[
    { type: 'date', key: 'startDate', label: '开始日期' },
    { type: 'date', key: 'endDate', label: '结束日期' },
    { type: 'select', key: 'tool', label: '工具', options: toolOptions },
    { type: 'select', key: 'host', label: '主机', options: hostOptions },
  ]}
  onFilterChange={handleFilterChange}
  onReset={handleReset}
  autoRefresh={autoRefresh}
  onAutoRefreshChange={setAutoRefresh}
/>
```

#### 5.2.3 增强表格功能

**现状**: 基础排序

**优化**: 添加列选择、导出、批量操作

```tsx
// 增强表格组件
<EnhancedTable
  data={data}
  columns={columns}
  features={{
    columnSelector: true,      // 列选择
    export: ['csv', 'json'],   // 导出
    pagination: true,          // 分页
    pageSize: [20, 50, 100],   // 每页条数
    rowSelection: true,        // 行选择
    batchActions: [            // 批量操作
      { label: '导出选中', action: handleExportSelected },
      { label: '删除选中', action: handleDeleteSelected },
    ],
  }}
/>
```

#### 5.2.4 添加全局搜索

```tsx
// Header.tsx 添加搜索框
<div className="global-search">
  <SearchInput
    placeholder="搜索功能、数据..."
    onSearch={handleGlobalSearch}
    suggestions={[
      { type: 'page', label: '消息列表', path: '/messages' },
      { type: 'action', label: '创建提示词', action: () => navigate('/prompts', { state: { create: true } }) },
      { type: 'data', label: '用户: admin', path: '/management', query: { user: 'admin' } },
    ]}
  />
</div>
```

#### 5.2.5 添加通知中心

```tsx
// Header.tsx 添加通知图标
<div className="notification-center">
  <Dropdown
    trigger={<Badge count={unreadCount}><i className="bi bi-bell" /></Badge>}
    content={<NotificationList notifications={notifications} />}
  />
</div>
```

#### 5.2.6 移动端优化

```css
/* 添加底部导航 */
@media (max-width: 768px) {
  .mobile-nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    justify-content: space-around;
    background: var(--bg-primary);
    border-top: 1px solid var(--border-color);
    padding: 8px 0;
    z-index: 1000;
  }
  
  .mobile-nav .nav-item {
    flex: 1;
    text-align: center;
  }
  
  .main-content {
    padding-bottom: 70px;
  }
}
```

### 5.3 实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| 第1周 | 导航分组、过滤器统一 | 5天 |
| 第2周 | 表格增强、全局搜索 | 5天 |
| 第3周 | 通知中心、移动端优化 | 5天 |

### 5.4 预期效果

- ✅ 保留所有现有功能
- ✅ 导航更清晰
- ✅ 操作更便捷
- ✅ 移动端可用
- ⚠️ 布局结构未改变
- ⚠️ "用"与"管"场景未分离

---

## 六、方案B: 双轨制重构方案

### 6.1 设计理念

**核心理念**: "用"与"管"分离，双入口设计

```
用户进入平台:
├── 🚀 工作模式 (用)
│   └── 专注AI对话和任务完成
└── 📊 管理模式 (管)
    └── 专注监控、分析、治理
```

### 6.2 整体布局

#### 6.2.1 工作模式布局

```
┌─────────────────────────────────────────────────────────────┐
│  Header: 搜索 | 通知 | 用户                                   │
├───────────┬─────────────────────────────────┬───────────────┤
│           │                                 │               │
│  会话列表  │         对话区域                 │   辅助面板    │
│           │                                 │               │
│  - 今日    │   ┌─────────────────────┐      │   - 提示词库  │
│  - 历史    │   │  AI消息             │      │   - 工具      │
│  - 收藏    │   └─────────────────────┘      │   - 文档      │
│           │                                 │               │
│  [+新建]   │   ┌─────────────────────┐      │               │
│           │   │  输入框             │      │               │
│           │   └─────────────────────┘      │               │
│           │                                 │               │
├───────────┴─────────────────────────────────┴───────────────┤
│  Status Bar: 模型 | Token用量 | 延迟                          │
└─────────────────────────────────────────────────────────────┘
```

#### 6.2.2 管理模式布局

```
┌─────────────────────────────────────────────────────────────┐
│  Header: 模式切换 | 搜索 | 通知 | 用户                        │
├───────────┬─────────────────────────────────────────────────┤
│           │                                                 │
│  导航菜单  │              主内容区域                          │
│           │                                                 │
│  概览     │   ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  ├──仪表盘 │   │ Token    │ │ 请求     │ │ 用户     │       │
│  分析     │   └──────────┘ └──────────┘ └──────────┘       │
│  ├──趋势   │                                                 │
│  ├──异常   │   ┌──────────────────────────────────────┐     │
│  治理     │   │           趋势图表                     │     │
│  ├──审计   │   └──────────────────────────────────────┘     │
│  ├──配额   │                                                 │
│  ├──安全   │   ┌─────────────────┐ ┌─────────────────┐      │
│  用户     │   │    活跃用户     │ │    工具分布     │      │
│  └──管理   │   └─────────────────┘ └─────────────────┘      │
│           │                                                 │
└───────────┴─────────────────────────────────────────────────┘
```

### 6.3 页面重构

#### 6.3.1 工作模式页面

| 页面 | 功能 | 布局 |
|------|------|------|
| **Workspace** | AI对话主界面 | 三栏布局(会话列表/对话区/辅助面板) |
| **Prompts** | 提示词库 | 侧边抽屉(从右侧滑出) |
| **Sessions** | 会话管理 | 集成到Workspace左侧 |

#### 6.3.2 管理模式页面

| 页面 | 功能 | 布局 |
|------|------|------|
| **Dashboard** | 概览仪表盘 | 卡片网格 + 图表 |
| **Analysis** | 数据分析 | 高级图表 + 下钻 |
| **Messages** | 消息查询 | 高级表格 + 详情面板 |
| **Governance** | 治理中心 | Tab页面(审计/配额/安全) |
| **Users** | 用户管理 | 表格 + 侧边详情 |

### 6.4 核心组件设计

#### 6.4.1 模式切换器

```tsx
// components/common/ModeSwitcher.tsx
export const ModeSwitcher: React.FC = () => {
  const [mode, setMode] = useState<'work' | 'manage'>('work');
  
  return (
    <div className="mode-switcher">
      <button
        className={cn('mode-btn', mode === 'work' && 'active')}
        onClick={() => setMode('work')}
      >
        <i className="bi bi-rocket" />
        <span>工作模式</span>
      </button>
      <button
        className={cn('mode-btn', mode === 'manage' && 'active')}
        onClick={() => setMode('manage')}
      >
        <i className="bi bi-bar-chart" />
        <span>管理模式</span>
      </button>
    </div>
  );
};
```

#### 6.4.2 工作模式布局

```tsx
// layouts/WorkLayout.tsx
export const WorkLayout: React.FC = ({ children }) => {
  return (
    <div className="work-layout">
      {/* 会话列表 */}
      <aside className="session-panel">
        <SessionList />
      </aside>
      
      {/* 主内容 */}
      <main className="main-panel">
        {children}
      </main>
      
      {/* 辅助面板 */}
      <aside className="assist-panel">
        <Tabs defaultTab="prompts">
          <Tab id="prompts" label="提示词">
            <PromptLibrary />
          </Tab>
          <Tab id="tools" label="工具">
            <ToolList />
          </Tab>
          <Tab id="docs" label="文档">
            <DocumentList />
          </Tab>
        </Tabs>
      </aside>
      
      {/* 状态栏 */}
      <footer className="status-bar">
        <span>模型: GPT-4</span>
        <span>Token: 1,234 / 10,000</span>
        <span>延迟: 120ms</span>
      </footer>
    </div>
  );
};
```

#### 6.4.3 管理模式布局

```tsx
// layouts/ManageLayout.tsx
export const ManageLayout: React.FC = ({ children }) => {
  return (
    <div className="manage-layout">
      {/* 侧边导航 */}
      <nav className="side-nav">
        <NavSection title="概览">
          <NavItem id="dashboard" icon="bi-speedometer2">仪表盘</NavItem>
        </NavSection>
        <NavSection title="分析">
          <NavItem id="trend" icon="bi-graph-up">趋势</NavItem>
          <NavItem id="anomaly" icon="bi-exclamation-triangle">异常</NavItem>
        </NavSection>
        <NavSection title="治理">
          <NavItem id="audit" icon="bi-journal-text">审计</NavItem>
          <NavItem id="quota" icon="bi-sliders">配额</NavItem>
          <NavItem id="security" icon="bi-shield">安全</NavItem>
        </NavSection>
        <NavSection title="用户">
          <NavItem id="users" icon="bi-people">管理</NavItem>
        </NavSection>
      </nav>
      
      {/* 主内容 */}
      <main className="main-content">
        {children}
      </main>
    </div>
  );
};
```

### 6.5 路由重构

```tsx
// 路由配置
const routes = [
  // 工作模式
  {
    path: '/work',
    layout: WorkLayout,
    children: [
      { path: '/', element: <Workspace /> },
      { path: 'session/:id', element: <Workspace /> },
    ],
  },
  
  // 管理模式
  {
    path: '/manage',
    layout: ManageLayout,
    children: [
      { path: 'dashboard', element: <Dashboard /> },
      { path: 'analysis', element: <Analysis /> },
      { path: 'messages', element: <Messages /> },
      { path: 'governance', element: <Governance /> },
      { path: 'users', element: <UserManagement /> },
    ],
  },
  
  // 公共页面
  { path: '/login', element: <Login /> },
  { path: '/report', element: <Report /> },
];
```

### 6.6 实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| 第1-2周 | 布局组件开发(WorkLayout/ManageLayout) | 10天 |
| 第3-4周 | 工作模式页面重构 | 10天 |
| 第5-6周 | 管理模式页面重构 | 10天 |

### 6.7 预期效果

- ✅ "用"与"管"场景清晰分离
- ✅ 针对不同场景优化布局
- ✅ 用户体验大幅提升
- ✅ 保留所有现有功能
- ⚠️ 需要用户适应新模式
- ⚠️ 开发周期较长

---

## 七、方案C: 全新设计系统方案

### 7.1 设计理念

**核心理念**: 引入现代设计系统，打造企业级UI组件库

```
设计系统:
├── Design Tokens (设计变量)
├── Component Library (组件库)
├── Layout System (布局系统)
├── Pattern Library (模式库)
└── Accessibility (无障碍)
```

### 7.2 技术选型

| 方面 | 现有 | 升级方案 |
|------|------|----------|
| **CSS框架** | Bootstrap 5 | Tailwind CSS |
| **组件库** | 自定义组件 | Radix UI + Tailwind |
| **图标** | Bootstrap Icons | Lucide Icons |
| **图表** | Chart.js | Recharts / ECharts |
| **表格** | 自定义表格 | TanStack Table |
| **表单** | 自定义表单 | React Hook Form + Zod |

### 7.3 设计系统架构

#### 7.3.1 Design Tokens

```css
/* tokens.css */
:root {
  /* 颜色系统 */
  --color-primary-50: #eff6ff;
  --color-primary-100: #dbeafe;
  --color-primary-500: #3b82f6;
  --color-primary-600: #2563eb;
  --color-primary-700: #1d4ed8;
  
  /* 语义颜色 */
  --color-success: #10b981;
  --color-warning: #f59e0b;
  --color-danger: #ef4444;
  --color-info: #3b82f6;
  
  /* 间距系统 */
  --spacing-1: 0.25rem;
  --spacing-2: 0.5rem;
  --spacing-3: 0.75rem;
  --spacing-4: 1rem;
  --spacing-6: 1.5rem;
  --spacing-8: 2rem;
  
  /* 圆角 */
  --radius-sm: 0.25rem;
  --radius-md: 0.375rem;
  --radius-lg: 0.5rem;
  --radius-xl: 0.75rem;
  
  /* 阴影 */
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1);
  
  /* 动画 */
  --duration-fast: 150ms;
  --duration-normal: 300ms;
  --ease-default: cubic-bezier(0.4, 0, 0.2, 1);
}
```

#### 7.3.2 组件库结构

```
components/
├── primitives/           # 基础组件
│   ├── Button/
│   ├── Input/
│   ├── Select/
│   ├── Checkbox/
│   ├── Radio/
│   └── ...
├── composite/            # 复合组件
│   ├── DataTable/
│   ├── FormBuilder/
│   ├── FilterBar/
│   ├── ChartCard/
│   └── ...
├── layouts/              # 布局组件
│   ├── AppLayout/
│   ├── WorkspaceLayout/
│   ├── DashboardLayout/
│   └── ...
└── patterns/             # 模式组件
    ├── EntityTable/
    ├── StatsOverview/
    ├── TrendChart/
    └── ...
```

### 7.4 核心组件设计

#### 7.4.1 DataTable (高级数据表格)

```tsx
// components/composite/DataTable.tsx
import { useReactTable, getCoreRowModel, getSortedRowModel } from '@tanstack/react-table';

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  features?: {
    sorting?: boolean;
    filtering?: boolean;
    pagination?: boolean;
    columnVisibility?: boolean;
    rowSelection?: boolean;
    export?: ('csv' | 'json' | 'excel')[];
  };
}

export function DataTable<T>({ data, columns, features }: DataTableProps<T>) {
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="data-table-container">
      {/* 工具栏 */}
      <div className="table-toolbar">
        {features?.columnVisibility && <ColumnSelector table={table} />}
        {features?.export && <ExportButton formats={features.export} />}
      </div>
      
      {/* 表格 */}
      <table className="data-table">
        <thead>
          {table.getHeaderGroups().map(group => (
            <tr key={group.id}>
              {group.headers.map(header => (
                <th key={header.id}>
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => (
            <tr key={row.id}>
              {row.getVisibleCells().map(cell => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      
      {/* 分页 */}
      {features?.pagination && <TablePagination table={table} />}
    </div>
  );
}
```

#### 7.4.2 FilterBar (统一过滤器)

```tsx
// components/composite/FilterBar.tsx
interface FilterConfig {
  key: string;
  type: 'text' | 'select' | 'date' | 'dateRange' | 'checkbox' | 'search';
  label: string;
  options?: { value: string; label: string }[];
  placeholder?: string;
}

interface FilterBarProps {
  filters: FilterConfig[];
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
  onReset?: () => void;
  autoRefresh?: boolean;
  onAutoRefreshChange?: (enabled: boolean) => void;
}

export function FilterBar({
  filters,
  values,
  onChange,
  onReset,
  autoRefresh,
  onAutoRefreshChange,
}: FilterBarProps) {
  return (
    <div className="filter-bar">
      <div className="filter-items">
        {filters.map(filter => (
          <FilterItem
            key={filter.key}
            config={filter}
            value={values[filter.key]}
            onChange={(value) => onChange(filter.key, value)}
          />
        ))}
      </div>
      
      <div className="filter-actions">
        {onReset && (
          <Button variant="ghost" size="sm" onClick={onReset}>
            <RotateCcw className="w-4 h-4" />
            重置
          </Button>
        )}
        {autoRefresh !== undefined && (
          <label className="auto-refresh">
            <Switch checked={autoRefresh} onCheckedChange={onAutoRefreshChange} />
            自动刷新
          </label>
        )}
      </div>
    </div>
  );
}
```

#### 7.4.3 StatsCard (统计卡片)

```tsx
// components/composite/StatsCard.tsx
interface StatsCardProps {
  title: string;
  value: string | number;
  change?: {
    value: number;
    type: 'increase' | 'decrease';
  };
  icon?: React.ReactNode;
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'danger';
  sparkline?: number[];
}

export function StatsCard({
  title,
  value,
  change,
  icon,
  variant = 'default',
  sparkline,
}: StatsCardProps) {
  return (
    <div className={cn('stats-card', `stats-card--${variant}`)}>
      <div className="stats-card__header">
        <span className="stats-card__title">{title}</span>
        {icon && <span className="stats-card__icon">{icon}</span>}
      </div>
      
      <div className="stats-card__value">{value}</div>
      
      {change && (
        <div className={cn('stats-card__change', change.type)}>
          {change.type === 'increase' ? (
            <TrendingUp className="w-4 h-4" />
          ) : (
            <TrendingDown className="w-4 h-4" />
          )}
          <span>{Math.abs(change.value)}%</span>
        </div>
      )}
      
      {sparkline && (
        <div className="stats-card__sparkline">
          <Sparkline data={sparkline} />
        </div>
      )}
    </div>
  );
}
```

### 7.5 页面模板

#### 7.5.1 Dashboard 页面模板

```tsx
// pages/Dashboard.tsx
export function Dashboard() {
  return (
    <PageLayout>
      <PageHeader title="仪表盘">
        <FilterBar filters={dashboardFilters} />
      </PageHeader>
      
      <StatsGrid columns={4}>
        <StatsCard title="今日Token" value="1.2M" change={{ value: 12, type: 'increase' }} />
        <StatsCard title="请求数" value="8,432" change={{ value: 5, type: 'increase' }} />
        <StatsCard title="活跃用户" value="156" change={{ value: 3, type: 'decrease' }} />
        <StatsCard title="健康分" value="98%" variant="success" />
      </StatsGrid>
      
      <Grid columns={12} gap={4}>
        <GridItem colSpan={8}>
          <Card title="使用趋势">
            <LineChart data={trendData} />
          </Card>
        </GridItem>
        <GridItem colSpan={4}>
          <Card title="工具分布">
            <PieChart data={distributionData} />
          </Card>
        </GridItem>
      </Grid>
      
      <Card title="工具详情">
        <DataTable data={toolsData} columns={toolColumns} features={{ sorting: true, export: ['csv'] }} />
      </Card>
    </PageLayout>
  );
}
```

### 7.6 实施计划

| 阶段 | 内容 | 时间 |
|------|------|------|
| 第1-2周 | 设计系统基础(Tokens/基础组件) | 10天 |
| 第3-4周 | 复合组件开发(DataTable/FilterBar等) | 10天 |
| 第5-6周 | 布局系统开发 | 10天 |
| 第7-8周 | 页面迁移(Dashboard/Analysis) | 10天 |
| 第9-10周 | 页面迁移(Management/Messages) | 10天 |
| 第11-12周 | 页面迁移(Workspace/Prompts) + 测试 | 10天 |

### 7.7 预期效果

- ✅ 现代化设计系统
- ✅ 高度可定制组件
- ✅ 优秀的开发体验
- ✅ 完善的无障碍支持
- ✅ 更好的性能
- ⚠️ 开发周期最长
- ⚠️ 需要团队学习新技术栈

---

## 八、方案对比与建议

### 8.1 方案对比矩阵

| 维度 | 方案A | 方案B | 方案C |
|------|-------|-------|-------|
| **改动程度** | 小 | 中 | 大 |
| **开发周期** | 2-3周 | 4-6周 | 8-12周 |
| **风险等级** | 低 | 中 | 高 |
| **用户体验提升** | 中 | 高 | 最高 |
| **保留现有功能** | ✅ 完全保留 | ✅ 完全保留 | ✅ 完全保留 |
| **"用""管"分离** | ❌ | ✅ | ✅ |
| **移动端优化** | ✅ | ✅ | ✅ |
| **设计系统升级** | ❌ | ❌ | ✅ |
| **团队学习成本** | 低 | 中 | 高 |
| **可维护性** | 中 | 高 | 最高 |

### 8.2 推荐选择

#### 推荐方案: B (双轨制重构方案)

**理由**:
1. **平衡投入产出**: 在合理开发周期内实现核心优化目标
2. **解决核心问题**: "用"与"管"场景分离，用户体验大幅提升
3. **风险可控**: 基于现有技术栈，团队学习成本低
4. **保留灵活性**: 后续可渐进升级到方案C

#### 实施建议:

```
第一阶段 (方案B):
├── 实现双轨制布局
├── 重构核心页面
└── 优化移动端体验

第二阶段 (渐进升级):
├── 引入Tailwind CSS
├── 升级关键组件
└── 完善设计系统

第三阶段 (持续优化):
├── 性能优化
├── 无障碍支持
└── 用户反馈迭代
```

### 8.3 下一步行动

1. **确认方案**: 与团队讨论确定最终方案
2. **详细设计**: 针对选定方案输出详细设计文档
3. **原型验证**: 制作关键页面原型进行用户测试
4. **迭代开发**: 按阶段实施开发计划

---

## 附录

### A. 参考资源

- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [Radix UI Primitives](https://www.radix-ui.com/primitives)
- [TanStack Table](https://tanstack.com/table)
- [Recharts](https://recharts.org/)
- [Enterprise UI Patterns](https://www.enterpriseui.com/patterns)

### B. 设计参考

- [Vercel Dashboard](https://vercel.com/dashboard)
- [Linear App](https://linear.app/)
- [Notion](https://notion.so)
- [GitHub Dashboard](https://github.com/dashboard)

---

> 文档维护: Open ACE 开发团队  
> 最后更新: 2026-03-23
# 后端功能模块与前端页面对应关系分析

> 分析日期: 2026-03-23
> 目的: 识别缺少前端页面的后端功能模块

---

## 一、对应关系总览

### ✅ 已有前端页面的后端模块

| 后端模块 | 路由文件 | 前端组件 | 状态 |
|---------|---------|---------|------|
| **Usage** | `app/routes/usage.py` | `Dashboard.tsx` | ✅ 完整 |
| **Messages** | `app/routes/messages.py` | `Messages.tsx` | ✅ 完整 |
| **Analysis** | `app/routes/analysis.py` | `TrendAnalysis.tsx`, `AnomalyDetection.tsx` | ✅ 完整 |
| **Workspace** | `app/routes/workspace.py` | `Workspace.tsx` | ✅ 完整 |
| **Auth** | `app/routes/auth.py` | `Login.tsx`, `LogoutSuccess.tsx` | ✅ 完整 |
| **Admin** | `app/routes/admin.py` | `UserManagement.tsx` | ✅ 完整 |
| **Governance** | `app/routes/governance.py` | `AuditLog.tsx`, `QuotaManagement.tsx`, `ContentFilter.tsx`, `SecuritySettings.tsx` | ✅ 完整 |

### ❌ 缺少前端页面的后端模块

| 后端模块 | 路由文件 | 功能描述 | 优先级 |
|---------|---------|---------|--------|
| **Alerts** | `app/routes/alerts.py` | 告警管理、通知偏好设置 | P1 |
| **Compliance** | `app/routes/compliance.py` | 合规报告、审计分析、数据保留 | P1 |
| **ROI** | `app/routes/roi.py` | ROI 分析、成本优化建议 | P2 |
| **Tenant** | `app/routes/tenant.py` | 多租户管理 | P2 |
| **SSO** | `app/routes/sso.py` | SSO 提供者管理 | P3 |
| **Analytics** | `app/routes/analytics.py` | 分析报告导出、预测、效率指标 | P2 |

---

## 二、缺失模块详细分析

### 2.1 Alerts (告警管理) - P1

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/alerts` | GET | 获取告警列表 |
| `/api/alerts/unread-count` | GET | 获取未读告警数 |
| `/api/alerts/<alert_id>/read` | POST | 标记告警已读 |
| `/api/alerts/read-all` | POST | 标记全部已读 |
| `/api/alerts/<alert_id>` | DELETE | 删除告警 |
| `/api/alerts/preferences` | GET/PUT | 获取/设置通知偏好 |
| `/api/alerts/stream` | GET | SSE 实时告警流 |

**建议前端页面**:

- **页面名称**: AlertManagement (告警管理)
- **路由**: `/manage/alerts`
- **所属分组**: 治理
- **功能**:
  - 告警列表展示（支持筛选：类型、严重程度、已读/未读）
  - 告警详情查看
  - 标记已读/全部已读
  - 删除告警
  - 通知偏好设置（邮件、推送、Webhook）
  - 实时告警通知（Header 通知图标）

**页面元素**:

| 区域 | 元素 | 类型 |
|------|------|------|
| **统计卡片** | 总告警数 | StatCard |
| | 未读告警 | StatCard |
| | 高危告警 | StatCard |
| **过滤器** | 告警类型 | Select |
| | 严重程度 | Select |
| | 已读状态 | Select |
| **列表** | 告警列表 | Table |
| | 标题/消息 | Column |
| | 类型/严重程度 | Column (Badge) |
| | 时间 | Column |
| | 操作 | Column (标记已读/删除) |
| **设置** | 通知偏好 | Modal |
| | 邮件开关 | Switch |
| | 推送开关 | Switch |
| | Webhook URL | Input |

---

### 2.2 Compliance (合规报告) - P1

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/compliance/reports` | GET | 获取报告类型列表 |
| `/api/compliance/reports` | POST | 生成合规报告 |
| `/api/compliance/reports/saved` | GET | 获取已保存报告 |
| `/api/compliance/reports/<report_id>` | GET | 获取报告详情 |
| `/api/compliance/audit/patterns` | GET | 审计模式分析 |
| `/api/compliance/audit/anomalies` | GET | 审计异常检测 |
| `/api/compliance/audit/user/<user_id>/profile` | GET | 用户行为画像 |
| `/api/compliance/audit/security-score` | GET | 安全评分 |
| `/api/compliance/retention/rules` | GET/PUT | 数据保留规则 |
| `/api/compliance/retention/cleanup` | POST | 执行数据清理 |
| `/api/compliance/retention/history` | GET | 清理历史 |
| `/api/compliance/retention/storage` | GET | 存储估算 |
| `/api/compliance/retention/status` | GET | 合规状态 |

**建议前端页面**:

#### 2.2.1 合规报告页面

- **页面名称**: ComplianceReport (合规报告)
- **路由**: `/manage/compliance/reports`
- **所属分组**: 治理
- **功能**:
  - 报告类型选择
  - 日期范围选择
  - 生成报告（JSON/CSV）
  - 查看已保存报告
  - 下载报告

#### 2.2.2 数据保留页面

- **页面名称**: DataRetention (数据保留)
- **路由**: `/manage/compliance/retention`
- **所属分组**: 治理
- **功能**:
  - 查看保留规则
  - 设置保留期限
  - 执行清理（支持预览）
  - 查看清理历史
  - 存储使用估算

#### 2.2.3 审计分析页面

- **页面名称**: AuditAnalysis (审计分析)
- **路由**: `/manage/compliance/audit`
- **所属分组**: 分析
- **功能**:
  - 审计模式分析
  - 异常行为检测
  - 用户行为画像
  - 安全评分展示

---

### 2.3 ROI (投资回报分析) - P2

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/roi` | GET | 获取 ROI 指标 |
| `/api/roi/trend` | GET | ROI 趋势 |
| `/api/roi/by-tool` | GET | 按工具 ROI |
| `/api/roi/by-user` | GET | 按用户 ROI |
| `/api/roi/cost-breakdown` | GET | 成本分解 |
| `/api/roi/daily-costs` | GET | 每日成本 |
| `/api/roi/summary` | GET | ROI 摘要 |
| `/api/optimization/suggestions` | GET | 成本优化建议 |
| `/api/optimization/cost-trend` | GET | 成本趋势 |
| `/api/optimization/efficiency` | GET | 效率报告 |

**建议前端页面**:

- **页面名称**: ROIAnalysis (ROI 分析)
- **路由**: `/manage/analysis/roi`
- **所属分组**: 分析
- **功能**:
  - ROI 总览卡片
  - ROI 趋势图表
  - 按工具/用户分解
  - 成本分解图表
  - 成本优化建议
  - 效率分析报告

**页面元素**:

| 区域 | 元素 | 类型 |
|------|------|------|
| **指标卡片** | 总 ROI | StatCard |
| | 总成本 | StatCard |
| | 节省成本 | StatCard |
| | 效率提升 | StatCard |
| **图表** | ROI 趋势 | LineChart |
| | 成本分解 | PieChart |
| | 按工具对比 | BarChart |
| | 每日成本 | AreaChart |
| **表格** | 优化建议 | Table |
| | 用户 ROI 排名 | Table |

---

### 2.4 Tenant (多租户管理) - P2

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/tenants` | GET | 租户列表 |
| `/api/tenants` | POST | 创建租户 |
| `/api/tenants/<id>` | GET/PUT/DELETE | 租户 CRUD |
| `/api/tenants/<id>/quota` | PUT | 更新配额 |
| `/api/tenants/<id>/settings` | PUT | 更新设置 |
| `/api/tenants/<id>/suspend` | POST | 暂停租户 |
| `/api/tenants/<id>/activate` | POST | 激活租户 |
| `/api/tenants/<id>/usage` | GET | 使用历史 |
| `/api/tenants/<id>/stats` | GET | 统计数据 |
| `/api/tenants/<id>/check-quota` | POST | 检查配额 |
| `/api/tenants/plans` | GET | 套餐配额 |

**建议前端页面**:

- **页面名称**: TenantManagement (租户管理)
- **路由**: `/manage/tenants`
- **所属分组**: 用户（或新增"系统"分组）
- **功能**:
  - 租户列表
  - 创建/编辑/删除租户
  - 配额管理
  - 设置管理
  - 暂停/激活租户
  - 使用统计查看

**页面元素**:

| 区域 | 元素 | 类型 |
|------|------|------|
| **统计卡片** | 总租户数 | StatCard |
| | 活跃租户 | StatCard |
| | 试用租户 | StatCard |
| **表格** | 租户列表 | Table |
| | 名称/套餐 | Column |
| | 状态 | Column (Badge) |
| | 配额使用 | Column (Progress) |
| | 操作 | Column |
| **模态框** | 创建租户 | Modal |
| | 编辑租户 | Modal |
| | 配额设置 | Modal |

---

### 2.5 SSO (单点登录管理) - P3

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/sso/providers` | GET | SSO 提供者列表 |
| `/api/sso/providers` | POST | 注册提供者 |
| `/api/sso/providers/<name>` | DELETE | 禁用提供者 |
| `/api/sso/login/<provider>` | GET | 开始登录 |
| `/api/sso/callback/<provider>` | GET | 回调处理 |
| `/api/sso/session` | GET/DELETE | 会话管理 |
| `/api/sso/identities/<user_id>` | GET | 用户身份 |
| `/api/sso/identities/<user_id>/<provider>` | DELETE | 解绑身份 |

**建议前端页面**:

- **页面名称**: SSOSettings (SSO 设置)
- **路由**: `/manage/settings/sso`
- **所属分组**: 治理（或设置）
- **功能**:
  - SSO 提供者列表
  - 注册新提供者
  - 配置提供者参数
  - 启用/禁用提供者
  - 查看用户绑定身份

---

### 2.6 Analytics (分析报告) - P2

**后端 API 端点**:

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/analytics/report` | GET | 综合使用报告 |
| `/api/analytics/forecast` | GET | 使用预测 |
| `/api/analytics/efficiency` | GET | 效率指标 |
| `/api/analytics/export` | GET | 导出数据 |

**建议**:

这些功能可以整合到现有的 `TrendAnalysis` 页面中，作为额外的 Tab 或卡片：
- 添加"预测"Tab 显示使用预测
- 添加"效率分析"卡片
- 添加"导出"按钮支持 CSV 导出

---

## 三、建议的导航结构调整

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
      { id: 'roi', label: 'ROI 分析', icon: 'bi-currency-dollar', path: '/manage/analysis/roi' },  // 新增
      { id: 'conversation-history', label: '会话历史', icon: 'bi-chat-history', path: '/manage/analysis/conversation-history' },
      { id: 'messages', label: '消息查询', icon: 'bi-chat-dots', path: '/manage/messages' },
    ],
  },
  {
    id: 'governance',
    title: '治理',
    items: [
      { id: 'audit', label: '审计日志', icon: 'bi-journal-text', path: '/manage/audit' },
      { id: 'audit-analysis', label: '审计分析', icon: 'bi-search', path: '/manage/compliance/audit' },  // 新增
      { id: 'quota', label: '配额管理', icon: 'bi-sliders', path: '/manage/quota' },
      { id: 'alerts', label: '告警管理', icon: 'bi-bell', path: '/manage/alerts' },  // 新增
      { id: 'content-filter', label: '内容过滤', icon: 'bi-shield-check', path: '/manage/governance/content-filter' },
      { id: 'compliance', label: '合规报告', icon: 'bi-file-earmark-text', path: '/manage/compliance/reports' },  // 新增
      { id: 'retention', label: '数据保留', icon: 'bi-database', path: '/manage/compliance/retention' },  // 新增
      { id: 'security', label: '安全设置', icon: 'bi-shield', path: '/manage/security' },
    ],
  },
  {
    id: 'users',
    title: '用户',
    items: [
      { id: 'users', label: '用户管理', icon: 'bi-people', path: '/manage/users' },
      { id: 'tenants', label: '租户管理', icon: 'bi-building', path: '/manage/tenants' },  // 新增
    ],
  },
  {
    id: 'settings',
    title: '设置',  // 新增分组
    items: [
      { id: 'sso', label: 'SSO 设置', icon: 'bi-key', path: '/manage/settings/sso' },
    ],
  },
];
```

---

## 四、实施优先级

### P1 - 高优先级（建议优先实现）

| 模块 | 页面 | 路由 | 工时估算 |
|------|------|------|----------|
| Alerts | AlertManagement | `/manage/alerts` | 4h |
| Compliance | ComplianceReport | `/manage/compliance/reports` | 4h |
| Compliance | DataRetention | `/manage/compliance/retention` | 4h |
| Compliance | AuditAnalysis | `/manage/compliance/audit` | 4h |

### P2 - 中优先级

| 模块 | 页面 | 路由 | 工时估算 |
|------|------|------|----------|
| ROI | ROIAnalysis | `/manage/analysis/roi` | 6h |
| Tenant | TenantManagement | `/manage/tenants` | 6h |
| Analytics | 整合到 TrendAnalysis | - | 2h |

### P3 - 低优先级

| 模块 | 页面 | 路由 | 工时估算 |
|------|------|------|----------|
| SSO | SSOSettings | `/manage/settings/sso` | 4h |

---

## 五、总结

### 缺失页面统计

| 优先级 | 模块数 | 页面数 | 总工时 |
|--------|--------|--------|--------|
| P1 | 2 | 4 | 16h |
| P2 | 3 | 3 | 14h |
| P3 | 1 | 1 | 4h |
| **合计** | **6** | **8** | **34h** |

### 建议实施顺序

1. **第一阶段 (P1)**: Alerts + Compliance 模块（约 2 天）
2. **第二阶段 (P2)**: ROI + Tenant 模块（约 2 天）
3. **第三阶段 (P3)**: SSO 设置（约 0.5 天）

---

> 文档维护: Open ACE 开发团队
> 最后更新: 2026-03-23

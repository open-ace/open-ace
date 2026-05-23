# 前端开发指南

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18.3.1 | UI 框架 |
| TypeScript | ~5.7.2 | 类型安全 |
| Vite | 6.0.3 | 构建工具和开发服务器 |
| TanStack React Query | 5.62.0 | 数据获取和缓存 |
| Zustand | 5.0.2 | 客户端状态管理 |
| react-router-dom | 7.0.2 | 路由 |
| Bootstrap | 5.3.8 | CSS 框架 |
| Chart.js | 4.4.7 | 数据可视化 |
| xterm.js | 6.0.0 | 终端模拟 |
| react-markdown | 10.1.0 | Markdown 渲染 |

## 项目结构

```
frontend/
├── public/                     # 静态资源
├── src/
│   ├── main.tsx                # 入口文件
│   ├── App.tsx                 # 根组件（含路由）
│   ├── api/                    # API 客户端层
│   │   ├── client.ts           # ApiClient（重试、超时、错误处理）
│   │   ├── auth.ts             # 认证 API
│   │   ├── dashboard.ts        # 仪表盘数据
│   │   ├── messages.ts         # 消息浏览
│   │   ├── sessions.ts         # 会话管理
│   │   ├── admin.ts            # 用户管理
│   │   ├── analysis.ts         # 数据分析
│   │   ├── remote.ts           # 远程机器和会话
│   │   ├── governance.ts       # 审计、内容过滤、安全
│   │   ├── tenant.ts           # 多租户管理
│   │   ├── sso.ts              # SSO 提供商管理
│   │   ├── prompts.ts          # 提示词模板
│   │   ├── projects.ts         # 项目管理
│   │   ├── toolAccounts.ts     # 工具账户映射
│   │   └── index.ts            # 统一导出
│   ├── components/
│   │   ├── common/             # 28 个共享 UI 组件
│   │   ├── layout/             # 布局容器（WorkLayout、ManageLayout）
│   │   ├── features/           # 页面级组件
│   │   │   ├── analysis/       # TrendAnalysis、AnomalyDetection、ROIAnalysis
│   │   │   ├── management/     # 14 个管理页面
│   │   │   ├── settings/       # SSOSettings
│   │   │   └── compliance/     # DataRetention、ComplianceReport
│   │   └── work/               # 工作模式专用组件
│   ├── hooks/                  # React Query hooks
│   ├── store/                  # Zustand 全局状态
│   ├── i18n/                   # 国际化翻译（en/zh/ja/ko）
│   ├── types/                  # TypeScript 接口
│   ├── utils/                  # 格式化器、辅助工具
│   └── styles/                 # CSS（Bootstrap 覆盖）
├── vite.config.ts              # 构建配置
├── tsconfig.json
└── package.json
```

## 开发工作流

```bash
# 安装依赖
npm install

# 启动开发服务器（端口 3000，API 代理到 localhost:5000）
npm run dev

# 生产构建（输出到 ../static/js/dist/）
npm run build

# 运行测试
npm run test

# 代码检查
npm run lint
```

开发服务器运行在 3000 端口，`/api` 和 `/auth` 请求代理到 5000 端口的 Flask 后端。

## 路由

### 工作模式 (`/work/*`) — 所有已认证用户

3 面板布局（`WorkLayout`）：会话列表 | 工作区（iframe）| 辅助面板

| 路由 | 组件 | 说明 |
|------|------|------|
| `/work` | Workspace | 主 AI 编码环境 |
| `/work/sessions` | SessionList | 会话历史 |
| `/work/prompts` | Prompts | 提示词模板 |
| `/work/usage` | UsageOverview | 个人使用统计 |
| `/work/insights` | InsightsReport | AI 生成的洞察 |

### 管理模式 (`/manage/*`) — 仅管理员

侧边栏导航布局（`ManageLayout`）

| 路由 | 组件 | 说明 |
|------|------|------|
| `/manage/dashboard` | Dashboard | 管理概览 |
| `/manage/analysis/trend` | TrendAnalysis | Token 趋势 |
| `/manage/analysis/anomaly` | AnomalyDetection | 使用异常 |
| `/manage/analysis/roi` | ROIAnalysis | ROI 指标 |
| `/manage/messages` | Messages | 消息浏览器 |
| `/manage/audit` | AuditCenter | 审计日志查看 |
| `/manage/quota` | QuotaManagement | 配额与告警 |
| `/manage/compliance` | Compliance | 数据保留 |
| `/manage/security` | SecurityCenter | 安全设置 |
| `/manage/users` | UserManagement | 用户 CRUD |
| `/manage/tenants` | TenantManagement | 多租户 |
| `/manage/projects` | ProjectManagement | 项目 CRUD |
| `/manage/remote/machines` | RemoteMachines | 机器管理 |
| `/manage/remote/api-keys` | ApiKeyManagement | API Key 代理 |
| `/manage/settings/sso` | SSOSettings | SSO 配置 |

旧版路由（`/dashboard`、`/messages` 等）会将管理员重定向到 `/manage/...`，普通用户重定向到 `/work/...`。

## 数据流

```
API Client (src/api/client.ts)
  → React Query hooks (src/hooks/)
    → 页面组件 (src/components/features/)
```

`ApiClient` 类封装了 `fetch`，提供：
- 自动重试（最多 3 次，指数退避）
- 30 秒超时
- 自动包含凭证（`credentials: 'include'`）
- 按 HTTP 状态码返回友好的错误消息

React Query 配置了 1 分钟的 stale time 和单次重试。

## 状态管理

Zustand 状态仓库（`src/store/index.ts`），支持 localStorage 持久化（key: `open-ace-store`）：

**持久化数据：**
- `theme`（light/dark）、`language`（en/zh/ja/ko）
- `appMode`（work/manage）、`sidebarCollapsed`
- `workspaceTabs` — 多标签工作区状态（type、sessionId、machineId 等）

**非持久化数据：**
- `user`、`isAuthenticated`、`authLoading`
- `workspaceFullscreen`

导出了细粒度订阅的选择器（`useUser`、`useTheme`、`useAppMode`）。

## 国际化

`src/i18n/index.ts` 中的轻量级自定义 i18n：

```typescript
import { t } from '@/i18n'

// 使用方法
t('common.save', language)
```

支持 4 种语言：**en**（默认）、**zh**、**ja**、**ko**。每种语言约 800+ 个键。

帮助文档也按语言存放在 `src/components/work/docs/` 中。

## 构建配置

`vite.config.ts` 关键设置：

| 设置 | 值 |
|------|-----|
| Base 路径 | `/static/js/dist/` |
| 输出目录 | `../static/js/dist` |
| 开发服务器端口 | 3000 |
| API 代理 | `/api`、`/auth` → `http://localhost:5000` |
| 编译目标 | ES2020 |
| 压缩器 | esbuild（生产环境移除 console.log） |

**路径别名：** `@` → `src/`、`@api` → `src/api/`、`@components` → `src/components/` 等。

**代码分割：** react-vendor、router、query、zustand、charts、date-fns、api、components、hooks、store、utils、i18n，以及每个懒加载页面的自动分块。

## 通用组件

全部位于 `src/components/common/`：

| 组件 | 说明 |
|------|------|
| Button | 可配置按钮，支持变体、尺寸、加载状态 |
| Card, StatCard | 内容卡片和统计展示 |
| Modal, ConfirmModal | 对话框弹窗，支持多种尺寸 |
| Select, SearchableSelect | 下拉选择 |
| Tabs, TabList, Tab, TabPanels | 标签页导航 |
| Loading, Skeleton, SkeletonCard | 加载指示器 |
| Error, EmptyState | 错误和空状态 |
| Badge, StatusBadge, CountBadge | 状态指示器 |
| Progress, CircularProgress | 进度指示器 |
| TextInput, Textarea, Checkbox | 表单输入 |
| Dropdown, SplitButton | 下拉菜单 |
| Avatar, AvatarUploader | 用户头像 |
| Tooltip | 工具提示 |
| ToastContainer, useToast | Toast 通知 |
| ModeSwitcher | 工作/管理模式切换 |
| SessionDetailContent | 会话详情查看器 |
| LazyCharts | 懒加载图表组件 |

## 添加新功能

1. **API 层** — 创建 `src/api/myFeature.ts`，包含带类型的 API 调用
2. **Hooks** — 创建 `src/hooks/useMyFeature.ts`，包含 React Query hooks
3. **组件** — 创建 `src/components/features/MyFeature.tsx`（懒加载）
4. **路由** — 在 `App.tsx` 的 WorkRoutes 或 ManageRoutes 中添加路由
5. **国际化** — 在 `src/i18n/index.ts` 中为所有 4 种语言添加键
6. **类型** — 在 `src/types/index.ts` 中添加接口

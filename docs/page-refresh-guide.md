# 页面刷新定制化功能使用指南

## 概述

页面刷新定制化功能提供了灵活的页面级刷新控制，替代了原有的全局刷新机制。

## 核心功能

### 1. usePageRefresh Hook

页面级刷新管理 Hook，提供：

- **查询键过滤刷新**：只刷新指定范围的查询，避免刷新无关数据
- **自动刷新控制**：可配置刷新间隔，支持动态开关
- **请求去重**：避免短时间内重复刷新同一查询
- **刷新节流/防抖**：防止频繁触发刷新
- **错误处理**：失败后自动降级，支持重试

#### 使用示例

```typescript
import { usePageRefresh } from '@/hooks';
import { createMatcherConfig } from '@/utils';

const pageRefresh = usePageRefresh({
  page: '/manage/dashboard',
  refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
  interval: 60000, // 1分钟
  enabled: true, // 默认启用
});
```

### 2. PageRefreshControl 组件

刷新控制 UI 组件，提供：

- **自动刷新开关**
- **刷新间隔选择器**
- **手动刷新按钮**（带防抖）
- **刷新状态显示**（上次刷新时间）
- **错误指示器**
- **紧凑模式**（移动端适配）

#### 使用示例

```typescript
import { PageRefreshControl } from '@/components/common';

<PageRefreshControl
  refresh={pageRefresh}
  showLastRefreshTime={true}
  showNextRefreshTime={false}
  compact={false}
/>
```

### 3. 查询键匹配机制

支持三种匹配模式：

- **精确匹配**：`['users']` 只匹配 `['users']`
- **前缀匹配**：`['dashboard']` 匹配 `['dashboard', 'stats']` 和 `['dashboard', 'charts']`
- **排除规则**：可指定不刷新的查询键

#### 使用示例

```typescript
import { createMatcherConfig } from '@/utils';

// 刷新 dashboard 相关查询，排除 hosts
const matcher = createMatcherConfig(
  [['dashboard']],
  'prefix',
  [['dashboard', 'hosts']]
);
```

### 4. 全局暂停功能

支持全局暂停所有页面刷新：

- **快捷键**：`Ctrl+Shift+P` 暂停/恢复
- **Hook**：`useGlobalRefreshPause()`

## 已集成页面

### 高优先级页面（实时数据）

1. **Dashboard** (`/manage/dashboard`)
   - 自动刷新默认启用
   - 刷新间隔：30秒/1分钟/5分钟可选
   - 排除 `['dashboard', 'hosts']` 查询

2. **Request Dashboard** (`/manage/analysis/request-dashboard`)
   - 自动刷新默认启用
   - 刷新间隔：30秒/1分钟/5分钟可选

3. **Messages** (`/manage/messages`)
   - 自动刷新默认禁用（历史消息数据）
   - 紧凑模式显示

## 状态管理

使用 Zustand 进行状态管理，支持：

- **持久化**：localStorage 存储，key 为 `page-refresh-config-v1`
- **多标签页同步**：通过 storage 事件同步状态
- **自动清理**：30天未访问的配置自动清理

## 迁移指南

### 从全局刷新迁移

原有 Header 中的全局刷新按钮已移除。如需刷新功能，请使用页面级的 PageRefreshControl 组件。

### useDashboard 的 autoRefresh 参数

`useDashboard` 的 `autoRefresh` 参数已标记为 **deprecated**，将在未来版本移除。

迁移方式：
1. 移除 `autoRefresh` 参数
2. 使用 `usePageRefresh` hook 替代

```typescript
// 旧方式（已废弃）
const { data } = useDashboard({ autoRefresh: true });

// 新方式
const pageRefresh = usePageRefresh({
  page: '/manage/dashboard',
  refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
  interval: 60000,
  enabled: true,
});
```

## 配置选项

### 刷新间隔

默认提供三个选项：
- 30秒
- 1分钟
- 5分钟

可自定义间隔选项：

```typescript
const customIntervals = [
  { value: 10000, label: '10s' },
  { value: 30000, label: '30s' },
  { value: 60000, label: '1min' },
];

<PageRefreshControl
  refresh={pageRefresh}
  intervalOptions={customIntervals}
/>
```

## 最佳实践

1. **实时数据页面**：启用自动刷新，使用较短间隔（30秒-1分钟）
2. **历史数据页面**：禁用自动刷新，仅提供手动刷新按钮
3. **配置数据页面**：不添加刷新功能（数据变更时手动刷新）
4. **特殊查询**：使用排除规则避免刷新特定查询

## 相关文件

- `frontend/src/store/pageRefreshStore.ts` - 状态管理
- `frontend/src/hooks/usePageRefresh.ts` - 刷新 Hook
- `frontend/src/hooks/useGlobalRefreshPause.ts` - 全局暂停 Hook
- `frontend/src/components/common/PageRefreshControl.tsx` - UI 组件
- `frontend/src/utils/queryKeyMatcher.ts` - 查询键匹配工具
- `frontend/src/utils/queryKeyRegistry.ts` - 查询键注册表
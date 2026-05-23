# Frontend Development Guide

## Tech Stack

| Technology | Version | Purpose |
|------------|---------|---------|
| React | 18.3.1 | UI framework |
| TypeScript | ~5.7.2 | Type safety |
| Vite | 6.0.3 | Build tool and dev server |
| TanStack React Query | 5.62.0 | Data fetching and caching |
| Zustand | 5.0.2 | Client state management |
| react-router-dom | 7.0.2 | Routing |
| Bootstrap | 5.3.8 | CSS framework |
| Chart.js | 4.4.7 | Data visualization |
| xterm.js | 6.0.0 | Terminal emulation |
| react-markdown | 10.1.0 | Markdown rendering |

## Project Structure

```
frontend/
├── public/                     # Static assets
├── src/
│   ├── main.tsx                # Entry point
│   ├── App.tsx                 # Root component with routing
│   ├── api/                    # API client layer
│   │   ├── client.ts           # ApiClient (retry, timeout, error handling)
│   │   ├── auth.ts             # Authentication APIs
│   │   ├── dashboard.ts        # Dashboard data
│   │   ├── messages.ts         # Message browsing
│   │   ├── sessions.ts         # Session management
│   │   ├── admin.ts            # User management
│   │   ├── analysis.ts         # Analytics
│   │   ├── remote.ts           # Remote machines & sessions
│   │   ├── governance.ts       # Audit, content filter, security
│   │   ├── tenant.ts           # Multi-tenant management
│   │   ├── sso.ts              # SSO provider management
│   │   ├── prompts.ts          # Prompt templates
│   │   ├── projects.ts         # Project management
│   │   ├── toolAccounts.ts     # Tool account mapping
│   │   └── index.ts            # Re-exports
│   ├── components/
│   │   ├── common/             # 28 shared UI components
│   │   ├── layout/             # Layout shells (WorkLayout, ManageLayout)
│   │   ├── features/           # Page-level components
│   │   │   ├── analysis/       # TrendAnalysis, AnomalyDetection, ROIAnalysis
│   │   │   ├── management/     # 14 admin pages
│   │   │   ├── settings/       # SSOSettings
│   │   │   └── compliance/     # DataRetention, ComplianceReport
│   │   └── work/               # Work-mode specific components
│   ├── hooks/                  # React Query hooks
│   ├── store/                  # Zustand global store
│   ├── i18n/                   # Translations (en/zh/ja/ko)
│   ├── types/                  # TypeScript interfaces
│   ├── utils/                  # Formatters, helpers
│   └── styles/                 # CSS (Bootstrap overrides)
├── vite.config.ts              # Build configuration
├── tsconfig.json
└── package.json
```

## Development Workflow

```bash
# Install dependencies
npm install

# Start dev server (port 3000, proxies API to localhost:5000)
npm run dev

# Build for production (outputs to ../static/js/dist/)
npm run build

# Run tests
npm run test

# Lint
npm run lint
```

The dev server runs on port 3000 with `/api` and `/auth` requests proxied to the Flask backend on port 5000.

## Routing

### Work Mode (`/work/*`) — All authenticated users

3-panel layout (`WorkLayout`): session list | workspace (iframe) | assist panel

| Route | Component | Description |
|-------|-----------|-------------|
| `/work` | Workspace | Main AI coding environment |
| `/work/sessions` | SessionList | Session history |
| `/work/prompts` | Prompts | Prompt templates |
| `/work/usage` | UsageOverview | Personal usage stats |
| `/work/insights` | InsightsReport | AI-generated insights |

### Manage Mode (`/manage/*`) — Admin only

Sidebar navigation layout (`ManageLayout`)

| Route | Component | Description |
|-------|-----------|-------------|
| `/manage/dashboard` | Dashboard | Admin overview |
| `/manage/analysis/trend` | TrendAnalysis | Token trends |
| `/manage/analysis/anomaly` | AnomalyDetection | Usage anomalies |
| `/manage/analysis/roi` | ROIAnalysis | ROI metrics |
| `/manage/messages` | Messages | Message browser |
| `/manage/audit` | AuditCenter | Audit log viewer |
| `/manage/quota` | QuotaManagement | Quota & alerts |
| `/manage/compliance` | Compliance | Data retention |
| `/manage/security` | SecurityCenter | Security settings |
| `/manage/users` | UserManagement | User CRUD |
| `/manage/tenants` | TenantManagement | Multi-tenant |
| `/manage/projects` | ProjectManagement | Project CRUD |
| `/manage/remote/machines` | RemoteMachines | Machine management |
| `/manage/remote/api-keys` | ApiKeyManagement | API key proxy |
| `/manage/settings/sso` | SSOSettings | SSO configuration |

Legacy routes (`/dashboard`, `/messages`, etc.) redirect admins to `/manage/...` and non-admins to `/work/...`.

## Data Flow

```
API Client (src/api/client.ts)
  → React Query hooks (src/hooks/)
    → Page components (src/components/features/)
```

The `ApiClient` class wraps `fetch` with:
- Automatic retry (up to 3 attempts with exponential backoff)
- 30-second timeout
- Credential inclusion (`credentials: 'include'`)
- Friendly error messages per HTTP status code

React Query is configured with 1-minute stale time and single retry.

## State Management

Zustand store (`src/store/index.ts`) with localStorage persistence (key: `open-ace-store`):

**Persisted:**
- `theme` (light/dark), `language` (en/zh/ja/ko)
- `appMode` (work/manage), `sidebarCollapsed`
- `workspaceTabs` — multi-tab workspace state (type, sessionId, machineId, etc.)

**Non-persisted:**
- `user`, `isAuthenticated`, `authLoading`
- `workspaceFullscreen`

Selectors are exported for granular subscriptions (`useUser`, `useTheme`, `useAppMode`).

## Internationalization

Custom lightweight i18n in `src/i18n/index.ts`:

```typescript
import { t } from '@/i18n'

// Usage
t('common.save', language)
```

Supports 4 languages: **en** (default), **zh**, **ja**, **ko**. ~800+ keys per language.

Help documents also exist per language in `src/components/work/docs/`.

## Build Configuration

`vite.config.ts` key settings:

| Setting | Value |
|---------|-------|
| Base path | `/static/js/dist/` |
| Output | `../static/js/dist` |
| Dev server port | 3000 |
| API proxy | `/api`, `/auth` → `http://localhost:5000` |
| Target | ES2020 |
| Minifier | esbuild (drops console.log in prod) |

**Path aliases:** `@` → `src/`, `@api` → `src/api/`, `@components` → `src/components/`, etc.

**Chunk splitting:** react-vendor, router, query, zustand, charts, date-fns, api, components, hooks, store, utils, i18n, plus auto-chunks for each lazy-loaded page.

## Common Components

All in `src/components/common/`:

| Component | Description |
|-----------|-------------|
| Button | Configurable button with variants, sizes, loading state |
| Card, StatCard | Content cards and stat display |
| Modal, ConfirmModal | Dialog modals with size variants |
| Select, SearchableSelect | Dropdown selects |
| Tabs, TabList, Tab, TabPanels | Tab navigation |
| Loading, Skeleton, SkeletonCard | Loading indicators |
| Error, EmptyState | Error and empty states |
| Badge, StatusBadge, CountBadge | Status indicators |
| Progress, CircularProgress | Progress indicators |
| TextInput, Textarea, Checkbox | Form inputs |
| Dropdown, SplitButton | Dropdown menus |
| Avatar, AvatarUploader | User avatars |
| Tooltip | Tooltips |
| ToastContainer, useToast | Toast notifications |
| ModeSwitcher | Work/Manage mode toggle |
| SessionDetailContent | Session detail viewer |
| LazyCharts | Lazy-loaded chart components |

## Adding a New Feature

1. **API layer** — Create `src/api/myFeature.ts` with typed API calls
2. **Hooks** — Create `src/hooks/useMyFeature.ts` with React Query hooks
3. **Component** — Create `src/components/features/MyFeature.tsx` (lazy-loaded)
4. **Route** — Add route in `App.tsx` under WorkRoutes or ManageRoutes
5. **i18n** — Add keys to all 4 languages in `src/i18n/index.ts`
6. **Types** — Add interfaces to `src/types/index.ts`

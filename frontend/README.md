# Open ACE Frontend

Modern React/TypeScript/Vite based frontend for Open ACE (AI Computing Explorer).

## 🚀 Quick Start

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Run tests
npm run test
```

## 📁 Project Structure

```
frontend/
├── src/
│   ├── api/               # API client and endpoints
│   │   ├── client.ts      # Centralized HTTP client
│   │   ├── dashboard.ts   # Dashboard API
│   │   ├── messages.ts    # Messages API
│   │   ├── sessions.ts    # Sessions API
│   │   ├── auth.ts        # Authentication API
│   │   └── index.ts       # API exports
│   ├── components/        # React components
│   │   ├── common/        # Reusable UI components
│   │   │   ├── Button.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Loading.tsx
│   │   │   ├── Error.tsx
│   │   │   └── Select.tsx
│   │   ├── layout/        # Layout components
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   └── Layout.tsx
│   │   └── features/      # Feature components
│   │       ├── Dashboard.tsx
│   │       ├── Messages.tsx
│   │       └── Analysis.tsx
│   ├── hooks/             # Custom React hooks
│   │   ├── useAuth.ts     # Authentication hook
│   │   ├── useDashboard.ts
│   │   ├── useMessages.ts
│   │   └── index.ts
│   ├── store/             # Zustand state management
│   │   └── index.ts
│   ├── utils/             # Utility functions
│   │   ├── format.ts      # Formatting utilities
│   │   ├── cn.ts          # Class name utility
│   │   └── index.ts
│   ├── i18n/              # Internationalization
│   │   └── index.ts
│   ├── types/             # TypeScript type definitions
│   │   └── index.ts
│   ├── styles/
│   │   └── main.css       # Global styles with CSS variables
│   ├── App.tsx            # Main application component
│   ├── main.tsx           # Application entry point
│   └── vite-env.d.ts      # Vite type definitions
├── .husky/                # Git hooks
├── .vscode/               # VS Code settings
├── eslint.config.js       # ESLint configuration (v9 flat config)
├── .prettierrc            # Prettier configuration
├── tsconfig.json          # TypeScript configuration
├── vite.config.ts         # Vite configuration
└── package.json
```

## 🛠️ Available Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start development server with hot reload |
| `npm run build` | Type-check and build for production |
| `npm run build:watch` | Build and watch for changes |
| `npm run preview` | Preview production build locally |
| `npm run lint` | Run ESLint |
| `npm run lint:fix` | Fix ESLint errors |
| `npm run format` | Format code with Prettier |
| `npm run format:check` | Check code formatting |
| `npm run typecheck` | Run TypeScript type checking |
| `npm run test` | Run tests with Vitest |
| `npm run test:coverage` | Run tests with coverage |
| `npm run clean` | Clean build artifacts |

## 📦 Tech Stack

- **Framework**: React 18.x
- **Build Tool**: Vite 6.x
- **Language**: TypeScript 5.x
- **State Management**: Zustand 5.x
- **Data Fetching**: TanStack Query 5.x
- **Routing**: React Router 7.x
- **Charts**: Chart.js 4.x + react-chartjs-2
- **Code Quality**: ESLint 9.x + Prettier 3.x
- **Git Hooks**: Husky 9.x + lint-staged
- **Testing**: Vitest + Testing Library

## 🎨 Features

### React Components
- **Common Components**: Button, Card, Loading, Error, Select, StatCard
- **Layout Components**: Sidebar, Header, Layout
- **Feature Components**: Dashboard, Messages, Analysis

### Custom Hooks
- **useAuth**: Authentication state and actions
- **useDashboard**: Dashboard data fetching with auto-refresh
- **useMessages**: Messages list with pagination
- **useTrendData**: Trend chart data

### State Management
- Zustand for global state
- Persistent storage for theme/language preferences
- Type-safe state updates

### API Client
- Centralized HTTP client with timeout support
- Automatic error handling
- Type-safe API responses
- AbortController support for request cancellation

### Internationalization
- Multi-language support (en, zh, ja, ko)
- Simple translation function with fallback

## 🔧 Configuration

### Path Aliases

The following path aliases are configured in `tsconfig.json` and `vite.config.ts`:

```typescript
import { apiClient } from '@/api/client';
import { Button } from '@/components/common';
import { useAuth } from '@/hooks';
import { formatNumber } from '@/utils/format';
import { useAppStore } from '@/store';
```

### Environment Variables

Create a `.env` file for environment-specific configuration:

```env
VITE_API_BASE_URL=http://localhost:5000
VITE_APP_TITLE=Open ACE
```

## 📝 Code Style

This project uses:
- **ESLint** for code linting
- **Prettier** for code formatting
- **TypeScript strict mode** for type safety
- **React Hooks** rules enforcement

Pre-commit hooks automatically format and lint staged files.

## 🔄 Build Output

Production builds are output to `../static/js/dist/` directory with code splitting:

- `react-vendor.[hash].js` - React core (~143KB gzipped: ~46KB)
- `query.[hash].js` - TanStack Query (~39KB gzipped: ~12KB)
- `components.[hash].js` - UI components (~24KB gzipped: ~6KB)
- `api.[hash].js` - API client (~2KB gzipped: ~1KB)
- `hooks.[hash].js` - Custom hooks (~2KB gzipped: ~1KB)
- `store.[hash].js` - State management (~1KB gzipped: ~0.4KB)
- `i18n.[hash].js` - Translations (~7KB gzipped: ~3KB)

## 📊 Performance

### Bundle Size
- Total JS: ~220KB (gzipped: ~70KB)
- CSS: ~8KB (gzipped: ~2KB)

### Code Splitting
- Vendor chunks separated for better caching
- Feature components lazy-loaded
- Shared utilities in separate chunks

## 📚 Architecture

### Phase 4: React Component Migration

The frontend has been migrated to React with the following architecture:

1. **Component-Based Architecture**
   - Reusable common components
   - Feature-specific components
   - Layout components for consistent UI

2. **State Management**
   - Zustand for global state (auth, theme, language)
   - TanStack Query for server state (API data)
   - Local state for component-specific data

3. **Data Fetching**
   - TanStack Query for caching and auto-refresh
   - Optimistic updates support
   - Request cancellation

4. **Styling**
   - CSS variables for theming
   - Bootstrap 5 integration
   - Responsive design

## 📄 License

MIT License - See LICENSE file for details.

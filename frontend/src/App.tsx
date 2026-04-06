/**
 * App Component - Main application component with routing
 *
 * Dual-track routing system:
 * - /work/* - Work mode (WorkLayout with three-column layout) - All users
 * - /manage/* - Manage mode (ManageLayout with sidebar navigation) - Admin only
 * - /login, /logout - Public routes
 *
 * Performance optimizations:
 * - Route-level code splitting with React.lazy
 * - Lazy-loaded page components reduce initial bundle size
 */

import React, { useEffect, Suspense, lazy } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout, WorkLayout, ManageLayout } from '@/components/layout';
import { Login } from '@/components/features/Login';
import { LogoutSuccess } from '@/components/features/LogoutSuccess';
import { LoadingOverlay, PageSkeleton } from '@/components/common';
import { useAuth, useTheme } from '@/hooks';
import { useAppStore } from '@/store';
import { t } from '@/i18n';

// Lazy-loaded page components for code splitting
// Each page is loaded only when navigated to, reducing initial bundle size
const Dashboard = lazy(() => import('@/components/features/Dashboard').then(m => ({ default: m.Dashboard })));
const Messages = lazy(() => import('@/components/features/Messages').then(m => ({ default: m.Messages })));
const Analysis = lazy(() => import('@/components/features/Analysis').then(m => ({ default: m.Analysis })));
const Report = lazy(() => import('@/components/features/Report').then(m => ({ default: m.Report })));
const Workspace = lazy(() => import('@/components/features/Workspace').then(m => ({ default: m.Workspace })));
const Sessions = lazy(() => import('@/components/features/Sessions').then(m => ({ default: m.Sessions })));
const Prompts = lazy(() => import('@/components/features/Prompts').then(m => ({ default: m.Prompts })));
const TrendAnalysis = lazy(() => import('@/components/features/analysis/TrendAnalysis').then(m => ({ default: m.TrendAnalysis })));
const AnomalyDetection = lazy(() => import('@/components/features/analysis/AnomalyDetection').then(m => ({ default: m.AnomalyDetection })));
const ROIAnalysis = lazy(() => import('@/components/features/analysis/ROIAnalysis').then(m => ({ default: m.ROIAnalysis })));
const ConversationHistory = lazy(() => import('@/components/features/ConversationHistory').then(m => ({ default: m.ConversationHistory })));
const RequestDashboard = lazy(() => import('@/components/features/management/RequestDashboard').then(m => ({ default: m.RequestDashboard })));
const UsageOverview = lazy(() => import('@/components/work/UsageOverview').then(m => ({ default: m.UsageOverview })));
const UserManagement = lazy(() => import('@/components/features/management/UserManagement').then(m => ({ default: m.UserManagement })));
const AuditCenter = lazy(() => import('@/components/features/management/AuditCenter').then(m => ({ default: m.AuditCenter })));
const QuotaAlerts = lazy(() => import('@/components/features/management/QuotaAlerts').then(m => ({ default: m.QuotaAlerts })));
const ComplianceMgmt = lazy(() => import('@/components/features/management/ComplianceMgmt').then(m => ({ default: m.ComplianceMgmt })));
const SecurityCenter = lazy(() => import('@/components/features/management/SecurityCenter').then(m => ({ default: m.SecurityCenter })));
const TenantManagement = lazy(() => import('@/components/features/management/TenantManagement').then(m => ({ default: m.TenantManagement })));
const SSOSettings = lazy(() => import('@/components/features/settings/SSOSettings').then(m => ({ default: m.SSOSettings })));
const ProjectManagement = lazy(() => import('@/components/features/management/ProjectManagement').then(m => ({ default: m.ProjectManagement })));

// Page loading fallback with skeleton
const PageLoader: React.FC = () => {
  return <PageSkeleton />;
};

// Create Query Client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

// Protected Route wrapper
const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isLoading, isAuthenticated } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <LoadingOverlay text={t('loading', useAppStore.getState().language)} />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
};

// Legacy App Content (for backward compatibility with old routes)
const LegacyAppContent: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const language = useAppStore((state) => state.language);

  // Get active section from path
  const getActiveSection = () => {
    const path = location.pathname.replace('/', '') || 'dashboard';
    return path;
  };

  // Handle navigation
  const handleNavigate = (section: string) => {
    navigate(`/${section}`);
  };

  // Section titles
  const sectionTitles: Record<string, string> = {
    dashboard: 'dashboard',
    messages: 'messages',
    analysis: 'analysis',
    management: 'management',
    sessions: 'sessions',
    prompts: 'prompts',
    report: 'report',
    workspace: 'workspace',
    security: 'security',
  };

  // Render section content
  const renderSection = () => {
    const section = getActiveSection();
    return (
      <Suspense fallback={<PageLoader />}>
        {section === 'dashboard' && <Dashboard />}
        {section === 'messages' && <Messages />}
        {section === 'analysis' && <Analysis />}
        {section === 'management' && <UserManagement />}
        {section === 'report' && <Report />}
        {section === 'workspace' && <Workspace />}
        {section === 'sessions' && <Sessions />}
        {section === 'prompts' && <Prompts />}
        {section === 'security' && <SecurityCenter />}
        {!['dashboard', 'messages', 'analysis', 'management', 'report', 'workspace', 'sessions', 'prompts', 'security'].includes(section) && <Dashboard />}
      </Suspense>
    );
  };

  const activeSection = getActiveSection();

  return (
    <Layout
      activeSection={activeSection}
      title={t(sectionTitles[activeSection] || activeSection, language)}
      onNavigate={handleNavigate}
    >
      {renderSection()}
    </Layout>
  );
};

// Work Mode Routes
const WorkRoutes: React.FC = () => {
  return (
    <WorkLayout>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/" element={<Workspace />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/prompts" element={<Prompts />} />
          <Route path="/usage" element={<UsageOverview />} />
          {/* Explicit /workspace route for session restore */}
          <Route path="/workspace" element={<Workspace />} />
          <Route path="*" element={<Navigate to="/work" replace />} />
        </Routes>
      </Suspense>
    </WorkLayout>
  );
};

// Manage Mode Routes
const ManageRoutes: React.FC = () => {
  return (
    <ManageLayout>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          {/* Overview */}
          <Route path="/dashboard" element={<Dashboard />} />

          {/* Analysis */}
          <Route path="/analysis" element={<Navigate to="/manage/analysis/trend" replace />} />
          <Route path="/analysis/trend" element={<TrendAnalysis />} />
          <Route path="/analysis/request-dashboard" element={<RequestDashboard />} />
          <Route path="/analysis/anomaly" element={<AnomalyDetection />} />
          <Route path="/analysis/roi" element={<ROIAnalysis />} />
          <Route path="/analysis/conversation-history" element={<ConversationHistory />} />
          <Route path="/messages" element={<Messages />} />

          {/* Governance - Merged Pages */}
          <Route path="/audit" element={<AuditCenter />} />
          <Route path="/quota" element={<QuotaAlerts />} />
          <Route path="/compliance" element={<ComplianceMgmt />} />
          <Route path="/security" element={<SecurityCenter />} />

          {/* Users */}
          <Route path="/users" element={<UserManagement />} />
          <Route path="/tenants" element={<TenantManagement />} />

          {/* Projects */}
          <Route path="/projects" element={<ProjectManagement />} />

          {/* Settings */}
          <Route path="/settings/sso" element={<SSOSettings />} />

          {/* Default */}
          <Route path="*" element={<Navigate to="/manage/dashboard" replace />} />
        </Routes>
      </Suspense>
    </ManageLayout>
  );
};

// Main App Content (requires auth)
const AppContent: React.FC = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  // Sync app mode with URL on mount
  useEffect(() => {
    const path = window.location.pathname;
    if (path.startsWith('/work')) {
      useAppStore.getState().setAppMode('work');
    } else if (path.startsWith('/manage')) {
      useAppStore.getState().setAppMode('manage');
    }
  }, []);

  return (
    <Routes>
      {/* Work Mode Routes - All users */}
      <Route path="/work/*" element={<WorkRoutes />} />

      {/* Manage Mode Routes - Admin only */}
      <Route
        path="/manage/*"
        element={isAdmin ? <ManageRoutes /> : <Navigate to="/work" replace />}
      />

      {/* Legacy Routes - redirect based on user role */}
      <Route
        path="/dashboard"
        element={
          isAdmin ? <Navigate to="/manage/dashboard" replace /> : <Navigate to="/work" replace />
        }
      />
      <Route
        path="/messages"
        element={
          isAdmin ? <Navigate to="/manage/messages" replace /> : <Navigate to="/work" replace />
        }
      />
      <Route
        path="/analysis"
        element={
          isAdmin ? <Navigate to="/manage/analysis" replace /> : <Navigate to="/work" replace />
        }
      />
      <Route
        path="/management"
        element={
          isAdmin ? <Navigate to="/manage/users" replace /> : <Navigate to="/work" replace />
        }
      />
      <Route
        path="/security"
        element={
          isAdmin ? <Navigate to="/manage/security" replace /> : <Navigate to="/work" replace />
        }
      />
      <Route path="/workspace" element={<Navigate to="/work" replace />} />
      <Route path="/sessions" element={<Navigate to="/work/sessions" replace />} />
      <Route path="/prompts" element={<Navigate to="/work/prompts" replace />} />

      {/* Report - Keep as standalone for now */}
      <Route path="/report" element={<LegacyAppContent />} />

      {/* Default redirect - Admin goes to manage mode, others go to work mode */}
      <Route
        path="/"
        element={isAdmin ? <Navigate to="/manage/dashboard" replace /> : <Navigate to="/work" replace />}
      />
      <Route
        path="*"
        element={isAdmin ? <Navigate to="/manage/dashboard" replace /> : <Navigate to="/work" replace />}
      />
    </Routes>
  );
};

export const App: React.FC = () => {
  const theme = useTheme();

  // Apply theme on mount and change
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    document.body.classList.toggle('dark-theme', theme === 'dark');
  }, [theme]);

  return (
    <QueryClientProvider client={queryClient}>
      <Routes>
        {/* Public routes */}
        <Route path="/login" element={<Login />} />
        <Route path="/logout" element={<LogoutSuccess />} />

        {/* Protected routes */}
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AppContent />
            </ProtectedRoute>
          }
        />
      </Routes>
    </QueryClientProvider>
  );
};

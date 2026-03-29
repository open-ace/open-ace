/**
 * App Component - Main application component with routing
 *
 * Dual-track routing system:
 * - /work/* - Work mode (WorkLayout with three-column layout) - All users
 * - /manage/* - Manage mode (ManageLayout with sidebar navigation) - Admin only
 * - /login, /logout - Public routes
 */

import React, { useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout, WorkLayout, ManageLayout } from '@/components/layout';
import {
  Dashboard,
  Messages,
  Analysis,
  Login,
  LogoutSuccess,
  Report,
  Workspace,
  Sessions,
  Prompts,
  TrendAnalysis,
  AnomalyDetection,
  ROIAnalysis,
  TenantManagement,
  SSOSettings,
  ConversationHistory,
} from '@/components/features';
import { UserManagement } from '@/components/features/management/UserManagement';
import { AuditCenter } from '@/components/features/management/AuditCenter';
import { QuotaAlerts } from '@/components/features/management/QuotaAlerts';
import { ComplianceMgmt } from '@/components/features/management/ComplianceMgmt';
import { SecurityCenter } from '@/components/features/management/SecurityCenter';
import { LoadingOverlay } from '@/components/common';
import { useAuth, useTheme } from '@/hooks';
import { useAppStore } from '@/store';
import { t } from '@/i18n';

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
    switch (section) {
      case 'dashboard':
        return <Dashboard />;
      case 'messages':
        return <Messages />;
      case 'analysis':
        return <Analysis />;
      case 'management':
        return <UserManagement />;
      case 'report':
        return <Report />;
      case 'workspace':
        return <Workspace />;
      case 'sessions':
        return <Sessions />;
      case 'prompts':
        return <Prompts />;
      case 'security':
        return <SecurityCenter />;
      default:
        return <Dashboard />;
    }
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
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/sessions" element={<Sessions />} />
        <Route path="/prompts" element={<Prompts />} />
        <Route path="*" element={<Navigate to="/work" replace />} />
      </Routes>
    </WorkLayout>
  );
};

// Manage Mode Routes
const ManageRoutes: React.FC = () => {
  return (
    <ManageLayout>
      <Routes>
        {/* Overview */}
        <Route path="/dashboard" element={<Dashboard />} />

        {/* Analysis */}
        <Route path="/analysis" element={<Navigate to="/manage/analysis/trend" replace />} />
        <Route path="/analysis/trend" element={<TrendAnalysis />} />
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

        {/* Settings */}
        <Route path="/settings/sso" element={<SSOSettings />} />

        {/* Default */}
        <Route path="*" element={<Navigate to="/manage/dashboard" replace />} />
      </Routes>
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

      {/* Default redirect - All users go to work mode */}
      <Route path="/" element={<Navigate to="/work" replace />} />
      <Route path="*" element={<Navigate to="/work" replace />} />
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

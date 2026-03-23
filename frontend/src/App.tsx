/**
 * App Component - Main application component with routing
 *
 * Dual-track routing system:
 * - /work/* - Work mode (WorkLayout with three-column layout)
 * - /manage/* - Manage mode (ManageLayout with sidebar navigation)
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
  Management,
  Report,
  Workspace,
  Sessions,
  Prompts,
  TrendAnalysis,
  AnomalyDetection,
} from '@/components/features';
import { SecuritySettings } from '@/components/features/management/SecuritySettings';
import { LoadingOverlay } from '@/components/common';
import { useAuth, useTheme } from '@/hooks';
import { useAppStore } from '@/store';
import { t } from '@/i18n';
import '@/styles/main.css';

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
        return <Management />;
      case 'report':
        return <Report />;
      case 'workspace':
        return <Workspace />;
      case 'sessions':
        return <Sessions />;
      case 'prompts':
        return <Prompts />;
      case 'security':
        return <SecuritySettings />;
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
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/analysis" element={<Navigate to="/manage/analysis/trend" replace />} />
        <Route path="/analysis/trend" element={<TrendAnalysis />} />
        <Route path="/analysis/anomaly" element={<AnomalyDetection />} />
        <Route path="/messages" element={<Messages />} />
        <Route path="/audit" element={<Management />} />
        <Route path="/quota" element={<Management />} />
        <Route path="/security" element={<SecuritySettings />} />
        <Route path="/users" element={<Management />} />
        <Route path="*" element={<Navigate to="/manage/dashboard" replace />} />
      </Routes>
    </ManageLayout>
  );
};

// Main App Content (requires auth)
const AppContent: React.FC = () => {
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
      {/* Work Mode Routes */}
      <Route path="/work/*" element={<WorkRoutes />} />

      {/* Manage Mode Routes */}
      <Route path="/manage/*" element={<ManageRoutes />} />

      {/* Legacy Routes - redirect to appropriate mode */}
      <Route path="/dashboard" element={<Navigate to="/manage/dashboard" replace />} />
      <Route path="/messages" element={<Navigate to="/manage/messages" replace />} />
      <Route path="/analysis" element={<Navigate to="/manage/analysis" replace />} />
      <Route path="/management" element={<Navigate to="/manage/users" replace />} />
      <Route path="/security" element={<Navigate to="/manage/security" replace />} />
      <Route path="/workspace" element={<Navigate to="/work" replace />} />
      <Route path="/sessions" element={<Navigate to="/work/sessions" replace />} />
      <Route path="/prompts" element={<Navigate to="/work/prompts" replace />} />

      {/* Report - Keep as standalone for now */}
      <Route path="/report" element={<LegacyAppContent />} />

      {/* Default redirect */}
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

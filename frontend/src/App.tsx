/**
 * App Component - Main application component with routing
 */

import React, { useEffect } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from '@/components/layout';
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

// Main App Content (requires auth)
const AppContent: React.FC = () => {
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

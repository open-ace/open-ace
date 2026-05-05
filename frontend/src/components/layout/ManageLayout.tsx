/**
 * ManageLayout Component - Sidebar navigation layout for Manage Mode
 *
 * Layout structure:
 * - Left: Navigation sidebar with grouped menu items (collapsible groups)
 * - Right: Main content area
 *
 * Navigation groups:
 * - Overview: Dashboard
 * - Analysis: Trend, Anomaly, ROI, Conversation History, Messages
 * - Governance: Audit Center, Quota & Alerts, Compliance, Security Center
 * - Users: Management, Tenants
 * - Settings: SSO
 *
 * Features:
 * - Collapsible navigation groups
 * - Active group auto-expands
 * - Collapse state persisted to localStorage
 * - Admin-only items are disabled for non-admin users
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage, useSidebarCollapsed } from '@/store';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';
import { t } from '@/i18n';
import { ModeSwitcher } from '@/components/common';
import { Header } from './Header';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  path: string;
  adminOnly?: boolean;
}

interface NavSection {
  id: string;
  title: string;
  items: NavItem[];
}

// Optimized navigation structure with merged pages
// Items with adminOnly: true are only accessible by admin users
const navSections: NavSection[] = [
  {
    id: 'overview',
    title: 'overview',
    items: [
      { id: 'dashboard', label: 'dashboard', icon: 'bi-speedometer2', path: '/manage/dashboard' },
    ],
  },
  {
    id: 'analysis',
    title: 'analysis',
    items: [
      { id: 'trend', label: 'tokenTrend', icon: 'bi-graph-up', path: '/manage/analysis/trend' },
      {
        id: 'request-dashboard',
        label: 'requestStatistics',
        icon: 'bi-lightning',
        path: '/manage/analysis/request-dashboard',
      },
      {
        id: 'anomaly',
        label: 'anomalyDetection',
        icon: 'bi-exclamation-triangle',
        path: '/manage/analysis/anomaly',
      },
      { id: 'roi', label: 'roiAnalysis', icon: 'bi-currency-dollar', path: '/manage/analysis/roi' },
      {
        id: 'conversation-history',
        label: 'conversationHistory',
        icon: 'bi-chat-square-text',
        path: '/manage/analysis/conversation-history',
      },
      { id: 'messages', label: 'messages', icon: 'bi-chat-dots', path: '/manage/messages' },
    ],
  },
  {
    id: 'governance',
    title: 'governance',
    items: [
      { id: 'audit', label: 'auditCenter', icon: 'bi-journal-text', path: '/manage/audit' },
      {
        id: 'quota',
        label: 'quotaAndAlerts',
        icon: 'bi-sliders',
        path: '/manage/quota',
        adminOnly: true,
      },
      {
        id: 'compliance',
        label: 'complianceManagement',
        icon: 'bi-file-earmark-text',
        path: '/manage/compliance',
        adminOnly: true,
      },
      {
        id: 'security',
        label: 'securityCenter',
        icon: 'bi-shield',
        path: '/manage/security',
        adminOnly: true,
      },
    ],
  },
  {
    id: 'users',
    title: 'user',
    items: [
      {
        id: 'users',
        label: 'userManagement',
        icon: 'bi-people',
        path: '/manage/users',
        adminOnly: true,
      },
      {
        id: 'tenants',
        label: 'tenantManagement',
        icon: 'bi-building',
        path: '/manage/tenants',
        adminOnly: true,
      },
    ],
  },
  {
    id: 'projects',
    title: 'projects',
    items: [
      {
        id: 'projects',
        label: 'projectManagement',
        icon: 'bi-folder',
        path: '/manage/projects',
      },
    ],
  },
  {
    id: 'remote',
    title: 'remoteWorkspaces',
    items: [
      {
        id: 'machines',
        label: 'remoteMachines',
        icon: 'bi-pc-display',
        path: '/manage/remote/machines',
        adminOnly: true,
      },
      {
        id: 'api-keys',
        label: 'apiKeys',
        icon: 'bi-key',
        path: '/manage/remote/api-keys',
        adminOnly: true,
      },
    ],
  },
  {
    id: 'settings',
    title: 'settings',
    items: [
      {
        id: 'sso',
        label: 'ssoSettings',
        icon: 'bi-key',
        path: '/manage/settings/sso',
        adminOnly: true,
      },
    ],
  },
];

// Local storage key for collapse state
const COLLAPSE_STATE_KEY = 'manage-nav-collapse-state';

interface ManageLayoutProps {
  children?: React.ReactNode;
}

export const ManageLayout: React.FC<ManageLayoutProps> = ({ children }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const collapsed = useSidebarCollapsed();
  const { user } = useAuth();

  // Initialize collapse state from localStorage or default to only active section expanded
  const getInitialCollapseState = useCallback(() => {
    try {
      const saved = localStorage.getItem(COLLAPSE_STATE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        // Validate and clean up the saved state
        // Ensure only valid section IDs are present
        const validState: Record<string, boolean> = {};
        navSections.forEach((section) => {
          if (section.id in parsed) {
            validState[section.id] = parsed[section.id];
          }
        });
        return validState;
      }
    } catch (e) {
      console.error('Failed to load nav collapse state:', e);
    }
    // Default: all collapsed (will be expanded by useEffect based on activeSection)
    return {};
  }, []);

  const [collapsedSections, setCollapsedSections] =
    useState<Record<string, boolean>>(getInitialCollapseState);

  // Get active nav item from path (use last segment for nested paths)
  const getActiveNavItem = useCallback(() => {
    const path = location.pathname;
    const parts = path.split('/').filter(Boolean);
    if (parts.length >= 2) {
      // For nested paths like /manage/analysis/trend, use the last segment
      return parts[parts.length - 1];
    }
    return 'dashboard';
  }, [location.pathname]);

  const activeNavItem = getActiveNavItem();

  // Find which section contains the active item
  const getActiveSection = useCallback(() => {
    for (const section of navSections) {
      if (section.items.some((item) => item.id === activeNavItem)) {
        return section.id;
      }
    }
    return 'overview';
  }, [activeNavItem]);

  const activeSection = getActiveSection();

  // Auto-expand active section when it changes (accordion mode: only one section expanded)
  useEffect(() => {
    setCollapsedSections((prev) => {
      // Check if current state already has only the active section expanded
      const onlyActiveExpanded = navSections.every((section) =>
        section.id === activeSection ? prev[section.id] === false : prev[section.id] !== false
      );

      if (onlyActiveExpanded) {
        return prev; // No change needed
      }

      // Force only active section to be expanded, collapse all others
      const newState: Record<string, boolean> = {};
      navSections.forEach((section) => {
        newState[section.id] = section.id !== activeSection;
      });
      localStorage.setItem(COLLAPSE_STATE_KEY, JSON.stringify(newState));
      return newState;
    });
  }, [activeSection]);

  // Toggle section collapse (accordion mode: only one section expanded at a time)
  const toggleSection = (sectionId: string) => {
    setCollapsedSections((prev) => {
      const isCurrentlyExpanded = prev[sectionId] === false;
      if (isCurrentlyExpanded) {
        // If currently expanded, collapse it
        const newState = { ...prev, [sectionId]: true };
        localStorage.setItem(COLLAPSE_STATE_KEY, JSON.stringify(newState));
        return newState;
      } else {
        // If currently collapsed, expand it and collapse all others
        const newState: Record<string, boolean> = {};
        navSections.forEach((section) => {
          newState[section.id] = section.id !== sectionId;
        });
        localStorage.setItem(COLLAPSE_STATE_KEY, JSON.stringify(newState));
        return newState;
      }
    });
  };

  const handleNavClick = (item: NavItem, disabled?: boolean) => {
    if (disabled) return;
    navigate(item.path);
  };

  const toggleSidebar = () => {
    useAppStore.getState().toggleSidebar();
  };

  return (
    <div className={cn('manage-layout', collapsed && 'sidebar-collapsed')}>
      {/* Sidebar */}
      <nav className={cn('manage-sidebar', collapsed && 'collapsed')}>
        {/* Logo */}
        <div className="sidebar-header">
          <div className="logo">
            <img
              src="/static/icons/icon.svg"
              alt="Open ACE"
              style={{ width: '28px', height: '28px' }}
            />
            {!collapsed && <span className="logo-text">Open ACE</span>}
          </div>
        </div>

        {/* Mode Switcher */}
        {!collapsed && (
          <div className="sidebar-mode-switcher">
            <ModeSwitcher />
          </div>
        )}

        {/* Navigation Sections */}
        <div className="sidebar-nav">
          {navSections.map((section) => {
            const isCollapsed = collapsedSections[section.id] !== false;
            const isActive = section.id === activeSection;

            return (
              <div key={section.id} className={cn('nav-section', isActive && 'active')}>
                {/* Section Header - Clickable to toggle */}
                {!collapsed && (
                  <button
                    className="nav-section-header"
                    onClick={() => toggleSection(section.id)}
                    aria-expanded={!isCollapsed}
                  >
                    <span className="nav-section-title">{t(section.title, language)}</span>
                    <i
                      className={cn(
                        'bi',
                        'bi-chevron-down',
                        'nav-section-chevron',
                        isCollapsed && 'collapsed'
                      )}
                    />
                  </button>
                )}
                {/* Section Items */}
                <ul
                  className={cn(
                    'nav-section-items',
                    isCollapsed && 'collapsed',
                    collapsed && 'sidebar-collapsed'
                  )}
                >
                  {section.items.map((item) => {
                    const isDisabled = item.adminOnly && user?.role !== 'admin';
                    return (
                      <li key={item.id}>
                        <button
                          className={cn(
                            'nav-item',
                            activeNavItem === item.id && !isDisabled && 'active',
                            isDisabled && 'disabled'
                          )}
                          onClick={() => handleNavClick(item, isDisabled)}
                          disabled={isDisabled}
                          title={
                            collapsed
                              ? t(item.label, language)
                              : isDisabled
                                ? t('adminOnly', language)
                                : undefined
                          }
                        >
                          <i className={cn('bi', item.icon)} />
                          {!collapsed && <span>{t(item.label, language)}</span>}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>

        {/* Collapse Toggle */}
        <button
          className="sidebar-toggle"
          onClick={toggleSidebar}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <i className={cn('bi', collapsed ? 'bi-chevron-right' : 'bi-chevron-left')} />
        </button>
      </nav>

      {/* Main Content */}
      <div className="manage-main">
        <Header />
        <main className="manage-content">{children ?? <Outlet />}</main>
      </div>
    </div>
  );
};

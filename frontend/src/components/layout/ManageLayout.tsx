/**
 * ManageLayout Component - Sidebar navigation layout for Manage Mode
 *
 * Layout structure:
 * - Left: Navigation sidebar with grouped menu items
 * - Right: Main content area
 *
 * Navigation groups:
 * - Overview: Dashboard
 * - Analysis: Trend, Anomaly
 * - Governance: Audit, Quota, Security
 * - Users: Management
 */

import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage, useSidebarCollapsed } from '@/store';
import { useAppStore } from '@/store';
import { t } from '@/i18n';
import { ModeSwitcher } from '@/components/common';
import { Header } from './Header';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  path: string;
}

interface NavSection {
  id: string;
  title: string;
  items: NavItem[];
}

const navSections: NavSection[] = [
  {
    id: 'overview',
    title: 'overview',
    items: [{ id: 'dashboard', label: 'dashboard', icon: 'bi-speedometer2', path: '/manage/dashboard' }],
  },
  {
    id: 'analysis',
    title: 'analysis',
    items: [
      { id: 'trend', label: 'tokenTrend', icon: 'bi-graph-up', path: '/manage/analysis/trend' },
      { id: 'anomaly', label: 'anomalyDetection', icon: 'bi-exclamation-triangle', path: '/manage/analysis/anomaly' },
      { id: 'messages', label: 'messages', icon: 'bi-chat-dots', path: '/manage/messages' },
    ],
  },
  {
    id: 'governance',
    title: 'governance',
    items: [
      { id: 'audit', label: 'auditLog', icon: 'bi-journal-text', path: '/manage/audit' },
      { id: 'quota', label: 'quotaManagement', icon: 'bi-sliders', path: '/manage/quota' },
      { id: 'security', label: 'securitySettings', icon: 'bi-shield', path: '/manage/security' },
    ],
  },
  {
    id: 'users',
    title: 'user',
    items: [{ id: 'users', label: 'userManagement', icon: 'bi-people', path: '/manage/users' }],
  },
];

interface ManageLayoutProps {
  children?: React.ReactNode;
}

export const ManageLayout: React.FC<ManageLayoutProps> = ({ children }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const collapsed = useSidebarCollapsed();

  // Get active nav item from path
  const getActiveNavItem = () => {
    const path = location.pathname;
    // Extract the last part of the path
    const parts = path.split('/').filter(Boolean);
    if (parts.length >= 2) {
      return parts[1]; // e.g., 'dashboard', 'analysis', etc.
    }
    return 'dashboard';
  };

  const activeNavItem = getActiveNavItem();

  const handleNavClick = (item: NavItem) => {
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
            <i className="bi bi-cpu fs-4" />
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
          {navSections.map((section) => (
            <div key={section.id} className="nav-section">
              {!collapsed && (
                <div className="nav-section-title">{t(section.title, language)}</div>
              )}
              <ul className="nav-section-items">
                {section.items.map((item) => (
                  <li key={item.id}>
                    <button
                      className={cn('nav-item', activeNavItem === item.id && 'active')}
                      onClick={() => handleNavClick(item)}
                      title={collapsed ? t(item.label, language) : undefined}
                    >
                      <i className={cn('bi', item.icon)} />
                      {!collapsed && <span>{t(item.label, language)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
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
        <main className="manage-content">
          {children || <Outlet />}
        </main>
      </div>
    </div>
  );
};
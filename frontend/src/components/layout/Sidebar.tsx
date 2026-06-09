/**
 * Sidebar Component - Navigation sidebar
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/utils';
import { useSidebarCollapsed, useLanguage } from '@/store';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';
import { t } from '@/i18n';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  href?: string;
  adminOnly?: boolean;
}

const navItems: NavItem[] = [
  { id: 'dashboard', label: 'dashboard', icon: 'bi-speedometer2' },
  { id: 'messages', label: 'messages', icon: 'bi-chat-dots' },
  { id: 'analysis', label: 'analysis', icon: 'bi-graph-up' },
  { id: 'management', label: 'management', icon: 'bi-gear', adminOnly: true },
  { id: 'sessions', label: 'sessions', icon: 'bi-collection' },
  { id: 'prompts', label: 'prompts', icon: 'bi-file-text' },
  { id: 'report', label: 'report', icon: 'bi-file-earmark-bar-graph' },
  { id: 'workspace', label: 'workspace', icon: 'bi-grid' },
];

interface SidebarProps {
  activeSection: string;
  mobileOpen?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeSection, mobileOpen }) => {
  const collapsed = useSidebarCollapsed();
  const language = useLanguage();
  const { user } = useAuth();

  const isAdmin = user?.role === 'admin';

  const handleNavClick = (e: React.MouseEvent<HTMLAnchorElement>, disabled?: boolean) => {
    if (disabled) {
      e.preventDefault();
      return;
    }
    useAppStore.getState().setMobileSidebarOpen(false);
  };

  const toggleSidebar = () => {
    useAppStore.getState().toggleSidebar();
  };

  const renderNavItem = (item: NavItem) => {
    const isDisabled = item.adminOnly && !isAdmin;
    const commonProps = {
      className: cn(
        'nav-link text-white text-start w-100 d-flex align-items-center',
        activeSection === item.id && !isDisabled && 'active bg-primary',
        isDisabled && 'disabled opacity-50'
      ),
      onClick: (e: React.MouseEvent<HTMLAnchorElement>) => handleNavClick(e, isDisabled),
      title: collapsed
        ? t(item.label, language)
        : isDisabled
          ? t('adminOnly', language)
          : undefined,
      'aria-disabled': isDisabled ? true : undefined,
      tabIndex: isDisabled ? -1 : undefined,
    };

    // External link: use <a> tag
    if (item.href) {
      return (
        <a
          href={item.href}
          target="_blank"
          rel="noopener noreferrer"
          {...commonProps}
        >
          <i className={cn('bi', item.icon, 'me-2')} />
          {!collapsed && <span>{t(item.label, language)}</span>}
        </a>
      );
    }

    // Internal navigation: use <Link> component
    return (
      <Link to={`/${item.id}`} {...commonProps}>
        <i className={cn('bi', item.icon, 'me-2')} />
        {!collapsed && <span>{t(item.label, language)}</span>}
      </Link>
    );
  };

  return (
    <nav
      className={cn(
        'sidebar bg-dark text-white',
        collapsed && 'sidebar-collapsed',
        mobileOpen && 'show'
      )}
    >
      {/* Logo */}
      <div className="sidebar-header p-3 border-bottom border-secondary">
        <div className="d-flex align-items-center">
          <img
            src="/static/icons/icon.svg"
            alt="Open ACE"
            style={{ width: '28px', height: '28px' }}
            className="me-2"
          />
          {!collapsed && <span className="fs-5 fw-bold">Open ACE</span>}
        </div>
      </div>

      {/* Navigation */}
      <ul className="nav flex-column py-3">
        {navItems.map((item) => (
          <li className="nav-item" key={item.id}>
            {renderNavItem(item)}
          </li>
        ))}
      </ul>

      {/* Collapse Toggle Button */}
      <button
        className="sidebar-toggle-btn"
        onClick={toggleSidebar}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <i className={cn('bi', collapsed ? 'bi-chevron-right' : 'bi-chevron-left')} />
      </button>
    </nav>
  );
};
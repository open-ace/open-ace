/**
 * Sidebar Component - Navigation sidebar
 */

import React from 'react';
import { cn } from '@/utils';
import { useSidebarCollapsed, useLanguage } from '@/store';
import { t } from '@/i18n';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  href?: string;
}

const navItems: NavItem[] = [
  { id: 'dashboard', label: 'dashboard', icon: 'bi-speedometer2' },
  { id: 'messages', label: 'messages', icon: 'bi-chat-dots' },
  { id: 'analysis', label: 'analysis', icon: 'bi-graph-up' },
  { id: 'management', label: 'management', icon: 'bi-gear' },
  { id: 'sessions', label: 'sessions', icon: 'bi-collection' },
  { id: 'prompts', label: 'prompts', icon: 'bi-file-text' },
  { id: 'report', label: 'report', icon: 'bi-file-earmark-bar-graph' },
  { id: 'workspace', label: 'workspace', icon: 'bi-grid' },
];

interface SidebarProps {
  activeSection: string;
  onNavigate: (section: string) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeSection, onNavigate }) => {
  const collapsed = useSidebarCollapsed();
  const language = useLanguage();

  const handleNavClick = (id: string, href?: string) => {
    if (href) {
      window.location.href = href;
    } else {
      onNavigate(id);
    }
  };

  return (
    <nav className={cn('sidebar bg-dark text-white', collapsed ? 'sidebar-collapsed' : '')}>
      {/* Logo */}
      <div className="sidebar-header p-3 border-bottom border-secondary">
        <div className="d-flex align-items-center">
          <i className="bi bi-cpu fs-4 me-2" />
          {!collapsed && <span className="fs-5 fw-bold">Open ACE</span>}
        </div>
      </div>

      {/* Navigation */}
      <ul className="nav flex-column py-3">
        {navItems.map((item) => (
          <li className="nav-item" key={item.id}>
            <button
              className={cn(
                'nav-link btn btn-link text-white text-start w-100 d-flex align-items-center',
                activeSection === item.id && 'active bg-primary'
              )}
              onClick={() => handleNavClick(item.id, item.href)}
              title={collapsed ? t(item.label, language) : undefined}
            >
              <i className={cn('bi', item.icon, 'me-2')} />
              {!collapsed && <span>{t(item.label, language)}</span>}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
};
/**
 * WorkLayout Component - Three-column layout for Work Mode
 *
 * Layout structure:
 * - Left: Session list
 * - Center: Main content (AI conversation)
 * - Right: Assist panel (prompts, tools, docs)
 * - Bottom: Status bar
 */

import React, { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { ModeSwitcher } from '@/components/common';
import { Header } from './Header';
import { SessionList, AssistPanel, StatusBar } from '@/components/work';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  path: string;
}

const workNavItems: NavItem[] = [
  { id: 'workspace', label: 'workspace', icon: 'bi-grid', path: '/work' },
  { id: 'sessions', label: 'sessions', icon: 'bi-collection', path: '/work/sessions' },
  { id: 'prompts', label: 'prompts', icon: 'bi-file-text', path: '/work/prompts' },
];

interface WorkLayoutProps {
  children?: React.ReactNode;
}

export const WorkLayout: React.FC<WorkLayoutProps> = ({ children }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);

  // Get active nav item from path
  const getActiveNavItem = () => {
    const path = location.pathname;
    if (path === '/work' || path === '/work/') return 'workspace';
    if (path.startsWith('/work/sessions')) return 'sessions';
    if (path.startsWith('/work/prompts')) return 'prompts';
    return 'workspace';
  };

  const activeNavItem = getActiveNavItem();

  const handleNavClick = (item: NavItem) => {
    navigate(item.path);
  };

  return (
    <div className="work-layout">
      {/* Header */}
      <header className="work-header">
        <div className="header-left">
          <div className="logo">
            <i className="bi bi-cpu fs-4" />
            <span className="logo-text">Open ACE</span>
          </div>
          <ModeSwitcher className="header-mode-switcher" />
        </div>
        <Header compact />
      </header>

      <div className="work-body">
        {/* Left Panel - Session List */}
        <aside className={cn('work-left-panel', leftPanelCollapsed && 'collapsed')}>
          <div className="panel-header">
            <span className="panel-title">{t('sessions', language)}</span>
            <button
              className="panel-toggle"
              onClick={() => setLeftPanelCollapsed(!leftPanelCollapsed)}
              title={leftPanelCollapsed ? t('showMore', language) : t('showLess', language)}
            >
              <i className={cn('bi', leftPanelCollapsed ? 'bi-chevron-right' : 'bi-chevron-left')} />
            </button>
          </div>

          {/* Work Navigation */}
          <nav className="work-nav">
            {workNavItems.map((item) => (
              <button
                key={item.id}
                className={cn('work-nav-item', activeNavItem === item.id && 'active')}
                onClick={() => handleNavClick(item)}
                title={t(item.label, language)}
              >
                <i className={cn('bi', item.icon)} />
                {!leftPanelCollapsed && <span>{t(item.label, language)}</span>}
              </button>
            ))}
          </nav>

          {/* Session List Component */}
          <SessionList collapsed={leftPanelCollapsed} />
        </aside>

        {/* Main Content */}
        <main className="work-main">
          {children || <Outlet />}
        </main>

        {/* Right Panel - Assist Panel */}
        <aside className={cn('work-right-panel', rightPanelCollapsed && 'collapsed')}>
          <div className="panel-header">
            <span className="panel-title">{t('tools', language)}</span>
            <button
              className="panel-toggle"
              onClick={() => setRightPanelCollapsed(!rightPanelCollapsed)}
              title={rightPanelCollapsed ? t('showMore', language) : t('showLess', language)}
            >
              <i className={cn('bi', rightPanelCollapsed ? 'bi-chevron-left' : 'bi-chevron-right')} />
            </button>
          </div>

          {/* Assist Panel Component */}
          <AssistPanel collapsed={rightPanelCollapsed} />
        </aside>
      </div>

      {/* Status Bar Component */}
      <StatusBar />
    </div>
  );
};
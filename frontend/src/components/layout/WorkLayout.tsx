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

          {/* Session List Content */}
          {!leftPanelCollapsed && (
            <div className="session-list-content">
              <div className="session-list-header">
                <span className="text-muted">{t('todayUsage', language)}</span>
              </div>
              {/* Session items will be rendered by child components */}
              <div className="session-items">
                <div className="session-item-placeholder">
                  <i className="bi bi-chat-dots" />
                  <span>{t('noSessionsFound', language)}</span>
                </div>
              </div>
            </div>
          )}
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

          {!rightPanelCollapsed && (
            <div className="assist-panel-content">
              <div className="assist-section">
                <h6>{t('prompts', language)}</h6>
                <div className="assist-items">
                  <div className="assist-item-placeholder">
                    <i className="bi bi-file-text" />
                    <span>{t('noPromptsFound', language)}</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </aside>
      </div>

      {/* Status Bar */}
      <footer className="work-status-bar">
        <div className="status-left">
          <span className="status-item">
            <i className="bi bi-cpu" />
            <span>GPT-4</span>
          </span>
        </div>
        <div className="status-center">
          <span className="status-item">
            <i className="bi bi-lightning" />
            <span>Token: 0 / 10,000</span>
          </span>
        </div>
        <div className="status-right">
          <span className="status-item">
            <i className="bi bi-clock" />
            <span>0ms</span>
          </span>
        </div>
      </footer>
    </div>
  );
};
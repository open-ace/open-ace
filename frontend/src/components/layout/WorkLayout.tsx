/**
 * WorkLayout Component - Three-column layout for Work Mode
 *
 * Layout structure:
 * - Left: Session list
 * - Center: Main content (AI conversation)
 * - Right: Assist panel (prompts, tools, docs)
 * - Bottom: Status bar
 *
 * Features:
 * - Fullscreen mode: collapses left and right panels
 * - ESC key to exit fullscreen
 * - Preserves panel state when entering/exiting fullscreen
 */

import React, { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage, useAppStore, useWorkspaceFullscreen } from '@/store';
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
  { id: 'usage', label: 'myUsage', icon: 'bi-bar-chart', path: '/work/usage' },
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

  // Fullscreen state from global store
  const workspaceFullscreen = useWorkspaceFullscreen();
  const {
    exitWorkspaceFullscreen,
    previousLeftPanelCollapsed,
    previousRightPanelCollapsed,
  } = useAppStore();

  // Get active nav item from path
  const getActiveNavItem = () => {
    const path = location.pathname;
    if (path === '/work' || path === '/work/') return 'workspace';
    if (path.startsWith('/work/sessions')) return 'sessions';
    if (path.startsWith('/work/prompts')) return 'prompts';
    if (path.startsWith('/work/usage')) return 'usage';
    return 'workspace';
  };

  const activeNavItem = getActiveNavItem();

  const handleNavClick = (item: NavItem) => {
    navigate(item.path);
  };

  // Handle ESC key to exit fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && workspaceFullscreen) {
        exitWorkspaceFullscreen();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [workspaceFullscreen, exitWorkspaceFullscreen]);

  // Update local panel state when fullscreen mode changes
  useEffect(() => {
    if (workspaceFullscreen) {
      // Entering fullscreen: collapse both panels
      setLeftPanelCollapsed(true);
      setRightPanelCollapsed(true);
    } else {
      // Exiting fullscreen: restore previous state
      setLeftPanelCollapsed(previousLeftPanelCollapsed);
      setRightPanelCollapsed(previousRightPanelCollapsed);
    }
  }, [workspaceFullscreen, previousLeftPanelCollapsed, previousRightPanelCollapsed]);

  return (
    <div className={cn('work-layout', workspaceFullscreen && 'fullscreen-mode')}>
      {/* Header - Hidden in fullscreen */}
      <header className={cn('work-header', workspaceFullscreen && 'd-none')}>
        <div className="header-left">
          <div className="logo">
            <img
              src="/static/icons/icon.svg"
              alt="Open ACE"
              style={{ width: '28px', height: '28px' }}
            />
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
              <i
                className={cn('bi', leftPanelCollapsed ? 'bi-chevron-right' : 'bi-chevron-left')}
              />
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
        <main className="work-main">{children}</main>

        {/* Right Panel - Assist Panel */}
        <aside className={cn('work-right-panel', rightPanelCollapsed && 'collapsed')}>
          <div className="panel-header">
            <span className="panel-title">{t('tools', language)}</span>
            <button
              className="panel-toggle"
              onClick={() => setRightPanelCollapsed(!rightPanelCollapsed)}
              title={rightPanelCollapsed ? t('showMore', language) : t('showLess', language)}
            >
              <i
                className={cn('bi', rightPanelCollapsed ? 'bi-chevron-left' : 'bi-chevron-right')}
              />
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
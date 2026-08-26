/**
 * WorkLayout Component - Layout for Work Mode
 *
 * Layout structure:
 * - Left: Navigation + session list
 * - Center: Main content (AI conversation)
 * - Right: Prompts drawer (workspace route only, floating overlay)
 * - Bottom: Status bar
 *
 * Features:
 * - Fullscreen mode: collapses the left panel (the prompts drawer is an
 *   overlay and stays available)
 * - ESC key to exit fullscreen (an open modal or the prompts drawer
 *   handles ESC first)
 * - Preserves panel state when entering/exiting fullscreen
 */

import React, { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { cn } from '@/utils';
import { isWorkspaceRoute } from '@/utils/urlUtils';
import { useLanguage, useAppStore, useWorkspaceFullscreen, usePromptsDrawerOpen } from '@/store';
import { t } from '@/i18n';
import { ModeSwitcher } from '@/components/common';
import { Header } from './Header';
import { SessionList, PromptsDrawer, StatusBar } from '@/components/work';
import { workspaceApi } from '@/api/workspace';
import { featureFlagsApi } from '@/api/featureFlags';

interface NavItem {
  id: string;
  label: string;
  icon: string;
  path: string;
}

const workNavItems: NavItem[] = [
  { id: 'workspace', label: 'workspace', icon: 'bi-grid', path: '/work' },
  { id: 'autonomous', label: 'autonomousDev', icon: 'bi-robot', path: '/work/autonomous' },
  { id: 'files', label: 'personalFiles', icon: 'bi-folder2-open', path: '/work/files' },
  { id: 'sessions', label: 'sessionHistory', icon: 'bi-collection', path: '/work/sessions' },
  { id: 'prompts', label: 'prompts', icon: 'bi-file-text', path: '/work/prompts' },
  { id: 'usage', label: 'myUsage', icon: 'bi-bar-chart', path: '/work/usage' },
  { id: 'insights', label: 'insights', icon: 'bi-lightbulb', path: '/work/insights' },
];

interface WorkLayoutProps {
  children?: React.ReactNode;
}

export const WorkLayout: React.FC<WorkLayoutProps> = ({ children }) => {
  const language = useLanguage();
  const location = useLocation();
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [promptsDrawerOpenedOnce, setPromptsDrawerOpenedOnce] = useState(false);

  // Fullscreen state from global store
  const workspaceFullscreen = useWorkspaceFullscreen();
  const promptsDrawerOpen = usePromptsDrawerOpen();
  const {
    exitWorkspaceFullscreen,
    setPromptsDrawerOpen,
    previousLeftPanelCollapsed,
    autonomousEnabled,
    setAutonomousEnabled,
    setModelGatewayEnabled,
    setRunTimelineEnabled,
    setPolicyEnabled,
    setConfigLoaded,
  } = useAppStore();

  const isWorkspace = isWorkspaceRoute(location.pathname);

  // Remember that the drawer has been opened at least once so it stays
  // mounted (preserving its state) and no API calls happen before then
  useEffect(() => {
    if (promptsDrawerOpen) {
      setPromptsDrawerOpenedOnce(true);
    }
  }, [promptsDrawerOpen]);

  // Load workspace config and feature flags on mount
  useEffect(() => {
    const loadConfig = async () => {
      try {
        // Load both workspace config and feature flags
        const [config, flags] = await Promise.all([
          workspaceApi.getConfig(),
          featureFlagsApi.getFlags(),
        ]);
        setAutonomousEnabled(config.autonomous_enabled);
        setModelGatewayEnabled(flags.model_gateway);
        setRunTimelineEnabled(flags.run_timeline);
        setPolicyEnabled(flags.policy);
        setConfigLoaded(true);
      } catch (error) {
        console.error('Failed to load config or feature flags:', error);
        // Config fetch failed, keep defaults but mark as loaded
        setConfigLoaded(true);
      }
    };
    loadConfig();
  }, [
    setAutonomousEnabled,
    setModelGatewayEnabled,
    setRunTimelineEnabled,
    setPolicyEnabled,
    setConfigLoaded,
  ]);

  // Filter nav items based on feature flags
  const visibleNavItems = autonomousEnabled
    ? workNavItems
    : workNavItems.filter((item) => item.id !== 'autonomous');

  // Get active nav item from path
  const getActiveNavItem = () => {
    const path = location.pathname;
    if (path === '/work' || path === '/work/') return 'workspace';
    if (path.startsWith('/work/files')) return 'files';
    if (path.startsWith('/work/sessions')) return 'sessions';
    if (path.startsWith('/work/prompts')) return 'prompts';
    if (path.startsWith('/work/usage')) return 'usage';
    if (path.startsWith('/work/insights')) return 'insights';
    if (path.startsWith('/work/autonomous')) return autonomousEnabled ? 'autonomous' : 'workspace';
    return 'workspace';
  };

  const activeNavItem = getActiveNavItem();

  // ESC key: an open modal handles ESC itself; otherwise close the prompts
  // drawer first; otherwise exit fullscreen
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (document.querySelector('.modal.show')) return;
      if (promptsDrawerOpen) {
        setPromptsDrawerOpen(false);
      } else if (workspaceFullscreen) {
        exitWorkspaceFullscreen();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [workspaceFullscreen, promptsDrawerOpen, exitWorkspaceFullscreen, setPromptsDrawerOpen]);

  // Update left panel state when fullscreen mode changes
  useEffect(() => {
    if (workspaceFullscreen) {
      // Entering fullscreen: collapse the left panel
      setLeftPanelCollapsed(true);
    } else {
      // Exiting fullscreen: restore previous state
      setLeftPanelCollapsed(previousLeftPanelCollapsed);
    }
  }, [workspaceFullscreen, previousLeftPanelCollapsed]);

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
        {/* Left Panel - Navigation + Session List */}
        <aside className={cn('work-left-panel', leftPanelCollapsed && 'collapsed')}>
          <div className="panel-header">
            <button
              className="panel-toggle"
              onClick={() => setLeftPanelCollapsed(!leftPanelCollapsed)}
              title={leftPanelCollapsed ? t('showMore', language) : t('showLess', language)}
              aria-label={leftPanelCollapsed ? t('showMore', language) : t('showLess', language)}
            >
              <i
                className={cn('bi', leftPanelCollapsed ? 'bi-chevron-right' : 'bi-chevron-left')}
              />
            </button>
          </div>

          {/* Work Navigation */}
          <nav className="work-nav">
            {visibleNavItems.map((item) => (
              <Link
                key={item.id}
                to={item.path}
                className={cn('work-nav-item', activeNavItem === item.id && 'active')}
                title={t(item.label, language)}
              >
                <i className={cn('bi', item.icon)} />
                {!leftPanelCollapsed && <span>{t(item.label, language)}</span>}
              </Link>
            ))}
          </nav>

          {/* Session List Component */}
          <SessionList collapsed={leftPanelCollapsed} />
        </aside>

        {/* Main Content */}
        <main className="work-main">{children}</main>

        {/* Prompts drawer - workspace route only, floating overlay.
            Mounted after first open so closed state costs no API calls;
            stays mounted afterwards to preserve drawer state. */}
        {isWorkspace && !promptsDrawerOpen && (
          <button
            className="prompts-drawer-toggle"
            onClick={() => setPromptsDrawerOpen(true)}
            title={t('prompts', language)}
            aria-label={t('prompts', language)}
            aria-haspopup="dialog"
          >
            <i className="bi bi-file-text" />
            <span>{t('prompts', language)}</span>
          </button>
        )}
        {isWorkspace && (promptsDrawerOpen || promptsDrawerOpenedOnce) && (
          <PromptsDrawer isOpen={promptsDrawerOpen} onClose={() => setPromptsDrawerOpen(false)} />
        )}
      </div>

      {/* Status Bar Component */}
      <StatusBar />
    </div>
  );
};

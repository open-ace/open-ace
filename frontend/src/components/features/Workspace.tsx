/**
 * Workspace Component - AI workspace with iframe embedding and tab support
 *
 * Features:
 * - Multiple tabs for different conversations
 * - Each tab has its own iframe
 * - Support for creating new tabs via URL parameter
 * - Quota checking - disables workspace when quota exceeded
 * - Multi-user mode support with per-user webui instances
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { workspaceApi, type WorkspaceConfig, type UserWebUIResponse } from '@/api';
import { requestApi, type QuotaStatusResponse } from '@/api/request';
import { sessionsApi } from '@/api/sessions';
import { useLanguage, useAppStore, useWorkspaceFullscreen, useEnableTabNotifications } from '@/store';
import { t } from '@/i18n';
import { Error, Button, Card, useToast, Modal } from '@/components/common';
import { cn } from '@/utils';

interface WorkspaceTab {
  id: string;
  title: string;
  url: string;
  token: string;
  createdAt: number;
  waitingForUser: boolean;
  waitingType: 'permission' | 'plan' | 'input' | null;
}

// Generate unique tab ID
const generateTabId = () => `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

// Quota check interval (5 minutes)
const QUOTA_CHECK_INTERVAL = 5 * 60 * 1000;

// Activity heartbeat interval (2 minutes)
const ACTIVITY_HEARTBEAT_INTERVAL = 2 * 60 * 1000;

export const Workspace: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [userWebUI, setUserWebUI] = useState<UserWebUIResponse | null>(null);
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isQuotaLoading, setIsQuotaLoading] = useState(true);
  const [loadingStage, setLoadingStage] = useState<string>('initializing'); // Track loading progress
  const [error, setError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set());
  const [renameTabId, setRenameTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [showRenameModal, setShowRenameModal] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [tabWidths, setTabWidths] = useState<Record<string, number>>({});
  const [resizingTabId, setResizingTabId] = useState<string | null>(null);

  // Tab notifications setting
  const enableTabNotifications = useEnableTabNotifications();
  const { toggleTabNotifications } = useAppStore();

  // Refs for iframe elements (to send focus messages)
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map());

  // Fullscreen state from global store
  const workspaceFullscreen = useWorkspaceFullscreen();
  const { toggleWorkspaceFullscreen, exitWorkspaceFullscreen } = useAppStore();

  // Load workspace config and user webui URL
  useEffect(() => {
    const loadConfig = async () => {
      try {
        setLoadingStage('loadingConfig');
        const workspaceConfig = await workspaceApi.getConfig();
        setConfig(workspaceConfig);

        // If multi-user mode is enabled, get user-specific URL
        if (workspaceConfig.enabled && workspaceConfig.multi_user_mode) {
          setLoadingStage('startingWorkspace');
          const userWebUIResponse = await workspaceApi.getUserWebUIUrl();
          if (userWebUIResponse.success) {
            setUserWebUI(userWebUIResponse);
            setLoadingStage('ready');
          } else {
            setError(userWebUIResponse.error || 'Failed to get user workspace URL');
          }
        } else {
          setLoadingStage('ready');
        }
      } catch (err) {
        const error = err as Error;
        setError(error?.message || 'Failed to load workspace config');
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, []);

  // Activity heartbeat for multi-user mode
  useEffect(() => {
    if (!config?.multi_user_mode || !userWebUI?.success) return;

    const sendHeartbeat = async () => {
      try {
        await workspaceApi.getUserWebUIUrl();
      } catch (err) {
        console.error('Failed to send activity heartbeat:', err);
      }
    };

    const interval = setInterval(sendHeartbeat, ACTIVITY_HEARTBEAT_INTERVAL);
    return () => clearInterval(interval);
  }, [config?.multi_user_mode, userWebUI?.success]);

  // Listen for fullscreen request from iframe (when user selects project and enters chat)
  useEffect(() => {
    const handleIframeMessage = (event: MessageEvent) => {
      // Validate message type for fullscreen request
      if (event.data?.type === 'openace-enter-chat') {
        useAppStore.getState().enterWorkspaceFullscreen(false, false);
      }

      // Listen for tab notification from qwen-code-webui iframe
      if (event.data?.type === 'qwen-code-tab-notification') {
        const { isWaiting, waitingType } = event.data;
        // Only update if tab notifications are enabled
        if (enableTabNotifications) {
          setActiveTabWaitingState(isWaiting, waitingType);
        }
      }
    };

    window.addEventListener('message', handleIframeMessage);
    return () => window.removeEventListener('message', handleIframeMessage);
  }, [enableTabNotifications]);

  // Check quota
  const checkQuota = useCallback(async () => {
    try {
      const status = await requestApi.getQuotaStatus();
      setQuotaStatus(status);
    } catch (err) {
      console.error('Failed to check quota:', err);
    } finally {
      setIsQuotaLoading(false);
    }
  }, []);

  // Initial quota check and periodic checks
  useEffect(() => {
    checkQuota();

    const interval = setInterval(checkQuota, QUOTA_CHECK_INTERVAL);

    return () => clearInterval(interval);
  }, [checkQuota]);

  // Auto exit fullscreen when quota exceeded
  useEffect(() => {
    if (quotaStatus?.over_quota?.any && workspaceFullscreen) {
      exitWorkspaceFullscreen();
      // Show a toast notification to inform user
      toast.warning(t('exitedFullscreenDueToQuotaTitle', language), t('exitedFullscreenDueToQuotaDesc', language));
    }
  }, [quotaStatus?.over_quota?.any, workspaceFullscreen, exitWorkspaceFullscreen, language, toast]);

  // Get the effective URL for iframe
  const getEffectiveUrl = useCallback((restoreSessionId?: string, encodedProjectName?: string, toolName?: string): string => {
    if (!config?.enabled) return '';

    // Multi-user mode: use user-specific URL with token and openace_url
    if (config.multi_user_mode && userWebUI?.success) {
      const baseUrl = userWebUI.url;
      const token = userWebUI.token;
      const openaceUrl = userWebUI.openace_url;
      // Add token and openace_url as URL parameters
      const separator = baseUrl.includes('?') ? '&' : '?';
      let url = `${baseUrl}${separator}token=${encodeURIComponent(token)}`;
      if (openaceUrl) {
        url = `${url}&openace_url=${encodeURIComponent(openaceUrl)}`;
      }
      // Add lang parameter for language sync
      url = `${url}&lang=${encodeURIComponent(language)}`;
      // Add sessionId, encodedProjectName, and toolName if restoring a session
      if (restoreSessionId) {
        url = `${url}&sessionId=${encodeURIComponent(restoreSessionId)}`;
      }
      if (encodedProjectName) {
        url = `${url}&encodedProjectName=${encodeURIComponent(encodedProjectName)}`;
      }
      if (toolName) {
        url = `${url}&toolName=${encodeURIComponent(toolName)}`;
      }
      return url;
    }

    // Single-user mode: use configured URL
    let url = config.url;
    // Add lang parameter for language sync
    const langSeparator = url.includes('?') ? '&' : '?';
    url = `${url}${langSeparator}lang=${encodeURIComponent(language)}`;
    if (restoreSessionId) {
      const separator = url.includes('?') ? '&' : '?';
      url = `${url}${separator}sessionId=${encodeURIComponent(restoreSessionId)}`;
    }
    if (encodedProjectName) {
      const separator = url.includes('?') ? '&' : '?';
      url = `${url}${separator}encodedProjectName=${encodeURIComponent(encodedProjectName)}`;
    }
    if (toolName) {
      const separator = url.includes('?') ? '&' : '?';
      url = `${url}${separator}toolName=${encodeURIComponent(toolName)}`;
    }
    return url;
  }, [config, userWebUI, language]);

  // Initialize first tab when config is loaded
  useEffect(() => {
    // Wait for both config and userWebUI (in multi-user mode) to be loaded
    if (!config?.enabled) return;

    // In multi-user mode, wait for userWebUI to be loaded
    if (config.multi_user_mode && !userWebUI?.success) return;

    // Skip if tabs already exist
    if (tabs.length > 0) return;

    // Check for session restore parameters
    // API returns: /work/workspace?sessionId=xxx&encodedProjectName=yyy&toolName=zzz
    // Also support legacy: /work/workspace?restoreSession=xxx
    const sessionId = searchParams.get('sessionId');
    const restoreSession = searchParams.get('restoreSession');
    const encodedProjectName = searchParams.get('encodedProjectName');
    const toolName = searchParams.get('toolName');
    const restoreSessionId = sessionId || restoreSession;

    // Create initial tab (with or without restore session)
    const effectiveUrl = getEffectiveUrl(restoreSessionId || undefined, encodedProjectName || undefined, toolName || undefined);
    if (!effectiveUrl) return;

    const initialTab: WorkspaceTab = {
      id: generateTabId(),
      title: restoreSessionId ? t('restoredSession', language) : t('newSession', language),
      url: effectiveUrl,
      token: userWebUI?.token || '',
      createdAt: Date.now(),
      waitingForUser: false,
      waitingType: null,
    };
    setTabs([initialTab]);
    setActiveTabId(initialTab.id);
    // Mark as loading
    setLoadingTabs(new Set([initialTab.id]));

    // Clear the restore parameters after using it
    if (restoreSessionId) {
      searchParams.delete('sessionId');
      searchParams.delete('restoreSession');
      searchParams.delete('encodedProjectName');
      searchParams.delete('toolName');
      setSearchParams(searchParams, { replace: true });
    }
  }, [config, userWebUI, tabs.length, language, getEffectiveUrl, searchParams, setSearchParams]);

  // Handle URL parameter for creating new tab
  useEffect(() => {
    const newTab = searchParams.get('newTab');
    const effectiveUrl = getEffectiveUrl();
    if (newTab === 'true' && config?.enabled && effectiveUrl) {
      // Clear the URL parameter
      searchParams.delete('newTab');
      setSearchParams(searchParams, { replace: true });

      // Create new tab
      createNewTab();
    }
  }, [searchParams, config, getEffectiveUrl]);

  // Create a new tab
  const createNewTab = useCallback((restoreSessionId?: string) => {
    const effectiveUrl = getEffectiveUrl(restoreSessionId || undefined);
    if (!effectiveUrl) return;

    const newTab: WorkspaceTab = {
      id: generateTabId(),
      title: restoreSessionId ? t('restoredSession', language) : t('newSession', language),
      url: effectiveUrl,
      token: userWebUI?.token || '',
      createdAt: Date.now(),
      waitingForUser: false,
      waitingType: null,
    };

    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
    // Mark as loading
    setLoadingTabs((prev) => new Set(prev).add(newTab.id));
  }, [getEffectiveUrl, userWebUI, language]);

  // Close a tab
  const closeTab = useCallback(
    (tabId: string, e: React.MouseEvent) => {
      e.stopPropagation();

      setTabs((prev) => {
        const newTabs = prev.filter((tab) => tab.id !== tabId);

        // If closing the active tab, switch to another tab
        if (activeTabId === tabId && newTabs.length > 0) {
          // Find the previous tab or the first one
          const closedIndex = prev.findIndex((tab) => tab.id === tabId);
          const newActiveIndex = Math.min(closedIndex, newTabs.length - 1);
          setActiveTabId(newTabs[newActiveIndex].id);
        }

        return newTabs;
      });
    },
    [activeTabId]
  );

  // Set waiting state for active tab
  const setActiveTabWaitingState = useCallback((isWaiting: boolean, waitingType: 'permission' | 'plan' | 'input' | null) => {
    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === activeTabId
          ? { ...tab, waitingForUser: isWaiting, waitingType }
          : tab
      )
    );
  }, [activeTabId]);

  // Switch to a tab
  const switchTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);

    // Send focus message to iframe after tab switch
    // Use setTimeout to ensure the iframe is visible before sending message
    setTimeout(() => {
      const iframe = iframeRefs.current.get(tabId);
      if (iframe?.contentWindow) {
        iframe.contentWindow.postMessage({ type: 'openace-focus-input' }, '*');
        // Also send tab-activated to clear notification
        iframe.contentWindow.postMessage({ type: 'openace-tab-activated' }, '*');
      }
    }, 100);
  }, []);

  // Rename a tab
  const handleRenameTab = useCallback((tabId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const tab = tabs.find((t) => t.id === tabId);
    if (tab) {
      setRenameTabId(tabId);
      setRenameValue(tab.title);
      setShowRenameModal(true);
    }
  }, [tabs]);

  const handleSaveRename = useCallback(async () => {
    if (!renameTabId || !renameValue.trim()) return;

    setIsRenaming(true);
    try {
      // Extract session ID from tab URL if it contains one
      // Tab URL format: http://.../c/{session_id}?...
      const tab = tabs.find((t) => t.id === renameTabId);
      if (!tab) return;

      // Try to extract session ID from URL
      const urlParts = tab.url.split('/c/');
      let sessionId: string | null = null;
      if (urlParts.length > 1) {
        sessionId = urlParts[1].split('?')[0].split('#')[0];
      }

      if (sessionId) {
        // Call backend API to rename session
        const response = await sessionsApi.renameSession(sessionId, renameValue.trim());
        if (!response.success) {
          toast.error(response.error || t('error', language));
          setIsRenaming(false);
          return;
        }
      }

      // Update tab title locally
      setTabs((prev) =>
        prev.map((tab) =>
          tab.id === renameTabId ? { ...tab, title: renameValue.trim() } : tab
        )
      );

      toast.success(t('sessionRenamed', language));
      setShowRenameModal(false);
      setRenameTabId(null);
      setRenameValue('');
    } catch (error) {
      console.error('Failed to rename session:', error);
      toast.error((error as Error).message || t('error', language));
    } finally {
      setIsRenaming(false);
    }
  }, [renameTabId, renameValue, tabs, language, toast]);

  const handleCancelRename = useCallback(() => {
    setShowRenameModal(false);
    setRenameTabId(null);
    setRenameValue('');
  }, []);

  // Handle tab resize
  const handleResizeStart = useCallback((tabId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    setResizingTabId(tabId);
  }, []);

  const handleResizeMove = useCallback((e: MouseEvent) => {
    if (!resizingTabId) return;

    const tabElement = document.querySelector(`[data-tab-id="${resizingTabId}"]`) as HTMLElement;
    if (!tabElement) return;

    const rect = tabElement.getBoundingClientRect();
    const newWidth = e.clientX - rect.left;

    // Constrain width between 100px and 400px
    const constrainedWidth = Math.max(100, Math.min(400, newWidth));

    setTabWidths((prev) => ({
      ...prev,
      [resizingTabId]: constrainedWidth,
    }));
  }, [resizingTabId]);

  const handleResizeEnd = useCallback(() => {
    setResizingTabId(null);
  }, []);

  // Add global mouse move/up listeners for resizing
  useEffect(() => {
    if (resizingTabId) {
      document.addEventListener('mousemove', handleResizeMove);
      document.addEventListener('mouseup', handleResizeEnd);
      return () => {
        document.removeEventListener('mousemove', handleResizeMove);
        document.removeEventListener('mouseup', handleResizeEnd);
      };
    }
    return undefined;
  }, [resizingTabId, handleResizeMove, handleResizeEnd]);

  // Handle iframe load complete
  const handleIframeLoad = useCallback((tabId: string) => {
    setLoadingTabs((prev) => {
      const newSet = new Set(prev);
      newSet.delete(tabId);
      return newSet;
    });
  }, []);

  // Navigate to usage page
  const goToUsage = useCallback(() => {
    navigate('/work/usage');
  }, [navigate]);

  if (isLoading || isQuotaLoading) {
    // Get loading message based on stage
    const getLoadingMessage = (): string => {
      switch (loadingStage) {
        case 'loadingConfig':
          return t('loadingWorkspaceConfig', language) || 'Loading workspace configuration...';
        case 'startingWorkspace':
          return t('startingWorkspaceInstance', language) || 'Starting your workspace instance...';
        case 'ready':
          return t('workspaceReady', language) || 'Workspace ready!';
        default:
          return t('loading', language);
      }
    };

    // Show progress indicator for workspace startup
    const showProgress = loadingStage === 'startingWorkspace';

    return (
      <div className="workspace-loading d-flex align-items-center justify-content-center h-100">
        <div className="text-center">
          <div className="spinner-border text-primary mb-3" role="status">
            <span className="visually-hidden">{t('loading', language)}</span>
          </div>
          <h5 className="mb-2">{getLoadingMessage()}</h5>
          {showProgress && (
            <p className="text-muted small mb-3">
              {t('workspaceStartupNote', language) || 'This may take a few seconds on first visit'}
            </p>
          )}
          <div className="progress-steps mt-3">
            <div className={`progress-step ${loadingStage === 'loadingConfig' || loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}>
              <i className="bi bi-check-circle-fill" />
              <span>{t('loadingConfig', language) || 'Load config'}</span>
            </div>
            <div className={`progress-step ${loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}>
              <i className={`bi ${loadingStage === 'startingWorkspace' ? 'bi-arrow-repeat spin' : 'bi-check-circle-fill'}`} />
              <span>{t('startingInstance', language) || 'Start instance'}</span>
            </div>
            <div className={`progress-step ${loadingStage === 'ready' ? 'active' : ''}`}>
              <i className="bi bi-check-circle-fill" />
              <span>{t('ready', language) || 'Ready'}</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return <Error message={error} />;
  }

  if (!config?.enabled) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-tools fs-1 text-muted" />
          <h4 className="mt-3">{t('workspaceNotConfigured', language)}</h4>
          <p className="text-muted">{t('workspaceNotConfiguredHelp', language)}</p>
        </div>
      </div>
    );
  }

  // In multi-user mode, check if user webui is available
  if (config.multi_user_mode && !userWebUI?.success) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-exclamation-circle fs-1 text-warning" />
          <h4 className="mt-3">{t('workspaceUnavailable', language)}</h4>
          <p className="text-muted">{userWebUI?.error || t('workspaceUnavailableHelp', language)}</p>
          <Button variant="primary" onClick={() => window.location.reload()}>
            <i className="bi bi-arrow-clockwise me-2" />
            {t('retry', language)}
          </Button>
        </div>
      </div>
    );
  }

  const effectiveUrl = getEffectiveUrl();
  if (!effectiveUrl) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-tools fs-1 text-muted" />
          <h4 className="mt-3">{t('workspaceNotConfigured', language)}</h4>
          <p className="text-muted">{t('workspaceNotConfiguredHelp', language)}</p>
        </div>
      </div>
    );
  }

  // Check if quota is exceeded
  const isQuotaExceeded = quotaStatus?.over_quota?.any ?? false;

  // Keyboard shortcut for switching tabs (Cmd/Ctrl + 1-9)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Check if the key is a digit (1-9)
      if (e.key >= '1' && e.key <= '9') {
        // Check modifier key: Cmd on Mac, Ctrl on Windows/Linux
        const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
        const modifierPressed = isMac ? e.metaKey : e.ctrlKey;

        if (modifierPressed) {
          e.preventDefault();

          // Calculate tab index (1 -> index 0, 2 -> index 1, etc.)
          const tabIndex = parseInt(e.key) - 1;

          // Only switch if the tab exists
          if (tabIndex < tabs.length) {
            const targetTab = tabs[tabIndex];
            if (targetTab && targetTab.id !== activeTabId) {
              switchTab(targetTab.id);
            }
          }
        }
      }
    };

    // Only add listener when there are tabs and workspace is not in quota exceeded state
    if (tabs.length > 0 && !isQuotaExceeded) {
      window.addEventListener('keydown', handleKeyDown);
      return () => window.removeEventListener('keydown', handleKeyDown);
    }

    return undefined;
  }, [tabs, activeTabId, switchTab, isQuotaExceeded]);

  // Render quota exceeded message
  if (isQuotaExceeded) {
    return (
      <div className="workspace h-100 d-flex flex-column">
        {/* Page Header */}
        <div className="page-header mb-3 px-3 pt-3">
          <h2>{t('workspace', language)}</h2>
        </div>

        {/* Quota Exceeded Warning */}
        <div className="flex-grow-1 d-flex align-items-center justify-content-center px-3">
          <Card className="text-center" style={{ maxWidth: '500px' }}>
            <div className="py-4">
              <i className="bi bi-exclamation-triangle-fill text-warning fs-1 mb-3" />
              <h4 className="text-danger mb-3">{t('quotaExceeded', language)}</h4>
              <p className="text-muted mb-4">
                {quotaStatus?.over_quota.daily_request && (
                  <span className="d-block">{t('dailyRequestQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.monthly_request && (
                  <span className="d-block">{t('monthlyRequestQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.daily_token && (
                  <span className="d-block">{t('dailyTokenQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.monthly_token && (
                  <span className="d-block">{t('monthlyTokenQuotaExceeded', language)}</span>
                )}
              </p>
              <p className="text-muted small mb-4">
                {t('quotaLimitsHelpDesc', language)}
              </p>
              <div className="d-flex gap-2 justify-content-center">
                <Button variant="outline-primary" onClick={goToUsage}>
                  <i className="bi bi-bar-chart me-2" />
                  {t('myUsage', language)}
                </Button>
                <Button variant="primary" onClick={checkQuota}>
                  <i className="bi bi-arrow-clockwise me-2" />
                  {t('retry', language)}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className={cn('workspace h-100 d-flex flex-column', workspaceFullscreen && 'fullscreen-mode')}>
      {/* Page Header - Hidden in fullscreen */}
      <div className={cn('page-header mb-3 px-3 pt-3 d-flex align-items-center', workspaceFullscreen && 'd-none')}>
        <div className="d-flex align-items-center flex-grow-1">
          <h2>{t('workspace', language)}</h2>
          {config.multi_user_mode && userWebUI?.system_account && (
            <small className="text-muted ms-2">
              ({userWebUI.system_account})
            </small>
          )}
        </div>
        <div className="d-flex align-items-center gap-2">
          {/* Tab notifications toggle */}
          <button
            className={cn(
              'btn btn-sm',
              enableTabNotifications 
                ? 'btn-outline-primary' 
                : 'btn-outline-secondary'
            )}
            onClick={toggleTabNotifications}
            title={enableTabNotifications ? t('disableTabNotifications', language) || 'Disable tab notifications' : t('enableTabNotifications', language) || 'Enable tab notifications'}
          >
            <i className={cn('bi me-1', enableTabNotifications ? 'bi-bell-fill' : 'bi-bell-slash')} />
            <span className="d-none d-sm-inline">
              {enableTabNotifications ? t('tabNotificationsOn', language) || 'Notifications On' : t('tabNotificationsOff', language) || 'Off'}
            </span>
          </button>
          {/* Fullscreen toggle button */}
          <button
            className="btn btn-sm btn-outline-secondary fullscreen-toggle-btn"
            onClick={() => toggleWorkspaceFullscreen(false, false)}
            title={workspaceFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)}
          >
            <i className={cn('bi me-1', workspaceFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen')} />
            {workspaceFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)}
          </button>
        </div>
      </div>
      {/* Tab Bar */}
      {tabs.length > 0 && (
        <div
          className={cn('workspace-tabs d-flex align-items-center border-bottom', workspaceFullscreen ? 'bg-white fullscreen-tabs' : 'bg-light')}
          style={{ minHeight: '40px' }}
        >
          {/* Tabs */}
          <div className="d-flex flex-grow-1" style={{ overflowX: 'auto', overflowY: 'hidden' }}>
            {tabs.map((tab) => {
              const tabWidth = tabWidths[tab.id] || 180;
              return (
                <div
                  key={tab.id}
                  data-tab-id={tab.id}
                  className={cn(
                    'workspace-tab d-flex align-items-center px-2 py-2 cursor-pointer',
                    'border-end position-relative',
                    activeTabId === tab.id && 'active bg-white'
                  )}
                  onClick={() => switchTab(tab.id)}
                  style={{
                    width: `${tabWidth}px`,
                    flexShrink: 0,
                    userSelect: 'none',
                  }}
                >
                  <i className={cn(
                    'bi me-2 flex-shrink-0',
                    tab.waitingForUser ? 'bi-bell-fill text-warning' : 'bi-chat-dots text-muted'
                  )} />
                  <span className={cn(
                    'text-truncate small flex-grow-1',
                    tab.waitingForUser && 'fw-semibold'
                  )} style={{ minWidth: 0 }}>
                    {tab.title}
                  </span>
                  {/* Waiting indicator badge */}
                  {tab.waitingForUser && activeTabId !== tab.id && (
                    <span className={cn(
                      'waiting-badge badge',
                      tab.waitingType === 'permission' && 'bg-danger',
                      tab.waitingType === 'plan' && 'bg-warning',
                      tab.waitingType === 'input' && 'bg-info'
                    )} style={{ 
                      fontSize: '0.65rem', 
                      padding: '0.2rem 0.4rem', 
                      marginLeft: '0.25rem',
                      borderRadius: '50%',
                      minWidth: '1.2rem',
                      height: '1.2rem',
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}>
                      {tab.waitingType === 'permission' && '!'}
                      {tab.waitingType === 'plan' && '⏳'}
                      {tab.waitingType === 'input' && '●'}
                    </span>
                  )}
                  <div className="tab-actions d-flex align-items-center">
                    <button
                      className="btn btn-sm btn-link p-0 text-muted tab-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRenameTab(tab.id, e);
                      }}
                      title={t('renameSession', language)}
                      tabIndex={-1}
                    >
                      <i className="bi bi-pencil" />
                    </button>
                    {tabs.length > 1 && (
                      <button
                        className="btn btn-sm btn-link p-0 text-muted tab-action-btn"
                        onClick={(e) => closeTab(tab.id, e)}
                        title={t('close', language)}
                        tabIndex={-1}
                      >
                        <i className="bi bi-x" />
                      </button>
                    )}
                  </div>
                  {/* Resize handle */}
                  <div
                    className="tab-resize-handle"
                    onMouseDown={(e) => handleResizeStart(tab.id, e)}
                    title="Drag to resize"
                  />
                </div>
              );
            })}
            
            {/* New Tab Button - placed right after the last tab */}
            <button
              className="btn btn-sm btn-link px-3 py-2 text-muted workspace-new-tab-btn"
              onClick={() => createNewTab()}
              title={t('newSession', language)}
              style={{
                flexShrink: 0,
                borderLeft: '1px solid rgba(0,0,0,0.1)',
              }}
            >
              <i className="bi bi-plus-lg" />
            </button>
          </div>

          {/* Exit Fullscreen Button - Only show in fullscreen mode */}
          {workspaceFullscreen && (
            <button
              className="btn btn-sm btn-outline-secondary px-3 py-1 mx-2"
              onClick={() => toggleWorkspaceFullscreen(false, false)}
              title={t('exitFullscreen', language)}
            >
              <i className="bi bi-fullscreen-exit me-1" />
              {t('exitFullscreen', language)}
            </button>
          )}
        </div>
      )}

      {/* Tab Content */}
      <div className="workspace-content flex-grow-1 position-relative">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={cn(
              'position-absolute top-0 start-0 w-100 h-100',
              activeTabId === tab.id ? 'd-block' : 'd-none'
            )}
          >
            {/* Loading overlay */}
            {loadingTabs.has(tab.id) && (
              <div
                className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-light"
                style={{ zIndex: 10 }}
              >
                <div className="text-center">
                  <div className="spinner-border text-primary mb-3" role="status">
                    <span className="visually-hidden">{t('loading', language)}</span>
                  </div>
                  <p className="text-muted">{t('workspaceLoading', language)}</p>
                </div>
              </div>
            )}
            <iframe
              ref={(el) => {
                if (el) {
                  iframeRefs.current.set(tab.id, el);
                } else {
                  iframeRefs.current.delete(tab.id);
                }
              }}
              src={tab.url}
              title={`Workspace - ${tab.title}`}
              className="w-100 h-100"
              style={{ border: 'none' }}
              allow="clipboard-read; clipboard-write"
              onLoad={() => handleIframeLoad(tab.id)}
            />
          </div>
        ))}
      </div>

      {/* Rename Modal */}
      <Modal
        isOpen={showRenameModal}
        onClose={handleCancelRename}
        title={t('renameSession', language)}
        size="sm"
      >
        <div className="mb-3">
          <label htmlFor="rename-tab-input" className="form-label">
            {t('enterNewSessionName', language)}
          </label>
          <input
            id="rename-tab-input"
            type="text"
            className="form-control"
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleSaveRename();
              } else if (e.key === 'Escape') {
                handleCancelRename();
              }
            }}
            autoFocus
          />
        </div>
        <div className="d-flex justify-content-end gap-2">
          <button className="btn btn-secondary" onClick={handleCancelRename}>
            {t('cancel', language)}
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSaveRename}
            disabled={!renameValue.trim() || isRenaming}
          >
            {isRenaming ? t('loading', language) : t('save', language)}
          </button>
        </div>
      </Modal>

      {/* Styles */}
      <style>{`
        .workspace-tab {
          transition: background-color 0.15s ease;
        }
        .workspace-tab:hover {
          background-color: rgba(0, 0, 0, 0.05);
        }
        .workspace-tab.active {
          border-bottom: 2px solid var(--primary, #0d6efd);
          margin-bottom: -1px;
        }
        .workspace-tab.active::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          height: 2px;
          background: var(--primary, #0d6efd);
        }
        .workspace-tabs::-webkit-scrollbar {
          height: 4px;
        }
        .workspace-tabs::-webkit-scrollbar-thumb {
          background: #ccc;
          border-radius: 2px;
        }
        .workspace-tabs::-webkit-scrollbar-track {
          background: transparent;
        }
        /* Tab actions - show on hover or active */
        .tab-actions {
          opacity: 0;
          transition: opacity 0.15s ease;
        }
        .workspace-tab:hover .tab-actions,
        .workspace-tab.active .tab-actions {
          opacity: 1;
        }
        /* Tab action buttons - compact size */
        .tab-action-btn {
          line-height: 1;
          min-width: 20px;
          height: 20px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .tab-action-btn i {
          font-size: 0.8rem;
        }
        /* Tab resize handle */
        .tab-resize-handle {
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          width: 8px;
          cursor: col-resize;
          opacity: 0;
          transition: opacity 0.15s ease;
          border-right: 2px solid transparent;
        }
        .workspace-tab:hover .tab-resize-handle,
        .workspace-tab.active .tab-resize-handle {
          opacity: 0.5;
        }
        .tab-resize-handle:hover {
          opacity: 1;
          border-right-color: var(--primary, #0d6efd);
        }
        /* New tab button - matches tab style */
        .workspace-new-tab-btn {
          transition: background-color 0.15s ease;
        }
        .workspace-new-tab-btn:hover {
          background-color: rgba(0, 0, 0, 0.05) !important;
        }
        .workspace-new-tab-btn i {
          font-size: 1rem;
        }
        /* Loading progress steps */
        .workspace-loading {
          padding: 20px;
        }
        .progress-steps {
          display: flex;
          justify-content: center;
          gap: 20px;
        }
        .progress-step {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 8px;
          background: rgba(0, 0, 0, 0.05);
          opacity: 0.5;
          transition: opacity 0.3s ease, background-color 0.3s ease;
        }
        .progress-step.active {
          opacity: 1;
          background: rgba(13, 110, 253, 0.1);
        }
        .progress-step i {
          font-size: 1.2rem;
        }
        .progress-step.active i.bi-check-circle-fill {
          color: #28a745;
        }
        .progress-step.active i.bi-arrow-repeat {
          color: #0d6efd;
        }
        .progress-step span {
          font-size: 0.85rem;
        }
        .spin {
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};

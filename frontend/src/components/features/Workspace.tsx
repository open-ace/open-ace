/* eslint-disable @typescript-eslint/no-non-null-assertion, @typescript-eslint/no-explicit-any, react-hooks/exhaustive-deps */
/**
 * Workspace Component - AI workspace with iframe embedding and tab support
 *
 * Features:
 * - Multiple tabs for different conversations
 * - Each tab has its own iframe
 * - Support for creating new tabs via URL parameter
 * - Quota checking - disables workspace when quota exceeded
 * - Multi-user mode support with per-user webui instances
 * - Workspace state persistence (Issue #65): Tabs state is saved to localStorage
 *   and restored when returning to workspace after navigating away
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  workspaceApi,
  type WorkspaceConfig,
  type UserWebUIResponse,
  type RemoteProject,
} from '@/api';
import { requestApi, type QuotaStatusResponse } from '@/api/request';
import { sessionsApi } from '@/api/sessions';
import {
  useLanguage,
  useTheme,
  useAppStore,
  useWorkspaceFullscreen,
  useEnableTabNotifications,
  useWorkspaceTabs,
  useWorkspaceActiveTabId,
  useSetWorkspaceActiveTabId,
  useAddWorkspaceTab,
  useUpdateWorkspaceTab,
  useRemoveWorkspaceTab,
  useWorkspaceTabsOrder,
  useSetWorkspaceTabsOrder,
  type WorkspaceTab as StoreWorkspaceTab,
} from '@/store';
import { t } from '@/i18n';
import { Error, Button, Card, useToast, Modal } from '@/components/common';
import { NewSessionModal } from '@/components/work/NewSessionModal';
import { TerminalTab } from '@/components/features/TerminalTab';
import { remoteApi } from '@/api/remote';
import { cn } from '@/utils';

/**
 * Extended WorkspaceTab for local use (includes URL which is generated at runtime)
 * The base WorkspaceTab from store is persisted to localStorage without URL
 * URL is regenerated when restoring tabs based on sessionId, encodedProjectName, toolName
 */
interface WorkspaceTab extends StoreWorkspaceTab {
  url: string; // Runtime-generated URL for iframe
  token: string; // Token for authentication (runtime)
}

// Generate unique tab ID
const generateTabId = () => `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

// Quota check interval (5 minutes)
const QUOTA_CHECK_INTERVAL = 5 * 60 * 1000;

// Activity heartbeat interval (2 minutes)
const ACTIVITY_HEARTBEAT_INTERVAL = 2 * 60 * 1000;

export const Workspace: React.FC = () => {
  const language = useLanguage();
  const theme = useTheme();
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

  // Local state for tabs with runtime-generated URL (extended from store state)
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  const [tabsOrder, setTabsOrder] = useState<string[]>([]); // Visual order for drag sort (Issue #1470)
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set());
  const [renameTabId, setRenameTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [showRenameModal, setShowRenameModal] = useState(false);
  const [showNewSessionModal, setShowNewSessionModal] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [remoteCloseTabId, setRemoteCloseTabId] = useState<string | null>(null);
  const [isStoppingRemote, setIsStoppingRemote] = useState(false);
  const [tabWidths, setTabWidths] = useState<Record<string, number>>({});
  const [resizingTabId, setResizingTabId] = useState<string | null>(null);
  const [draggedTabId, setDraggedTabId] = useState<string | null>(null);
  const [dragOverTabId, setDragOverTabId] = useState<string | null>(null);

  // Flag to track if tabs have been initialized (Issue #65)
  const [tabsInitialized, setTabsInitialized] = useState(false);

  // Remote projects list (Issue #417: Populate "Your Projects" for remote workspace)
  const [remoteProjects, setRemoteProjects] = useState<RemoteProject[]>([]);

  // State for switch project request from iframe (Issue #229)
  const [switchProjectRequest, setSwitchProjectRequest] = useState<{
    workspaceType: 'local' | 'remote';
    machineId?: string;
  } | null>(null);

  // Tab notifications setting
  const enableTabNotifications = useEnableTabNotifications();

  // Refs for iframe elements (to send focus messages)
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map());

  // Workspace tabs state from store (Issue #65)
  const storedTabs = useWorkspaceTabs();
  const storedActiveTabId = useWorkspaceActiveTabId();
  const storedTabsOrder = useWorkspaceTabsOrder(); // Issue #1470

  // Use stable action selectors (fixes infinite loop)
  const setStoredActiveTabId = useSetWorkspaceActiveTabId();
  const addStoredTab = useAddWorkspaceTab();
  const updateStoredTab = useUpdateWorkspaceTab();
  const removeStoredTab = useRemoveWorkspaceTab();
  const setStoredTabsOrder = useSetWorkspaceTabsOrder(); // Issue #1470

  // Issue #1470: Compute ordered tabs for visual display (Tab Bar only)
  // Tab Content uses original tabs order to prevent iframe re-creation
  const orderedTabs = useMemo(() => {
    if (tabsOrder.length === 0) return tabs;
    // Sort tabs by tabsOrder, then append any tabs not in order (new tabs)
    const ordered = tabsOrder
      .map((id) => tabs.find((t) => t.id === id))
      .filter((tab): tab is WorkspaceTab => tab !== undefined);
    // Add any new tabs that aren't in tabsOrder yet
    const orderedIds = new Set(tabsOrder);
    const newTabs = tabs.filter((t) => !orderedIds.has(t.id));
    return [...ordered, ...newTabs];
  }, [tabs, tabsOrder]);

  // Shared terminal proxy polling helper
  const pollTerminalProxy = useCallback(
    async (tabId: string, terminalId: string, machineId: string, maxAttempts: number = 30) => {
      const poll = async (attempt: number) => {
        if (terminalPollCancelRefs.current.get(tabId)) return;
        if (attempt > maxAttempts) {
          toast.error(
            t('terminalError', language) || 'Terminal Error',
            'Timed out waiting for WebSocket proxy'
          );
          return;
        }
        if (attempt > 0) await new Promise((r) => setTimeout(r, 1000));
        if (terminalPollCancelRefs.current.get(tabId)) return;
        try {
          const status = await remoteApi.getTerminalStatus(terminalId, machineId);
          const wsUrl = status.terminal.ws_url ?? '';
          if (status.terminal.status === 'running' && wsUrl && status.terminal.token) {
            setTabs((prev) =>
              prev.map((t) =>
                t.id === tabId
                  ? {
                      ...t,
                      terminalWsUrl: status.terminal.ws_url!,
                      terminalToken: status.terminal.token!,
                    }
                  : t
              )
            );
            updateStoredTab(tabId, {
              terminalWsUrl: status.terminal.ws_url!,
              terminalToken: status.terminal.token!,
            });
          } else if (status.terminal.status === 'error') {
            toast.error(
              t('terminalError', language) || 'Terminal Error',
              status.terminal.error ?? 'Failed to start terminal'
            );
          } else {
            poll(attempt + 1);
          }
        } catch {
          poll(attempt + 1);
        }
      };
      terminalPollCancelRefs.current.delete(tabId);
      poll(0);
    },
    [language, t, toast, updateStoredTab]
  );

  // Track terminal polling that should be cancelled on tab close
  const terminalPollCancelRefs = useRef<Map<string, boolean>>(new Map());

  // Track terminal attach attempts (to avoid duplicate attach calls)
  const terminalAttachAttemptedRefs = useRef<Map<string, boolean>>(new Map());

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

        // Get user-specific URL (needed for token and openace_url in both modes)
        if (workspaceConfig.enabled) {
          // Check if we only need terminal — skip webui startup if so
          const wsType = searchParams.get('workspaceType');
          const hasOnlyTerminalParams = wsType === 'terminal' && searchParams.get('terminalId');

          if (!hasOnlyTerminalParams) {
            setLoadingStage('startingWorkspace');
            try {
              const userWebUIResponse = await workspaceApi.getUserWebUIUrl();
              if (userWebUIResponse.success) {
                setUserWebUI(userWebUIResponse);
              }
            } catch {
              // In single-user mode, token may not be critical for local tabs
            }
          }
          setLoadingStage('ready');
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

  // Fetch remote projects list (Issue #417)
  useEffect(() => {
    const fetchRemoteProjects = async () => {
      try {
        const response = await workspaceApi.getRemoteProjects();
        if (response.success && response.projects.length > 0) {
          setRemoteProjects(response.projects);
        }
      } catch (err) {
        console.error('Failed to fetch remote projects:', err);
      }
    };

    fetchRemoteProjects();
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

  // Theme sync: Send postMessage to all iframes when theme changes (Issue #104)
  useEffect(() => {
    if (!tabsInitialized || tabs.length === 0) return;

    // Send theme change message to all iframe tabs
    tabs.forEach((tab) => {
      const iframe = iframeRefs.current.get(tab.id);
      if (iframe?.contentWindow && iframe.src) {
        // Use iframe's actual origin for security (instead of '*')
        const targetOrigin = new URL(iframe.src).origin;
        iframe.contentWindow.postMessage({ type: 'openace-theme-change', theme }, targetOrigin);
      }
    });
  }, [theme, tabs, tabsInitialized]);

  // Language sync: Send postMessage to all iframes when language changes (Issue #1425)
  useEffect(() => {
    if (!tabsInitialized || tabs.length === 0) return;

    // Send language change message to all iframe tabs
    tabs.forEach((tab) => {
      const iframe = iframeRefs.current.get(tab.id);
      if (iframe?.contentWindow && iframe.src) {
        try {
          // Use iframe's actual origin for security (instead of '*')
          const targetOrigin = new URL(iframe.src).origin;
          iframe.contentWindow.postMessage(
            { type: 'openace-language-change', language },
            targetOrigin
          );
        } catch (error) {
          // Fallback to '*' origin if URL parsing fails (e.g., relative URLs)
          console.warn('[Language Sync] Failed to get iframe origin, using wildcard:', error);
          iframe.contentWindow.postMessage({ type: 'openace-language-change', language }, '*');
        }
      }
    });
  }, [language, tabs, tabsInitialized]);

  // Clear notification state for a tab (used when leaving/switching away from a tab)
  // Declared early so effects below can reference it
  const clearTabNotification = useCallback((tabId: string) => {
    const prevIframe = iframeRefs.current.get(tabId);
    if (prevIframe?.contentWindow) {
      prevIframe.contentWindow.postMessage({ type: 'openace-clear-notification-state' }, '*');
    }
    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === tabId ? { ...tab, waitingForUser: false, waitingType: null } : tab
      )
    );
    useAppStore.getState().updateWorkspaceTab(tabId, {
      waitingForUser: false,
      waitingType: null,
    });
  }, []);

  // Issue #1470: Sync tabsOrder to store for persistence
  useEffect(() => {
    if (tabsOrder.length > 0 && tabsInitialized) {
      setStoredTabsOrder(tabsOrder);
    }
  }, [tabsOrder, tabsInitialized, setStoredTabsOrder]);

  // Listen for fullscreen request from iframe (when user selects project and enters chat)
  // Also listen for session ID update from iframe (Issue #65)
  // Also listen for tab switch request from iframe (Issue #68)
  useEffect(() => {
    const handleIframeMessage = (event: MessageEvent) => {
      // Validate message type for fullscreen request
      // Issue #121: Only auto-fullscreen if setting is enabled
      if (event.data?.type === 'openace-enter-chat') {
        if (useAppStore.getState().autoFullscreenOnEnterChat) {
          useAppStore.getState().enterWorkspaceFullscreen(false, false);
        }
      }

      // Listen for session ID update from qwen-code-webui iframe (Issue #65)
      // This allows the iframe to inform the workspace about its current session
      if (event.data?.type === 'qwen-code-session-update') {
        const { sessionId, encodedProjectName, toolName, title, settings } = event.data;

        // Find the tab that sent this message by matching event.source to iframe contentWindow
        // This prevents session info from being incorrectly assigned to the wrong tab
        // when multiple iframes send updates simultaneously (Issue #916)
        let sourceTabId: string | null = null;
        if (event.source) {
          for (const [tabId, iframe] of iframeRefs.current.entries()) {
            if (iframe.contentWindow === event.source) {
              sourceTabId = tabId;
              break;
            }
          }
        }

        // Only process session update if we can identify the source tab
        if (sessionId && sourceTabId) {
          // Filter out generic "Conversation(xxx...)" titles from iframe
          const isGenericTitle = title && /^Conversation\([a-f0-9]+\.\.\.\)$/i.test(title);
          // Only update title if it's explicitly provided (not undefined) and not generic
          // Otherwise keep the existing title (e.g., "New Session")
          const updateData: Partial<StoreWorkspaceTab> = {
            sessionId,
            encodedProjectName,
            toolName,
            settings, // Save settings for tab restoration (Issue #70)
          };
          if (title && !isGenericTitle) {
            updateData.title = title;
          }
          // Fallback: use decoded project name if title is generic Conversation(xxx...)
          if (isGenericTitle && encodedProjectName) {
            updateData.title = decodeURIComponent(encodedProjectName);
          }
          useAppStore.getState().updateWorkspaceTab(sourceTabId, updateData);
          // Also update local tabs state
          const effectiveTitle =
            title && !isGenericTitle
              ? title
              : isGenericTitle && encodedProjectName
                ? decodeURIComponent(encodedProjectName)
                : undefined;
          setTabs((prev) =>
            prev.map((tab) =>
              tab.id === sourceTabId
                ? {
                    ...tab,
                    sessionId,
                    encodedProjectName,
                    toolName,
                    title: effectiveTitle ?? tab.title,
                    settings,
                  }
                : tab
            )
          );
        }
      }

      // Listen for tab notification from qwen-code-webui iframe
      if (event.data?.type === 'qwen-code-tab-notification') {
        const { isWaiting, waitingType } = event.data;

        // Only update if tab notifications are enabled
        if (enableTabNotifications) {
          // Find the tab that sent this message by matching event.source to iframe contentWindow
          let sourceTabId: string | null = null;
          if (event.source) {
            for (const [tabId, iframe] of iframeRefs.current.entries()) {
              if (iframe.contentWindow === event.source) {
                sourceTabId = tabId;
                break;
              }
            }
          }

          // Only process notification if we can identify the source tab
          // Do NOT use fallback to activeTabId - this prevents notifications from being
          // incorrectly assigned to the current active tab when source cannot be matched
          if (sourceTabId) {
            setTabs((prev) =>
              prev.map((tab) =>
                tab.id === sourceTabId ? { ...tab, waitingForUser: isWaiting, waitingType } : tab
              )
            );
            // Update store
            useAppStore.getState().updateWorkspaceTab(sourceTabId, {
              waitingForUser: isWaiting,
              waitingType,
            });
          }
        }
      }

      // Listen for tab switch request from qwen-code-webui iframe (Issue #68)
      // When user presses Cmd/Ctrl+ArrowLeft/ArrowRight inside iframe, it sends this message
      if (event.data?.type === 'qwen-code-tab-switch-request') {
        const { direction } = event.data; // "prev" or "next"
        // Get current tabs and active tab from state
        setTabs((currentTabs) => {
          if (currentTabs.length <= 1) return currentTabs;

          const currentActiveTabId = useAppStore.getState().workspaceActiveTabId;
          const currentIndex = currentTabs.findIndex((tab) => tab.id === currentActiveTabId);

          // Calculate new index
          let newIndex: number;
          if (direction === 'prev') {
            newIndex = currentIndex <= 0 ? currentTabs.length - 1 : currentIndex - 1;
          } else {
            newIndex = currentIndex >= currentTabs.length - 1 ? 0 : currentIndex + 1;
          }

          const targetTab = currentTabs[newIndex];
          if (targetTab && targetTab.id !== currentActiveTabId) {
            // Clear notification state for the tab we're leaving
            clearTabNotification(currentActiveTabId);

            // Switch to the target tab
            setActiveTabId(targetTab.id);
            useAppStore.getState().setWorkspaceActiveTabId(targetTab.id);

            // Send focus message to iframe after tab switch
            setTimeout(() => {
              const iframe = iframeRefs.current.get(targetTab.id);
              if (iframe?.contentWindow) {
                iframe.contentWindow.postMessage({ type: 'openace-focus-input' }, '*');
                iframe.contentWindow.postMessage({ type: 'openace-clear-notification-state' }, '*');
              }
            }, 100);
          }
          return currentTabs; // Don't modify tabs, just use it for checking
        });
      }

      // Listen for ESC key forwarded from qwen-code-webui iframe (Issue #103)
      if (event.data?.type === 'qwen-code-esc-pressed' && workspaceFullscreen) {
        exitWorkspaceFullscreen();
      }

      // Listen for switch project request from qwen-code-webui iframe (Issue #229)
      // When user clicks "Switch project" button inside iframe while thinking,
      // create a new tab for project selection instead of interrupting current session
      if (event.data?.type === 'qwen-code-switch-project-request') {
        const { workspaceType, machineId } = event.data;
        // Set state to trigger tab creation in separate useEffect
        setSwitchProjectRequest({
          workspaceType: workspaceType ?? 'local',
          machineId: machineId ?? undefined,
        });
      }
    };

    window.addEventListener('message', handleIframeMessage);
    return () => window.removeEventListener('message', handleIframeMessage);
  }, [
    enableTabNotifications,
    language,
    workspaceFullscreen,
    exitWorkspaceFullscreen,
    clearTabNotification,
  ]);

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
      toast.warning(
        t('exitedFullscreenDueToQuotaTitle', language),
        t('exitedFullscreenDueToQuotaDesc', language)
      );
    }
  }, [quotaStatus?.over_quota?.any, workspaceFullscreen, exitWorkspaceFullscreen, language, toast]);

  // Get the effective URL for iframe
  const getEffectiveUrl = useCallback(
    (
      restoreSessionId?: string,
      encodedProjectName?: string,
      toolName?: string,
      settings?: { model?: string; useWebUI?: boolean; permissionMode?: string },
      remoteParams?: {
        workspaceType?: 'local' | 'remote';
        machineId?: string;
        machineName?: string;
      },
      resumeHint?: boolean
    ): string => {
      if (!config?.enabled) return '';

      // Helper to append parameter to URL
      const appendParam = (url: string, key: string, value: string | undefined) => {
        if (value === undefined) return url;
        const separator = url.includes('?') ? '&' : '?';
        return `${url}${separator}${key}=${encodeURIComponent(value)}`;
      };

      // Helper to append remote workspace parameters
      const appendRemoteParams = (url: string) => {
        if (!remoteParams?.workspaceType) return url;
        let result = appendParam(url, 'workspaceType', remoteParams.workspaceType);
        result = appendParam(result, 'machineId', remoteParams.machineId);
        result = appendParam(result, 'machineName', remoteParams.machineName);
        return result;
      };

      // Helper to append recent remote projects (Issue #417)
      const appendRecentProjects = (url: string) => {
        if (remoteProjects.length === 0) return url;
        // Use JSON format to avoid delimiter collision (paths may contain : or ,)
        // Format: JSON array of {path, name, machineId, machineName}
        const projectsParam = encodeURIComponent(
          JSON.stringify(
            remoteProjects.map((p) => ({
              path: p.project_path,
              name: p.encoded_project_name,
              machineId: p.machine_id,
              machineName: p.machine_name,
            }))
          )
        );
        return appendParam(url, 'recentProjects', projectsParam);
      };

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
        url = appendParam(url, 'lang', language);
        url = appendParam(url, 'theme', theme);
        // Add sessionId, encodedProjectName, and toolName if restoring a session
        if (restoreSessionId) {
          url = appendParam(url, 'sessionId', restoreSessionId);
        }
        if (encodedProjectName) {
          url = appendParam(url, 'encodedProjectName', encodedProjectName);
        }
        if (toolName) {
          url = appendParam(url, 'toolName', toolName);
        }
        // Add settings parameters (Issue #70)
        if (settings?.model) {
          url = appendParam(url, 'model', settings.model);
        }
        if (settings?.useWebUI !== undefined) {
          url = appendParam(url, 'useWebUI', String(settings.useWebUI));
        }
        if (settings?.permissionMode) {
          url = appendParam(url, 'permissionMode', settings.permissionMode);
        }
        // File changes panel visibility (Issue #144)
        const showPanel = useAppStore.getState().showFileChangesPanel;
        url = appendParam(url, 'showFileChangesPanel', String(showPanel));
        // Resume hint for CLI session (Issue #669)
        if (resumeHint) {
          url = appendParam(url, 'resumeHint', 'true');
        }
        // Remote workspace parameters
        url = appendRemoteParams(url);
        // Recent remote projects (Issue #417)
        url = appendRecentProjects(url);
        return url;
      }

      // Single-user mode: use configured URL (preserve port)
      // Note: Port should NOT be removed - WebUI runs on a specific port (e.g., 3100)
      // The backend handles hostname replacement via _replace_host_from_request if needed
      let url = config.url;
      // Add lang parameter for language sync
      const langSeparator = url.includes('?') ? '&' : '?';
      url = `${url}${langSeparator}lang=${encodeURIComponent(language)}&theme=${theme}`;
      // Add token and openace_url for authentication (same as multi-user mode)
      if (userWebUI?.success && userWebUI.token) {
        url = appendParam(url, 'token', userWebUI.token);
        if (userWebUI.openace_url) {
          url = appendParam(url, 'openace_url', userWebUI.openace_url);
        }
      }
      if (restoreSessionId) {
        url = appendParam(url, 'sessionId', restoreSessionId);
      }
      if (encodedProjectName) {
        url = appendParam(url, 'encodedProjectName', encodedProjectName);
      }
      if (toolName) {
        url = appendParam(url, 'toolName', toolName);
      }
      // Add settings parameters (Issue #70)
      if (settings?.model) {
        url = appendParam(url, 'model', settings.model);
      }
      if (settings?.useWebUI !== undefined) {
        url = appendParam(url, 'useWebUI', String(settings.useWebUI));
      }
      if (settings?.permissionMode) {
        url = appendParam(url, 'permissionMode', settings.permissionMode);
      }
      // File changes panel visibility (Issue #144)
      const showPanel = useAppStore.getState().showFileChangesPanel;
      url = appendParam(url, 'showFileChangesPanel', String(showPanel));
      // Resume hint for CLI session (Issue #669)
      if (resumeHint) {
        url = appendParam(url, 'resumeHint', 'true');
      }
      // Remote workspace parameters
      url = appendRemoteParams(url);
      // Recent remote projects (Issue #417)
      url = appendRecentProjects(url);
      return url;
    },
    [config, userWebUI, language, theme, remoteProjects]
  );

  // Initialize tabs when config is loaded (Issue #65: Restore from store if available)
  useEffect(() => {
    // Wait for config and userWebUI to be loaded
    if (!config?.enabled) return;

    // Wait for userWebUI to finish loading (it's loaded in both modes now)
    if (isLoading) return;

    // Skip if tabs already initialized
    if (tabsInitialized) return;

    // Check for session restore parameters from URL
    // API returns: /work/workspace?sessionId=xxx&encodedProjectName=yyy&toolName=zzz
    // Also support legacy: /work/workspace?restoreSession=xxx
    const urlSessionId = searchParams.get('sessionId');
    const restoreSession = searchParams.get('restoreSession');
    const urlEncodedProjectName = searchParams.get('encodedProjectName');
    const urlToolName = searchParams.get('toolName');
    // Settings from URL (Issue #70)
    const urlModel = searchParams.get('model');
    const urlUseWebUI = searchParams.get('useWebUI');
    const urlPermissionMode = searchParams.get('permissionMode');
    // Remote workspace params from URL
    const urlWorkspaceType = searchParams.get('workspaceType') as
      | 'local'
      | 'remote'
      | 'terminal'
      | null;
    const urlMachineId = searchParams.get('machineId');
    const urlMachineName = searchParams.get('machineName');
    const urlTerminalId = searchParams.get('terminalId');
    const urlResumeHint = searchParams.get('resumeHint') === 'true';
    const restoreSessionId = urlSessionId ?? restoreSession;

    // Determine if we should restore from store or create new
    // Priority: URL restore params > Store saved state > New session
    let initialTabs: WorkspaceTab[] = [];
    let initialActiveTabId = '';

    if (restoreSessionId) {
      // Case 1: URL restore params - create a single tab with the restore session
      // Build settings from URL params
      const urlSettings:
        | { model?: string; useWebUI?: boolean; permissionMode?: string }
        | undefined =
        urlModel || urlUseWebUI !== null || urlPermissionMode
          ? {
              model: urlModel ?? undefined,
              useWebUI: urlUseWebUI === 'true' ? true : urlUseWebUI === 'false' ? false : undefined,
              permissionMode: urlPermissionMode ?? undefined,
            }
          : undefined;

      // Build remote params from URL (only for local/remote, not terminal)
      const remoteParams:
        | { workspaceType?: 'local' | 'remote'; machineId?: string; machineName?: string }
        | undefined =
        urlWorkspaceType && urlWorkspaceType !== 'terminal'
          ? {
              workspaceType: urlWorkspaceType,
              machineId: urlMachineId ?? undefined,
              machineName: urlMachineName ?? undefined,
            }
          : undefined;

      // Handle terminal session restoration separately
      if (urlWorkspaceType === 'terminal' && urlTerminalId && urlMachineId) {
        // Create terminal tab
        const tab: WorkspaceTab = {
          id: generateTabId(),
          title: t('restoredSession', language),
          url: '', // Terminal tabs don't use iframe URL
          token: '',
          sessionId: restoreSessionId,
          tabType: 'terminal',
          terminalId: urlTerminalId,
          machineId: urlMachineId,
          machineName: urlMachineName ?? undefined,
          createdAt: Date.now(),
          waitingForUser: false,
          waitingType: null,
        };
        initialTabs = [tab];
        initialActiveTabId = tab.id;

        // Save to store
        addStoredTab({
          id: tab.id,
          title: tab.title,
          tabType: 'terminal',
          terminalId: urlTerminalId,
          machineId: urlMachineId,
          machineName: urlMachineName ?? undefined,
          createdAt: tab.createdAt,
          waitingForUser: false,
          waitingType: null,
        });
      } else {
        // Regular session (local or remote)
        const effectiveUrl = getEffectiveUrl(
          restoreSessionId,
          urlEncodedProjectName ?? undefined,
          urlToolName ?? undefined,
          urlSettings,
          remoteParams,
          urlResumeHint
        );
        if (effectiveUrl) {
          const tab: WorkspaceTab = {
            id: generateTabId(),
            title: t('restoredSession', language),
            url: effectiveUrl,
            token: userWebUI?.token ?? '',
            sessionId: restoreSessionId,
            encodedProjectName: urlEncodedProjectName ?? undefined,
            toolName: urlToolName ?? undefined,
            settings: urlSettings,
            workspaceType:
              urlWorkspaceType && urlWorkspaceType !== 'terminal' ? urlWorkspaceType : undefined,
            machineId: urlMachineId ?? undefined,
            machineName: urlMachineName ?? undefined,
            createdAt: Date.now(),
            waitingForUser: false,
            waitingType: null,
          };
          initialTabs = [tab];
          initialActiveTabId = tab.id;

          // Save to store (this replaces any previous stored tabs)
          addStoredTab({
            id: tab.id,
            title: tab.title,
            sessionId: tab.sessionId,
            encodedProjectName: tab.encodedProjectName,
            toolName: tab.toolName,
            settings: tab.settings,
            workspaceType: tab.workspaceType,
            machineId: tab.machineId,
            machineName: tab.machineName,
            createdAt: tab.createdAt,
            waitingForUser: tab.waitingForUser,
            waitingType: tab.waitingType,
          });
        }
      }

      // Clear the restore parameters after using it
      searchParams.delete('sessionId');
      searchParams.delete('restoreSession');
      searchParams.delete('encodedProjectName');
      searchParams.delete('toolName');
      searchParams.delete('workspaceType');
      searchParams.delete('machineId');
      searchParams.delete('machineName');
      searchParams.delete('terminalId');
      searchParams.delete('resumeHint');
      setSearchParams(searchParams, { replace: true });
    } else if (storedTabs.length > 0) {
      // Case 2: Restore from store - regenerate URLs for each tab
      initialTabs = storedTabs.map((storedTab) => {
        // Terminal tabs don't need URL regeneration
        if (storedTab.tabType === 'terminal') {
          return {
            ...storedTab,
            url: '',
            token: '',
            terminalWsUrl: '',
            terminalToken: '',
          };
        }

        // Regenerate URL based on sessionId if available
        // Include settings and remote params in URL for restoration
        const remoteParams = storedTab.workspaceType
          ? {
              workspaceType: storedTab.workspaceType,
              machineId: storedTab.machineId,
              machineName: storedTab.machineName,
            }
          : undefined;
        const effectiveUrl = storedTab.sessionId
          ? getEffectiveUrl(
              storedTab.sessionId,
              storedTab.encodedProjectName,
              storedTab.toolName,
              storedTab.settings,
              remoteParams
            )
          : getEffectiveUrl(undefined, undefined, undefined, undefined, remoteParams);

        return {
          ...storedTab,
          url: effectiveUrl ?? '',
          token: userWebUI?.token ?? '',
        };
      });

      // Use stored active tab ID if it exists in the restored tabs
      initialActiveTabId = storedTabs.find((t) => t.id === storedActiveTabId)
        ? storedActiveTabId
        : initialTabs.length > 0
          ? initialTabs[0].id
          : '';

      console.log('[Issue #65] Restored workspace tabs from store:', {
        tabsCount: initialTabs.length,
        activeTabId: initialActiveTabId,
      });
    } else {
      // Case 3: No stored state and no URL params - create a new session tab
      const effectiveUrl = getEffectiveUrl();
      if (effectiveUrl) {
        const tab: WorkspaceTab = {
          id: generateTabId(),
          title: t('newSession', language),
          url: effectiveUrl,
          token: userWebUI?.token ?? '',
          createdAt: Date.now(),
          waitingForUser: false,
          waitingType: null,
        };
        initialTabs = [tab];
        initialActiveTabId = tab.id;

        // Save new tab to store
        addStoredTab({
          id: tab.id,
          title: tab.title,
          sessionId: tab.sessionId,
          encodedProjectName: tab.encodedProjectName,
          toolName: tab.toolName,
          createdAt: tab.createdAt,
          waitingForUser: tab.waitingForUser,
          waitingType: tab.waitingType,
        });
      }
    }

    if (initialTabs.length > 0) {
      setTabs(initialTabs);
      setActiveTabId(initialActiveTabId);
      setStoredActiveTabId(initialActiveTabId);
      // Mark as loading
      setLoadingTabs(new Set(initialTabs.map((t) => t.id)));

      // Issue #1470: Initialize tabsOrder from store or use default order
      if (storedTabsOrder.length > 0) {
        // Filter storedTabsOrder to only include existing tabs
        const validOrder = storedTabsOrder.filter((id) => initialTabs.some((t) => t.id === id));
        // Add any new tabs that aren't in the order
        const orderedIds = new Set(validOrder);
        const newTabIds = initialTabs.filter((t) => !orderedIds.has(t.id)).map((t) => t.id);
        setTabsOrder([...validOrder, ...newTabIds]);
      } else {
        // Default order: all tabs in their initial order
        setTabsOrder(initialTabs.map((t) => t.id));
      }

      setTabsInitialized(true);
    }
  }, [
    config,

    userWebUI,
    tabsInitialized,
    storedTabs,
    storedActiveTabId,
    storedTabsOrder, // Issue #1470
    language,
    getEffectiveUrl,
    searchParams,
    setSearchParams,

    addStoredTab,

    setStoredActiveTabId,
  ]);

  // Handle URL parameter for creating new tab
  useEffect(() => {
    const newTab = searchParams.get('newTab');
    const effectiveUrl = getEffectiveUrl();
    if (newTab === 'true' && config?.enabled && effectiveUrl && tabsInitialized) {
      // Read remote params from URL
      const remoteParams = {
        workspaceType: searchParams.get('workspaceType') as 'local' | 'remote' | null,
        machineId: searchParams.get('machineId'),
        machineName: searchParams.get('machineName'),
      };
      const sessionId = searchParams.get('sessionId') ?? undefined;

      // Clear the URL parameters
      searchParams.delete('newTab');
      searchParams.delete('workspaceType');
      searchParams.delete('machineId');
      searchParams.delete('machineName');
      searchParams.delete('sessionId');
      setSearchParams(searchParams, { replace: true });

      // Create new tab with remote params
      const rp = remoteParams.workspaceType
        ? {
            workspaceType: remoteParams.workspaceType,
            machineId: remoteParams.machineId ?? undefined,
            machineName: remoteParams.machineName ?? undefined,
          }
        : undefined;
      createNewTab(sessionId, rp);
    }
  }, [searchParams, config, getEffectiveUrl, tabsInitialized]);

  // Handle URL params for creating terminal tab directly
  useEffect(() => {
    const terminalParam = searchParams.get('workspaceType');
    if (terminalParam === 'terminal' && tabsInitialized && config?.enabled) {
      const machineId = searchParams.get('machineId');
      const machineName = searchParams.get('machineName') ?? 'Terminal';

      if (machineId) {
        // Clear URL params
        searchParams.delete('workspaceType');
        searchParams.delete('machineId');
        searchParams.delete('machineName');
        setSearchParams(searchParams, { replace: true });

        // Create terminal
        console.log('[Terminal] Creating terminal from URL params:', { machineId, machineName });
        remoteApi
          .startTerminal({
            machine_id: machineId,
            work_dir: '',
          })
          .then((result) => {
            if (result.success && result.terminal) {
              const tabId = generateTabId();
              const terminalId = result.terminal.terminal_id;
              const initialWsUrl = result.terminal.ws_url || '';
              const initialToken = result.terminal.token || '';

              console.log('[Terminal] Terminal created:', {
                terminalId,
                wsUrl: initialWsUrl,
                hasWsUrl: Boolean(initialWsUrl),
              });

              const newTab: WorkspaceTab = {
                id: tabId,
                title: `Terminal - ${machineName}`,
                tabType: 'terminal',
                url: '',
                token: '',
                createdAt: Date.now(),
                waitingForUser: false,
                waitingType: null,
                workspaceType: 'remote',
                machineId,
                machineName,
                terminalId,
                terminalWsUrl: initialWsUrl,
                terminalToken: initialToken,
              };
              setTabs((prev) => [...prev, newTab]);
              // Clear notification state for the previously active tab
              const prevActiveTabId = useAppStore.getState().workspaceActiveTabId;
              if (prevActiveTabId) {
                clearTabNotification(prevActiveTabId);
              }
              setActiveTabId(tabId);
              setStoredActiveTabId(tabId);

              // Poll for terminal status until WebSocket proxy is ready
              pollTerminalProxy(tabId, terminalId, machineId);
            } else {
              toast.error(t('terminalCreateFailed', language) || 'Failed to create terminal');
            }
          })
          .catch((err) => {
            console.error('[Terminal] Failed to create terminal:', err);
            toast.error(t('terminalCreateFailed', language) || 'Failed to create terminal');
          });
      }
    }
  }, [
    searchParams,
    tabsInitialized,
    config,
    toast,
    language,
    setSearchParams,
    updateStoredTab,
    clearTabNotification,
    pollTerminalProxy,
    setStoredActiveTabId,
  ]);

  // Attach to existing terminal tabs (restored from localStorage)
  // This handles browser refresh: reconnect to the same terminal session
  useEffect(() => {
    if (!tabsInitialized || !config?.enabled) return;

    // Find terminal tabs that need attach
    const terminalTabs = tabs.filter(
      (t) => t.tabType === 'terminal' && t.terminalId && t.machineId
    );

    for (const tab of terminalTabs) {
      const tabId = tab.id;
      const terminalId = tab.terminalId!;
      const machineId = tab.machineId!;

      // Skip if already attempted attach
      if (terminalAttachAttemptedRefs.current.get(terminalId)) continue;
      terminalAttachAttemptedRefs.current.set(terminalId, true);

      console.log('[Terminal] Attaching to existing terminal:', {
        tabId,
        terminalId,
        machineId,
      });

      // Call attach API to get current ws_url and token
      remoteApi
        .attachTerminal({
          terminal_id: terminalId,
          machine_id: machineId,
        })
        .then((result) => {
          if (result.success && result.terminal?.status === 'pending') {
            // Terminal exists, poll for status
            const pollForAttach = async (attempt: number) => {
              if (terminalPollCancelRefs.current.get(tabId)) return;
              if (attempt > 30) {
                console.log('[Terminal] Attach polling timed out');
                return;
              }
              if (attempt > 0) {
                await new Promise((r) => setTimeout(r, 1000));
              }
              if (terminalPollCancelRefs.current.get(tabId)) return;
              try {
                const status = await remoteApi.getTerminalStatus(terminalId, machineId);
                const wsUrl = status.terminal.ws_url ?? '';

                console.log('[Terminal] Attach poll:', {
                  attempt,
                  status: status.terminal.status,
                  wsUrl,
                });

                if (status.terminal.status === 'running' && wsUrl && status.terminal.token) {
                  // Update tab with fresh ws_url and token
                  setTabs((prev) =>
                    prev.map((t) =>
                      t.id === tabId
                        ? {
                            ...t,
                            terminalWsUrl: status.terminal.ws_url!,
                            terminalToken: status.terminal.token!,
                          }
                        : t
                    )
                  );
                  updateStoredTab(tabId, {
                    terminalWsUrl: status.terminal.ws_url!,
                    terminalToken: status.terminal.token!,
                  });
                  console.log('[Terminal] Attached successfully:', wsUrl);
                } else if (status.terminal.status === 'error') {
                  // Terminal exited, need to show error
                  setTabs((prev) =>
                    prev.map((t) =>
                      t.id === tabId ? { ...t, terminalWsUrl: '', terminalToken: '' } : t
                    )
                  );
                } else if (status.terminal.status === 'not_found') {
                  // Terminal no longer exists on agent - create a new one
                  console.log('[Terminal] Terminal not found on agent, creating new');
                  try {
                    const startResult = await remoteApi.startTerminal({
                      machine_id: machineId,
                      work_dir: '',
                    });
                    if (startResult.success && startResult.terminal) {
                      const newTerminalId = startResult.terminal.terminal_id;
                      const newWsUrl = startResult.terminal.ws_url || '';
                      const newToken = startResult.terminal.token || '';

                      setTabs((prev) =>
                        prev.map((t) =>
                          t.id === tabId
                            ? {
                                ...t,
                                terminalId: newTerminalId,
                                terminalWsUrl: newWsUrl,
                                terminalToken: newToken,
                              }
                            : t
                        )
                      );
                      updateStoredTab(tabId, {
                        terminalId: newTerminalId,
                        terminalWsUrl: newWsUrl,
                        terminalToken: newToken,
                      } as any);

                      // Poll for proxy URL
                      const pollNewTerminal = async (pollAttempt: number) => {
                        if (pollAttempt > 30) return;
                        if (terminalPollCancelRefs.current.get(tabId)) return;
                        if (pollAttempt > 0) await new Promise((r) => setTimeout(r, 1000));
                        if (terminalPollCancelRefs.current.get(tabId)) return;
                        try {
                          const newStatus = await remoteApi.getTerminalStatus(
                            newTerminalId,
                            machineId
                          );
                          const newWs = newStatus.terminal.ws_url ?? '';
                          if (
                            newStatus.terminal.status === 'running' &&
                            newWs &&
                            newStatus.terminal.token
                          ) {
                            setTabs((prev) =>
                              prev.map((t) =>
                                t.id === tabId
                                  ? {
                                      ...t,
                                      terminalWsUrl: newStatus.terminal.ws_url!,
                                      terminalToken: newStatus.terminal.token!,
                                    }
                                  : t
                              )
                            );
                            updateStoredTab(tabId, {
                              terminalWsUrl: newStatus.terminal.ws_url!,
                              terminalToken: newStatus.terminal.token!,
                            });
                          } else if (newStatus.terminal.status !== 'error') {
                            pollNewTerminal(pollAttempt + 1);
                          }
                        } catch {
                          pollNewTerminal(pollAttempt + 1);
                        }
                      };
                      pollNewTerminal(0);
                    }
                  } catch (startErr) {
                    console.error('[Terminal] Failed to create new terminal:', startErr);
                  }
                } else {
                  pollForAttach(attempt + 1);
                }
              } catch {
                pollForAttach(attempt + 1);
              }
            };
            terminalPollCancelRefs.current.delete(tabId);
            pollForAttach(0);
          } else {
            // Terminal not found, clear ws_url
            console.log('[Terminal] Attach failed, terminal not found');
            setTabs((prev) =>
              prev.map((t) => (t.id === tabId ? { ...t, terminalWsUrl: '', terminalToken: '' } : t))
            );
          }
        })
        .catch((err) => {
          console.error('[Terminal] Attach failed:', err);
          // Keep stored values as fallback
        });
    }
  }, [tabs, tabsInitialized, config, setTabs, updateStoredTab]);

  // Create a new tab
  const createNewTab = useCallback(
    (
      restoreSessionId?: string,
      remoteParams?: {
        workspaceType?: 'local' | 'remote';
        machineId?: string;
        machineName?: string;
        sessionId?: string;
        projectPath?: string;
      }
    ) => {
      // Pass raw project path — getEffectiveUrl will URL-encode it.
      // ChatPage Strategy 1.5 decodes it, correctly handling hyphens in paths.
      const encodedProjectName = remoteParams?.projectPath ?? undefined;
      const effectiveUrl = getEffectiveUrl(
        restoreSessionId ?? remoteParams?.sessionId ?? undefined,
        encodedProjectName,
        undefined,
        undefined,
        remoteParams
      );
      if (!effectiveUrl) return;

      // Title: "Restored Session" only when restoring from session list, not new remote sessions
      const isNewRemote = remoteParams?.workspaceType === 'remote';
      const newTab: WorkspaceTab = {
        id: generateTabId(),
        title:
          restoreSessionId && !isNewRemote
            ? t('restoredSession', language)
            : t('newSession', language),
        url: effectiveUrl,
        token: userWebUI?.token ?? '',
        createdAt: Date.now(),
        waitingForUser: false,
        waitingType: null,
        sessionId: restoreSessionId ?? remoteParams?.sessionId,
        encodedProjectName,
        workspaceType: remoteParams?.workspaceType,
        machineId: remoteParams?.machineId,
        machineName: remoteParams?.machineName,
      };

      // Clear notification state for the previously active tab before switching
      const previousTabId = useAppStore.getState().workspaceActiveTabId;
      if (previousTabId) {
        clearTabNotification(previousTabId);
      }

      // Update local state
      setTabs((prev) => [...prev, newTab]);
      setTabsOrder((prev) => [...prev, newTab.id]); // Issue #1470: Add new tab to order
      setActiveTabId(newTab.id);

      // Update store (Issue #65)
      addStoredTab({
        id: newTab.id,
        title: newTab.title,
        sessionId: newTab.sessionId,
        encodedProjectName: newTab.encodedProjectName,
        toolName: newTab.toolName,
        createdAt: newTab.createdAt,
        waitingForUser: newTab.waitingForUser,
        waitingType: newTab.waitingType,
        workspaceType: newTab.workspaceType,
        machineId: newTab.machineId,
        machineName: newTab.machineName,
      });
      setStoredActiveTabId(newTab.id);
      // Issue #1470: Update tabsOrder in store
      setStoredTabsOrder((prev) => [...prev, newTab.id]);

      // Mark as loading
      setLoadingTabs((prev) => new Set(prev).add(newTab.id));
    },
    [
      getEffectiveUrl,
      userWebUI,
      language,
      addStoredTab,
      setStoredActiveTabId,
      clearTabNotification,
      setStoredTabsOrder,
    ]
  );

  // Handle switch project request from iframe (Issue #229)
  // Create a new tab for project selection without interrupting current session
  useEffect(() => {
    if (switchProjectRequest) {
      createNewTab(undefined, {
        workspaceType: switchProjectRequest.workspaceType,
        machineId: switchProjectRequest.machineId,
        machineName: undefined, // machineName will be set after project selection
      });
      // Clear the request after handling
      setSwitchProjectRequest(null);
    }
  }, [switchProjectRequest, createNewTab]);

  // Actually remove a tab (shared by closeTab and remote close confirmation)
  const doCloseTab = useCallback(
    (tabId: string) => {
      const isLastTab = tabs.length === 1;
      setTabs((prev) => {
        const newTabs = prev.filter((tab) => tab.id !== tabId);
        if (activeTabId === tabId && newTabs.length > 0) {
          const closedIndex = prev.findIndex((tab) => tab.id === tabId);
          const newActiveIndex = Math.min(closedIndex, newTabs.length - 1);
          setActiveTabId(newTabs[newActiveIndex].id);
        }
        return newTabs;
      });
      // Issue #1470: Remove from tabsOrder
      setTabsOrder((prev) => prev.filter((id) => id !== tabId));
      removeStoredTab(tabId);
      if (isLastTab) {
        createNewTab();
      }
    },
    [tabs.length, activeTabId, removeStoredTab, createNewTab]
  );

  // Close a tab
  const closeTab = useCallback(
    (tabId: string, e: React.MouseEvent) => {
      e.stopPropagation();

      const tab = tabs.find((t) => t.id === tabId);

      // For terminal tabs, stop the terminal and close
      if (tab?.tabType === 'terminal') {
        // Cancel any pending terminal status polling
        terminalPollCancelRefs.current.set(tabId, true);
        if (tab.terminalId && tab.machineId) {
          remoteApi
            .stopTerminal({
              terminal_id: tab.terminalId,
              machine_id: tab.machineId,
            })
            .catch((err) => console.error('Failed to stop terminal:', err));
        }
        doCloseTab(tabId);
        return;
      }

      // For remote workspace tabs, show confirmation dialog
      if (tab?.workspaceType === 'remote' && tab?.sessionId) {
        setRemoteCloseTabId(tabId);
        return;
      }

      doCloseTab(tabId);
    },
    [tabs, doCloseTab]
  );

  // Handle remote tab close with session stop
  const handleRemoteCloseStop = useCallback(async () => {
    if (!remoteCloseTabId) return;
    const tab = tabs.find((t) => t.id === remoteCloseTabId);
    setIsStoppingRemote(true);
    try {
      if (tab?.sessionId) {
        await remoteApi.stopSession(tab.sessionId);
        // Notify SessionList to refresh after session stopped successfully (Issue #358)
        window.postMessage({ type: 'openace-session-stopped' }, '*');
        toast.success(t('sessionStoppedSuccess', language));
      }
    } catch (err) {
      console.error('Failed to stop remote session:', err);
      toast.error(t('sessionStoppedFailed', language));
    }
    setIsStoppingRemote(false);
    doCloseTab(remoteCloseTabId);
    setRemoteCloseTabId(null);
  }, [remoteCloseTabId, tabs, doCloseTab, toast, language]);

  // Handle remote tab close without stopping session
  const handleRemoteCloseKeep = useCallback(() => {
    if (!remoteCloseTabId) return;
    doCloseTab(remoteCloseTabId);
    setRemoteCloseTabId(null);
  }, [remoteCloseTabId, doCloseTab]);

  // Cancel remote tab close
  const handleRemoteCloseCancel = useCallback(() => {
    setRemoteCloseTabId(null);
  }, []);

  // Switch to a tab
  const switchTab = useCallback(
    (tabId: string) => {
      const previousTabId = activeTabId;
      setActiveTabId(tabId);
      // Update store (Issue #65)
      setStoredActiveTabId(tabId);

      // Clear notification state for the tab we're leaving
      if (previousTabId && previousTabId !== tabId) {
        clearTabNotification(previousTabId);
      }

      // Send focus message to the new active iframe
      // Use setTimeout to ensure the iframe is visible before sending message
      setTimeout(() => {
        const iframe = iframeRefs.current.get(tabId);
        if (iframe?.contentWindow) {
          iframe.contentWindow.postMessage({ type: 'openace-focus-input' }, '*');
          // Scroll to bottom when switching tabs to show latest messages (Issue #1232)
          iframe.contentWindow.postMessage({ type: 'openace-scroll-to-bottom' }, '*');
        }
      }, 100);
    },
    [activeTabId, setStoredActiveTabId, clearTabNotification]
  );

  // Rename a tab
  const handleRenameTab = useCallback(
    (tabId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      const tab = tabs.find((t) => t.id === tabId);
      if (tab) {
        setRenameTabId(tabId);
        setRenameValue(tab.title);
        setShowRenameModal(true);
      }
    },
    [tabs]
  );

  // Drag and drop handlers for tab reordering (Issue #946)
  const handleDragStart = useCallback((tabId: string, e: React.DragEvent) => {
    setDraggedTabId(tabId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', tabId);
  }, []);

  const handleDragOver = useCallback(
    (tabId: string, e: React.DragEvent) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (draggedTabId && tabId !== draggedTabId) {
        setDragOverTabId(tabId);
      }
    },
    [draggedTabId]
  );

  const handleDragLeave = useCallback(() => {
    setDragOverTabId(null);
  }, []);

  const handleDrop = useCallback(
    (tabId: string) => {
      if (!draggedTabId || draggedTabId === tabId) return;

      // Issue #1470: Use tabsOrder for visual sorting, don't modify tabs array
      // This prevents iframe re-creation when tab order changes
      const fromIndex = tabsOrder.indexOf(draggedTabId);
      const toIndex = tabsOrder.indexOf(tabId);

      if (fromIndex !== -1 && toIndex !== -1) {
        // Update visual order only
        const newOrder = [...tabsOrder];
        const [removed] = newOrder.splice(fromIndex, 1);
        newOrder.splice(toIndex, 0, removed);
        setTabsOrder(newOrder);

        // Update store for persistence
        setStoredTabsOrder(newOrder);
      }

      setDraggedTabId(null);
      setDragOverTabId(null);
    },
    [draggedTabId, tabsOrder, setStoredTabsOrder]
  );

  const handleDragEnd = useCallback(() => {
    setDraggedTabId(null);
    setDragOverTabId(null);
  }, []);

  const handleSaveRename = useCallback(async () => {
    if (!renameTabId || !renameValue.trim()) return;

    setIsRenaming(true);
    try {
      // Extract session ID from tab URL if it contains one
      // Tab URL format: http://.../c/{session_id}?...
      // Or from stored tab data
      const tab = tabs.find((t) => t.id === renameTabId);
      if (!tab) {
        setIsRenaming(false);
        return;
      }

      // Try to extract session ID from URL or from stored sessionId
      let sessionId: string | null = tab.sessionId ?? null;
      if (!sessionId) {
        const urlParts = tab.url.split('/c/');
        if (urlParts.length > 1) {
          sessionId = urlParts[1].split('?')[0].split('#')[0];
        }
      }

      // If sessionId exists, try to rename in backend
      // If session doesn't exist in backend, just update locally
      if (sessionId) {
        try {
          const response = await sessionsApi.renameSession(sessionId, renameValue.trim());
          if (!response.success) {
            // If session not found in backend, just update locally (session may not have started yet)
            console.log('Session not found in backend, updating locally only:', response.error);
          }
        } catch (apiError) {
          // API call failed (e.g., session doesn't exist), proceed with local update
          console.log('Rename API failed, updating locally:', apiError);
        }
      }

      // Always update tab title locally (regardless of backend result)
      setTabs((prev) =>
        prev.map((tab) => (tab.id === renameTabId ? { ...tab, title: renameValue.trim() } : tab))
      );

      // Update store (Issue #65)
      updateStoredTab(renameTabId, { title: renameValue.trim() });

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
  }, [renameTabId, renameValue, tabs, language, toast, updateStoredTab]);

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

  const handleResizeMove = useCallback(
    (e: MouseEvent) => {
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
    },
    [resizingTabId]
  );

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

  // Check if quota is exceeded (computed before early returns to avoid hooks order violation)
  const isQuotaExceeded = quotaStatus?.over_quota?.any ?? false;

  // Keyboard shortcut for switching tabs (Cmd/Ctrl + Shift + ,/.)
  // Shift+, (<) = previous tab, Shift+. (>) = next tab
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't handle if quota exceeded or loading
      if (isQuotaExceeded || isLoading || isQuotaLoading) return;

      // Don't handle if no tabs or only one tab
      if (tabs.length <= 1) return;

      // Check modifier keys: Cmd+Shift on Mac, Ctrl+Shift on Windows/Linux
      const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
      const modifierPressed = isMac ? e.metaKey && e.shiftKey : e.ctrlKey && e.shiftKey;

      // Handle Comma key (previous tab) and Period key (next tab)
      // Use e.code instead of e.key to support non-English input methods
      if (modifierPressed && (e.code === 'Comma' || e.code === 'Period')) {
        e.preventDefault();

        console.log('[Keyboard Shortcut] Detected:', {
          key: e.key,
          code: e.code,
          isMac,
          metaKey: e.metaKey,
          ctrlKey: e.ctrlKey,
          shiftKey: e.shiftKey,
          direction: e.code === 'Comma' ? 'prev' : 'next',
          tabsLength: tabs.length,
          activeTabId,
        });

        // Find current active tab index
        const currentIndex = tabs.findIndex((tab) => tab.id === activeTabId);

        // Calculate new index
        let newIndex: number;
        if (e.code === 'Comma') {
          // Previous tab (wrap around to last if at first)
          newIndex = currentIndex <= 0 ? tabs.length - 1 : currentIndex - 1;
        } else {
          // Next tab (wrap around to first if at last)
          newIndex = currentIndex >= tabs.length - 1 ? 0 : currentIndex + 1;
        }

        const targetTab = tabs[newIndex];
        if (targetTab) {
          console.log('[Keyboard Shortcut] Switching to tab:', targetTab.id);
          switchTab(targetTab.id);
        }
      }
    };

    // Always listen for keyboard events

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [tabs.length, activeTabId, switchTab, isQuotaExceeded, isLoading, isQuotaLoading]);

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
            <div
              className={`progress-step ${loadingStage === 'loadingConfig' || loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}
            >
              <i className="bi bi-check-circle-fill" />
              <span>{t('loadingConfig', language) || 'Load config'}</span>
            </div>
            <div
              className={`progress-step ${loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}
            >
              <i
                className={`bi ${loadingStage === 'startingWorkspace' ? 'bi-arrow-repeat spin' : 'bi-check-circle-fill'}`}
              />
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
  // Allow terminal-only tabs to work even when webui is unavailable
  const urlHasTerminal =
    searchParams.get('workspaceType') === 'terminal' || searchParams.get('terminalId') !== null;
  const hasTerminalOnly = tabs.length === 0 || tabs.every((tab) => tab.tabType === 'terminal');
  if (config.multi_user_mode && !userWebUI?.success && !hasTerminalOnly && !urlHasTerminal) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-exclamation-circle fs-1 text-warning" />
          <h4 className="mt-3">{t('workspaceUnavailable', language)}</h4>
          <p className="text-muted">
            {userWebUI?.error ?? t('workspaceUnavailableHelp', language)}
          </p>
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
              <p className="text-muted small mb-4">{t('quotaLimitsHelpDesc', language)}</p>
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
    <div
      className={cn('workspace h-100 d-flex flex-column', workspaceFullscreen && 'fullscreen-mode')}
    >
      {/* Page Header - Hidden in fullscreen */}
      <div
        className={cn(
          'page-header mb-3 px-3 pt-3 d-flex align-items-center',
          workspaceFullscreen && 'd-none'
        )}
      >
        <div className="d-flex align-items-center flex-grow-1">
          <h2>{t('workspace', language)}</h2>
          {config.multi_user_mode && userWebUI?.system_account && (
            <small className="text-muted ms-2">({userWebUI.system_account})</small>
          )}
        </div>
        <div className="d-flex align-items-center gap-2">
          {/* Fullscreen toggle button */}
          <button
            className="btn btn-sm btn-outline-secondary fullscreen-toggle-btn"
            onClick={() => toggleWorkspaceFullscreen(false, false)}
            title={
              workspaceFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)
            }
          >
            <i
              className={cn(
                'bi me-1',
                workspaceFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen'
              )}
            />
            {workspaceFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)}
          </button>
        </div>
      </div>
      {/* Tab Bar */}
      {tabs.length > 0 && (
        <div
          className={cn(
            'workspace-tabs d-flex align-items-center border-bottom',
            workspaceFullscreen && 'fullscreen-tabs'
          )}
          style={{
            minHeight: '40px',
            backgroundColor: workspaceFullscreen
              ? 'var(--bg-primary, #ffffff)'
              : 'var(--bg-secondary, #f8f9fa)',
          }}
        >
          {/* Tabs */}
          <div className="d-flex flex-grow-1" style={{ overflowX: 'auto', overflowY: 'hidden' }}>
            {orderedTabs.map((tab) => {
              const tabWidth = tabWidths[tab.id] || 180;
              return (
                <div
                  key={tab.id}
                  data-tab-id={tab.id}
                  draggable
                  onDragStart={(e) => handleDragStart(tab.id, e)}
                  onDragOver={(e) => handleDragOver(tab.id, e)}
                  onDragLeave={handleDragLeave}
                  onDrop={() => handleDrop(tab.id)}
                  onDragEnd={handleDragEnd}
                  className={cn(
                    'workspace-tab d-flex align-items-center px-2 py-2 cursor-pointer',
                    'border-end position-relative',
                    activeTabId === tab.id && 'active',
                    draggedTabId === tab.id && 'dragging',
                    dragOverTabId === tab.id && 'drag-over'
                  )}
                  onClick={() => switchTab(tab.id)}
                  style={{
                    width: `${tabWidth}px`,
                    flexShrink: 0,
                    userSelect: 'none',
                  }}
                >
                  <i
                    className={cn(
                      'bi me-2 flex-shrink-0',
                      tab.tabType === 'terminal'
                        ? 'bi-terminal text-warning'
                        : tab.waitingForUser
                          ? 'bi-bell-fill text-info'
                          : 'bi-chat-dots text-muted'
                    )}
                  />
                  <span
                    className={cn(
                      'text-truncate small flex-grow-1',
                      tab.waitingForUser && 'fw-semibold'
                    )}
                    style={{ minWidth: 0 }}
                    title={tab.title}
                  >
                    {tab.tabType === 'terminal' ? (
                      <i className="bi bi-terminal-fill text-warning me-1" title="Terminal" />
                    ) : tab.workspaceType === 'remote' ? (
                      <i
                        className="bi bi-cloud-fill text-primary me-1"
                        title={`Remote: ${tab.machineName ?? tab.machineId}`}
                      />
                    ) : (
                      <i className="bi bi-laptop text-success me-1" title="Local" />
                    )}
                    {tab.title}
                  </span>
                  {/* Waiting indicator badge */}
                  {tab.waitingForUser && activeTabId !== tab.id && (
                    <span
                      className={cn('waiting-badge badge bg-info')}
                      style={{
                        fontSize: '0.65rem',
                        padding: '0.2rem 0.4rem',
                        marginLeft: '0.25rem',
                        borderRadius: '50%',
                        minWidth: '1.2rem',
                        height: '1.2rem',
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      ●
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
                    <button
                      className="btn btn-sm btn-link p-0 text-muted tab-action-btn"
                      onClick={(e) => closeTab(tab.id, e)}
                      title={t('close', language)}
                      tabIndex={-1}
                    >
                      <i className="bi bi-x" />
                    </button>
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
              onClick={() => setShowNewSessionModal(true)}
              title={t('newSession', language)}
              style={{
                flexShrink: 0,
                borderLeft: '1px solid var(--border-color, rgba(0,0,0,0.1))',
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
            {/* Loading overlay - only for workspace tabs */}
            {loadingTabs.has(tab.id) && tab.tabType !== 'terminal' && (
              <div
                className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center"
                style={{ zIndex: 10, backgroundColor: 'var(--bg-secondary, #f8f9fa)' }}
              >
                <div className="text-center">
                  <div className="spinner-border text-primary mb-3" role="status">
                    <span className="visually-hidden">{t('loading', language)}</span>
                  </div>
                  <p className="text-muted">{t('workspaceLoading', language)}</p>
                </div>
              </div>
            )}
            {tab.tabType === 'terminal' ? (
              /* Terminal Tab */
              <TerminalTab
                wsUrl={tab.terminalWsUrl ?? ''}
                token={tab.terminalToken ?? ''}
                isActive={activeTabId === tab.id}
                machineName={tab.machineName}
                terminalId={tab.terminalId}
                machineId={tab.machineId}
                onError={(error) => {
                  console.error('Terminal error:', error);
                  toast.error(t('terminalError', language), error);
                }}
                onAuthFailed={() => {
                  setTabs((prev) =>
                    prev.map((t) =>
                      t.id === tab.id ? { ...t, terminalWsUrl: '', terminalToken: '' } : t
                    )
                  );
                  if (tab.terminalId) {
                    terminalAttachAttemptedRefs.current.delete(tab.terminalId);
                  }
                }}
                onReattachNeeded={() => {
                  if (tab.terminalId && tab.machineId) {
                    console.log('[Terminal] Reattach needed, calling attach_terminal API');
                    // Reset reconnect counter
                    terminalAttachAttemptedRefs.current.delete(tab.terminalId);
                    // Clear current wsUrl/token
                    setTabs((prev) =>
                      prev.map((t) =>
                        t.id === tab.id ? { ...t, terminalWsUrl: '', terminalToken: '' } : t
                      )
                    );
                    // Call attach_terminal API to restart terminal_server if needed
                    remoteApi
                      .attachTerminal({
                        terminal_id: tab.terminalId,
                        machine_id: tab.machineId,
                      })
                      .then((result) => {
                        console.log('[Terminal] Attach result:', result);
                        if (result.success && result.terminal?.status === 'pending') {
                          // Poll for new ws_url
                          const pollForReattach = async (attempt: number) => {
                            if (attempt > 30) {
                              console.log('[Terminal] Reattach polling timed out');
                              return;
                            }
                            if (terminalPollCancelRefs.current.get(tab.id)) return;
                            if (attempt > 0) {
                              await new Promise((r) => setTimeout(r, 1000));
                            }
                            if (terminalPollCancelRefs.current.get(tab.id)) return;
                            try {
                              const status = await remoteApi.getTerminalStatus(
                                tab.terminalId!,
                                tab.machineId!
                              );
                              const wsUrl = status.terminal.ws_url ?? '';
                              if (
                                status.terminal.status === 'running' &&
                                wsUrl &&
                                status.terminal.token
                              ) {
                                setTabs((prev) =>
                                  prev.map((t) =>
                                    t.id === tab.id
                                      ? {
                                          ...t,
                                          terminalWsUrl: status.terminal.ws_url!,
                                          terminalToken: status.terminal.token!,
                                        }
                                      : t
                                  )
                                );
                                updateStoredTab(tab.id, {
                                  terminalWsUrl: status.terminal.ws_url!,
                                  terminalToken: status.terminal.token!,
                                });
                                console.log('[Terminal] Reattached successfully:', wsUrl);
                              } else if (
                                status.terminal.status === 'not_found' ||
                                status.terminal.status === 'error'
                              ) {
                                // Terminal gone, need to create new one
                                console.log('[Terminal] Terminal not found, creating new');
                                remoteApi
                                  .startTerminal({
                                    machine_id: tab.machineId!,
                                    work_dir: '',
                                  })
                                  .then((newResult) => {
                                    if (newResult.success && newResult.terminal) {
                                      const newTerminalId = newResult.terminal.terminal_id;
                                      setTabs((prev) =>
                                        prev.map((t) =>
                                          t.id === tab.id
                                            ? {
                                                ...t,
                                                terminalId: newTerminalId,
                                                terminalWsUrl: newResult.terminal?.ws_url ?? '',
                                                terminalToken: newResult.terminal?.token ?? '',
                                              }
                                            : t
                                        )
                                      );
                                      updateStoredTab(tab.id, {
                                        terminalId: newTerminalId,
                                        terminalWsUrl: newResult.terminal?.ws_url || '',
                                        terminalToken: newResult.terminal?.token || '',
                                      });
                                    }
                                  });
                              } else {
                                pollForReattach(attempt + 1);
                              }
                            } catch {
                              pollForReattach(attempt + 1);
                            }
                          };
                          pollForReattach(0);
                        } else if (result.terminal?.status === 'not_found') {
                          // Terminal gone, create new
                          console.log('[Terminal] Terminal not found, creating new');
                          remoteApi
                            .startTerminal({
                              machine_id: tab.machineId!,
                              work_dir: '',
                            })
                            .then((newResult) => {
                              if (newResult.success && newResult.terminal) {
                                const newTerminalId = newResult.terminal.terminal_id;
                                const newWsUrl = newResult.terminal.ws_url || '';
                                const newToken = newResult.terminal.token || '';
                                setTabs((prev) =>
                                  prev.map((t) =>
                                    t.id === tab.id
                                      ? {
                                          ...t,
                                          terminalId: newTerminalId,
                                          terminalWsUrl: newWsUrl,
                                          terminalToken: newToken,
                                        }
                                      : t
                                  )
                                );
                                updateStoredTab(tab.id, {
                                  terminalId: newTerminalId,
                                  terminalWsUrl: newWsUrl,
                                  terminalToken: newToken,
                                });
                              }
                            });
                        }
                      });
                  }
                }}
              />
            ) : (
              /* Workspace Tab (iframe) */
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
            )}
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
          color: var(--text-secondary, #6c757d);
        }
        .workspace-tab:hover {
          background-color: var(--bg-tertiary, rgba(0, 0, 0, 0.05));
          color: var(--text-primary, #212529);
        }
        .workspace-tab.active {
          background-color: var(--bg-primary, #ffffff);
          color: var(--text-primary, #212529);
          border-bottom: 2px solid var(--color-primary, #0d6efd);
          margin-bottom: -1px;
        }
        .workspace-tab.active::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          height: 2px;
          background: var(--color-primary, #0d6efd);
        }
        .workspace-tabs::-webkit-scrollbar {
          height: 4px;
        }
        .workspace-tabs::-webkit-scrollbar-thumb {
          background: var(--border-color-dark, #ccc);
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
          background-color: var(--bg-tertiary, rgba(0, 0, 0, 0.05)) !important;
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
          background: var(--bg-tertiary, rgba(0, 0, 0, 0.05));
          color: var(--text-secondary, #6c757d);
          opacity: 0.5;
          transition: opacity 0.3s ease, background-color 0.3s ease;
        }
        .progress-step.active {
          opacity: 1;
          background: var(--color-primary-light, rgba(13, 110, 253, 0.1));
        }
        .progress-step i {
          font-size: 1.2rem;
        }
        .progress-step.active i.bi-check-circle-fill {
          color: var(--color-success, #28a745);
        }
        .progress-step.active i.bi-arrow-repeat {
          color: var(--color-primary, #0d6efd);
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
      {/* New Session Modal - shared by "+" button and SessionList */}
      <NewSessionModal
        show={showNewSessionModal}
        onClose={() => setShowNewSessionModal(false)}
        onCreateLocal={() => {
          setShowNewSessionModal(false);
          createNewTab(undefined, { workspaceType: 'local' });
        }}
        onCreateRemote={(params: {
          machineId: string;
          machineName: string;
          sessionId: string;
          projectPath: string;
        }) => {
          setShowNewSessionModal(false);
          createNewTab(undefined, {
            workspaceType: 'remote',
            machineId: params.machineId,
            machineName: params.machineName,
            sessionId: params.sessionId,
            projectPath: params.projectPath,
          });
        }}
        onCreateTerminal={async (params: {
          machineId: string;
          machineName: string;
          workDir: string;
        }) => {
          setShowNewSessionModal(false);
          try {
            const result = await remoteApi.startTerminal({
              machine_id: params.machineId,
              work_dir: params.workDir,
            });
            if (result.success && result.terminal) {
              const tabId = generateTabId();
              const terminalId = result.terminal.terminal_id;
              const initialWsUrl = result.terminal.ws_url || '';
              const initialToken = result.terminal.token || '';

              console.log('[Terminal] Created terminal:', {
                terminalId,
                initialWsUrl,
                initialToken,
                hasWsUrl: Boolean(initialWsUrl),
              });

              const newTab: WorkspaceTab = {
                id: tabId,
                title: `Terminal - ${params.machineName}`,
                tabType: 'terminal',
                url: '',
                token: '',
                createdAt: Date.now(),
                waitingForUser: false,
                waitingType: null,
                workspaceType: 'remote',
                machineId: params.machineId,
                machineName: params.machineName,
                terminalId,
                terminalWsUrl: result.terminal.ws_url || '',
                terminalToken: result.terminal.token || '',
              };
              setTabs((prev) => [...prev, newTab]);
              // Clear notification state for the previously active tab
              const prevActiveTabId = useAppStore.getState().workspaceActiveTabId;
              if (prevActiveTabId) {
                clearTabNotification(prevActiveTabId);
              }
              setActiveTabId(tabId);
              setStoredActiveTabId(tabId);

              const storeTab = {
                id: tabId,
                title: newTab.title,
                tabType: 'terminal' as const,
                workspaceType: 'remote' as const,
                machineId: params.machineId,
                machineName: params.machineName,
                terminalId,
                terminalWsUrl: result.terminal.ws_url || '',
                terminalToken: result.terminal.token || '',
                createdAt: newTab.createdAt,
                waitingForUser: false,
                waitingType: null as 'input' | 'permission' | 'plan' | null,
              };
              addStoredTab(storeTab);
              setStoredActiveTabId(tabId);

              // Poll for terminal status until WebSocket proxy is ready
              pollTerminalProxy(tabId, terminalId, params.machineId);
            } else {
              toast.error(
                t('terminalError', language) || 'Terminal Error',
                result.error ?? 'Failed to start terminal'
              );
            }
          } catch (err) {
            toast.error(t('terminalError', language) || 'Terminal Error', (err as Error).message);
          }
        }}
      />

      {/* Remote Session Close Confirmation Modal */}
      <Modal
        isOpen={remoteCloseTabId !== null}
        onClose={handleRemoteCloseCancel}
        title="关闭远程工作区"
        size="sm"
      >
        <p
          style={{
            color: 'var(--text-secondary, #6c757d)',
            fontSize: '0.875rem',
            marginBottom: '1rem',
          }}
        >
          这是一个远程工作区会话。关闭前请选择是否停止远程会话。
        </p>
        <div
          style={{
            background: 'var(--bg-tertiary, #f8f9fa)',
            borderRadius: '0.5rem',
            padding: '0.75rem',
            marginBottom: '1rem',
            fontSize: '0.8125rem',
            color: 'var(--text-secondary, #6c757d)',
          }}
        >
          不停止会话可以恢复，但会占用服务器资源。
        </div>
        <div className="d-flex justify-content-end gap-2">
          <button className="btn btn-secondary" onClick={handleRemoteCloseCancel}>
            取消
          </button>
          <button
            className="btn btn-outline-warning"
            onClick={handleRemoteCloseKeep}
            disabled={isStoppingRemote}
          >
            保留会话并关闭
          </button>
          <button
            className="btn btn-danger"
            onClick={handleRemoteCloseStop}
            disabled={isStoppingRemote}
          >
            {isStoppingRemote ? '正在停止...' : '停止会话并关闭'}
          </button>
        </div>
      </Modal>
    </div>
  );
};

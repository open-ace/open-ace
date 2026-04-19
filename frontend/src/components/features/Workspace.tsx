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

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { workspaceApi, type WorkspaceConfig, type UserWebUIResponse } from '@/api';
import { requestApi, type QuotaStatusResponse } from '@/api/request';
import { sessionsApi } from '@/api/sessions';
import {
  useLanguage,
  useAppStore,
  useWorkspaceFullscreen,
  useEnableTabNotifications,
  useWorkspaceTabs,
  useWorkspaceActiveTabId,
  useSetWorkspaceActiveTabId,
  useAddWorkspaceTab,
  useUpdateWorkspaceTab,
  useRemoveWorkspaceTab,
  type WorkspaceTab as StoreWorkspaceTab,
} from '@/store';
import { t } from '@/i18n';
import { Error, Button, Card, useToast, Modal } from '@/components/common';
import { cn } from '@/utils';

/**
 * Extended WorkspaceTab for local use (includes URL which is generated at runtime)
 * The base WorkspaceTab from store is persisted to localStorage without URL
 * URL is regenerated when restoring tabs based on sessionId, encodedProjectName, toolName
 */
interface WorkspaceTab extends StoreWorkspaceTab {
  url: string;       // Runtime-generated URL for iframe
  token: string;     // Token for authentication (runtime)
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

  // Local state for tabs with runtime-generated URL (extended from store state)
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set());
  const [renameTabId, setRenameTabId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [showRenameModal, setShowRenameModal] = useState(false);
  const [isRenaming, setIsRenaming] = useState(false);
  const [tabWidths, setTabWidths] = useState<Record<string, number>>({});
  const [resizingTabId, setResizingTabId] = useState<string | null>(null);

  // Flag to track if tabs have been initialized (Issue #65)
  const [tabsInitialized, setTabsInitialized] = useState(false);

  // Tab notifications setting
  const enableTabNotifications = useEnableTabNotifications();
  const { toggleTabNotifications } = useAppStore();

  // Refs for iframe elements (to send focus messages)
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map());

  // Fullscreen state from global store
  const workspaceFullscreen = useWorkspaceFullscreen();
  const { toggleWorkspaceFullscreen, exitWorkspaceFullscreen } = useAppStore();

  // Workspace tabs state from store (Issue #65)
  const storedTabs = useWorkspaceTabs();
  const storedActiveTabId = useWorkspaceActiveTabId();

  // Use stable action selectors (fixes infinite loop)
  const setStoredActiveTabId = useSetWorkspaceActiveTabId();
  const addStoredTab = useAddWorkspaceTab();
  const updateStoredTab = useUpdateWorkspaceTab();
  const removeStoredTab = useRemoveWorkspaceTab();

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
  // Also listen for session ID update from iframe (Issue #65)
  // Also listen for tab switch request from iframe (Issue #68)
  useEffect(() => {
    const handleIframeMessage = (event: MessageEvent) => {
      // Validate message type for fullscreen request
      if (event.data?.type === 'openace-enter-chat') {
        useAppStore.getState().enterWorkspaceFullscreen(false, false);
      }

      // Listen for session ID update from qwen-code-webui iframe (Issue #65)
      // This allows the iframe to inform the workspace about its current session
      if (event.data?.type === 'qwen-code-session-update') {
        const { sessionId, encodedProjectName, toolName, title, settings } = event.data;
        // Update the current active tab with session info
        if (sessionId) {
          const currentActiveTabId = useAppStore.getState().workspaceActiveTabId;
          if (currentActiveTabId) {
            // Only update title if it's explicitly provided (not undefined)
            // Otherwise keep the existing title (e.g., "New Session")
            const updateData: Partial<StoreWorkspaceTab> = {
              sessionId,
              encodedProjectName,
              toolName,
              settings, // Save settings for tab restoration (Issue #70)
            };
            if (title) {
              updateData.title = title;
            }
            useAppStore.getState().updateWorkspaceTab(currentActiveTabId, updateData);
            // Also update local tabs state
            setTabs((prev) =>
              prev.map((tab) =>
                tab.id === currentActiveTabId
                  ? { ...tab, sessionId, encodedProjectName, toolName, title: title || tab.title, settings }
                  : tab
              )
            );
          }
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

          // If we couldn't find the source, use currentActiveTabId as fallback
          if (!sourceTabId) {
            sourceTabId = useAppStore.getState().workspaceActiveTabId;
          }

          if (sourceTabId) {
            setTabs((prev) =>
              prev.map((tab) =>
                tab.id === sourceTabId
                  ? { ...tab, waitingForUser: isWaiting, waitingType }
                  : tab
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
          const currentIndex = currentTabs.findIndex(tab => tab.id === currentActiveTabId);
          
          // Calculate new index
          let newIndex: number;
          if (direction === 'prev') {
            newIndex = currentIndex <= 0 ? currentTabs.length - 1 : currentIndex - 1;
          } else {
            newIndex = currentIndex >= currentTabs.length - 1 ? 0 : currentIndex + 1;
          }
          
          const targetTab = currentTabs[newIndex];
          if (targetTab && targetTab.id !== currentActiveTabId) {
            // Switch to the target tab
            setActiveTabId(targetTab.id);
            useAppStore.getState().setWorkspaceActiveTabId(targetTab.id);

            // Send focus message to iframe after tab switch
            setTimeout(() => {
              const iframe = iframeRefs.current.get(targetTab.id);
              if (iframe?.contentWindow) {
                iframe.contentWindow.postMessage({ type: 'openace-focus-input' }, '*');
                iframe.contentWindow.postMessage({ type: 'openace-tab-activated' }, '*');
              }
            }, 100);
          }
          return currentTabs; // Don't modify tabs, just use it for checking
        });
      }
    };

    window.addEventListener('message', handleIframeMessage);
    return () => window.removeEventListener('message', handleIframeMessage);
  }, [enableTabNotifications, language]);

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
  const getEffectiveUrl = useCallback((
    restoreSessionId?: string,
    encodedProjectName?: string,
    toolName?: string,
    settings?: { model?: string; useWebUI?: boolean; permissionMode?: string },
    remoteParams?: { workspaceType?: 'local' | 'remote'; machineId?: string; machineName?: string }
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
      // Add settings parameters (Issue #70)
      if (settings?.model) {
        url = `${url}&model=${encodeURIComponent(settings.model)}`;
      }
      if (settings?.useWebUI !== undefined) {
        url = `${url}&useWebUI=${settings.useWebUI}`;
      }
      if (settings?.permissionMode) {
        url = `${url}&permissionMode=${encodeURIComponent(settings.permissionMode)}`;
      }
      // Remote workspace parameters
      url = appendRemoteParams(url);
      return url;
    }

    // Single-user mode: use configured URL
    let url = config.url;
    // Add lang parameter for language sync
    const langSeparator = url.includes('?') ? '&' : '?';
    url = `${url}${langSeparator}lang=${encodeURIComponent(language)}`;
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
      url = `${url}&useWebUI=${settings.useWebUI}`;
    }
    if (settings?.permissionMode) {
      url = appendParam(url, 'permissionMode', settings.permissionMode);
    }
    // Remote workspace parameters
    url = appendRemoteParams(url);
    return url;
  }, [config, userWebUI, language]);

  // Initialize tabs when config is loaded (Issue #65: Restore from store if available)
  useEffect(() => {
    // Wait for both config and userWebUI (in multi-user mode) to be loaded
    if (!config?.enabled) return;

    // In multi-user mode, wait for userWebUI to be loaded
    if (config.multi_user_mode && !userWebUI?.success) return;

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
    const urlWorkspaceType = searchParams.get('workspaceType') as 'local' | 'remote' | null;
    const urlMachineId = searchParams.get('machineId');
    const urlMachineName = searchParams.get('machineName');
    const restoreSessionId = urlSessionId || restoreSession;

    // Determine if we should restore from store or create new
    // Priority: URL restore params > Store saved state > New session
    let initialTabs: WorkspaceTab[] = [];
    let initialActiveTabId = '';

    if (restoreSessionId) {
      // Case 1: URL restore params - create a single tab with the restore session
      // Build settings from URL params
      const urlSettings: { model?: string; useWebUI?: boolean; permissionMode?: string } | undefined =
        (urlModel || urlUseWebUI !== null || urlPermissionMode)
          ? {
              model: urlModel || undefined,
              useWebUI: urlUseWebUI === 'true' ? true : urlUseWebUI === 'false' ? false : undefined,
              permissionMode: urlPermissionMode || undefined,
            }
          : undefined;

      // Build remote params from URL
      const remoteParams: { workspaceType?: 'local' | 'remote'; machineId?: string; machineName?: string } | undefined =
        urlWorkspaceType
          ? { workspaceType: urlWorkspaceType, machineId: urlMachineId || undefined, machineName: urlMachineName || undefined }
          : undefined;

      const effectiveUrl = getEffectiveUrl(
        restoreSessionId,
        urlEncodedProjectName || undefined,
        urlToolName || undefined,
        urlSettings,
        remoteParams
      );
      if (effectiveUrl) {
        const tab: WorkspaceTab = {
          id: generateTabId(),
          title: t('restoredSession', language),
          url: effectiveUrl,
          token: userWebUI?.token || '',
          sessionId: restoreSessionId,
          encodedProjectName: urlEncodedProjectName || undefined,
          toolName: urlToolName || undefined,
          settings: urlSettings,
          workspaceType: urlWorkspaceType || undefined,
          machineId: urlMachineId || undefined,
          machineName: urlMachineName || undefined,
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

        // Clear the restore parameters after using it
        searchParams.delete('sessionId');
        searchParams.delete('restoreSession');
        searchParams.delete('encodedProjectName');
        searchParams.delete('toolName');
        searchParams.delete('workspaceType');
        searchParams.delete('machineId');
        searchParams.delete('machineName');
        setSearchParams(searchParams, { replace: true });
      }
    } else if (storedTabs.length > 0) {
      // Case 2: Restore from store - regenerate URLs for each tab
      initialTabs = storedTabs.map((storedTab) => {
        // Regenerate URL based on sessionId if available
        // Include settings and remote params in URL for restoration
        const remoteParams = storedTab.workspaceType
          ? { workspaceType: storedTab.workspaceType, machineId: storedTab.machineId, machineName: storedTab.machineName }
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
          url: effectiveUrl || '',
          token: userWebUI?.token || '',
        };
      });

      // Use stored active tab ID if it exists in the restored tabs
      initialActiveTabId = storedTabs.find(t => t.id === storedActiveTabId)
        ? storedActiveTabId
        : (initialTabs.length > 0 ? initialTabs[0].id : '');

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
          token: userWebUI?.token || '',
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
      setLoadingTabs(new Set(initialTabs.map(t => t.id)));
      setTabsInitialized(true);
    }
  }, [
    config,
    userWebUI,
    tabsInitialized,
    storedTabs,
    storedActiveTabId,
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
      const sessionId = searchParams.get('sessionId') || undefined;

      // Clear the URL parameters
      searchParams.delete('newTab');
      searchParams.delete('workspaceType');
      searchParams.delete('machineId');
      searchParams.delete('machineName');
      searchParams.delete('sessionId');
      setSearchParams(searchParams, { replace: true });

      // Create new tab with remote params
      const rp = remoteParams.workspaceType
        ? { workspaceType: remoteParams.workspaceType, machineId: remoteParams.machineId || undefined, machineName: remoteParams.machineName || undefined }
        : undefined;
      createNewTab(sessionId, rp);
    }
  }, [searchParams, config, getEffectiveUrl, tabsInitialized]);

  // Create a new tab
  const createNewTab = useCallback((
    restoreSessionId?: string,
    remoteParams?: { workspaceType?: 'local' | 'remote'; machineId?: string; machineName?: string },
  ) => {
    const effectiveUrl = getEffectiveUrl(restoreSessionId || undefined, undefined, undefined, undefined, remoteParams);
    if (!effectiveUrl) return;

    const newTab: WorkspaceTab = {
      id: generateTabId(),
      title: restoreSessionId ? t('restoredSession', language) : t('newSession', language),
      url: effectiveUrl,
      token: userWebUI?.token || '',
      createdAt: Date.now(),
      waitingForUser: false,
      waitingType: null,
      sessionId: restoreSessionId,
      workspaceType: remoteParams?.workspaceType,
      machineId: remoteParams?.machineId,
      machineName: remoteParams?.machineName,
    };

    // Update local state
    setTabs((prev) => [...prev, newTab]);
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

    // Mark as loading
    setLoadingTabs((prev) => new Set(prev).add(newTab.id));
  }, [getEffectiveUrl, userWebUI, language, addStoredTab, setStoredActiveTabId]);

  // Close a tab
  const closeTab = useCallback(
    (tabId: string, e: React.MouseEvent) => {
      e.stopPropagation();

      // Update local state
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

      // Update store (Issue #65)
      removeStoredTab(tabId);
    },
    [activeTabId, removeStoredTab]
  );

  // Switch to a tab
  const switchTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
    // Update store (Issue #65)
    setStoredActiveTabId(tabId);

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
  }, [setStoredActiveTabId]);

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
      // Or from stored tab data
      const tab = tabs.find((t) => t.id === renameTabId);
      if (!tab) {
        setIsRenaming(false);
        return;
      }

      // Try to extract session ID from URL or from stored sessionId
      let sessionId: string | null = tab.sessionId || null;
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
        prev.map((tab) =>
          tab.id === renameTabId ? { ...tab, title: renameValue.trim() } : tab
        )
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
      const modifierPressed = isMac ? (e.metaKey && e.shiftKey) : (e.ctrlKey && e.shiftKey);

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
          activeTabId
        });

        // Find current active tab index
        const currentIndex = tabs.findIndex(tab => tab.id === activeTabId);

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
                    tab.waitingForUser ? 'bi-bell-fill text-info' : 'bi-chat-dots text-muted'
                  )} />
                  <span className={cn(
                    'text-truncate small flex-grow-1',
                    tab.waitingForUser && 'fw-semibold'
                  )} style={{ minWidth: 0 }}>
                    {tab.workspaceType === 'remote' && (
                      <span className="me-1" title={`Remote: ${tab.machineName || tab.machineId}`}>
                        <i className="bi bi-cloud text-purple-500" style={{ fontSize: '0.7rem' }} />
                      </span>
                    )}
                    {tab.title}
                  </span>
                  {/* Waiting indicator badge */}
                  {tab.waitingForUser && activeTabId !== tab.id && (
                    <span className={cn(
                      'waiting-badge badge bg-info'
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

          {/* Tab notifications toggle - Show in fullscreen mode */}
          {workspaceFullscreen && (
            <button
              className={cn(
                'btn btn-sm px-3 py-1 mx-2',
                enableTabNotifications
                  ? 'btn-outline-primary'
                  : 'btn-outline-secondary'
              )}
              onClick={toggleTabNotifications}
              title={enableTabNotifications ? t('disableTabNotifications', language) : t('enableTabNotifications', language)}
            >
              <i className={cn('bi me-1', enableTabNotifications ? 'bi-bell-fill' : 'bi-bell-slash')} />
              {enableTabNotifications ? t('tabNotificationsOn', language) : t('tabNotificationsOff', language)}
            </button>
          )}
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

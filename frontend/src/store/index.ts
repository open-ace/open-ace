/**
 * App Store - Global application state using Zustand
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User, Theme, Language, AppMode } from '@/types';

/**
 * WorkspaceTab - Represents a tab in the workspace with session state
 * This is persisted to localStorage so workspace state can be restored
 * after navigating away and returning.
 */
export interface WorkspaceTab {
  id: string;
  title: string;
  tabType?: 'workspace' | 'terminal'; // Tab type: workspace (iframe) or terminal (xterm.js)
  sessionId?: string; // Session ID from qwen-code-webui (extracted from URL or backend)
  terminalId?: string; // Terminal ID for terminal tabs
  encodedProjectName?: string; // Encoded project path for session restoration
  toolName?: string; // Tool name for session restoration
  createdAt: number;
  waitingForUser?: boolean;
  waitingType?: 'permission' | 'plan' | 'input' | null;
  // Settings for tab restoration (Issue #70)
  settings?: {
    model?: string; // Selected model ID
    useWebUI?: boolean; // Use WebUI components toggle
    permissionMode?: string; // Permission mode: default, plan, auto-edit, yolo
  };
  // Remote workspace fields
  workspaceType?: 'local' | 'remote';
  machineId?: string; // Remote machine ID
  machineName?: string; // Remote machine display name
  // Terminal fields
  terminalWsUrl?: string; // WebSocket URL for terminal connection
  terminalToken?: string; // Auth token for terminal WebSocket
}

/**
 * Persisted state type - defines what gets stored in localStorage
 * IMPORTANT: When adding new fields to WorkspaceTab or AppState, ensure they are either:
 * 1. Safe to persist (add to partialize below)
 * 2. Sensitive (exclude from partialize and handle specially)
 * Issue #2953: Added explicit type for safety
 */
export interface PersistedState {
  theme: Theme;
  language: Language;
  sidebarCollapsed: boolean;
  appMode: AppMode;
  enableTabNotifications: boolean;
  autoFullscreenOnEnterChat: boolean;
  showFileChangesPanel: boolean;
  workspaceTabs: WorkspaceTab[];
  workspaceActiveTabId: string;
  workspaceTabsOrder: string[];
}

interface AppState {
  // Auth state
  user: User | null;
  isAuthenticated: boolean;
  authLoading: boolean;

  // UI state
  theme: Theme;
  language: Language;
  sidebarCollapsed: boolean;
  sidebarMobileOpen: boolean;
  appMode: AppMode;

  // Workspace fullscreen state
  workspaceFullscreen: boolean;
  previousLeftPanelCollapsed: boolean;
  previousRightPanelCollapsed: boolean;

  // Workspace tabs state (Issue #65: Persist workspace state)
  workspaceTabs: WorkspaceTab[];
  workspaceActiveTabId: string;
  workspaceTabsOrder: string[]; // Visual order of tabs (Issue #1470)

  // Tab notification settings
  enableTabNotifications: boolean;

  // Auto fullscreen on enter chat setting (Issue #121)
  autoFullscreenOnEnterChat: boolean;

  // File changes panel visibility setting (Issue #144)
  showFileChangesPanel: boolean;

  // Feature flags
  autonomousEnabled: boolean;
  modelGatewayEnabled: boolean;
  runTimelineEnabled: boolean;
  policyEnabled: boolean;
  configLoaded: boolean;

  // Actions
  setUser: (user: User | null) => void;
  setAuthenticated: (isAuthenticated: boolean) => void;
  setAuthLoading: (loading: boolean) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleMobileSidebar: () => void;
  setMobileSidebarOpen: (open: boolean) => void;
  setAppMode: (mode: AppMode) => void;
  logout: () => void;

  // Workspace fullscreen actions
  enterWorkspaceFullscreen: (leftCollapsed: boolean, rightCollapsed: boolean) => void;
  exitWorkspaceFullscreen: () => void;
  toggleWorkspaceFullscreen: (leftCollapsed: boolean, rightCollapsed: boolean) => void;

  // Workspace tabs actions (Issue #65)
  setWorkspaceTabs: (tabs: WorkspaceTab[]) => void;
  setWorkspaceActiveTabId: (tabId: string) => void;
  addWorkspaceTab: (tab: WorkspaceTab) => void;
  updateWorkspaceTab: (tabId: string, updates: Partial<WorkspaceTab>) => void;
  removeWorkspaceTab: (tabId: string) => void;
  clearWorkspaceTabs: () => void;
  reorderWorkspaceTabs: (fromIndex: number, toIndex: number) => void;
  setWorkspaceTabsOrder: (order: string[] | ((prev: string[]) => string[])) => void; // Issue #1470

  // Tab notification actions
  setEnableTabNotifications: (enabled: boolean) => void;
  toggleTabNotifications: () => void;

  // Auto fullscreen actions (Issue #121)
  setAutoFullscreenOnEnterChat: (enabled: boolean) => void;
  toggleAutoFullscreenOnEnterChat: () => void;

  // File changes panel actions (Issue #144)
  setShowFileChangesPanel: (enabled: boolean) => void;
  toggleFileChangesPanel: () => void;

  // Feature flag actions
  setAutonomousEnabled: (enabled: boolean) => void;
  setModelGatewayEnabled: (enabled: boolean) => void;
  setRunTimelineEnabled: (enabled: boolean) => void;
  setPolicyEnabled: (enabled: boolean) => void;
  setConfigLoaded: (loaded: boolean) => void;
}

/**
 * Validate workspace tab has required fields
 * Issue #2953: Defensive validation
 */
export const isValidWorkspaceTab = (tab: unknown): tab is WorkspaceTab => {
  if (typeof tab !== 'object' || tab === null) return false;
  const t = tab as Record<string, unknown>;
  return typeof t.id === 'string' && typeof t.title === 'string';
};

/**
 * Validate and sanitize stored state
 * Issue #2953: Schema validation for localStorage data
 * @param persistedState - The raw state from localStorage
 * @returns Partial validated state with only valid fields
 */
export const validateStoredState = (persistedState: unknown): Partial<PersistedState> => {
  if (typeof persistedState !== 'object' || persistedState === null) {
    return {};
  }

  const state = persistedState as Record<string, unknown>;
  const validated: Partial<PersistedState> = {};

  // Validate workspaceTabs - must be array with valid structure
  if (Array.isArray(state.workspaceTabs)) {
    const validTabs: WorkspaceTab[] = [];
    for (const tab of state.workspaceTabs) {
      if (isValidWorkspaceTab(tab)) {
        validTabs.push(tab);
      } else {
        console.warn('[Store] Invalid workspaceTab item removed:', tab);
      }
    }
    validated.workspaceTabs = validTabs;
  } else if (state.workspaceTabs !== undefined) {
    console.warn('[Store] Invalid workspaceTabs, resetting to empty array');
  }

  // Validate workspaceActiveTabId - must be string
  if (typeof state.workspaceActiveTabId === 'string') {
    validated.workspaceActiveTabId = state.workspaceActiveTabId;
  } else if (state.workspaceActiveTabId !== undefined) {
    console.warn('[Store] Invalid workspaceActiveTabId, resetting to empty string');
  }

  // Validate workspaceTabsOrder - must be array of strings
  if (Array.isArray(state.workspaceTabsOrder)) {
    const validOrder: string[] = [];
    for (const id of state.workspaceTabsOrder) {
      if (typeof id === 'string') {
        validOrder.push(id);
      } else {
        console.warn('[Store] Invalid workspaceTabsOrder item removed:', id);
      }
    }
    validated.workspaceTabsOrder = validOrder;
  } else if (state.workspaceTabsOrder !== undefined) {
    console.warn('[Store] Invalid workspaceTabsOrder, resetting to empty array');
  }

  // Validate other persisted fields
  if (['light', 'dark', 'system'].includes(state.theme as string)) {
    validated.theme = state.theme as Theme;
  }
  if (typeof state.language === 'string') {
    validated.language = state.language as Language;
  }
  if (typeof state.sidebarCollapsed === 'boolean') {
    validated.sidebarCollapsed = state.sidebarCollapsed;
  }
  if (['work', 'history', 'settings', 'analysis'].includes(state.appMode as string)) {
    validated.appMode = state.appMode as AppMode;
  }
  if (typeof state.enableTabNotifications === 'boolean') {
    validated.enableTabNotifications = state.enableTabNotifications;
  }
  if (typeof state.autoFullscreenOnEnterChat === 'boolean') {
    validated.autoFullscreenOnEnterChat = state.autoFullscreenOnEnterChat;
  }
  if (typeof state.showFileChangesPanel === 'boolean') {
    validated.showFileChangesPanel = state.showFileChangesPanel;
  }

  return validated;
};

/**
 * Migrate from older versions
 * Issue #2953: Version migration support
 * @param persistedState - The raw state from localStorage
 * @param version - The version number of the persisted state
 * @returns Fully migrated and validated state
 */
export const migrate = (persistedState: unknown, version: number): PersistedState => {
  console.log('[Store] Migrating from version', version, 'to version 1');

  const validated = validateStoredState(persistedState);

  // Migration from version 0 (undefined) to version 1:
  // Add workspaceTabsOrder if missing
  const defaultState: PersistedState = {
    theme: 'light',
    language: 'en',
    sidebarCollapsed: false,
    appMode: 'work',
    enableTabNotifications: true,
    autoFullscreenOnEnterChat: false,
    showFileChangesPanel: true,
    workspaceTabs: [],
    workspaceActiveTabId: '',
    workspaceTabsOrder: [],
  };

  return {
    ...defaultState,
    ...validated,
    // Ensure workspaceTabsOrder defaults to order of existing tabs if missing
    workspaceTabsOrder:
      validated.workspaceTabsOrder ??
      (Array.isArray(validated.workspaceTabs) ? validated.workspaceTabs.map((t) => t.id) : []),
  };
};

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      // Initial state
      user: null,
      isAuthenticated: false,
      authLoading: true,
      theme: 'light',
      language: 'en',
      sidebarCollapsed: false,
      sidebarMobileOpen: false,
      appMode: 'work',

      // Workspace fullscreen state
      workspaceFullscreen: false,
      previousLeftPanelCollapsed: false,
      previousRightPanelCollapsed: false,

      // Workspace tabs state (Issue #65)
      workspaceTabs: [],
      workspaceActiveTabId: '',
      workspaceTabsOrder: [], // Issue #1470

      // Tab notification settings
      enableTabNotifications: true,

      // Auto fullscreen on enter chat setting (Issue #121)
      autoFullscreenOnEnterChat: false,

      // File changes panel visibility (Issue #144)
      showFileChangesPanel: true,

      // Feature flags
      autonomousEnabled: true,
      modelGatewayEnabled: false,
      runTimelineEnabled: false,
      policyEnabled: false,
      configLoaded: false,

      // Actions
      setUser: (user) => set({ user }),
      setAuthenticated: (isAuthenticated) => set({ isAuthenticated }),
      setAuthLoading: (authLoading) => set({ authLoading }),
      setTheme: (theme) => {
        set({ theme });
        // Apply theme to document
        document.documentElement.setAttribute('data-theme', theme);
        document.body.classList.toggle('dark-theme', theme === 'dark');
      },
      setLanguage: (language) => {
        set({ language });
        // Sync i18n module and i18next library
        localStorage.setItem('language', language);
        localStorage.setItem('i18nextLng', language);
      },
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
      toggleMobileSidebar: () => set((state) => ({ sidebarMobileOpen: !state.sidebarMobileOpen })),
      setMobileSidebarOpen: (open) => set({ sidebarMobileOpen: open }),
      setAppMode: (appMode) => set({ appMode }),
      logout: () =>
        set({
          user: null,
          isAuthenticated: false,
          authLoading: false,
          // Clear workspace tabs on logout
          workspaceTabs: [],
          workspaceActiveTabId: '',
        }),

      // Workspace fullscreen actions
      enterWorkspaceFullscreen: (leftCollapsed, rightCollapsed) =>
        set({
          workspaceFullscreen: true,
          previousLeftPanelCollapsed: leftCollapsed,
          previousRightPanelCollapsed: rightCollapsed,
        }),
      exitWorkspaceFullscreen: () => set({ workspaceFullscreen: false }),
      toggleWorkspaceFullscreen: (leftCollapsed, rightCollapsed) =>
        set((state) => {
          if (state.workspaceFullscreen) {
            return { workspaceFullscreen: false };
          } else {
            return {
              workspaceFullscreen: true,
              previousLeftPanelCollapsed: leftCollapsed,
              previousRightPanelCollapsed: rightCollapsed,
            };
          }
        }),

      // Workspace tabs actions (Issue #65)
      setWorkspaceTabs: (tabs) => set({ workspaceTabs: tabs }),
      setWorkspaceActiveTabId: (tabId) => set({ workspaceActiveTabId: tabId }),
      addWorkspaceTab: (tab) =>
        set((state) => ({
          workspaceTabs: [...state.workspaceTabs, tab],
          workspaceActiveTabId: tab.id,
        })),
      updateWorkspaceTab: (tabId, updates) =>
        set((state) => ({
          workspaceTabs: state.workspaceTabs.map((tab) =>
            tab.id === tabId ? { ...tab, ...updates } : tab
          ),
        })),
      removeWorkspaceTab: (tabId) =>
        set((state) => {
          const newTabs = state.workspaceTabs.filter((tab) => tab.id !== tabId);
          // If removing active tab, switch to another tab
          let newActiveTabId = state.workspaceActiveTabId;
          if (state.workspaceActiveTabId === tabId && newTabs.length > 0) {
            const removedIndex = state.workspaceTabs.findIndex((tab) => tab.id === tabId);
            const newActiveIndex = Math.min(removedIndex, newTabs.length - 1);
            newActiveTabId = newTabs[newActiveIndex].id;
          } else if (newTabs.length === 0) {
            newActiveTabId = '';
          }
          return {
            workspaceTabs: newTabs,
            workspaceActiveTabId: newActiveTabId,
          };
        }),
      clearWorkspaceTabs: () =>
        set({
          workspaceTabs: [],
          workspaceActiveTabId: '',
        }),
      reorderWorkspaceTabs: (fromIndex, toIndex) =>
        set((state) => {
          const tabs = [...state.workspaceTabs];
          const [removed] = tabs.splice(fromIndex, 1);
          tabs.splice(toIndex, 0, removed);
          return { workspaceTabs: tabs };
        }),
      setWorkspaceTabsOrder: (order) =>
        set((state) => ({
          workspaceTabsOrder: typeof order === 'function' ? order(state.workspaceTabsOrder) : order,
        })),

      // Tab notification actions
      setEnableTabNotifications: (enabled) => set({ enableTabNotifications: enabled }),
      toggleTabNotifications: () =>
        set((state) => ({ enableTabNotifications: !state.enableTabNotifications })),

      // Auto fullscreen actions (Issue #121)
      setAutoFullscreenOnEnterChat: (enabled) => set({ autoFullscreenOnEnterChat: enabled }),
      toggleAutoFullscreenOnEnterChat: () =>
        set((state) => ({ autoFullscreenOnEnterChat: !state.autoFullscreenOnEnterChat })),

      // File changes panel actions (Issue #144)
      setShowFileChangesPanel: (enabled) => set({ showFileChangesPanel: enabled }),
      toggleFileChangesPanel: () =>
        set((state) => ({ showFileChangesPanel: !state.showFileChangesPanel })),

      // Feature flag actions
      setAutonomousEnabled: (enabled) => set({ autonomousEnabled: enabled }),
      setModelGatewayEnabled: (enabled) => set({ modelGatewayEnabled: enabled }),
      setRunTimelineEnabled: (enabled) => set({ runTimelineEnabled: enabled }),
      setPolicyEnabled: (enabled) => set({ policyEnabled: enabled }),
      setConfigLoaded: (loaded) => set({ configLoaded: loaded }),
    }),
    {
      name: 'open-ace-store',
      version: 1, // Issue #2953: Add version for migration support
      migrate, // Issue #2953: Migration function handles version updates and validation
      // Issue #2953: Error handling during rehydration is done in migrate function
      partialize: (state): PersistedState => ({
        theme: state.theme,
        language: state.language,
        sidebarCollapsed: state.sidebarCollapsed,
        appMode: state.appMode,
        enableTabNotifications: state.enableTabNotifications,
        autoFullscreenOnEnterChat: state.autoFullscreenOnEnterChat,
        showFileChangesPanel: state.showFileChangesPanel,
        // Issue #65: Persist workspace tabs state (exclude sensitive fields)
        // IMPORTANT: When adding new WorkspaceTab fields, ensure they are either:
        // 1. Safe to persist (add to rest spread below)
        // 2. Sensitive (add to destructuring exclusion)
        workspaceTabs: state.workspaceTabs.map(
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          ({ terminalToken, terminalWsUrl, waitingForUser, waitingType, ...rest }) => rest
        ),
        workspaceActiveTabId: state.workspaceActiveTabId,
        workspaceTabsOrder: state.workspaceTabsOrder, // Issue #1470
      }),
    }
  )
);

// Selectors
export const useUser = () => useAppStore((state) => state.user);
export const useIsAuthenticated = () => useAppStore((state) => state.isAuthenticated);
export const useAuthLoading = () => useAppStore((state) => state.authLoading);
export const useMustChangePassword = () =>
  useAppStore((state) => state.user?.must_change_password ?? false);
export const useTheme = () => useAppStore((state) => state.theme);
export const useLanguage = () => useAppStore((state) => state.language);
export const useSidebarCollapsed = () => useAppStore((state) => state.sidebarCollapsed);
export const useMobileSidebarOpen = () => useAppStore((state) => state.sidebarMobileOpen);
export const useAppMode = () => useAppStore((state) => state.appMode);
export const useWorkspaceFullscreen = () => useAppStore((state) => state.workspaceFullscreen);
export const useEnableTabNotifications = () => useAppStore((state) => state.enableTabNotifications);
export const useAutoFullscreenOnEnterChat = () =>
  useAppStore((state) => state.autoFullscreenOnEnterChat);
export const useShowFileChangesPanel = () => useAppStore((state) => state.showFileChangesPanel);
export const usePreviousPanelState = () =>
  useAppStore((state) => ({
    left: state.previousLeftPanelCollapsed,
    right: state.previousRightPanelCollapsed,
  }));

// Workspace tabs selectors (Issue #65)
export const useWorkspaceTabs = () => useAppStore((state) => state.workspaceTabs);
export const useWorkspaceActiveTabId = () => useAppStore((state) => state.workspaceActiveTabId);
export const useWorkspaceTabsOrder = () => useAppStore((state) => state.workspaceTabsOrder); // Issue #1470

// Separate action selectors for stable references (fixes infinite loop)
export const useSetWorkspaceTabs = () => useAppStore((state) => state.setWorkspaceTabs);
export const useSetWorkspaceActiveTabId = () =>
  useAppStore((state) => state.setWorkspaceActiveTabId);
export const useAddWorkspaceTab = () => useAppStore((state) => state.addWorkspaceTab);
export const useUpdateWorkspaceTab = () => useAppStore((state) => state.updateWorkspaceTab);
export const useRemoveWorkspaceTab = () => useAppStore((state) => state.removeWorkspaceTab);
export const useClearWorkspaceTabs = () => useAppStore((state) => state.clearWorkspaceTabs);
export const useReorderWorkspaceTabs = () => useAppStore((state) => state.reorderWorkspaceTabs);
export const useSetWorkspaceTabsOrder = () => useAppStore((state) => state.setWorkspaceTabsOrder); // Issue #1470

// Legacy selector - DEPRECATED: Use individual action selectors instead for stable references
export const useWorkspaceTabsActions = () =>
  useAppStore((state) => ({
    setTabs: state.setWorkspaceTabs,
    setActiveTabId: state.setWorkspaceActiveTabId,
    addTab: state.addWorkspaceTab,
    updateTab: state.updateWorkspaceTab,
    removeTab: state.removeWorkspaceTab,
    clearTabs: state.clearWorkspaceTabs,
  }));

// Export page refresh store
export {
  usePageRefreshStore,
  usePageConfig,
  useGlobalPaused,
  useSetPageConfig,
  useRecordRefresh,
  type PageRefreshConfig,
} from './pageRefreshStore';

// Feature flags selectors
export const useAutonomousEnabled = () => useAppStore((state) => state.autonomousEnabled);
export const useModelGatewayEnabled = () => useAppStore((state) => state.modelGatewayEnabled);
export const useRunTimelineEnabled = () => useAppStore((state) => state.runTimelineEnabled);
export const usePolicyEnabled = () => useAppStore((state) => state.policyEnabled);
export const useConfigLoaded = () => useAppStore((state) => state.configLoaded);

// Admin tenant store (Issue #2841)
export { useAdminTenantStore } from './adminTenantStore';

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
  sessionId?: string; // Session ID from qwen-code-webui (extracted from URL or backend)
  encodedProjectName?: string; // Encoded project path for session restoration
  toolName?: string; // Tool name for session restoration
  createdAt: number;
  waitingForUser: boolean;
  waitingType: 'permission' | 'plan' | 'input' | null;
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
  appMode: AppMode;

  // Workspace fullscreen state
  workspaceFullscreen: boolean;
  previousLeftPanelCollapsed: boolean;
  previousRightPanelCollapsed: boolean;

  // Workspace tabs state (Issue #65: Persist workspace state)
  workspaceTabs: WorkspaceTab[];
  workspaceActiveTabId: string;

  // Tab notification settings
  enableTabNotifications: boolean;

  // Actions
  setUser: (user: User | null) => void;
  setAuthenticated: (isAuthenticated: boolean) => void;
  setAuthLoading: (loading: boolean) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
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

  // Tab notification actions
  setEnableTabNotifications: (enabled: boolean) => void;
  toggleTabNotifications: () => void;
}

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
      appMode: 'work',

      // Workspace fullscreen state
      workspaceFullscreen: false,
      previousLeftPanelCollapsed: false,
      previousRightPanelCollapsed: false,

      // Workspace tabs state (Issue #65)
      workspaceTabs: [],
      workspaceActiveTabId: '',

      // Tab notification settings
      enableTabNotifications: true,

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
      setLanguage: (language) => set({ language }),
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
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

      // Tab notification actions
      setEnableTabNotifications: (enabled) => set({ enableTabNotifications: enabled }),
      toggleTabNotifications: () =>
        set((state) => ({ enableTabNotifications: !state.enableTabNotifications })),
    }),
    {
      name: 'open-ace-store',
      partialize: (state) => ({
        theme: state.theme,
        language: state.language,
        sidebarCollapsed: state.sidebarCollapsed,
        appMode: state.appMode,
        enableTabNotifications: state.enableTabNotifications,
        // Issue #65: Persist workspace tabs state
        workspaceTabs: state.workspaceTabs,
        workspaceActiveTabId: state.workspaceActiveTabId,
      }),
    }
  )
);

// Selectors
export const useUser = () => useAppStore((state) => state.user);
export const useIsAuthenticated = () => useAppStore((state) => state.isAuthenticated);
export const useAuthLoading = () => useAppStore((state) => state.authLoading);
export const useTheme = () => useAppStore((state) => state.theme);
export const useLanguage = () => useAppStore((state) => state.language);
export const useSidebarCollapsed = () => useAppStore((state) => state.sidebarCollapsed);
export const useAppMode = () => useAppStore((state) => state.appMode);
export const useWorkspaceFullscreen = () => useAppStore((state) => state.workspaceFullscreen);
export const useEnableTabNotifications = () => useAppStore((state) => state.enableTabNotifications);
export const usePreviousPanelState = () =>
  useAppStore((state) => ({
    left: state.previousLeftPanelCollapsed,
    right: state.previousRightPanelCollapsed,
  }));

// Workspace tabs selectors (Issue #65)
export const useWorkspaceTabs = () => useAppStore((state) => state.workspaceTabs);
export const useWorkspaceActiveTabId = () => useAppStore((state) => state.workspaceActiveTabId);

// Separate action selectors for stable references (fixes infinite loop)
export const useSetWorkspaceTabs = () => useAppStore((state) => state.setWorkspaceTabs);
export const useSetWorkspaceActiveTabId = () =>
  useAppStore((state) => state.setWorkspaceActiveTabId);
export const useAddWorkspaceTab = () => useAppStore((state) => state.addWorkspaceTab);
export const useUpdateWorkspaceTab = () => useAppStore((state) => state.updateWorkspaceTab);
export const useRemoveWorkspaceTab = () => useAppStore((state) => state.removeWorkspaceTab);
export const useClearWorkspaceTabs = () => useAppStore((state) => state.clearWorkspaceTabs);

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

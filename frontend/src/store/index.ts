/**
 * App Store - Global application state using Zustand
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User, Theme, Language, AppMode } from '@/types';

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

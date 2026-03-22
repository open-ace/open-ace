/**
 * App Store - Global application state using Zustand
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User, Theme, Language } from '@/types';

interface AppState {
  // Auth state
  user: User | null;
  isAuthenticated: boolean;
  authLoading: boolean;

  // UI state
  theme: Theme;
  language: Language;
  sidebarCollapsed: boolean;

  // Actions
  setUser: (user: User | null) => void;
  setAuthenticated: (isAuthenticated: boolean) => void;
  setAuthLoading: (loading: boolean) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  logout: () => void;
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
      logout: () =>
        set({
          user: null,
          isAuthenticated: false,
          authLoading: false,
        }),
    }),
    {
      name: 'open-ace-store',
      partialize: (state) => ({
        theme: state.theme,
        language: state.language,
        sidebarCollapsed: state.sidebarCollapsed,
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

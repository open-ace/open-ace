/**
 * Tests for App Store
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
  useAppStore,
  useUser,
  useIsAuthenticated,
  useAuthLoading,
  useTheme,
  useLanguage,
  useSidebarCollapsed,
} from './index';

// Mock zustand persist
vi.mock('zustand/middleware', () => ({
  persist: vi.fn((fn) => fn),
}));

describe('App Store', () => {
  beforeEach(() => {
    // Reset store state before each test
    useAppStore.setState({
      user: null,
      isAuthenticated: false,
      authLoading: true,
      theme: 'light',
      language: 'en',
      sidebarCollapsed: false,
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have correct initial values', () => {
      const state = useAppStore.getState();

      expect(state.user).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.authLoading).toBe(true);
      expect(state.theme).toBe('light');
      expect(state.language).toBe('en');
      expect(state.sidebarCollapsed).toBe(false);
    });
  });

  describe('auth actions', () => {
    it('should set user', () => {
      const user = {
        id: '1',
        username: 'testuser',
        email: 'test@example.com',
        role: 'admin' as const,
        createdAt: '2024-01-01',
      };

      act(() => {
        useAppStore.getState().setUser(user);
      });

      expect(useAppStore.getState().user).toEqual(user);
    });

    it('should set authenticated', () => {
      act(() => {
        useAppStore.getState().setAuthenticated(true);
      });

      expect(useAppStore.getState().isAuthenticated).toBe(true);
    });

    it('should set auth loading', () => {
      act(() => {
        useAppStore.getState().setAuthLoading(false);
      });

      expect(useAppStore.getState().authLoading).toBe(false);
    });

    it('should logout and clear auth state', () => {
      // Set up authenticated state
      act(() => {
        useAppStore.getState().setUser({
          id: '1',
          username: 'testuser',
          email: 'test@example.com',
          role: 'admin',
          createdAt: '2024-01-01',
        });
        useAppStore.getState().setAuthenticated(true);
      });

      // Logout
      act(() => {
        useAppStore.getState().logout();
      });

      const state = useAppStore.getState();
      expect(state.user).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.authLoading).toBe(false);
    });
  });

  describe('theme actions', () => {
    it('should set theme', () => {
      const mockSetAttribute = vi.fn();
      const mockToggle = vi.fn();
      document.documentElement.setAttribute = mockSetAttribute;
      document.body.classList.toggle = mockToggle;

      act(() => {
        useAppStore.getState().setTheme('dark');
      });

      expect(useAppStore.getState().theme).toBe('dark');
      expect(mockSetAttribute).toHaveBeenCalledWith('data-theme', 'dark');
      expect(mockToggle).toHaveBeenCalledWith('dark-theme', true);
    });
  });

  describe('language actions', () => {
    it('should set language', () => {
      act(() => {
        useAppStore.getState().setLanguage('zh');
      });

      expect(useAppStore.getState().language).toBe('zh');
    });
  });

  describe('sidebar actions', () => {
    it('should toggle sidebar', () => {
      expect(useAppStore.getState().sidebarCollapsed).toBe(false);

      act(() => {
        useAppStore.getState().toggleSidebar();
      });

      expect(useAppStore.getState().sidebarCollapsed).toBe(true);

      act(() => {
        useAppStore.getState().toggleSidebar();
      });

      expect(useAppStore.getState().sidebarCollapsed).toBe(false);
    });

    it('should set sidebar collapsed', () => {
      act(() => {
        useAppStore.getState().setSidebarCollapsed(true);
      });

      expect(useAppStore.getState().sidebarCollapsed).toBe(true);
    });
  });

  describe('selectors', () => {
    it('should select user', () => {
      const { result } = renderHook(() => useUser());

      expect(result.current).toBeNull();

      act(() => {
        useAppStore.getState().setUser({
          id: '1',
          username: 'testuser',
          email: 'test@example.com',
          role: 'admin',
          createdAt: '2024-01-01',
        });
      });

      expect(result.current).toEqual({
        id: '1',
        username: 'testuser',
        email: 'test@example.com',
        role: 'admin',
        createdAt: '2024-01-01',
      });
    });

    it('should select isAuthenticated', () => {
      const { result } = renderHook(() => useIsAuthenticated());

      expect(result.current).toBe(false);

      act(() => {
        useAppStore.getState().setAuthenticated(true);
      });

      expect(result.current).toBe(true);
    });

    it('should select authLoading', () => {
      const { result } = renderHook(() => useAuthLoading());

      expect(result.current).toBe(true);

      act(() => {
        useAppStore.getState().setAuthLoading(false);
      });

      expect(result.current).toBe(false);
    });

    it('should select theme', () => {
      const { result } = renderHook(() => useTheme());

      expect(result.current).toBe('light');

      act(() => {
        useAppStore.getState().setTheme('dark');
      });

      expect(result.current).toBe('dark');
    });

    it('should select language', () => {
      const { result } = renderHook(() => useLanguage());

      expect(result.current).toBe('en');

      act(() => {
        useAppStore.getState().setLanguage('ja');
      });

      expect(result.current).toBe('ja');
    });

    it('should select sidebarCollapsed', () => {
      const { result } = renderHook(() => useSidebarCollapsed());

      expect(result.current).toBe(false);

      act(() => {
        useAppStore.getState().setSidebarCollapsed(true);
      });

      expect(result.current).toBe(true);
    });
  });
});

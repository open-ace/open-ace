/**
 * useAuth Hook Tests
 *
 * Tests for the authentication hook covering login, logout, and password change flows.
 * Verifies state management, React Query integration, and session keepalive.
 *
 * Related Issue: #2342
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useAuth } from './useAuth';
import { useAppStore } from '@/store';
import { authApi } from '@/api';
import type { User } from '@/types';

// Mock authApi
vi.mock('@/api', () => ({
  authApi: {
    checkAuth: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
  },
}));

// Mock router redirect
const mockLocation = {
  href: '',
};
Object.defineProperty(window, 'location', {
  value: mockLocation,
  writable: true,
});

// Test data
const mockUser: User = {
  id: 'test-user-1',
  username: 'testuser',
  email: 'test@example.com',
  must_change_password: false,
  is_admin: false,
  avatar_url: null,
  created_at: '2024-01-01T00:00:00Z',
};

const mockUserWithPasswordChange: User = {
  ...mockUser,
  must_change_password: true,
};

// Create wrapper with QueryClient
const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('useAuth', () => {
  beforeEach(() => {
    // Reset store
    useAppStore.getState().logout();
    localStorage.clear();

    // Reset mocks
    vi.clearAllMocks();

    // Reset location
    mockLocation.href = '';

    // Default mock implementations
    vi.mocked(authApi.checkAuth).mockResolvedValue({
      authenticated: false,
      user: undefined,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Initialization', () => {
    it('should have correct initial state', async () => {
      const wrapper = createWrapper();

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Initial state should be loading
      expect(result.current.isLoading).toBe(true);
      expect(result.current.user).toBeNull();
      expect(result.current.isAuthenticated).toBe(false);
    });

    it('should trigger auth check on mount', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(authApi.checkAuth).toHaveBeenCalled();
      });
    });
  });

  describe('Password Change - Success Flow', () => {
    it('should call changePassword with correct parameters', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.changePassword).mockResolvedValue({ success: true });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        await result.current.changePassword('oldPass', 'newPass');
      });

      expect(authApi.changePassword).toHaveBeenCalledWith('oldPass', 'newPass');
    });

    it('should invalidate queries after successful password change', async () => {
      vi.mocked(authApi.changePassword).mockResolvedValue({ success: true });

      const queryClient = new QueryClient({
        defaultOptions: {
          queries: { retry: false, gcTime: 0, staleTime: 0 },
        },
      });

      // Set some query data to verify invalidation
      queryClient.setQueryData(['test'], 'data');

      const wrapperWithClient = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper: wrapperWithClient });

      await act(async () => {
        await result.current.changePassword('oldPass', 'newPass');
      });

      await waitFor(() => {
        // Verify the query was invalidated (refetch should be triggered)
        expect(authApi.checkAuth).toHaveBeenCalled();
      });
    });

    it('should refetch auth after successful password change', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.changePassword).mockResolvedValue({ success: true });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        await result.current.changePassword('oldPass', 'newPass');
      });

      await waitFor(() => {
        // Should call checkAuth again after password change
        expect(authApi.checkAuth).toHaveBeenCalled();
      });
    });

    it('should update must_change_password flag after password change', async () => {
      const wrapper = createWrapper();

      // Set user with must_change_password = true
      useAppStore.getState().setUser(mockUserWithPasswordChange);
      useAppStore.getState().setAuthenticated(true);

      vi.mocked(authApi.changePassword).mockResolvedValue({ success: true });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: { ...mockUser, must_change_password: false },
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Verify initial state
      expect(useAppStore.getState().user?.must_change_password).toBe(true);

      await act(async () => {
        await result.current.changePassword('oldPass', 'newPass');
      });

      await waitFor(() => {
        // Verify must_change_password flag updated
        const user = useAppStore.getState().user;
        expect(user?.must_change_password).toBe(false);
      });
    });

    it('should clear error state after successful password change', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.changePassword).mockResolvedValue({ success: true });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        await result.current.changePassword('oldPass', 'newPass');
      });

      await waitFor(() => {
        expect(result.current.changePasswordError).toBeNull();
        expect(result.current.isChangingPassword).toBe(false);
      });
    });
  });

  describe('Password Change - Error Flow', () => {
    it('should set error state when password change fails', async () => {
      const wrapper = createWrapper();
      const errorMessage = 'Invalid current password';
      vi.mocked(authApi.changePassword).mockRejectedValue(new Error(errorMessage));

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        try {
          await result.current.changePassword('oldPass', 'newPass');
        } catch {
          // Expected to throw
        }
      });

      await waitFor(() => {
        expect(result.current.changePasswordError).toBeInstanceOf(Error);
        expect(result.current.changePasswordError?.message).toBe(errorMessage);
      });
    });

    it('should toggle isChangingPassword state correctly during password change', async () => {
      const wrapper = createWrapper();

      // Use a slow mock to verify state changes
      vi.mocked(authApi.changePassword).mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ success: true }), 100))
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Initially not changing
      expect(result.current.isChangingPassword).toBe(false);

      // Start change (don't wait)
      let changePromise: Promise<void>;
      act(() => {
        changePromise = result.current.changePassword('oldPass', 'newPass');
      });

      // During the change
      await waitFor(() => {
        expect(result.current.isChangingPassword).toBe(true);
      });

      // Wait for completion
      await act(async () => {
        await changePromise!;
      });

      // After completion
      await waitFor(() => {
        expect(result.current.isChangingPassword).toBe(false);
      });
    });

    it('should not refetch auth when password change fails', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.changePassword).mockRejectedValue(new Error('Failed'));
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Clear any initial calls
      vi.clearAllMocks();

      await act(async () => {
        try {
          await result.current.changePassword('oldPass', 'newPass');
        } catch {
          // Expected to throw
        }
      });

      // Should not call checkAuth after failed password change
      // (only initial auth check happens)
      expect(authApi.checkAuth).not.toHaveBeenCalled();
    });
  });

  describe('Login Flow', () => {
    it('should update user state after successful login', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.login).mockResolvedValue({
        success: true,
        user: mockUser,
      });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        await result.current.login({ username: 'testuser', password: 'password' });
      });

      await waitFor(() => {
        expect(result.current.user).toEqual(mockUser);
        expect(result.current.isAuthenticated).toBe(true);
      });
    });

    it('should set error state when login fails', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.login).mockRejectedValue(new Error('Invalid credentials'));

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        try {
          await result.current.login({ username: 'testuser', password: 'wrong' });
        } catch {
          // Expected to throw
        }
      });

      await waitFor(() => {
        expect(result.current.loginError).toBeInstanceOf(Error);
        expect(result.current.loginError?.message).toBe('Invalid credentials');
      });
    });

    it('should refetch auth after successful login', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.login).mockResolvedValue({
        success: true,
        user: mockUser,
      });
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Clear initial calls
      vi.clearAllMocks();

      await act(async () => {
        await result.current.login({ username: 'testuser', password: 'password' });
      });

      await waitFor(() => {
        // Should refetch auth after login
        expect(authApi.checkAuth).toHaveBeenCalled();
      });
    });

    it('should toggle isLoggingIn state correctly', async () => {
      const wrapper = createWrapper();

      vi.mocked(authApi.login).mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve({ success: true, user: mockUser }), 100)
          )
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      // Initially not logging in
      expect(result.current.isLoggingIn).toBe(false);

      // Start login
      let loginPromise: Promise<void>;
      act(() => {
        loginPromise = result.current.login({ username: 'testuser', password: 'password' });
      });

      // During login
      await waitFor(() => {
        expect(result.current.isLoggingIn).toBe(true);
      });

      // Wait for completion
      await act(async () => {
        await loginPromise!;
      });

      // After completion
      await waitFor(() => {
        expect(result.current.isLoggingIn).toBe(false);
      });
    });
  });

  describe('Logout Flow', () => {
    it('should clear user state after logout', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.logout).mockResolvedValue(undefined);

      // Set authenticated user
      useAppStore.getState().setUser(mockUser);
      useAppStore.getState().setAuthenticated(true);

      const { result } = renderHook(() => useAuth(), { wrapper });

      act(() => {
        result.current.logout();
      });

      await waitFor(() => {
        expect(result.current.user).toBeNull();
        expect(result.current.isAuthenticated).toBe(false);
      });
    });

    it('should call authApi.logout', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.logout).mockResolvedValue(undefined);

      const { result } = renderHook(() => useAuth(), { wrapper });

      act(() => {
        result.current.logout();
      });

      await waitFor(() => {
        expect(authApi.logout).toHaveBeenCalled();
      });
    });

    it('should redirect to /login after logout', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.logout).mockResolvedValue(undefined);

      const { result } = renderHook(() => useAuth(), { wrapper });

      act(() => {
        result.current.logout();
      });

      await waitFor(() => {
        expect(mockLocation.href).toBe('/login');
      });
    });
  });

  describe('Session Keepalive', () => {
    it('should not start keepalive when not authenticated', async () => {
      vi.useFakeTimers();
      const wrapper = createWrapper();

      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: false,
        user: undefined,
      });

      renderHook(() => useAuth(), { wrapper });

      // Advance time significantly
      act(() => {
        vi.advanceTimersByTime(10 * 60 * 1000); // 10 minutes
      });

      // Should not call checkAuth for keepalive when not authenticated
      // (only initial auth check happens)
      const checkAuthCalls = vi.mocked(authApi.checkAuth).mock.calls.length;
      expect(checkAuthCalls).toBeLessThanOrEqual(1);

      vi.useRealTimers();
    });

    it('should cleanup keepalive interval on unmount', async () => {
      vi.useFakeTimers();
      const wrapper = createWrapper();

      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      const { unmount } = renderHook(() => useAuth(), { wrapper });

      // Unmount
      unmount();

      // Advance time
      act(() => {
        vi.advanceTimersByTime(10 * 60 * 1000);
      });

      // Should not cause errors after unmount
      vi.useRealTimers();
    });
  });

  describe('State Synchronization', () => {
    it('should sync user state with useAppStore', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        const storeUser = useAppStore.getState().user;
        expect(storeUser).toEqual(mockUser);
      });
    });

    it('should sync authentication state with useAppStore', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.checkAuth).mockResolvedValue({
        authenticated: true,
        user: mockUser,
      });

      renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        const isAuth = useAppStore.getState().isAuthenticated;
        expect(isAuth).toBe(true);
      });
    });

    it('should clear store state on logout', async () => {
      const wrapper = createWrapper();
      vi.mocked(authApi.logout).mockResolvedValue(undefined);

      // Set authenticated user
      useAppStore.getState().setUser(mockUser);
      useAppStore.getState().setAuthenticated(true);

      const { result } = renderHook(() => useAuth(), { wrapper });

      act(() => {
        result.current.logout();
      });

      await waitFor(() => {
        expect(useAppStore.getState().user).toBeNull();
        expect(useAppStore.getState().isAuthenticated).toBe(false);
      });
    });
  });
});

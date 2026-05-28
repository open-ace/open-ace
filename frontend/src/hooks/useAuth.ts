/**
 * useAuth Hook - Authentication hook
 */

import { useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authApi } from '@/api';
import { useAppStore } from '@/store';
import type { LoginRequest } from '@/api';

// Session keepalive interval (5 minutes)
// Backend refreshes session when remaining time < 10 minutes
// So 5-minute interval ensures session is always refreshed before expiry
const SESSION_KEEPALIVE_INTERVAL_MS = 5 * 60 * 1000;

export function useAuth() {
  const queryClient = useQueryClient();
  const {
    user,
    isAuthenticated,
    authLoading,
    setUser,
    setAuthenticated,
    setAuthLoading,
    logout: logoutStore,
  } = useAppStore();

  // Ref for keepalive interval
  const keepaliveIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check auth status on mount
  const {
    isLoading: isCheckingAuth,
    data: authData,
    refetch: refetchAuth,
  } = useQuery({
    queryKey: ['auth', 'check'],
    queryFn: authApi.checkAuth,
    staleTime: 0, // Always refetch
    refetchOnWindowFocus: true,
  });

  // Session keepalive - periodically refresh auth to extend session
  useEffect(() => {
    // Clear existing interval
    if (keepaliveIntervalRef.current) {
      clearInterval(keepaliveIntervalRef.current);
      keepaliveIntervalRef.current = null;
    }

    // Only start keepalive when authenticated
    if (isAuthenticated) {
      keepaliveIntervalRef.current = setInterval(() => {
        // Silently refetch auth to trigger session refresh on backend
        refetchAuth().catch(() => {
          // Ignore errors - session may have expired, will be handled by auth check
        });
      }, SESSION_KEEPALIVE_INTERVAL_MS);
    }

    // Cleanup on unmount or when auth status changes
    return () => {
      if (keepaliveIntervalRef.current) {
        clearInterval(keepaliveIntervalRef.current);
        keepaliveIntervalRef.current = null;
      }
    };
  }, [isAuthenticated, refetchAuth]);

  // Update store when auth check completes
  useEffect(() => {
    if (!isCheckingAuth && authData) {
      setAuthLoading(false);
      if (authData.authenticated && authData.user) {
        setUser(authData.user);
        setAuthenticated(true);
      } else {
        setUser(null);
        setAuthenticated(false);
      }
    }
  }, [isCheckingAuth, authData, setAuthLoading, setUser, setAuthenticated]);

  // Login mutation
  const loginMutation = useMutation({
    mutationFn: (credentials: LoginRequest) => authApi.login(credentials),
    onSuccess: async (data) => {
      if (data.success && data.user) {
        setUser(data.user);
        setAuthenticated(true);
        setAuthLoading(false);
        // Force refetch auth status
        await refetchAuth();
      }
    },
  });

  // Logout mutation
  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      logoutStore();
      queryClient.clear();
      window.location.href = '/login';
    },
  });

  const login = useCallback(
    (credentials: LoginRequest) => loginMutation.mutateAsync(credentials),
    [loginMutation]
  );

  const logout = useCallback(() => logoutMutation.mutate(), [logoutMutation]);

  return {
    user,
    isAuthenticated,
    isLoading: authLoading || isCheckingAuth,
    login,
    logout,
    loginError: loginMutation.error,
    isLoggingIn: loginMutation.isPending,
    isLoggingOut: logoutMutation.isPending,
  };
}

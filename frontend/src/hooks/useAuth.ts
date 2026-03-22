/**
 * useAuth Hook - Authentication hook
 */

import { useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { authApi } from '@/api';
import { useAppStore } from '@/store';
import type { LoginRequest } from '@/api';

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

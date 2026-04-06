/**
 * useSessions Hook - Sessions data fetching hook
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { sessionsApi } from '@/api';
import type { SessionFilters, AgentSession } from '@/api/sessions';

interface UseSessionsOptions {
  filters?: SessionFilters;
  pageSize?: number;
  enabled?: boolean;
  page?: number;
}

export function useSessions(options: UseSessionsOptions = {}) {
  const { filters = {}, pageSize = 20, enabled = true, page = 1 } = options;

  return useQuery({
    queryKey: ['sessions', page, { filters, pageSize }],
    queryFn: () => sessionsApi.getSessions(filters, page, pageSize),
    enabled,
    staleTime: 30 * 1000, // 30 seconds
  });
}

export function useSession(sessionId: string, includeMessages: boolean = false, enabled = true) {
  return useQuery<{ success: boolean; data: AgentSession; error?: string }>({
    queryKey: ['sessions', sessionId, includeMessages],
    queryFn: () => sessionsApi.getSession(sessionId, includeMessages),
    enabled: enabled && !!sessionId,
    staleTime: 60 * 1000, // 1 minute
  });
}

export function useSessionStats(enabled = true) {
  return useQuery({
    queryKey: ['sessions', 'stats'],
    queryFn: () => sessionsApi.getSessionStats(),
    enabled,
    staleTime: 60 * 1000, // 1 minute
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.deleteSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function useCompleteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.completeSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function useRenameSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ sessionId, newName }: { sessionId: string; newName: string }) =>
      sessionsApi.renameSession(sessionId, newName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function useRestoreSession() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.restoreSession(sessionId),
    onSuccess: (data) => {
      if (data.success && data.data) {
        // Navigate to workspace with the restored session
        // The backend returns the full URL with all parameters
        console.log('Restoring session:', data.data);
        navigate(data.data.url);
      } else if (data.error) {
        console.error('Restore failed:', data.error);
      }
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
    onError: (error) => {
      console.error('Restore mutation error:', error);
    },
  });
}

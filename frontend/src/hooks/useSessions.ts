/**
 * useSessions Hook - Sessions data fetching hook
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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

export function useSession(
  sessionId: string,
  includeMessages: boolean = false,
  enabled = true,
  messageLimit?: number
) {
  return useQuery<{ success: boolean; data: AgentSession; error?: string }>({
    queryKey: ['sessions', sessionId, includeMessages, messageLimit ?? null],
    queryFn: () => sessionsApi.getSession(sessionId, includeMessages, messageLimit),
    enabled: enabled && !!sessionId,
    staleTime: 60 * 1000, // 1 minute
  });
}

/**
 * Fetch one older page of session messages via keyset cursor (Issue #241 #22).
 *
 * The most-recent page is delivered embedded in ``useSession(..., true)``; this
 * hook is for walking further back. It is keyed on the cursor so each distinct
 * older page is cached independently, and ``enabled`` lets the caller wait
 * until a cursor is available.
 */
export function useSessionMessages(
  sessionId: string,
  cursor: { timestamp: string; id: number } | null,
  enabled = true,
  limit?: number
) {
  return useQuery<{ success: boolean; data: import('@/api/sessions').SessionMessagesPage }>({
    queryKey: ['session-messages', sessionId, cursor?.timestamp ?? null, cursor?.id ?? null],
    queryFn: () =>
      sessionsApi.getSessionMessages(sessionId, {
        beforeTimestamp: cursor?.timestamp,
        beforeId: cursor?.id,
        limit,
      }),
    enabled: enabled && !!sessionId && !!cursor,
    staleTime: 5 * 60 * 1000, // 5 minutes
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

  return useMutation({
    mutationFn: (sessionId: string) => sessionsApi.restoreSession(sessionId),
    onSuccess: (data) => {
      if (data.success && data.data) {
        // Navigate to workspace with the restored session
        // The backend returns the full URL with all parameters
        console.log('Restoring session:', data.data);
        // Use window.location.href for a full page reload to ensure URL parameters are processed
        window.location.href = data.data.url;
      } else if (data.can_recreate) {
        // Issue #669: Session process terminated, show recreation options
        // Return data for caller to display modal - mutation result will contain can_recreate info
        console.log('Session terminated, can recreate:', data);
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

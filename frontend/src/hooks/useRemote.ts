/**
 * Remote Workspace Hooks - Custom hooks for remote machine, API key, and session management
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { remoteApi } from '@/api';
import type { StoreApiKeyRequest, CreateRemoteSessionRequest } from '@/api';

// ==================== Machine Hooks ====================

export function useMachines() {
  return useQuery({
    queryKey: ['remote', 'machines'],
    queryFn: () => remoteApi.listMachines(),
  });
}

export function useMachineUsers(machineId: string | null) {
  return useQuery({
    queryKey: ['remote', 'machines', machineId, 'users'],
    queryFn: () => remoteApi.getMachineUsers(machineId!),
    enabled: !!machineId,
  });
}

export function useGenerateToken() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (tenantId?: number) => remoteApi.generateRegistrationToken(tenantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines'] });
    },
  });
}

export function useDeregisterMachine() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (machineId: string) => remoteApi.deregisterMachine(machineId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines'] });
      queryClient.invalidateQueries({ queryKey: ['remote', 'available-machines'] });
    },
  });
}

export function useAssignUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ machineId, userId, permission }: { machineId: string; userId: number; permission?: string }) =>
      remoteApi.assignUser(machineId, userId, permission),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines', variables.machineId, 'users'] });
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines'] });
      queryClient.invalidateQueries({ queryKey: ['remote', 'available-machines'] });
    },
  });
}

export function useRevokeUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ machineId, userId }: { machineId: string; userId: number }) =>
      remoteApi.revokeUser(machineId, userId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines', variables.machineId, 'users'] });
      queryClient.invalidateQueries({ queryKey: ['remote', 'machines'] });
      queryClient.invalidateQueries({ queryKey: ['remote', 'available-machines'] });
    },
  });
}

// ==================== API Key Hooks ====================

export function useApiKeys(tenantId?: number) {
  return useQuery({
    queryKey: ['remote', 'api-keys', tenantId],
    queryFn: () => remoteApi.listApiKeys(tenantId),
  });
}

export function useStoreApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: StoreApiKeyRequest) => remoteApi.storeApiKey(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'api-keys'] });
    },
  });
}

export function useDeleteApiKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ keyId, tenantId }: { keyId: number; tenantId?: number }) =>
      remoteApi.deleteApiKey(keyId, tenantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'api-keys'] });
    },
  });
}

// ==================== Available Machines Hooks ====================

export function useAvailableMachines() {
  return useQuery({
    queryKey: ['remote', 'available-machines'],
    queryFn: () => remoteApi.getAvailableMachines(),
  });
}

// ==================== Session Hooks ====================

export function useCreateRemoteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateRemoteSessionRequest) => remoteApi.createSession(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function useRemoteSession(sessionId: string | null) {
  return useQuery({
    queryKey: ['remote', 'sessions', sessionId],
    queryFn: () => remoteApi.getSession(sessionId!),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const data = query.state.data;
      // Paused sessions don't need frequent polling
      if (data && (data as any).status === 'paused') return 30000;
      return sessionId ? 3000 : false;
    },
  });
}

export function useSendMessage() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ sessionId, content }: { sessionId: string; content: string }) =>
      remoteApi.sendMessage(sessionId, content),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['remote', 'sessions', variables.sessionId] });
    },
  });
}

export function useStopRemoteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => remoteApi.stopSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function usePauseRemoteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => remoteApi.pauseSession(sessionId),
    onMutate: async (sessionId) => {
      await queryClient.cancelQueries({ queryKey: ['remote', 'sessions', sessionId] });
      const previous = queryClient.getQueryData(['remote', 'sessions', sessionId]);
      queryClient.setQueryData(['remote', 'sessions', sessionId], (old: any) =>
        old ? { ...old, status: 'paused' } : old
      );
      return { previous, sessionId };
    },
    onError: (_err, sessionId, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['remote', 'sessions', sessionId], context.previous);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

export function useResumeRemoteSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (sessionId: string) => remoteApi.resumeSession(sessionId),
    onMutate: async (sessionId) => {
      await queryClient.cancelQueries({ queryKey: ['remote', 'sessions', sessionId] });
      const previous = queryClient.getQueryData(['remote', 'sessions', sessionId]);
      queryClient.setQueryData(['remote', 'sessions', sessionId], (old: any) =>
        old ? { ...old, status: 'active' } : old
      );
      return { previous, sessionId };
    },
    onError: (_err, sessionId, context) => {
      if (context?.previous) {
        queryClient.setQueryData(['remote', 'sessions', sessionId], context.previous);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
}

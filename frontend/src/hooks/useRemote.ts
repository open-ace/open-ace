/**
 * Remote Workspace Hooks - Custom hooks for remote machine and API key management
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { remoteApi } from '@/api';
import type { StoreApiKeyRequest } from '@/api';

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

/**
 * Admin Hooks - Custom hooks for admin operations
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminApi, governanceApi } from '@/api';
import type {
  CreateUserRequest,
  UpdateUserRequest,
  UpdateQuotaRequest,
  AuditLogFilters,
  CreateFilterRuleRequest,
  SecuritySettings,
} from '@/api';

// User Management Hooks
export function useUsers() {
  return useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => adminApi.getUsers(),
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateUserRequest) => adminApi.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ userId, data }: { userId: number; data: UpdateUserRequest }) =>
      adminApi.updateUser(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (userId: number) => adminApi.deleteUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });
}

export function useUpdateUserPassword() {
  return useMutation({
    mutationFn: ({ userId, password }: { userId: number; password: string }) =>
      adminApi.updateUserPassword(userId, password),
  });
}

// Quota Management Hooks
export function useQuotaUsage() {
  return useQuery({
    queryKey: ['admin', 'quota'],
    queryFn: () => adminApi.getQuotaUsage(),
  });
}

export function useUpdateQuota() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ userId, data }: { userId: number; data: UpdateQuotaRequest }) =>
      adminApi.updateUserQuota(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'quota'] });
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });
}

// Audit Log Hooks
export function useAuditLogs(filters?: AuditLogFilters) {
  return useQuery({
    queryKey: ['admin', 'audit-logs', filters],
    queryFn: () => governanceApi.getAuditLogs(filters),
  });
}

// Content Filter Hooks
export function useFilterRules() {
  return useQuery({
    queryKey: ['admin', 'filter-rules'],
    queryFn: () => governanceApi.getFilterRules(),
  });
}

export function useCreateFilterRule() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateFilterRuleRequest) => governanceApi.createFilterRule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'filter-rules'] });
    },
  });
}

export function useUpdateFilterRule() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ ruleId, data }: { ruleId: number; data: Partial<CreateFilterRuleRequest> }) =>
      governanceApi.updateFilterRule(ruleId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'filter-rules'] });
    },
  });
}

export function useDeleteFilterRule() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (ruleId: number) => governanceApi.deleteFilterRule(ruleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'filter-rules'] });
    },
  });
}

// Security Settings Hooks
export function useSecuritySettings() {
  return useQuery({
    queryKey: ['admin', 'security-settings'],
    queryFn: () => governanceApi.getSecuritySettings(),
  });
}

export function useUpdateSecuritySettings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Partial<SecuritySettings>) => governanceApi.updateSecuritySettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'security-settings'] });
    },
  });
}

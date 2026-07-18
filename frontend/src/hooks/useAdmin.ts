/**
 * Admin Hooks - Custom hooks for admin operations
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { adminApi, governanceApi, complianceApi, tenantApi } from '@/api';
import {
  aiAgentSettingsApi,
  type AiAgentSettings,
  type TokenValidationRequest,
} from '@/api/aiAgentSettings';
import type {
  CreateUserRequest,
  UpdateUserRequest,
  UpdateQuotaRequest,
  AuditLogFilters,
  CreateFilterRuleRequest,
  SecuritySettings,
  AuditThresholds,
} from '@/api';

// User Management Hooks
export function useUsers(tenantId?: number) {
  return useQuery({
    queryKey: ['admin', 'users', tenantId],
    queryFn: () => adminApi.getUsers(tenantId),
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

export function useResetUserPassword() {
  return useMutation({
    mutationFn: (userId: number) => adminApi.resetUserPassword(userId),
  });
}

export function useSyncFeishuOrg() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (tenantId?: number) => adminApi.syncFeishuOrg(tenantId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });
}

// Tenant Management Hooks
export function useTenants() {
  return useQuery({
    queryKey: ['admin', 'tenants'],
    queryFn: () => tenantApi.listTenants(),
  });
}

// Quota Management Hooks
export function useQuotaUsage() {
  return useQuery({
    queryKey: ['admin', 'quota'],
    queryFn: () => adminApi.getQuotaUsage(),
  });
}

export function useQuotaStats() {
  return useQuery({
    queryKey: ['admin', 'quota-stats'],
    queryFn: () => adminApi.getQuotaStats(),
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
      // Also invalidate password policy cache so regular users see updated policy
      queryClient.invalidateQueries({ queryKey: ['password-policy'] });
    },
  });
}

// Password Policy Hooks (accessible to all authenticated users)
export function usePasswordPolicy() {
  return useQuery({
    queryKey: ['password-policy'],
    queryFn: () => governanceApi.getPasswordPolicy(),
  });
}

// Audit Threshold Hooks
export function useAuditThresholds() {
  return useQuery({
    queryKey: ['admin', 'audit-thresholds'],
    queryFn: () => complianceApi.getAuditThresholds(),
  });
}

export function useUpdateAuditThresholds() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Partial<AuditThresholds>) => complianceApi.updateAuditThresholds(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'audit-thresholds'] });
    },
  });
}

// AI Agent Settings Hooks
export function useAiAgentSettings() {
  return useQuery({
    queryKey: ['admin', 'ai-agent-settings'],
    queryFn: () => aiAgentSettingsApi.getSettings(),
  });
}

export function useUpdateAiAgentSettings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Partial<AiAgentSettings>) => aiAgentSettingsApi.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'ai-agent-settings'] });
    },
  });
}

export function useValidateGithubToken() {
  return useMutation({
    mutationFn: (payload: TokenValidationRequest) =>
      aiAgentSettingsApi.validateGithubToken(payload),
  });
}

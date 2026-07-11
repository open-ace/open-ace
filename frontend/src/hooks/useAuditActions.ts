/**
 * useAuditActions Hook - Fetches audit action types from backend API
 *
 * Features:
 * - React Query caching (24h stale time, infinite cache time)
 * - Automatic retry on failure (3 attempts)
 * - Fallback to hardcoded constants if API fails
 * - Category-based organization for grouped display
 */

import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import type { AuditActionItem, AuditCategory, AuditActionsResponse } from '@/types';

/**
 * Fallback audit action options (31 types matching backend AuditAction enum).
 * Used when API is unavailable or returns an error.
 * MUST be kept in sync with backend AuditAction enum in audit_logger.py.
 */
export const AUDIT_ACTION_OPTIONS_FALLBACK: AuditActionItem[] = [
  // Authentication
  { value: 'login', label: 'Login', category: 'auth', i18n_key: 'actionLogin' },
  { value: 'logout', label: 'Logout', category: 'auth', i18n_key: 'actionLogout' },
  { value: 'login_failed', label: 'Login Failed', category: 'auth', i18n_key: 'actionLoginFailed' },
  { value: 'session_expired', label: 'Session Expired', category: 'auth', i18n_key: 'actionSessionExpired' },
  // User Management
  { value: 'user_create', label: 'User Create', category: 'user_management', i18n_key: 'actionUserCreate' },
  { value: 'user_update', label: 'User Update', category: 'user_management', i18n_key: 'actionUserUpdate' },
  { value: 'user_delete', label: 'User Delete', category: 'user_management', i18n_key: 'actionUserDelete' },
  { value: 'user_password_change', label: 'Password Change', category: 'user_management', i18n_key: 'actionUserPasswordChange' },
  { value: 'user_role_change', label: 'Role Change', category: 'user_management', i18n_key: 'actionUserRoleChange' },
  { value: 'user_status_change', label: 'Status Change', category: 'user_management', i18n_key: 'actionUserStatusChange' },
  // Permission
  { value: 'permission_grant', label: 'Permission Grant', category: 'permission', i18n_key: 'actionPermissionGrant' },
  { value: 'permission_revoke', label: 'Permission Revoke', category: 'permission', i18n_key: 'actionPermissionRevoke' },
  // Quota
  { value: 'quota_update', label: 'Quota Update', category: 'quota', i18n_key: 'actionQuotaUpdate' },
  { value: 'quota_alert', label: 'Quota Alert', category: 'quota', i18n_key: 'actionQuotaAlert' },
  { value: 'quota_exceeded', label: 'Quota Exceeded', category: 'quota', i18n_key: 'actionQuotaExceeded' },
  // Data
  { value: 'data_view', label: 'Data View', category: 'data', i18n_key: 'actionDataView' },
  { value: 'data_export', label: 'Data Export', category: 'data', i18n_key: 'actionDataExport' },
  { value: 'data_import', label: 'Data Import', category: 'data', i18n_key: 'actionDataImport' },
  { value: 'data_delete', label: 'Data Delete', category: 'data', i18n_key: 'actionDataDelete' },
  // System
  { value: 'system_config_change', label: 'Config Change', category: 'system', i18n_key: 'actionSystemConfigChange' },
  { value: 'system_start', label: 'System Start', category: 'system', i18n_key: 'actionSystemStart' },
  { value: 'system_stop', label: 'System Stop', category: 'system', i18n_key: 'actionSystemStop' },
  // Content
  { value: 'content_blocked', label: 'Content Blocked', category: 'content', i18n_key: 'actionContentBlocked' },
  { value: 'content_flagged', label: 'Content Flagged', category: 'content', i18n_key: 'actionContentFlagged' },
  { value: 'content_warned', label: 'Content Warned', category: 'content', i18n_key: 'actionContentWarned' },
  { value: 'content_redacted', label: 'Content Redacted', category: 'content', i18n_key: 'actionContentRedacted' },
  // Agent
  { value: 'agent_register', label: 'Agent Register', category: 'agent', i18n_key: 'actionAgentRegister' },
  { value: 'agent_token_rotate', label: 'Token Rotate', category: 'agent', i18n_key: 'actionAgentTokenRotate' },
  { value: 'agent_token_revoke', label: 'Token Revoke', category: 'agent', i18n_key: 'actionAgentTokenRevoke' },
  { value: 'agent_auth_failure', label: 'Auth Failure', category: 'agent', i18n_key: 'actionAgentAuthFailure' },
  { value: 'agent_reconnect', label: 'Agent Reconnect', category: 'agent', i18n_key: 'actionAgentReconnect' },
];

export const AUDIT_CATEGORIES_FALLBACK: AuditCategory[] = [
  { key: 'auth', label: 'Authentication', i18n_key: 'categoryAuth' },
  { key: 'user_management', label: 'User Management', i18n_key: 'categoryUserManagement' },
  { key: 'permission', label: 'Permission', i18n_key: 'categoryPermission' },
  { key: 'quota', label: 'Quota', i18n_key: 'categoryQuota' },
  { key: 'data', label: 'Data', i18n_key: 'categoryData' },
  { key: 'system', label: 'System', i18n_key: 'categorySystem' },
  { key: 'content', label: 'Content', i18n_key: 'categoryContent' },
  { key: 'agent', label: 'Agent', i18n_key: 'categoryAgent' },
];

/**
 * Fetches audit actions from the backend API.
 */
async function fetchAuditActions(): Promise<AuditActionsResponse> {
  return apiClient.get<AuditActionsResponse>('/api/audit-actions');
}

/**
 * Hook for fetching audit action types from the backend.
 *
 * Uses React Query with:
 * - 24 hour stale time (data considered fresh for 24h)
 * - Infinite cache time (data cached indefinitely)
 * - 3 retry attempts on failure
 * - Fallback to hardcoded constants if API fails
 *
 * @returns Object with actions, categories, loading, error, and refetch
 */
export function useAuditActions() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['audit-actions'],
    queryFn: fetchAuditActions,
    staleTime: 1000 * 60 * 60 * 24, // 24 hours
    gcTime: Infinity, // Cache indefinitely (formerly cacheTime)
    refetchOnWindowFocus: false,
    retry: 3,
  });

  // Use API data if available, otherwise fall back to hardcoded constants
  const actions = data?.actions ?? AUDIT_ACTION_OPTIONS_FALLBACK;
  const categories = data?.categories ?? AUDIT_CATEGORIES_FALLBACK;

  // Log when using fallback data
  if (!data) {
    console.warn('Using fallback audit actions data');
  }

  return {
    actions,
    categories,
    isLoading,
    error,
    refetch,
    isFallback: !data, // True if using fallback constants
  };
}
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
 * Fallback audit action options used when API is unavailable or returns an
 * error. Mirrors the backend AuditAction enum (audit_logger.py) in full —
 * every value, label, category, and i18n_key matches what
 * GET /api/audit-actions derives from get_action_categories().
 *
 * Kept in sync with the backend enum by tests/unit/test_audit_action_fallback_sync.py,
 * which fails whenever either side drifts (missing or extra action values).
 */
export const AUDIT_ACTION_OPTIONS_FALLBACK: AuditActionItem[] = [
  // Authentication
  {
    value: 'login',
    label: 'Login',
    category: 'auth',
    i18n_key: 'actionLogin',
  },
  {
    value: 'logout',
    label: 'Logout',
    category: 'auth',
    i18n_key: 'actionLogout',
  },
  {
    value: 'login_failed',
    label: 'Login Failed',
    category: 'auth',
    i18n_key: 'actionLoginFailed',
  },
  {
    value: 'session_expired',
    label: 'Session Expired',
    category: 'auth',
    i18n_key: 'actionSessionExpired',
  },
  // User Management
  {
    value: 'user_create',
    label: 'User Create',
    category: 'user_management',
    i18n_key: 'actionUserCreate',
  },
  {
    value: 'user_update',
    label: 'User Update',
    category: 'user_management',
    i18n_key: 'actionUserUpdate',
  },
  {
    value: 'user_delete',
    label: 'User Delete',
    category: 'user_management',
    i18n_key: 'actionUserDelete',
  },
  {
    value: 'user_restore',
    label: 'User Restore',
    category: 'user_management',
    i18n_key: 'actionUserRestore',
  },
  {
    value: 'user_password_change',
    label: 'Password Change',
    category: 'user_management',
    i18n_key: 'actionUserPasswordChange',
  },
  {
    value: 'user_password_change_failed',
    label: 'Password Change Failed',
    category: 'user_management',
    i18n_key: 'actionUserPasswordChangeFailed',
  },
  {
    value: 'user_role_change',
    label: 'Role Change',
    category: 'user_management',
    i18n_key: 'actionUserRoleChange',
  },
  {
    value: 'user_status_change',
    label: 'Status Change',
    category: 'user_management',
    i18n_key: 'actionUserStatusChange',
  },
  // Permission
  {
    value: 'permission_grant',
    label: 'Permission Grant',
    category: 'permission',
    i18n_key: 'actionPermissionGrant',
  },
  {
    value: 'permission_revoke',
    label: 'Permission Revoke',
    category: 'permission',
    i18n_key: 'actionPermissionRevoke',
  },
  {
    value: 'shared_project_permission_setup_start',
    label: 'Shared Project Permission Setup Start',
    category: 'permission',
    i18n_key: 'actionSharedProjectPermissionSetupStart',
  },
  {
    value: 'shared_project_permission_setup_complete',
    label: 'Shared Project Permission Setup Complete',
    category: 'permission',
    i18n_key: 'actionSharedProjectPermissionSetupComplete',
  },
  {
    value: 'project_user_add',
    label: 'Project User Add',
    category: 'permission',
    i18n_key: 'actionProjectUserAdd',
  },
  {
    value: 'project_user_remove',
    label: 'Project User Remove',
    category: 'permission',
    i18n_key: 'actionProjectUserRemove',
  },
  {
    value: 'project_user_batch_update',
    label: 'Project User Batch Update',
    category: 'permission',
    i18n_key: 'actionProjectUserBatchUpdate',
  },
  // Quota
  {
    value: 'quota_update',
    label: 'Quota Update',
    category: 'quota',
    i18n_key: 'actionQuotaUpdate',
  },
  {
    value: 'quota_alert',
    label: 'Quota Alert',
    category: 'quota',
    i18n_key: 'actionQuotaAlert',
  },
  {
    value: 'quota_exceeded',
    label: 'Quota Exceeded',
    category: 'quota',
    i18n_key: 'actionQuotaExceeded',
  },
  // Tenant Billing
  {
    value: 'tenant_billing_period_reset',
    label: 'Tenant Billing Period Reset',
    category: 'tenant_billing',
    i18n_key: 'actionTenantBillingPeriodReset',
  },
  // Data
  {
    value: 'data_view',
    label: 'Data View',
    category: 'data',
    i18n_key: 'actionDataView',
  },
  {
    value: 'data_export',
    label: 'Data Export',
    category: 'data',
    i18n_key: 'actionDataExport',
  },
  {
    value: 'data_import',
    label: 'Data Import',
    category: 'data',
    i18n_key: 'actionDataImport',
  },
  {
    value: 'data_delete',
    label: 'Data Delete',
    category: 'data',
    i18n_key: 'actionDataDelete',
  },
  // System
  {
    value: 'system_config_change',
    label: 'Config Change',
    category: 'system',
    i18n_key: 'actionSystemConfigChange',
  },
  {
    value: 'system_start',
    label: 'System Start',
    category: 'system',
    i18n_key: 'actionSystemStart',
  },
  {
    value: 'system_stop',
    label: 'System Stop',
    category: 'system',
    i18n_key: 'actionSystemStop',
  },
  // Content
  {
    value: 'content_blocked',
    label: 'Content Blocked',
    category: 'content',
    i18n_key: 'actionContentBlocked',
  },
  {
    value: 'content_flagged',
    label: 'Content Flagged',
    category: 'content',
    i18n_key: 'actionContentFlagged',
  },
  {
    value: 'content_warned',
    label: 'Content Warned',
    category: 'content',
    i18n_key: 'actionContentWarned',
  },
  {
    value: 'content_redacted',
    label: 'Content Redacted',
    category: 'content',
    i18n_key: 'actionContentRedacted',
  },
  // Agent
  {
    value: 'agent_register',
    label: 'Agent Register',
    category: 'agent',
    i18n_key: 'actionAgentRegister',
  },
  {
    value: 'agent_token_rotate',
    label: 'Token Rotate',
    category: 'agent',
    i18n_key: 'actionAgentTokenRotate',
  },
  {
    value: 'agent_token_revoke',
    label: 'Token Revoke',
    category: 'agent',
    i18n_key: 'actionAgentTokenRevoke',
  },
  {
    value: 'agent_auth_failure',
    label: 'Auth Failure',
    category: 'agent',
    i18n_key: 'actionAgentAuthFailure',
  },
  {
    value: 'agent_reconnect',
    label: 'Agent Reconnect',
    category: 'agent',
    i18n_key: 'actionAgentReconnect',
  },
  {
    value: 'agent_token_rotate_confirmed',
    label: 'Token Rotate Confirmed',
    category: 'agent',
    i18n_key: 'actionAgentTokenRotateConfirmed',
  },
  {
    value: 'agent_token_force_revoked',
    label: 'Token Force Revoked',
    category: 'agent',
    i18n_key: 'actionAgentTokenForceRevoked',
  },
  {
    value: 'usage_report_accepted',
    label: 'Usage Report Accepted',
    category: 'agent',
    i18n_key: 'actionUsageReportAccepted',
  },
  {
    value: 'usage_report_auth_failure',
    label: 'Usage Report Auth Failure',
    category: 'agent',
    i18n_key: 'actionUsageReportAuthFailure',
  },
  {
    value: 'usage_report_binding_mismatch',
    label: 'Usage Report Binding Mismatch',
    category: 'agent',
    i18n_key: 'actionUsageReportBindingMismatch',
  },
  // SSRF Protection
  {
    value: 'llm_proxy_url_blocked',
    label: 'LLM Proxy URL Blocked',
    category: 'ssrf_protection',
    i18n_key: 'actionLLMProxyURLBlocked',
  },
  {
    value: 'allowlist_entry_invalid',
    label: 'Allowlist Entry Invalid',
    category: 'ssrf_protection',
    i18n_key: 'actionAllowlistEntryInvalid',
  },
  {
    value: 'ip_resolved_mismatch',
    label: 'IP Resolved Mismatch',
    category: 'ssrf_protection',
    i18n_key: 'actionIPResolvedMismatch',
  },
  {
    value: 'ssrf_config_reset',
    label: 'SSRF Config Reset',
    category: 'ssrf_protection',
    i18n_key: 'actionSSRFConfigReset',
  },
  {
    value: 'proxy_key_echo_blocked',
    label: 'Proxy Key Echo Blocked',
    category: 'ssrf_protection',
    i18n_key: 'actionProxyKeyEchoBlocked',
  },
  // External Identity
  {
    value: 'external_token_issued',
    label: 'External Token Issued',
    category: 'external_identity',
    i18n_key: 'actionExternalTokenIssued',
  },
  // URL Token Security
  {
    value: 'query_session_token_rejected',
    label: 'Query Session Token Rejected',
    category: 'url_token_security',
    i18n_key: 'actionQuerySessionTokenRejected',
  },
  {
    value: 'webui_token_in_query_used',
    label: 'WebUI Token in Query Used',
    category: 'url_token_security',
    i18n_key: 'actionWebuiTokenInQueryUsed',
  },
  {
    value: 'proxy_token_in_query_used',
    label: 'Proxy Token in Query Used',
    category: 'url_token_security',
    i18n_key: 'actionProxyTokenInQueryUsed',
  },
  {
    value: 'browser_token_in_query_used',
    label: 'Browser Token in Query Used',
    category: 'url_token_security',
    i18n_key: 'actionBrowserTokenInQueryUsed',
  },
  {
    value: 'url_token_path_violation',
    label: 'URL Token Path Violation',
    category: 'url_token_security',
    i18n_key: 'actionUrlTokenPathViolation',
  },
  {
    value: 'legacy_webui_token_used',
    label: 'Legacy WebUI Token Used',
    category: 'url_token_security',
    i18n_key: 'actionLegacyWebuiTokenUsed',
  },
  {
    value: 'token_leak_suspected',
    label: 'Token Leak Suspected',
    category: 'url_token_security',
    i18n_key: 'actionTokenLeakSuspected',
  },
  // Admin Access
  {
    value: 'admin_cross_tenant_access',
    label: 'Admin Cross-Tenant Access',
    category: 'admin_access',
    i18n_key: 'actionAdminCrossTenantAccess',
  },
  {
    value: 'admin_global_session_list',
    label: 'Admin Global Session List',
    category: 'admin_access',
    i18n_key: 'actionAdminGlobalSessionList',
  },
  // SMTP Configuration
  {
    value: 'smtp_config_save',
    label: 'SMTP Config Save',
    category: 'smtp_config',
    i18n_key: 'actionSmtpConfigSave',
  },
  {
    value: 'smtp_config_delete',
    label: 'SMTP Config Delete',
    category: 'smtp_config',
    i18n_key: 'actionSmtpConfigDelete',
  },
  // Feishu Configuration
  {
    value: 'feishu_config_save',
    label: 'Feishu Config Save',
    category: 'feishu_config',
    i18n_key: 'actionFeishuConfigSave',
  },
  {
    value: 'feishu_config_delete',
    label: 'Feishu Config Delete',
    category: 'feishu_config',
    i18n_key: 'actionFeishuConfigDelete',
  },
  // Notification Integration
  {
    value: 'webhook_config_save',
    label: 'Webhook Config Save',
    category: 'notification_integration',
    i18n_key: 'actionWebhookConfigSave',
  },
  {
    value: 'webhook_config_delete',
    label: 'Webhook Config Delete',
    category: 'notification_integration',
    i18n_key: 'actionWebhookConfigDelete',
  },
  {
    value: 'dingtalk_config_save',
    label: 'DingTalk Config Save',
    category: 'notification_integration',
    i18n_key: 'actionDingtalkConfigSave',
  },
  {
    value: 'dingtalk_config_delete',
    label: 'DingTalk Config Delete',
    category: 'notification_integration',
    i18n_key: 'actionDingtalkConfigDelete',
  },
  // Tool Account Mapping
  {
    value: 'tool_account_mapping_create',
    label: 'Tool Account Mapping Create',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingCreate',
  },
  {
    value: 'tool_account_mapping_update',
    label: 'Tool Account Mapping Update',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingUpdate',
  },
  {
    value: 'tool_account_mapping_delete',
    label: 'Tool Account Mapping Delete',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingDelete',
  },
  {
    value: 'tool_account_mapping_batch',
    label: 'Tool Account Mapping Batch',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingBatch',
  },
  {
    value: 'tool_account_mapping_verify',
    label: 'Tool Account Mapping Verify',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingVerify',
  },
  {
    value: 'tool_account_mapping_verify_batch',
    label: 'Tool Account Mapping Verify Batch',
    category: 'tool_account_mapping',
    i18n_key: 'actionToolAccountMappingVerifyBatch',
  },
];

export const AUDIT_CATEGORIES_FALLBACK: AuditCategory[] = [
  { key: 'auth', label: 'Authentication', i18n_key: 'categoryAuth', resource_types: ['session'] },
  {
    key: 'user_management',
    label: 'User Management',
    i18n_key: 'categoryUserManagement',
    resource_types: ['user'],
  },
  {
    key: 'permission',
    label: 'Permission',
    i18n_key: 'categoryPermission',
    resource_types: ['user', 'project'],
  },
  { key: 'quota', label: 'Quota', i18n_key: 'categoryQuota', resource_types: ['quota_alert'] },
  {
    key: 'tenant_billing',
    label: 'Tenant Billing',
    i18n_key: 'categoryTenantBilling',
    resource_types: ['tenant'],
  },
  {
    key: 'data',
    label: 'Data',
    i18n_key: 'categoryData',
    resource_types: ['analytics_report', 'analytics', 'data'],
  },
  {
    key: 'system',
    label: 'System',
    i18n_key: 'categorySystem',
    resource_types: [
      'content_filter',
      'filter_rule',
      'security_settings',
      'ai_agent_settings',
      'tenant_settings',
    ],
  },
  { key: 'content', label: 'Content', i18n_key: 'categoryContent', resource_types: ['content'] },
  {
    key: 'agent',
    label: 'Agent',
    i18n_key: 'categoryAgent',
    resource_types: ['remote_machine', 'agent_token', 'usage_report'],
  },
  {
    key: 'ssrf_protection',
    label: 'SSRF Protection',
    i18n_key: 'categorySSRFProtection',
    resource_types: ['llm_proxy', 'allowlist'],
  },
  {
    key: 'external_identity',
    label: 'External Identity',
    i18n_key: 'categoryExternalIdentity',
    resource_types: ['external_identity'],
  },
  {
    key: 'url_token_security',
    label: 'URL Token Security',
    i18n_key: 'categoryUrlTokenSecurity',
    resource_types: ['url_token', 'session'],
  },
  {
    key: 'admin_access',
    label: 'Admin Access',
    i18n_key: 'categoryAdminAccess',
    resource_types: ['session', 'user'],
  },
  {
    key: 'smtp_config',
    label: 'SMTP Configuration',
    i18n_key: 'categorySmtpConfig',
    resource_types: ['smtp_config'],
  },
  {
    key: 'feishu_config',
    label: 'Feishu Configuration',
    i18n_key: 'categoryFeishuConfig',
    resource_types: ['feishu_config'],
  },
  {
    key: 'notification_integration',
    label: 'Notification Integration',
    i18n_key: 'categoryNotificationIntegration',
    resource_types: ['webhook_config', 'dingtalk_config'],
  },
  {
    key: 'tool_account_mapping',
    label: 'Tool Account Mapping',
    i18n_key: 'categoryToolAccountMapping',
    resource_types: ['tool_account_mapping'],
  },
];

export function buildAuditActionMappings(actions: AuditActionItem[], categories: AuditCategory[]) {
  const categoryResourceTypes = Object.fromEntries(
    categories.map((category) => [category.key, category.resource_types ?? []])
  );
  const actionToCategory: Record<string, string> = {};
  const actionToResourceTypes: Record<string, string[]> = {};
  const resourceToCategories: Record<string, string[]> = {};

  actions.forEach((action) => {
    actionToCategory[action.value] = action.category;
    actionToResourceTypes[action.value] =
      action.resource_types ?? categoryResourceTypes[action.category] ?? [];
  });

  categories.forEach((category) => {
    (category.resource_types ?? []).forEach((resourceType) => {
      resourceToCategories[resourceType] = resourceToCategories[resourceType] ?? [];
      if (!resourceToCategories[resourceType].includes(category.key)) {
        resourceToCategories[resourceType].push(category.key);
      }
    });
  });

  return { actionToCategory, actionToResourceTypes, resourceToCategories };
}

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
  const fallbackMappings = buildAuditActionMappings(actions, categories);
  const actionToCategory = data?.actionToCategory ?? fallbackMappings.actionToCategory;
  const actionToResourceTypes =
    data?.actionToResourceTypes ?? fallbackMappings.actionToResourceTypes;
  const resourceToCategories = data?.resourceToCategories ?? fallbackMappings.resourceToCategories;

  // Log when using fallback data
  if (!data) {
    console.warn('Using fallback audit actions data');
  }

  return {
    actions,
    categories,
    actionToCategory,
    actionToResourceTypes,
    resourceToCategories,
    isLoading,
    error,
    refetch,
    isFallback: !data, // True if using fallback constants
  };
}

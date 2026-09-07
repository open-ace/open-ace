/**
 * Hooks Module - Export all custom hooks
 */

export { useAuth } from './useAuth';
export { useTodayUsage, useSummary, useTrendData, useHosts, useDashboard } from './useDashboard';
export {
  useMessages,
  useInfiniteMessages,
  useMessage,
  useMessageCount,
  useConversationHistory,
  useConversationTimeline,
  useSenders,
  useTools,
} from './useMessages';
export {
  useRenderPerformance,
  usePerformance,
  usePerformanceMetrics,
  useAsyncPerformance,
} from './usePerformance';
export {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useRestoreUser,
  useUpdateUserPassword,
  useResetUserPassword,
  useSyncFeishuOrg,
  useTenants,
  useQuotaUsage,
  useQuotaStats,
  useUpdateQuota,
  useAuditLogs,
  useFilterRules,
  useFilterStats,
  useCreateFilterRule,
  useUpdateFilterRule,
  useDeleteFilterRule,
  useSecuritySettings,
  useUpdateSecuritySettings,
  usePasswordPolicy,
  useAuditThresholds,
  useUpdateAuditThresholds,
  useAiAgentSettings,
  useUpdateAiAgentSettings,
  useValidateGithubToken,
  usePolicyRules,
  useCreatePolicyRule,
  useUpdatePolicyRule,
  useTogglePolicyRule,
  usePolicyDecisions,
  useSensitiveKeywords,
  useCreateSensitiveKeyword,
  useUpdateSensitiveKeyword,
  useDeleteSensitiveKeyword,
  useSsrfStatus,
  useResetSsrfConfig,
  useUploadAuthStatus,
} from './useAdmin';
export { useMyUsage } from './useReport';
export {
  useBatchAnalysis,
  useKeyMetrics,
  useDailyHourlyUsage,
  usePeakUsage,
  useUserRanking,
  useConversationStats,
  useToolComparison,
  useUserSegmentation,
  useRecommendations,
  useAnomalyDetection,
  useAnomalyTrend,
  useDataRange,
  useUsageForecast,
  useEnterpriseReport,
  useEfficiencyMetrics,
} from './useAnalysis';
export {
  useSessions,
  useSession,
  useSessionStats,
  useDeleteSession,
  useCompleteSession,
  useRenameSession,
  useRestoreSession,
} from './useSessions';
export { useGlobalFetch } from './useFetch';
export {
  useMachines,
  useMachineUsers,
  useMachineSessions,
  useGenerateToken,
  useDeregisterMachine,
  useRotateMachineToken,
  useRevokeMachineToken,
  useAssignUser,
  useRevokeUser,
  useApiKeys,
  useStoreApiKey,
  useUpdateApiKey,
  useDeleteApiKey,
  useAvailableMachines,
  useCreateRemoteSession,
  useRemoteSession,
  useSendMessage,
  useStopRemoteSession,
  usePauseRemoteSession,
  useResumeRemoteSession,
  useRunEvents,
  useRunApprovals,
} from './useRemote';

export {
  usePrompts,
  usePromptCategories,
  useCreatePrompt,
  useUpdatePrompt,
  useDeletePrompt,
  useCopyPrompt,
} from './usePrompts';

// Re-export store hooks
export {
  useUser,
  useIsAuthenticated,
  useAuthLoading,
  useMustChangePassword,
  useTheme,
  useLanguage,
  useSidebarCollapsed,
  useAppMode,
} from '@/store';

// Page refresh hooks
export {
  usePageRefresh,
  type UsePageRefreshOptions,
  type UsePageRefreshReturn,
} from './usePageRefresh';
export { useGlobalRefreshPause } from './useGlobalRefreshPause';

// Audit actions hook
export {
  useAuditActions,
  AUDIT_ACTION_OPTIONS_FALLBACK,
  AUDIT_CATEGORIES_FALLBACK,
} from './useAuditActions';
export type { AuditActionItem, AuditCategory, AuditActionsResponse } from '@/types';

// Safe workspace state hook (Issue #2953)
export {
  useSafeWorkspaceState,
  validateActiveTabId,
  validateTabsOrder,
} from './useSafeWorkspaceState';

// Admin tenant selection hook (Issue #2841)
export { useAdminTenant } from './useAdminTenant';

// Alert stream hook (Issue #3332)
export { useAlertStream } from './useAlertStream';

// Encryption keys hooks (Issue #3326)
export {
  useEncryptionKeys,
  useValidateKey,
  useRotateKey,
  useGenerateEnvConfig,
  useEncryptionKeysAuditLog,
  useEncryptionKeysSyncStatus,
  useReEncryptPreCheck,
  useReEncrypt,
} from './useEncryptionKeys';

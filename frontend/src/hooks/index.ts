/**
 * Hooks Module - Export all custom hooks
 */

export { useAuth } from './useAuth';
export { useTodayUsage, useSummary, useTrendData, useHosts, useDashboard } from './useDashboard';
export { useMessages, useInfiniteMessages, useMessage, useMessageCount, useConversationHistory, useConversationTimeline, useSenders } from './useMessages';
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
  useUpdateUserPassword,
  useQuotaUsage,
  useUpdateQuota,
  useAuditLogs,
  useFilterRules,
  useCreateFilterRule,
  useUpdateFilterRule,
  useDeleteFilterRule,
  useSecuritySettings,
  useUpdateSecuritySettings,
} from './useAdmin';
export { useMyUsage } from './useReport';
export {
  useKeyMetrics,
  useDailyHourlyUsage,
  usePeakUsage,
  useUserRanking,
  useConversationStats,
  useToolComparison,
  useUserSegmentation,
  useRecommendations,
} from './useAnalysis';
export {
  useSessions,
  useSession,
  useSessionStats,
  useDeleteSession,
  useCompleteSession,
} from './useSessions';

// Re-export store hooks
export {
  useUser,
  useIsAuthenticated,
  useAuthLoading,
  useTheme,
  useLanguage,
  useSidebarCollapsed,
  useAppMode,
} from '@/store';

/**
 * API Module - Export all API clients
 */

export { apiClient, ApiClient } from './client';
export { dashboardApi } from './dashboard';
export type {
  TodayUsageResponse,
  SummaryResponse,
  TrendDataPoint,
  TrendResponse,
  HostsResponse,
} from './dashboard';
export { messagesApi } from './messages';
export type {
  MessagesResponse,
  ConversationHistory,
  ConversationMessage,
} from './messages';
export { sessionsApi } from './sessions';
export type {
  AgentSession,
  SessionMessage,
  SessionFilters,
  SessionsListResponse,
  SessionStatsResponse,
} from './sessions';
export { authApi } from './auth';
export type { AuthCheckResponse, LoginRequest, LoginResponse } from './auth';
export { adminApi } from './admin';
export type {
  AdminUser,
  CreateUserRequest,
  UpdateUserRequest,
  UpdateQuotaRequest,
  QuotaUsage,
} from './admin';
export { governanceApi } from './governance';
export type {
  AuditLog,
  AuditLogFilters,
  AuditLogResponse,
  ContentFilterRule,
  CreateFilterRuleRequest,
  FilterCheckResult,
  SecuritySettings,
} from './governance';
export { reportApi } from './report';
export type { MyUsageReport, DailyUsage } from './report';
export { workspaceApi } from './workspace';
export type { WorkspaceConfig } from './workspace';
export { analysisApi } from './analysis';
export type {
  KeyMetrics,
  HourlyUsage,
  DailyHourlyUsage,
  PeakUsage,
  UserRanking,
  ConversationStats,
  ToolComparison,
  Recommendation,
} from './analysis';
export { promptsApi } from './prompts';
export type {
  PromptTemplate,
  PromptVariable,
  PromptListResponse,
  PromptFilters,
  CreatePromptRequest,
  UpdatePromptRequest,
  RenderPromptRequest,
  RenderPromptResponse,
  CategoryInfo,
} from './prompts';

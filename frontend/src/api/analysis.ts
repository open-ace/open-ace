/**
 * Analysis API - Analysis related API calls
 */

import { apiClient } from './client';

// Types
export interface KeyMetrics {
  total_sessions: number;
  total_messages: number;
  total_tokens: number;
  avg_tokens_per_session: number;
  avg_messages_per_session: number;
  top_tools: Array<{ tool: string; count: number }>;
  top_hosts: Array<{ host: string; count: number }>;
}

export interface HourlyUsage {
  hour: number;
  tokens: number;
  requests: number;
}

export interface DailyHourlyUsage {
  daily: Array<{ date: string; tokens: number; requests: number }>;
  hourly: HourlyUsage[];
}

export interface PeakUsage {
  peak_hours: Array<{ hour: number; avg_tokens: number }>;
  peak_days: Array<{ date: string; tokens: number }>;
}

export interface UserRanking {
  users: Array<{
    user_id: number;
    username: string;
    tokens: number;
    requests: number;
  }>;
}

export interface ConversationStats {
  total_conversations: number;
  avg_conversation_length: number;
  avg_tokens_per_conversation: number;
  conversation_distribution?: Array<{ length: number; count: number }>;
}

export interface ToolComparison {
  tools: Array<{
    tool_name: string;
    total_tokens: number;
    total_requests: number;
    avg_tokens_per_request: number;
  }>;
}

export interface Recommendation {
  type: string;
  title: string;
  description: string;
  impact: string;
}

export interface UserSegmentation {
  high: number;      // >10K tokens
  medium: number;    // 1K-10K tokens
  low: number;       // <1K tokens
  dormant: number;   // No activity
}

// Batch response type
export interface BatchAnalysisResponse {
  key_metrics: KeyMetrics;
  daily_hourly_usage: DailyHourlyUsage;
  peak_usage: PeakUsage;
  user_ranking: UserRanking;
  conversation_stats: ConversationStats;
  tool_comparison: ToolComparison;
  user_segmentation: UserSegmentation;
}

// API
export const analysisApi = {
  /**
   * Get all analysis data in a single request (optimized)
   */
  async getBatchAnalysis(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<BatchAnalysisResponse> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<BatchAnalysisResponse>('/api/analysis/batch', params);
  },

  async getKeyMetrics(startDate?: string, endDate?: string, host?: string): Promise<KeyMetrics> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<KeyMetrics>('/api/analysis/key-metrics', params);
  },

  async getHourlyUsage(date?: string, tool?: string, host?: string): Promise<HourlyUsage[]> {
    const params: Record<string, string> = {};
    if (date) params.date = date;
    if (tool) params.tool = tool;
    if (host) params.host = host;

    const response = await apiClient.get<HourlyUsage[]>(
      '/api/analysis/hourly-usage',
      params
    );
    return response || [];
  },

  async getDailyHourlyUsage(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<DailyHourlyUsage> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<DailyHourlyUsage>('/api/analysis/daily-hourly-usage', params);
  },

  async getPeakUsage(startDate?: string, endDate?: string, host?: string): Promise<PeakUsage> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<PeakUsage>('/api/analysis/peak-usage', params);
  },

  async getUserRanking(
    startDate?: string,
    endDate?: string,
    host?: string,
    limit?: number
  ): Promise<UserRanking> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    if (limit) params.limit = String(limit);

    return apiClient.get<UserRanking>('/api/analysis/user-ranking', params);
  },

  async getConversationStats(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<ConversationStats> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<ConversationStats>('/api/analysis/conversation-stats', params);
  },

  async getToolComparison(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<ToolComparison> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<ToolComparison>('/api/analysis/tool-comparison', params);
  },

  async getUserSegmentation(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<UserSegmentation> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<UserSegmentation>('/api/analysis/user-segmentation', params);
  },

  async getRecommendations(host?: string): Promise<Recommendation[]> {
    const params: Record<string, string> = {};
    if (host) params.host = host;

    const response = await apiClient.get<{ recommendations: Recommendation[] }>(
      '/api/analysis/recommendations',
      params
    );
    return response.recommendations || [];
  },
};

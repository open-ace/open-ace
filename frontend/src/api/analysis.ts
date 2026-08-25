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
  total_messages: number;
  multi_turn_session_count: number;
  /** Fraction of conversations with >= 2 messages, in [0, 1]. */
  multi_turn_ratio: number;
  average_messages_per_conversation: number;
  average_tokens_per_conversation: number;
  /**
   * Backward-compatible alias of average_messages_per_conversation. Kept so
   * calculateHealthScore and other existing consumers keep working.
   */
  avg_conversation_length: number;
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
  message: string;
  details?: string;
}

export interface UserSegmentation {
  high: number; // >10K tokens
  medium: number; // 1K-10K tokens
  low: number; // <1K tokens
  dormant: number; // No activity
}

// Issue #3079: User role distribution for role-based grouping
export interface UserRoleDistributionItem {
  count: number;
  label: string;
  description: string;
}

export interface UserRoleDistribution {
  admin: UserRoleDistributionItem;
  manager: UserRoleDistributionItem;
  user: UserRoleDistributionItem;
  unknown: UserRoleDistributionItem;
}

export interface Anomaly {
  date: string;
  tokens: number;
  expected: number;
  deviation: number;
  type: 'spike' | 'drop';
  severity: 'high' | 'medium' | 'low';
  // Forward-compatible: the day's top contributing tool + its share of that
  // day's tokens. Omitted when backend drill-down is unavailable (legacy cache
  // or older deployments) — consumers must degrade gracefully.
  top_contributor?: {
    tool: string;
    share_pct: number;
  };
}

export interface AnomalyDetectionResponse {
  anomalies: Anomaly[];
  summary: {
    total: number;
    high: number;
    medium: number;
    low: number;
  };
  statistics?: {
    average: number;
    std_deviation: number;
    data_points: number;
  };
}

export interface AnomalyTrendResponse {
  trend: Array<{
    date: string;
    count: number;
    spikes: number;
    drops: number;
  }>;
  total_anomalies: number;
}

// Data range type
export interface DataRange {
  min_date: string;
  max_date: string;
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
  user_role_distribution?: UserRoleDistribution; // Issue #3079
  data_range?: DataRange;
}

// Forecast response type
export interface ForecastAvailableTrue {
  forecast_available: true;
  method: 'moving_average';
  period_days: number;
  daily_forecast: { tokens: number; requests: number };
  total_forecast: { tokens: number; requests: number };
  forecast_dates: string[];
  confidence: number; // Decimal, e.g., 0.7 for 70%
}

export interface ForecastAvailableFalse {
  forecast_available: false;
  reason: string;
}

export type ForecastResponse = ForecastAvailableTrue | ForecastAvailableFalse;

// Enterprise Report Types
export interface EnterpriseReportResponse {
  period: {
    start: string;
    end: string;
  };
  summary: {
    total_tokens: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_requests: number;
    unique_tools: number;
    unique_hosts: number;
    daily_average_tokens: number;
    daily_average_requests: number;
    peak_day: string | null;
    peak_tokens: number;
  };
  trends: TrendItem[];
  anomalies: AnomalyItem[];
  breakdown_by_tool: Record<string, ToolBreakdown>;
  breakdown_by_host: Record<string, HostBreakdown>;
}

export interface TrendItem {
  metric: string;
  direction: 'up' | 'down' | 'stable';
  change_percentage: number;
  current_value: number;
  previous_value: number;
  period_days: number;
  confidence: number;
}

export interface AnomalyItem {
  type: 'spike' | 'drop' | 'unusual_pattern';
  metric: string;
  date: string;
  expected_value: number;
  actual_value: number;
  deviation_percentage: number;
  severity: 'low' | 'medium' | 'high';
  description: string;
}

export interface ToolBreakdown {
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  requests: number;
  days_active: number;
}

export interface HostBreakdown {
  tokens: number;
  requests: number;
  days_active: number;
}

export interface EfficiencyMetricsResponse {
  efficiency_available: boolean;
  output_ratio?: number;
  tokens_per_request?: number;
  output_per_request?: number;
  input_output_ratio?: number;
  summary?: {
    total_tokens: number;
    total_input: number;
    total_output: number;
    total_requests: number;
  };
}

export interface ExportResponse {
  report: EnterpriseReportResponse;
  exported_at: string;
  format: 'json';
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

  /**
   * Get the global data range (min/max dates) for the "All" quick-range button.
   * Returns null when there is no data.
   */
  async getDataRange(): Promise<DataRange | null> {
    return apiClient.get<DataRange | null>('/api/analysis/data-range');
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

    const response = await apiClient.get<HourlyUsage[]>('/api/analysis/hourly-usage', params);
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

  // Issue #3079: Get user role distribution for role-based grouping
  async getUserRoleDistribution(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<UserRoleDistribution> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;

    return apiClient.get<UserRoleDistribution>('/api/analysis/user-role-distribution', params);
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

  async getAnomalyDetection(
    startDate?: string,
    endDate?: string,
    host?: string,
    type?: string,
    severity?: string
  ): Promise<AnomalyDetectionResponse> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    if (type) params.type = type;
    if (severity) params.severity = severity;

    return apiClient.get<AnomalyDetectionResponse>('/api/analysis/anomaly-detection', params);
  },

  async getAnomalyTrend(
    startDate?: string,
    endDate?: string,
    host?: string,
    type?: string,
    severity?: string
  ): Promise<AnomalyTrendResponse> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    if (type) params.type = type;
    if (severity) params.severity = severity;

    return apiClient.get<AnomalyTrendResponse>('/api/analysis/anomaly-trend', params);
  },

  /**
   * Get usage forecast for the next N days.
   * @param days Number of days to forecast (1-90, default: 7)
   */
  async getForecast(days: number = 7): Promise<ForecastResponse> {
    // Validate days parameter: 1-90, default 7
    let validatedDays = days;
    if (typeof days !== 'number' || isNaN(days)) {
      validatedDays = 7;
    } else {
      validatedDays = Math.floor(days); // Round down
      if (validatedDays <= 0) validatedDays = 1;
      if (validatedDays > 90) validatedDays = 90;
    }

    return apiClient.get<ForecastResponse>('/api/analytics/forecast', {
      days: String(validatedDays),
    });
  },

  /**
   * Get enterprise analytics report.
   * @param params Report parameters
   */
  async getEnterpriseReport(params: {
    startDate?: string;
    endDate?: string;
    days?: number;
    trends?: boolean;
    anomalies?: boolean;
  }): Promise<EnterpriseReportResponse> {
    const queryParams: Record<string, string> = {};
    if (params.startDate) queryParams.start_date = params.startDate;
    if (params.endDate) queryParams.end_date = params.endDate;
    if (params.days !== undefined) queryParams.days = String(params.days);
    if (params.trends !== undefined) queryParams.trends = String(params.trends);
    if (params.anomalies !== undefined) queryParams.anomalies = String(params.anomalies);

    return apiClient.get<EnterpriseReportResponse>('/api/analytics/report', queryParams);
  },

  /**
   * Get efficiency metrics.
   * @param params Efficiency metrics parameters
   */
  async getEfficiencyMetrics(params: {
    startDate?: string;
    endDate?: string;
    days?: number;
  }): Promise<EfficiencyMetricsResponse> {
    const queryParams: Record<string, string> = {};
    if (params.startDate) queryParams.start_date = params.startDate;
    if (params.endDate) queryParams.end_date = params.endDate;
    if (params.days !== undefined) queryParams.days = String(params.days);

    return apiClient.get<EfficiencyMetricsResponse>('/api/analytics/efficiency', queryParams);
  },

  /**
   * Export analytics report.
   * @param params Export parameters
   */
  async exportReport(params: {
    startDate?: string;
    endDate?: string;
    days?: number;
    format: 'csv' | 'json';
  }): Promise<Blob | ExportResponse> {
    const queryParams: Record<string, string> = {
      format: params.format,
    };
    if (params.startDate) queryParams.start_date = params.startDate;
    if (params.endDate) queryParams.end_date = params.endDate;
    if (params.days !== undefined) queryParams.days = String(params.days);

    if (params.format === 'csv') {
      // CSV returns a blob for download
      const response = await fetch(
        `/api/analytics/export?${new URLSearchParams(queryParams).toString()}`,
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem('token') ?? ''}`,
          },
        }
      );
      if (!response.ok) {
        throw new Error(`Export failed: ${response.statusText}`);
      }
      return response.blob();
    }

    // JSON returns data for preview
    return apiClient.get<ExportResponse>('/api/analytics/export', queryParams);
  },
};

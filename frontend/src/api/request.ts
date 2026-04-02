/**
 * Request API - Request statistics and quota checking API calls
 */

import { apiClient } from './client';

// Types
export interface RequestTodayStats {
  date: string;
  total_requests: number;
  by_tool: Record<string, number>;
}

export interface RequestTrendData {
  date: string;
  requests: number;
}

export interface RequestTrendByToolData {
  date: string;
  tool: string;
  requests: number;
}

export interface RequestStatsByUser {
  user: string;
  tool: string;
  requests: number;
  tokens: number;
}

export interface MonthlyRequestStats {
  user: string;
  requests: number;
  tokens: number;
}

export interface QuotaCheckResponse {
  user_id: number;
  username: string;
  daily: {
    tokens: {
      used: number;
      limit: number | null;
      percentage: number;
      over_quota: boolean;
    };
    requests: {
      used: number;
      limit: number | null;
      percentage: number;
      over_quota: boolean;
    };
  };
  monthly: {
    tokens: {
      used: number;
      limit: number | null;
      percentage: number;
      over_quota: boolean;
    };
    requests: {
      used: number;
      limit: number | null;
      percentage: number;
      over_quota: boolean;
    };
  };
  can_use: boolean;
  alerts: Array<{
    id: number;
    user_id: number;
    alert_type: string;
    quota_type: string;
    threshold: number;
    current_usage: number;
    quota_limit: number;
    percentage: number;
    message: string;
    created_at: string;
  }>;
}

export interface QuotaStatusResponse {
  user: {
    id: number;
    username: string;
    email: string;
  };
  daily: {
    tokens: {
      used: number;
      limit: number | null;
    };
    requests: {
      used: number;
      limit: number | null;
    };
  };
  monthly: {
    tokens: {
      used: number;
      limit: number | null;
    };
    requests: {
      used: number;
      limit: number | null;
    };
  };
  over_quota: {
    daily_token: boolean;
    daily_request: boolean;
    monthly_token: boolean;
    monthly_request: boolean;
    any: boolean;
  };
}

export interface UserUsageResponse {
  user: {
    id: number;
    username: string;
  };
  limits: {
    daily_token: number | null;
    monthly_token: number | null;
    daily_request: number | null;
    monthly_request: number | null;
  };
  usage: {
    trend: Array<{
      date: string;
      requests: number;
      tokens: number;
    }>;
  };
  date_range: {
    start: string;
    end: string;
  };
}

// API
export const requestApi = {
  // Request Statistics
  async getTodayStats(host?: string): Promise<RequestTodayStats> {
    const params = host ? { host } : undefined;
    return apiClient.get<RequestTodayStats>('/api/request/today', params);
  },

  async getTrendData(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<RequestTrendData[]> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    return apiClient.get<RequestTrendData[]>('/api/request/trend', params);
  },

  async getTrendByTool(
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<RequestTrendByToolData[]> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    return apiClient.get<RequestTrendByToolData[]>('/api/request/by-tool', params);
  },

  async getStatsByUser(date?: string, host?: string): Promise<RequestStatsByUser[]> {
    const params: Record<string, string> = {};
    if (date) params.date = date;
    if (host) params.host = host;
    return apiClient.get<RequestStatsByUser[]>('/api/request/by-user', params);
  },

  async getUserTrend(
    userName: string,
    startDate?: string,
    endDate?: string,
    host?: string
  ): Promise<Array<{ date: string; requests: number; tokens: number }>> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    if (host) params.host = host;
    return apiClient.get<
      Array<{ date: string; requests: number; tokens: number }>
    >(`/api/request/user/${encodeURIComponent(userName)}/trend`, params);
  },

  async getMonthlyStats(
    year?: number,
    month?: number,
    host?: string
  ): Promise<MonthlyRequestStats[]> {
    const params: Record<string, string> = {};
    if (year) params.year = year.toString();
    if (month) params.month = month.toString();
    if (host) params.host = host;
    return apiClient.get<MonthlyRequestStats[]>('/api/request/monthly', params);
  },

  // Quota Checking
  async checkQuota(): Promise<QuotaCheckResponse> {
    return apiClient.get<QuotaCheckResponse>('/api/quota/check');
  },

  async getQuotaStatus(): Promise<QuotaStatusResponse> {
    return apiClient.get<QuotaStatusResponse>('/api/quota/status');
  },

  async getMyUsage(
    startDate?: string,
    endDate?: string
  ): Promise<UserUsageResponse> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;
    return apiClient.get<UserUsageResponse>('/api/quota/usage/me', params);
  },
};
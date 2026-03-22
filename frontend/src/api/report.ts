/**
 * Report API - Report related API calls
 */

import { apiClient } from './client';

// Types
export interface MyUsageReport {
  user_id: number;
  username: string;
  date_range: {
    start: string;
    end: string;
  };
  totals: {
    tokens: number;
    input_tokens: number;
    output_tokens: number;
    requests: number;
  };
  daily_usage: DailyUsage[];
}

export interface DailyUsage {
  date: string;
  tool_name?: string;
  tokens_used: number;
  input_tokens: number;
  output_tokens: number;
  request_count: number;
}

// API
export const reportApi = {
  /**
   * Get current user's usage report
   */
  async getMyUsage(startDate?: string, endDate?: string): Promise<MyUsageReport> {
    const params: Record<string, string> = {};
    if (startDate) params.start = startDate;
    if (endDate) params.end = endDate;

    return apiClient.get<MyUsageReport>('/api/report/my-usage', params);
  },
};

/**
 * Dashboard API - Dashboard related API calls
 */

import { apiClient } from './client';
import type { ToolUsage, SummaryData } from '@/types';

export interface TodayUsageResponse {
  data: ToolUsage[];
}

export interface SummaryResponse {
  data: SummaryData;
}

export interface TrendDataPoint {
  date: string;
  tool: string;
  tokens: number;
}

export interface TrendResponse {
  data: TrendDataPoint[];
}

export interface HostsResponse {
  hosts: string[];
}

export const dashboardApi = {
  /**
   * Get today's usage data
   */
  async getTodayUsage(tool?: string, host?: string): Promise<ToolUsage[]> {
    const params: Record<string, string> = {};
    if (tool) params.tool = tool;
    if (host) params.host = host;

    const response = await apiClient.get<ToolUsage[]>('/api/today', params);
    return response;
  },

  /**
   * Get summary data
   */
  async getSummary(host?: string): Promise<SummaryData> {
    const params: Record<string, string> = {};
    if (host) params.host = host;

    const response = await apiClient.get<SummaryData>('/api/summary', params);
    return response;
  },

  /**
   * Get trend data for a date range
   */
  async getTrendData(startDate: string, endDate: string, host?: string): Promise<TrendDataPoint[]> {
    const params: Record<string, string> = {
      start: startDate,
      end: endDate,
    };
    if (host) params.host = host;

    const response = await apiClient.get<TrendDataPoint[]>('/api/trend', params);
    return response;
  },

  /**
   * Get list of hosts
   */
  async getHosts(): Promise<string[]> {
    const response = await apiClient.get<string[]>('/api/hosts');
    return response || [];
  },
};

/**
 * Insights API - AI conversation insights report API calls
 */

import { apiClient } from './client';

// Types
export interface InsightSuggestion {
  title: string;
  description: string;
  example?: string;
}

export interface InsightsUsageSummary {
  total_conversations: number;
  total_messages: number;
  total_tokens: number;
  avg_messages_per_conversation: number;
}

export interface InsightsReport {
  id?: number;
  overall_score: number;
  overall_assessment: string;
  strengths: string[];
  areas_for_improvement: string[];
  suggestions: InsightSuggestion[];
  usage_summary: InsightsUsageSummary;
  model?: string;
  start_date?: string;
  end_date?: string;
  created_at?: string;
}

export interface InsightsHistoryItem {
  id: number;
  start_date: string;
  end_date: string;
  overall_score: number;
  created_at: string;
}

export interface InsightsHistoryResponse {
  reports: InsightsHistoryItem[];
}

export interface InsightsGenerateResponse {
  error?: string;
  message?: string;
  [key: string]: unknown;
}

const INSIGHTS_TIMEOUT = 90000; // 90 seconds for AI generation

export const insightsApi = {
  /**
   * Generate or retrieve a cached insights report
   */
  async generateReport(
    startDate?: string,
    endDate?: string,
    signal?: AbortSignal
  ): Promise<InsightsReport | InsightsGenerateResponse> {
    const body: Record<string, string> = {};
    if (startDate) body.start_date = startDate;
    if (endDate) body.end_date = endDate;
    return apiClient.post<InsightsReport | InsightsGenerateResponse>(
      '/api/insights/generate',
      body,
      signal,
      INSIGHTS_TIMEOUT
    );
  },

  /**
   * Get user's insights report history
   */
  async getHistory(signal?: AbortSignal): Promise<InsightsHistoryResponse> {
    return apiClient.get<InsightsHistoryResponse>('/api/insights/history', undefined, signal);
  },

  /**
   * Delete an insights report
   */
  async deleteReport(id: number): Promise<{ message: string }> {
    return apiClient.delete<{ message: string }>(`/api/insights/${id}`);
  },
};

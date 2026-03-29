/**
 * ROI API - ROI analysis and cost optimization API calls
 */

import { apiClient } from './client';

// Types
export interface ROIMetrics {
  period: string;
  start_date: string;
  end_date: string;
  total_cost: number;
  tokens_used: number;
  input_tokens: number;
  output_tokens: number;
  input_cost: number;
  output_cost: number;
  requests_made: number;
  estimated_hours_saved: number;
  estimated_savings: number;
  productivity_gain: number;
  roi_percentage: number;
  cost_per_request: number;
  cost_per_token: number;
  efficiency_score?: number;
  total_savings?: number;
}

export interface ROITrend {
  period: string;
  start_date: string;
  end_date: string;
  month?: string;
  total_cost: number;
  tokens_used: number;
  requests_made: number;
  estimated_savings: number;
  roi_percentage: number;
  cost?: number;
  savings?: number;
}

export interface ROIBreakdown {
  tool_name: string;
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  total_savings?: number;
  roi_percentage?: number;
  token_count?: number;
}

export interface CostBreakdown {
  tool_name: string;
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  category?: string;
  percentage?: number;
  token_count?: number;
}

export interface DailyCost {
  date: string;
  input_tokens: number;
  output_tokens: number;
  input_cost: number;
  output_cost: number;
  total_cost: number;
  cost?: number;
  tokens?: number;
  requests?: number;
}

export interface OptimizationSuggestion {
  suggestion_id: string;
  suggestion_type: string;
  title: string;
  description: string;
  potential_savings: number;
  priority: 'high' | 'medium' | 'low';
  impact?: 'high' | 'medium' | 'low';
  action_items: string[];
  affected_users: number[];
  affected_tools: string[];
  implementation_effort: string;
  current_cost: number;
  optimized_cost: number;
  savings_percentage: number;
  created_at: string;
  type?: string;
  details?: Record<string, unknown>;
}

export interface EfficiencyReport {
  period_days: number;
  total_tokens: number;
  total_requests: number;
  avg_tokens_per_request: number;
  output_ratio: number;
  input_output_ratio: number;
  model_distribution: Record<string, number>;
  unique_models: number;
  unique_tools: number;
  overall_efficiency?: number;
  avg_cost_per_request?: number;
  waste_percentage?: number;
  recommendations?: string[];
}

// API
export const roiApi = {
  async getROI(params?: {
    start_date?: string;
    end_date?: string;
    user_id?: number;
    tool_name?: string;
  }): Promise<ROIMetrics> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);
    if (params?.tool_name) queryParams.tool_name = params.tool_name;

    const response = await apiClient.get<{ success: boolean; data: ROIMetrics }>(
      '/api/roi',
      queryParams
    );
    return response.data;
  },

  async getROITrend(months?: number, userId?: number): Promise<ROITrend[]> {
    const queryParams: Record<string, string> = {};
    if (months) queryParams.months = String(months);
    if (userId) queryParams.user_id = String(userId);

    const response = await apiClient.get<{ success: boolean; data: ROITrend[] }>(
      '/api/roi/trend',
      queryParams
    );
    return response.data;
  },

  async getROIByTool(params?: {
    start_date?: string;
    end_date?: string;
  }): Promise<Record<string, ROIBreakdown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;

    const response = await apiClient.get<{ success: boolean; data: Record<string, ROIBreakdown> }>(
      '/api/roi/by-tool',
      queryParams
    );
    return response.data;
  },

  async getROIByUser(params?: {
    start_date?: string;
    end_date?: string;
  }): Promise<Record<string, ROIBreakdown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;

    const response = await apiClient.get<{ success: boolean; data: Record<string, ROIBreakdown> }>(
      '/api/roi/by-user',
      queryParams
    );
    return response.data;
  },

  async getCostBreakdown(params?: {
    start_date?: string;
    end_date?: string;
    user_id?: number;
  }): Promise<{ breakdown: CostBreakdown[]; total_cost: number }> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);

    const response = await apiClient.get<{
      success: boolean;
      data: { breakdown: CostBreakdown[]; total_cost: number };
    }>('/api/roi/cost-breakdown', queryParams);
    return response.data;
  },

  async getDailyCosts(params?: {
    start_date?: string;
    end_date?: string;
    user_id?: number;
  }): Promise<DailyCost[]> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);

    const response = await apiClient.get<{ success: boolean; data: DailyCost[] }>(
      '/api/roi/daily-costs',
      queryParams
    );
    return response.data;
  },

  async getROISummary(params?: {
    start_date?: string;
    end_date?: string;
    user_id?: number;
  }): Promise<Record<string, unknown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);

    const response = await apiClient.get<{ success: boolean; data: Record<string, unknown> }>(
      '/api/roi/summary',
      queryParams
    );
    return response.data;
  },

  // Optimization
  async getOptimizationSuggestions(days?: number): Promise<OptimizationSuggestion[]> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    const response = await apiClient.get<{ success: boolean; data: OptimizationSuggestion[] }>(
      '/api/optimization/suggestions',
      queryParams
    );
    return response.data;
  },

  async getEfficiencyReport(days?: number): Promise<EfficiencyReport> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    const response = await apiClient.get<{ success: boolean; data: EfficiencyReport }>(
      '/api/optimization/efficiency',
      queryParams
    );
    return response.data;
  },
};

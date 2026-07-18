/**
 * ROI API - ROI analysis and cost optimization API calls
 */

import { apiClient } from './client';

// Types
export interface ROIAssumptions {
  hourly_labor_cost: number;
  productivity_multiplier: number;
  avg_time_saved_per_request: number;
  currency: string;
}

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
  assumptions?: ROIAssumptions | null;
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
  /** Language-neutral interpolation params for localized title/description. */
  params?: Record<string, string | number>;
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

/** Structured, language-neutral efficiency recommendation for i18n. */
export interface RecommendationItem {
  type: string;
  params?: Record<string, string | number>;
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
  /** Structured recommendation items (preferred for localization). */
  recommendation_items?: RecommendationItem[];
  /** @deprecated Language-neutral fallback string list; use recommendation_items. */
  recommendations?: string[];
}

interface ROIRequestParams {
  start_date?: string;
  end_date?: string;
  user_id?: number;
  tool_name?: string;
  assumptions?: ROIAssumptions;
}

function appendAssumptions(queryParams: Record<string, string>, assumptions?: ROIAssumptions) {
  if (!assumptions) return;
  queryParams.hourly_labor_cost = String(assumptions.hourly_labor_cost);
  queryParams.productivity_multiplier = String(assumptions.productivity_multiplier);
  queryParams.avg_time_saved_per_request = String(assumptions.avg_time_saved_per_request);
  queryParams.currency = assumptions.currency;
}

// API
export const roiApi = {
  async getROI(params?: ROIRequestParams): Promise<ROIMetrics> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);
    if (params?.tool_name) queryParams.tool_name = params.tool_name;
    appendAssumptions(queryParams, params?.assumptions);

    const response = await apiClient.get<{ success: boolean; data: ROIMetrics }>(
      '/api/roi',
      queryParams
    );
    return response.data;
  },

  async getROITrend(
    months?: number,
    userId?: number,
    assumptions?: ROIAssumptions
  ): Promise<ROITrend[]> {
    const queryParams: Record<string, string> = {};
    if (months) queryParams.months = String(months);
    if (userId) queryParams.user_id = String(userId);
    appendAssumptions(queryParams, assumptions);

    const response = await apiClient.get<{ success: boolean; data: ROITrend[] }>(
      '/api/roi/trend',
      queryParams
    );
    return response.data;
  },

  async getROIByTool(
    params?: Omit<ROIRequestParams, 'user_id' | 'tool_name'>
  ): Promise<Record<string, ROIBreakdown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    appendAssumptions(queryParams, params?.assumptions);

    const response = await apiClient.get<{ success: boolean; data: Record<string, ROIBreakdown> }>(
      '/api/roi/by-tool',
      queryParams
    );
    return response.data;
  },

  async getROIByUser(
    params?: Omit<ROIRequestParams, 'user_id' | 'tool_name'>
  ): Promise<Record<string, ROIBreakdown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    appendAssumptions(queryParams, params?.assumptions);

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
    assumptions?: ROIAssumptions;
  }): Promise<Record<string, unknown>> {
    const queryParams: Record<string, string> = {};
    if (params?.start_date) queryParams.start_date = params.start_date;
    if (params?.end_date) queryParams.end_date = params.end_date;
    if (params?.user_id) queryParams.user_id = String(params.user_id);
    appendAssumptions(queryParams, params?.assumptions);

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

/**
 * Model Gateway Config API - LiteLLM-compatible model gateway configuration.
 *
 * This file is part of the removable model_gateway feature; deleting it (plus the
 * component, route, and nav entry) is part of the feature's removal checklist.
 */

import { apiClient } from './client';

export interface ModelGatewayConfig {
  id: number;
  mode: string;
  base_url: string;
  api_key_masked?: string;
  model_prefix_mode: boolean;
  model_prefix?: string | null;
  created_by?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ModelGatewayTestResult {
  ok: boolean;
  status: number | null;
  message: string;
}

export const modelGatewayApi = {
  async getConfig(): Promise<ModelGatewayConfig | null> {
    const response = await apiClient.get<{
      success: boolean;
      data: ModelGatewayConfig | null;
      message?: string;
    }>('/api/management/model-gateway-config');
    return response.data;
  },

  async saveConfig(config: {
    base_url: string;
    api_key?: string;
    model_prefix_mode?: boolean;
    model_prefix?: string | null;
  }): Promise<ModelGatewayConfig> {
    const response = await apiClient.put<{
      success: boolean;
      data: ModelGatewayConfig;
      message?: string;
    }>('/api/management/model-gateway-config', config);
    return response.data;
  },

  async deleteConfig(): Promise<void> {
    await apiClient.delete<{ success: boolean }>('/api/management/model-gateway-config');
  },

  async testConnection(config?: {
    base_url?: string;
    api_key?: string;
  }): Promise<ModelGatewayTestResult> {
    const response = await apiClient.post<{
      success: boolean;
      data: ModelGatewayTestResult;
    }>('/api/management/model-gateway-config/test', config ?? {});
    return response.data;
  },
};

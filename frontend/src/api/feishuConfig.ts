/**
 * Feishu Config API - Feishu configuration management API calls
 */

import { apiClient } from './client';

// Types
export interface FeishuConfigResponse {
  id: number;
  app_id: string;
  app_secret_masked?: string;
  tenant_id?: string;
  auto_sync: boolean;
  sync_interval: number;
  is_verified: boolean;
  last_verified_at?: string;
  last_sync_at?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: number;
  created_by_username?: string;
}

export interface FeishuTestResult {
  success: boolean;
  message: string;
}

// API
export const feishuConfigApi = {
  async getConfig(): Promise<FeishuConfigResponse | null> {
    const response = await apiClient.get<{
      success: boolean;
      data: FeishuConfigResponse | null;
      message?: string;
    }>('/api/management/feishu-config');
    return response.data;
  },

  async saveConfig(config: {
    app_id: string;
    app_secret?: string;
    tenant_id?: string;
    auto_sync?: boolean;
    sync_interval?: number;
  }): Promise<FeishuConfigResponse> {
    const response = await apiClient.put<{
      success: boolean;
      data: FeishuConfigResponse;
      message?: string;
    }>('/api/management/feishu-config', config);
    return response.data;
  },

  async testConnection(config?: {
    app_id?: string;
    app_secret?: string;
  }): Promise<FeishuTestResult> {
    const response = await apiClient.post<FeishuTestResult>(
      '/api/management/feishu-config/test',
      config ?? {}
    );
    return response;
  },

  async deleteConfig(): Promise<void> {
    await apiClient.delete<{ success: boolean }>('/api/management/feishu-config');
  },
};
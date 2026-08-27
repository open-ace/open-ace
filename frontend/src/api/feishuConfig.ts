/**
 * Feishu Config API - Feishu configuration management API calls
 */

import { apiClient } from './client';

// Types
export type FeishuVerificationStatus =
  | 'configured_unverified'
  | 'connected'
  | 'connection_failed'
  | 'configuration_error';

export interface FeishuConfigResponse {
  app_id: string;
  app_secret_masked?: string;
  org_sync_enabled: boolean;
  org_sync_interval_minutes: number;
  org_sync_tenant_id?: number;
  org_sync_max_runtime_seconds?: number;
  org_sync_auto_recover?: boolean;
  // Verification status fields
  verification_status?: FeishuVerificationStatus;
  last_tested_at?: string;
  last_test_error_code?: string;
  last_test_error_summary?: string;
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
    org_sync_enabled?: boolean;
    org_sync_interval_minutes?: number;
    org_sync_tenant_id?: number;
    org_sync_max_runtime_seconds?: number;
    org_sync_auto_recover?: boolean;
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

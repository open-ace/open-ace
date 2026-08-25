import { apiClient } from './client';

export type ChannelStatus = Record<string, { status: string; verified?: boolean }>;
export interface DingTalkConfig {
  app_key?: string;
  app_secret_configured?: boolean;
  fallback_webhook_secret_configured?: boolean;
  sync_enabled?: boolean;
  target_tenant_id?: number | null;
  interval_minutes?: number;
  root_dept_id?: string;
  max_runtime_seconds?: number;
  auto_recovery?: boolean;
}
export interface WebhookConfig {
  webhook_secret_configured?: boolean;
  allow_private_webhook_urls?: boolean;
  enabled?: boolean;
}

export const notificationChannelsApi = {
  async status(): Promise<ChannelStatus> {
    return (
      await apiClient.get<{ success: boolean; data: ChannelStatus }>(
        '/api/management/notification-channels/status'
      )
    ).data;
  },
  async getWebhook() {
    return (
      await apiClient.get<{ success: boolean; data: WebhookConfig | null }>(
        '/api/management/webhook-config'
      )
    ).data;
  },
  async saveWebhook(data: Record<string, unknown>) {
    return (
      await apiClient.put<{ success: boolean; data: Record<string, unknown> }>(
        '/api/management/webhook-config',
        data
      )
    ).data;
  },
  async getDingTalk() {
    return (
      await apiClient.get<{ success: boolean; data: DingTalkConfig | null }>(
        '/api/management/dingtalk-config'
      )
    ).data;
  },
  async saveDingTalk(data: Record<string, unknown>) {
    return (
      await apiClient.put<{ success: boolean; data: Record<string, unknown> }>(
        '/api/management/dingtalk-config',
        data
      )
    ).data;
  },
  async testDingTalk(data: { app_key?: string; app_secret?: string }) {
    return await apiClient.post<{
      success: boolean;
      message: string;
      checks?: Record<string, { status: string; message: string }>;
    }>('/api/management/dingtalk-config/test', data);
  },
  async syncDingTalk(tenantId: number) {
    return await apiClient.post<{ success: boolean; result: Record<string, unknown> }>(
      '/api/admin/dingtalk/sync',
      { tenant_id: tenantId }
    );
  },
};

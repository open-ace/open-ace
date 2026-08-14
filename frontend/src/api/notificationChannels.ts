import { apiClient } from './client';

export type ChannelStatus = Record<string, { status: string; verified?: boolean }>;

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
      await apiClient.get<{ success: boolean; data: Record<string, unknown> | null }>(
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
      await apiClient.get<{ success: boolean; data: Record<string, unknown> | null }>(
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
};

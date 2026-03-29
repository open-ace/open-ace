/**
 * Alerts API - Alert management API calls
 */

import { apiClient } from './client';

// Types
export interface Alert {
  id: string;
  type: 'quota' | 'system' | 'security';
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  user_id?: number;
  username?: string;
  is_read: boolean;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface AlertListResponse {
  alerts: Alert[];
  unread_count: number;
}

export interface NotificationPreferences {
  email_enabled: boolean;
  push_enabled: boolean;
  webhook_url?: string;
  alert_types: string[];
  min_severity: 'info' | 'warning' | 'critical';
}

// API
export const alertsApi = {
  async getAlerts(params?: {
    type?: string;
    severity?: string;
    unread_only?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<AlertListResponse> {
    const queryParams: Record<string, string> = {};
    if (params?.type) queryParams.type = params.type;
    if (params?.severity) queryParams.severity = params.severity;
    if (params?.unread_only) queryParams.unread_only = 'true';
    if (params?.limit) queryParams.limit = String(params.limit);
    if (params?.offset) queryParams.offset = String(params.offset);

    const response = await apiClient.get<{ success: boolean; data: AlertListResponse }>(
      '/api/alerts',
      queryParams
    );
    return response.data;
  },

  async getUnreadCount(): Promise<number> {
    const response = await apiClient.get<{ success: boolean; data: { count: number } }>(
      '/api/alerts/unread-count'
    );
    return response.data.count;
  },

  async markAsRead(alertId: string): Promise<void> {
    await apiClient.post<{ success: boolean }>(`/api/alerts/${alertId}/read`);
  },

  async markAllAsRead(): Promise<number> {
    const response = await apiClient.post<{ success: boolean; data: { marked_count: number } }>(
      '/api/alerts/read-all'
    );
    return response.data.marked_count;
  },

  async deleteAlert(alertId: string): Promise<void> {
    await apiClient.delete<{ success: boolean }>(`/api/alerts/${alertId}`);
  },

  async getPreferences(): Promise<NotificationPreferences> {
    const response = await apiClient.get<{ success: boolean; data: NotificationPreferences }>(
      '/api/alerts/preferences'
    );
    return response.data;
  },

  async updatePreferences(prefs: Partial<NotificationPreferences>): Promise<void> {
    await apiClient.put<{ success: boolean }>('/api/alerts/preferences', prefs);
  },

  async createTestAlert(data: {
    type?: string;
    severity?: string;
    title?: string;
    message?: string;
  }): Promise<Alert> {
    const response = await apiClient.post<{ success: boolean; data: Alert }>(
      '/api/alerts/test',
      data
    );
    return response.data;
  },
};

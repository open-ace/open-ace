/**
 * Alerts API - Alert management API calls
 */

import { apiClient } from './client';

// Types
export interface Alert {
  alert_id: string;
  type: 'quota' | 'system' | 'security';
  severity: 'info' | 'warning' | 'critical';
  title: string;
  message: string;
  user_id?: number;
  username?: string;
  read: boolean;
  created_at: string;
  metadata?: Record<string, unknown>;
  tool_name?: string;
  action_url?: string;
  action_text?: string;
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
  notification_email?: string;
  email_verified?: boolean;
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

  async getAdminQuotaAlerts(params?: {
    unacknowledged_only?: boolean;
    limit?: number;
  }): Promise<AlertListResponse> {
    const queryParams: Record<string, string> = {};
    if (params?.unacknowledged_only) queryParams.unacknowledged_only = 'true';
    if (params?.limit) queryParams.limit = String(params.limit);

    // Admin API returns Alert[] directly (array), need to wrap it
    const response = await apiClient.get<Alert[]>('/api/quota/alerts', queryParams);
    // Calculate unread count from the array
    const unread_count = response.filter((a) => !a.read).length;
    return { alerts: response, unread_count };
  },

  // Issue #3082: Manager 获取租户范围告警
  async getTenantAlerts(params?: { limit?: number; offset?: number }): Promise<AlertListResponse> {
    const queryParams: Record<string, string> = {};
    if (params?.limit) queryParams.limit = String(params.limit);
    if (params?.offset) queryParams.offset = String(params.offset);

    // Tenant API returns Alert[] directly (array), need to wrap it
    const response = await apiClient.get<Alert[]>('/api/alerts/tenant', queryParams);
    // Calculate unread count from the array
    const unread_count = response.filter((a) => !a.read).length;
    return { alerts: response, unread_count };
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

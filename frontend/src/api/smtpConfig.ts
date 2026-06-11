/**
 * SMTP Config API - SMTP configuration management API calls
 */

import { apiClient } from './client';

// Types
export interface SMTPConfig {
  id: number;
  smtp_host: string;
  smtp_port: number;
  smtp_user?: string;
  smtp_password_masked?: string;
  from_address: string;
  use_tls: boolean;
  is_verified: boolean;
  last_verified_at?: string;
  created_at?: string;
  updated_at?: string;
  created_by?: number;
}

export interface SMTPTestResult {
  success: boolean;
  message: string;
}

export interface EmailStatistics {
  total_sent: number;
  successful: number;
  failed: number;
  pending: number;
  success_rate: number;
  average_retries: number;
  period_days: number;
}

export interface SendTestEmailResult {
  success: boolean;
  message: string;
  log_id?: number;
  rate_limit_remaining?: number;
}

// API
export const smtpConfigApi = {
  async getConfig(): Promise<SMTPConfig | null> {
    const response = await apiClient.get<{
      success: boolean;
      data: SMTPConfig | null;
      message?: string;
    }>('/api/management/smtp-config');
    return response.data;
  },

  async saveConfig(config: {
    smtp_host: string;
    smtp_port: number;
    smtp_user?: string;
    smtp_password?: string;
    from_address: string;
    use_tls?: boolean;
  }): Promise<SMTPConfig> {
    const response = await apiClient.put<{
      success: boolean;
      data: SMTPConfig;
      message?: string;
    }>('/api/management/smtp-config', config);
    return response.data;
  },

  async testConnection(config?: {
    smtp_host?: string;
    smtp_port?: number;
    smtp_user?: string;
    smtp_password?: string;
    from_address?: string;
    use_tls?: boolean;
  }): Promise<SMTPTestResult> {
    const response = await apiClient.post<SMTPTestResult>(
      '/api/management/smtp-config/test',
      config || {}
    );
    return response;
  },

  async deleteConfig(): Promise<void> {
    await apiClient.delete<{ success: boolean }>('/api/management/smtp-config');
  },

  async getStatistics(days?: number): Promise<EmailStatistics> {
    const params = days ? { days: String(days) } : {};
    const response = await apiClient.get<{
      success: boolean;
      data: EmailStatistics;
    }>('/api/management/smtp-config/statistics', params);
    return response.data;
  },

  async sendTestEmail(
    recipient_email: string,
    language?: string
  ): Promise<SendTestEmailResult> {
    const response = await apiClient.post<SendTestEmailResult>(
      '/api/management/smtp-config/send-test',
      { recipient_email, language }
    );
    return response;
  },
};
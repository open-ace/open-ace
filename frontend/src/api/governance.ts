/**
 * Governance API - Governance related API calls (audit logs, content filter, security)
 */

import { apiClient } from './client';

// Types
export interface AuditLog {
  id: number;
  user_id: number;
  username?: string;
  action: string;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  ip_address?: string;
  timestamp: string;
}

export interface AuditLogFilters {
  user_id?: number;
  action?: string;
  resource_type?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  limit?: number;
}

export interface AuditLogResponse {
  logs: AuditLog[];
  total: number;
  limit: number;
  offset: number;
}

export interface ContentFilterRule {
  id: number;
  pattern: string;
  type: 'keyword' | 'regex' | 'pii';
  severity: 'low' | 'medium' | 'high';
  action: 'warn' | 'block' | 'redact';
  is_enabled: boolean;
  description?: string;
  created_at: string;
  updated_at?: string;
}

export interface CreateFilterRuleRequest {
  pattern: string;
  type: 'keyword' | 'regex' | 'pii';
  severity: 'low' | 'medium' | 'high';
  action: 'warn' | 'block' | 'redact';
  description?: string;
  is_enabled?: boolean;
}

export interface FilterCheckResult {
  passed: boolean;
  risk_level: 'low' | 'medium' | 'high';
  matched_rules: string[];
  suggestion?: string;
}

export interface SecuritySettings {
  session_timeout: number;
  max_login_attempts: number;
  password_min_length: number;
  password_require_uppercase: boolean;
  password_require_lowercase: boolean;
  password_require_number: boolean;
  password_require_special: boolean;
  two_factor_enabled: boolean;
  ip_whitelist: string[];
}

// API
export const governanceApi = {
  // Audit Logs
  async getAuditLogs(filters?: AuditLogFilters): Promise<AuditLogResponse> {
    const params: Record<string, string> = {};
    if (filters?.user_id) params.user_id = String(filters.user_id);
    if (filters?.action) params.action = filters.action;
    if (filters?.resource_type) params.resource_type = filters.resource_type;
    if (filters?.start_date) params.start_date = filters.start_date;
    if (filters?.end_date) params.end_date = filters.end_date;
    if (filters?.page) params.page = String(filters.page);
    if (filters?.limit) params.limit = String(filters.limit);

    return apiClient.get<AuditLogResponse>('/api/governance/audit-logs', params);
  },

  async exportAuditLogs(format: 'json' | 'csv' = 'json'): Promise<Blob> {
    const response = await fetch(`/api/governance/audit-logs/export?format=${format}`);
    return response.blob();
  },

  // Content Filter
  async getFilterRules(): Promise<ContentFilterRule[]> {
    return apiClient.get<ContentFilterRule[]>('/api/filter-rules');
  },

  async createFilterRule(data: CreateFilterRuleRequest): Promise<{ success: boolean; id: number }> {
    return apiClient.post<{ success: boolean; id: number }>('/api/filter-rules', data);
  },

  async updateFilterRule(
    ruleId: number,
    data: Partial<CreateFilterRuleRequest>
  ): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(`/api/filter-rules/${ruleId}`, data);
  },

  async deleteFilterRule(ruleId: number): Promise<{ success: boolean }> {
    return apiClient.delete<{ success: boolean }>(`/api/filter-rules/${ruleId}`);
  },

  async checkContent(content: string): Promise<FilterCheckResult> {
    return apiClient.post<FilterCheckResult>('/api/governance/content/check', { content });
  },

  // Security Settings
  async getSecuritySettings(): Promise<SecuritySettings> {
    return apiClient.get<SecuritySettings>('/api/security-settings');
  },

  async updateSecuritySettings(data: Partial<SecuritySettings>): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>('/api/security-settings', data);
  },
};

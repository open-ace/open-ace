/**
 * Admin API - Admin related API calls
 */

import { apiClient } from './client';

// Types
export interface AdminUser {
  id: number;
  username: string;
  email: string;
  role: 'admin' | 'manager' | 'user' | 'readonly';
  is_active: boolean;
  created_at: string;
  last_login?: string;
  system_account?: string;
  daily_token_quota?: number;
  monthly_token_quota?: number;
  daily_request_quota?: number;
  monthly_request_quota?: number;
  tenant_id?: number;
  tenant_name?: string;
}

export interface CreateUserRequest {
  username: string;
  email: string;
  password: string;
  role?: 'admin' | 'manager' | 'user' | 'readonly';
  system_account?: string;
  tenant_id?: number;
}

export interface UpdateUserRequest {
  username?: string;
  email?: string;
  role?: 'admin' | 'manager' | 'user' | 'readonly';
  is_active?: boolean;
  system_account?: string;
  password?: string;
  tenant_id?: number;
}

export interface UpdateQuotaRequest {
  daily_token_quota?: number;
  monthly_token_quota?: number;
  daily_request_quota?: number;
  monthly_request_quota?: number;
}

export interface QuotaUsage extends AdminUser {
  tokens_used_today?: number;
  tokens_used_month?: number;
  requests_today?: number;
  requests_month?: number;
}

export interface QuotaStats {
  tenant_quota: {
    daily_token_limit: number;
    monthly_token_limit: number;
    daily_request_limit: number;
    monthly_request_limit: number;
    max_users: number;
  };
  allocated: {
    daily_token: number;
    monthly_token: number;
    daily_request: number;
    monthly_request: number;
  };
  remaining: {
    daily_token: number;
    monthly_token: number;
    daily_request: number;
    monthly_request: number;
  };
  percentages: {
    daily_token: number;
    monthly_token: number;
    daily_request: number;
    monthly_request: number;
  };
  user_count: {
    total: number;
    active: number;
    max: number;
  };
}

export interface FeishuSyncResult {
  tenant_id: number;
  departments_seen: number;
  users_seen: number;
  teams_created: number;
  teams_updated: number;
  users_created: number;
  users_linked: number;
  users_updated: number;
  memberships_added: number;
  memberships_removed: number;
  started_at?: string | null;
  finished_at?: string | null;
  warnings: string[];
}

export interface AuditLogEntry {
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

export interface ContentFilterRule {
  id: number;
  pattern: string;
  type: 'keyword' | 'regex' | 'pii';
  severity: 'low' | 'medium' | 'high';
  action: 'warn' | 'block' | 'redact';
  is_enabled: boolean;
  created_at: string;
  updated_at?: string;
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
export const adminApi = {
  // User Management
  async getUsers(tenantId?: number): Promise<AdminUser[]> {
    const queryParams = tenantId ? { tenant_id: String(tenantId) } : undefined;
    return apiClient.get<AdminUser[]>('/api/admin/users', queryParams);
  },

  async createUser(data: CreateUserRequest): Promise<{ success: boolean; user_id: number }> {
    return apiClient.post<{ success: boolean; user_id: number }>('/api/admin/users', data);
  },

  async updateUser(userId: number, data: UpdateUserRequest): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(`/api/admin/users/${userId}`, data);
  },

  async deleteUser(userId: number): Promise<{ success: boolean }> {
    return apiClient.delete<{ success: boolean }>(`/api/admin/users/${userId}`);
  },

  async updateUserPassword(userId: number, password: string): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(`/api/admin/users/${userId}/password`, { password });
  },

  async resetUserPassword(userId: number): Promise<{
    success: boolean;
    temporary_password: string;
    message?: string;
  }> {
    return apiClient.post<{
      success: boolean;
      temporary_password: string;
      message?: string;
    }>(`/api/admin/users/${userId}/reset-password`);
  },

  // Quota Management
  async getQuotaUsage(): Promise<QuotaUsage[]> {
    return apiClient.get<QuotaUsage[]>('/api/admin/quota/usage');
  },

  async getQuotaStats(): Promise<QuotaStats> {
    return apiClient.get<QuotaStats>('/api/admin/quota/stats');
  },

  async updateUserQuota(userId: number, data: UpdateQuotaRequest): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(`/api/admin/users/${userId}/quota`, data);
  },

  async syncFeishuOrg(tenantId?: number): Promise<{ success: boolean; result: FeishuSyncResult }> {
    return apiClient.post<{ success: boolean; result: FeishuSyncResult }>(
      '/api/admin/feishu/sync',
      tenantId ? { tenant_id: tenantId } : {}
    );
  },
};

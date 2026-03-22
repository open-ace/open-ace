/**
 * Admin API - Admin related API calls
 */

import { apiClient } from './client';

// Types
export interface AdminUser {
  id: number;
  username: string;
  email: string;
  role: 'admin' | 'user' | 'viewer';
  is_active: boolean;
  created_at: string;
  last_login?: string;
  linux_account?: string;
  daily_token_quota?: number;
  monthly_token_quota?: number;
  daily_request_quota?: number;
  monthly_request_quota?: number;
}

export interface CreateUserRequest {
  username: string;
  email: string;
  password: string;
  role?: 'admin' | 'user' | 'viewer';
  linux_account?: string;
}

export interface UpdateUserRequest {
  username?: string;
  email?: string;
  role?: 'admin' | 'user' | 'viewer';
  is_active?: boolean;
  linux_account?: string;
  password?: string;
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
  async getUsers(): Promise<AdminUser[]> {
    return apiClient.get<AdminUser[]>('/api/admin/users');
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

  // Quota Management
  async getQuotaUsage(): Promise<QuotaUsage[]> {
    return apiClient.get<QuotaUsage[]>('/api/admin/quota/usage');
  },

  async updateUserQuota(userId: number, data: UpdateQuotaRequest): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(`/api/admin/users/${userId}/quota`, data);
  },
};

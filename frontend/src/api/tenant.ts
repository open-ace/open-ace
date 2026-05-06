/**
 * Tenant API - Multi-tenant management API calls
 */

import { apiClient } from './client';

// Types
export interface Tenant {
  id: number;
  name: string;
  slug: string;
  plan: 'standard' | 'premium' | 'enterprise';
  status: 'active' | 'suspended' | 'trial';
  contact_email?: string;
  contact_name?: string;
  contact_phone?: string;
  trial_ends_at?: string;
  subscription_ends_at?: string;
  created_at: string;
  updated_at?: string;
  quota?: TenantQuota;
  settings?: Record<string, unknown>;
  user_count?: number;
  total_tokens_used?: number;
  total_requests_made?: number;
}

export interface TenantQuota {
  daily_token_limit: number;
  monthly_token_limit: number;
  daily_request_limit: number;
  monthly_request_limit: number;
  max_users: number;
  max_sessions_per_user: number;
}

export interface CreateTenantRequest {
  name: string;
  slug?: string;
  plan?: 'standard' | 'premium' | 'enterprise';
  contact_email?: string;
  contact_name?: string;
  trial_days?: number;
}

export interface UpdateTenantRequest {
  name?: string;
  slug?: string;
  plan?: 'standard' | 'premium' | 'enterprise';
  status?: 'active' | 'suspended' | 'trial';
  contact_email?: string;
  contact_phone?: string;
  contact_name?: string;
  trial_ends_at?: string;
  subscription_ends_at?: string;
}

export interface UpdateTenantQuotaRequest {
  daily_token_limit?: number;
  monthly_token_limit?: number;
  daily_request_limit?: number;
  monthly_request_limit?: number;
  max_users?: number;
  max_sessions_per_user?: number;
}

export interface TenantUsage {
  date: string;
  tokens: number;
  requests: number;
}

export interface TenantStats {
  total_users: number;
  active_users: number;
  total_sessions: number;
  total_tokens: number;
  total_requests: number;
}

export interface PlanQuota {
  plan: string;
  monthly_tokens: number;
  monthly_requests: number;
  features: string[];
}

// API
export const tenantApi = {
  async listTenants(params?: {
    status?: string;
    plan?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ tenants: Tenant[]; count: number }> {
    const queryParams: Record<string, string> = {};
    if (params?.status) queryParams.status = params.status;
    if (params?.plan) queryParams.plan = params.plan;
    if (params?.limit) queryParams.limit = String(params.limit);
    if (params?.offset) queryParams.offset = String(params.offset);

    return apiClient.get<{ tenants: Tenant[]; count: number }>('/api/tenants', queryParams);
  },

  async getTenant(tenantId: number): Promise<Tenant> {
    return apiClient.get<Tenant>(`/api/tenants/${tenantId}`);
  },

  async getTenantBySlug(slug: string): Promise<Tenant> {
    return apiClient.get<Tenant>(`/api/tenants/slug/${slug}`);
  },

  async createTenant(data: CreateTenantRequest): Promise<Tenant> {
    return apiClient.post<Tenant>('/api/tenants', data);
  },

  async updateTenant(tenantId: number, data: UpdateTenantRequest): Promise<Tenant> {
    return apiClient.put<Tenant>(`/api/tenants/${tenantId}`, data);
  },

  async updateQuota(tenantId: number, data: UpdateTenantQuotaRequest): Promise<Tenant> {
    return apiClient.put<Tenant>(`/api/tenants/${tenantId}/quota`, data);
  },

  async updateSettings(tenantId: number, data: Record<string, unknown>): Promise<Tenant> {
    return apiClient.put<Tenant>(`/api/tenants/${tenantId}/settings`, data);
  },

  async suspendTenant(tenantId: number, reason?: string): Promise<Tenant> {
    return apiClient.post<Tenant>(`/api/tenants/${tenantId}/suspend`, { reason });
  },

  async activateTenant(tenantId: number): Promise<Tenant> {
    return apiClient.post<Tenant>(`/api/tenants/${tenantId}/activate`);
  },

  async deleteTenant(tenantId: number, hard?: boolean): Promise<void> {
    let url = `/api/tenants/${tenantId}`;
    if (hard) {
      url += '?hard=true';
    }
    await apiClient.delete<{ message: string }>(url);
  },

  async getTenantUsage(tenantId: number, days?: number): Promise<TenantUsage[]> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    const response = await apiClient.get<{
      tenant_id: number;
      days: number;
      usage: TenantUsage[];
    }>(`/api/tenants/${tenantId}/usage`, queryParams);
    return response.usage;
  },

  async getTenantStats(tenantId: number): Promise<TenantStats> {
    return apiClient.get<TenantStats>(`/api/tenants/${tenantId}/stats`);
  },

  async checkQuota(
    tenantId: number,
    data: { tokens?: number; requests?: number }
  ): Promise<{ allowed: boolean; reason?: string }> {
    return apiClient.post<{ allowed: boolean; reason?: string }>(
      `/api/tenants/${tenantId}/check-quota`,
      data
    );
  },

  async getPlanQuotas(): Promise<Record<string, PlanQuota>> {
    return apiClient.get<Record<string, PlanQuota>>('/api/tenants/plans');
  },
};

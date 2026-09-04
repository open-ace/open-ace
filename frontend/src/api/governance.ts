/**
 * Governance API - Governance related API calls (audit logs, content filter, security)
 */

import { apiClient } from './client';

// Types
export interface AuditLog {
  id: number;
  user_id: number | null;
  username?: string | null;
  action: string;
  severity: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown>;
  ip_address?: string | null;
  user_agent?: string | null;
  session_id?: string | null;
  success: boolean;
  error_message?: string | null;
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

export interface PasswordPolicy {
  password_min_length: number;
  password_require_uppercase: boolean;
  password_require_lowercase: boolean;
  password_require_number: boolean;
  password_require_special: boolean;
}

// SSRF Protection Status Types (Issue #3328)
export interface SsrfPortWhitelist {
  value: number[];
  is_customized: boolean;
  default_value: number[];
}

export interface SsrfGlobalAllowlist {
  count: number;
  entries: Array<{ host: string; type: string }>;
  is_customized: boolean;
}

export interface SsrfTenantAllowlist {
  enabled: boolean;
  tenant_count: number;
}

export interface SsrfDefaultPolicy {
  blocked_hostnames: string[];
  blocked_private_networks: string[];
  default_port_whitelist: number[];
}

export interface SsrfInterceptionStats {
  last_24h: number;
  last_7d: number;
  last_30d: number;
}

export interface SsrfStatus {
  ssrf_protection_enabled: boolean;
  emergency_mode: boolean;
  config_source: 'database' | 'environment' | 'default';
  config_version: number;
  port_whitelist: SsrfPortWhitelist;
  global_allowlist: SsrfGlobalAllowlist;
  tenant_allowlist: SsrfTenantAllowlist;
  default_policy: SsrfDefaultPolicy;
  interception_stats: SsrfInterceptionStats;
  can_reset: boolean;
}

export interface ResetSsrfConfigRequest {
  reset_ports: boolean;
  reset_global_allowlist: boolean;
  expected_version: number;
  reason?: string;
}

export interface ResetSsrfConfigResponse {
  success: boolean;
  reset_items: string[];
  new_config_version: number;
  message: string;
}

export interface FilterStats {
  enabled: boolean;
  redact_pii: boolean;
  block_high_risk: boolean;
  pattern_count: number;
  keyword_count: number;
  patterns: string[];
  compiled_cache_size: number;
  compiled_cache_hits: number;
  compiled_cache_misses: number;
  compiled_cache_hit_rate: number;
  compiled_cache_max_size: number;
}

// Sensitive Keywords Types (Issue #3059)
export interface SensitiveKeyword {
  id: number;
  tenant_id: number;
  keyword: string;
  is_enabled: boolean;
  created_by: number;
  created_at: string;
  updated_at?: string;
  is_new?: boolean; // Idempotency indicator in POST response
}

export interface SensitiveKeywordsResponse {
  keywords: SensitiveKeyword[];
  total: number;
  limit: number;
  offset: number;
  tenant_id: number;
}

export interface CreateSensitiveKeywordRequest {
  keyword: string;
}

export interface SensitiveKeywordsFilters {
  limit?: number;
  offset?: number;
  is_enabled?: boolean;
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
    const response = await fetch(`/api/audit/logs/export?format=${format}`);
    return response.blob();
  },

  // Content Filter
  async getFilterRules(): Promise<ContentFilterRule[]> {
    // GET /api/filter-rules returns a paginated envelope
    // ({rules, total, limit, offset} — see app/routes/governance.py).
    // SecurityCenter consumes the bare rule array; unwrapping here keeps
    // rules.map() from throwing ("N.map is not a function") and blanking
    // the whole Security Center page.
    const response = await apiClient.get<{ rules?: ContentFilterRule[] } | ContentFilterRule[]>(
      '/api/filter-rules'
    );
    return Array.isArray(response) ? response : (response.rules ?? []);
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
    return apiClient.post<FilterCheckResult>('/api/content/check', { content });
  },

  // Filter Statistics
  async getFilterStats(): Promise<FilterStats> {
    return apiClient.get<FilterStats>('/api/content/filter/stats');
  },

  // Security Settings
  async getSecuritySettings(): Promise<SecuritySettings> {
    return apiClient.get<SecuritySettings>('/api/security-settings');
  },

  async updateSecuritySettings(data: Partial<SecuritySettings>): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>('/api/security-settings', data);
  },

  // Password Policy (accessible to all authenticated users)
  async getPasswordPolicy(): Promise<PasswordPolicy> {
    return apiClient.get<PasswordPolicy>('/api/password-policy');
  },

  // SSRF Protection Status (Issue #3328)
  async getSsrfStatus(): Promise<SsrfStatus> {
    return apiClient.get<SsrfStatus>('/api/security-settings/ssrf-status');
  },

  async resetSsrfConfig(data: ResetSsrfConfigRequest): Promise<ResetSsrfConfigResponse> {
    return apiClient.post<ResetSsrfConfigResponse>('/api/security-settings/ssrf/reset', data);
  },

  // Sensitive Keywords (Issue #3059)
  async getSensitiveKeywords(
    tenantId: number,
    filters?: SensitiveKeywordsFilters
  ): Promise<SensitiveKeywordsResponse> {
    const params: Record<string, string> = {};
    if (filters?.limit) params.limit = String(filters.limit);
    if (filters?.offset) params.offset = String(filters.offset);
    if (filters?.is_enabled !== undefined) params.is_enabled = String(filters.is_enabled);
    return apiClient.get<SensitiveKeywordsResponse>(
      `/api/tenants/${tenantId}/sensitive-keywords`,
      params
    );
  },

  async createSensitiveKeyword(
    tenantId: number,
    data: CreateSensitiveKeywordRequest
  ): Promise<SensitiveKeyword> {
    return apiClient.post<SensitiveKeyword>(`/api/tenants/${tenantId}/sensitive-keywords`, data);
  },

  async updateSensitiveKeyword(
    tenantId: number,
    keywordId: number,
    data: { is_enabled: boolean }
  ): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>(
      `/api/tenants/${tenantId}/sensitive-keywords/${keywordId}`,
      data
    );
  },

  async deleteSensitiveKeyword(tenantId: number, keywordId: number): Promise<{ success: boolean }> {
    return apiClient.delete<{ success: boolean }>(
      `/api/tenants/${tenantId}/sensitive-keywords/${keywordId}`
    );
  },
};

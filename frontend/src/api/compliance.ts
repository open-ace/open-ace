/**
 * Compliance API - Compliance reporting and data retention API calls
 */

import { apiClient } from './client';

// Types
export interface ReportType {
  type: string;
  name: string;
  description: string;
}

export interface ComplianceReport {
  metadata: {
    report_id: string;
    report_type: string;
    generated_at: string;
    period_start: string;
    period_end: string;
    generated_by?: number;
  };
  summary: Record<string, unknown>;
  sections: Record<string, unknown>[];
}

export interface SavedReport {
  report_id: string;
  report_type: string;
  generated_at: string;
  period_start: string;
  period_end: string;
  generated_by?: number;
}

export interface AuditPattern {
  login_patterns: Record<string, number>;
  operation_distribution: Record<string, number>;
  resource_access_heatmap: Record<string, number>;
}

export interface AuditAnomaly {
  type: string;
  description: string;
  timestamp: string;
  severity: 'low' | 'medium' | 'high';
  details?: Record<string, unknown>;
}

export interface UserProfile {
  user_id: number;
  username?: string;
  active_hours: Record<string, number>;
  common_operations: string[];
  access_patterns: Record<string, number>;
}

export interface SecurityScore {
  overall_score: number;
  categories: Record<string, { score: number; status: string }>;
  recommendations: string[];
}

export interface RetentionRule {
  data_type: string;
  retention_days: number;
  action: 'delete' | 'archive';
}

export interface RetentionHistory {
  executed_at: string;
  cleanup_type: string;
  records_deleted: number;
  status: 'success' | 'failed';
}

export interface StorageEstimate {
  data_type: string;
  record_count: number;
  estimated_size_mb: number;
}

// API
export const complianceApi = {
  // Reports
  async getReportTypes(): Promise<ReportType[]> {
    const response = await apiClient.get<{ report_types: ReportType[] }>('/api/compliance/reports');
    return response.report_types;
  },

  async generateReport(data: {
    report_type: string;
    period_start?: string;
    period_end?: string;
    format?: 'json' | 'csv';
    tenant_id?: number;
    filters?: Record<string, unknown>;
  }): Promise<ComplianceReport> {
    return apiClient.post<ComplianceReport>('/api/compliance/reports', data);
  },

  async getSavedReports(params?: {
    report_type?: string;
    tenant_id?: number;
    limit?: number;
  }): Promise<SavedReport[]> {
    const queryParams: Record<string, string> = {};
    if (params?.report_type) queryParams.report_type = params.report_type;
    if (params?.tenant_id) queryParams.tenant_id = String(params.tenant_id);
    if (params?.limit) queryParams.limit = String(params.limit);

    const response = await apiClient.get<{ reports: SavedReport[] }>(
      '/api/compliance/reports/saved',
      queryParams
    );
    return response.reports;
  },

  async getSavedReport(reportId: string, format?: 'json' | 'csv'): Promise<ComplianceReport> {
    const queryParams: Record<string, string> = format ? { format } : {};
    return apiClient.get<ComplianceReport>(`/api/compliance/reports/${reportId}`, queryParams);
  },

  // Audit Analysis
  async analyzePatterns(days?: number): Promise<AuditPattern> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    return apiClient.get<AuditPattern>('/api/compliance/audit/patterns', queryParams);
  },

  async detectAnomalies(days?: number): Promise<{ anomalies: AuditAnomaly[]; count: number }> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    return apiClient.get<{ anomalies: AuditAnomaly[]; count: number }>(
      '/api/compliance/audit/anomalies',
      queryParams
    );
  },

  async getUserProfile(userId: number, days?: number): Promise<UserProfile> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    return apiClient.get<UserProfile>(`/api/compliance/audit/user/${userId}/profile`, queryParams);
  },

  async getSecurityScore(days?: number): Promise<SecurityScore> {
    const queryParams: Record<string, string> = days ? { days: String(days) } : {};
    return apiClient.get<SecurityScore>('/api/compliance/audit/security-score', queryParams);
  },

  // Data Retention
  async getRetentionRules(): Promise<Record<string, RetentionRule>> {
    const response = await apiClient.get<{ rules: Record<string, RetentionRule> }>(
      '/api/compliance/retention/rules'
    );
    return response.rules;
  },

  async setRetentionRule(data: {
    data_type: string;
    retention_days: number;
    action?: 'delete' | 'archive';
  }): Promise<RetentionRule> {
    const response = await apiClient.post<{ rule: RetentionRule }>(
      '/api/compliance/retention/rules',
      data
    );
    return response.rule;
  },

  async runCleanup(dryRun?: boolean): Promise<Record<string, unknown>> {
    let url = '/api/compliance/retention/cleanup';
    if (dryRun) {
      url += '?dry_run=true';
    }
    return apiClient.post<Record<string, unknown>>(url);
  },

  async getRetentionHistory(limit?: number): Promise<RetentionHistory[]> {
    const queryParams: Record<string, string> = limit ? { limit: String(limit) } : {};
    const response = await apiClient.get<{ history: RetentionHistory[] }>(
      '/api/compliance/retention/history',
      queryParams
    );
    return response.history;
  },

  async getStorageEstimates(): Promise<StorageEstimate[]> {
    return apiClient.get<StorageEstimate[]>('/api/compliance/retention/storage');
  },

  async getRetentionStatus(): Promise<Record<string, unknown>> {
    return apiClient.get<Record<string, unknown>>('/api/compliance/retention/status');
  },
};

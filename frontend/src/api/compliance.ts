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
  period: { start: string; end: string };
  total_events: number;
  hourly_distribution: Record<string, number>;
  login_hourly_distribution: Record<string, number>;
  daily_distribution: Record<string, number>;
  action_distribution: Record<string, number>;
  unique_users: number;
  top_users: [number, number][];
}

export interface AuditAnomaly {
  anomaly_type: string;
  description: string;
  severity: 'low' | 'medium' | 'high';
  affected_users: number[];
  occurrences: number;
  first_seen: string;
  last_seen: string;
  details?: Record<string, unknown>;
  status?: 'pending' | 'processed' | 'ignored';
  processed_at?: string | null;
}

export interface UserProfile {
  user_id: number;
  period_days: number;
  total_actions: number;
  actions_per_day: number;
  action_breakdown: Record<string, number>;
  peak_activity_hour: number;
  peak_activity_day: string;
  hourly_distribution: Record<string, number>;
  daily_distribution: Record<string, number>;
  first_activity: string;
  last_activity: string;
}

export interface SecurityScore {
  score: number;
  grade: string;
  anomaly_count: number;
  high_severity_count: number;
  medium_severity_count: number;
  low_severity_count: number;
  anomalies: { type: string; severity: string; description: string }[];
  recommendations: string[];
}

export interface AuditThresholds {
  audit_failed_login_threshold: number;
  audit_rapid_action_threshold: number;
  audit_off_hours_threshold: number;
  audit_role_change_threshold: number;
  audit_permission_change_threshold: number;
}

export interface RetentionRule {
  data_type: string;
  retention_days: number;
  action: 'delete' | 'archive' | 'anonymize';
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

export interface AppliedRule {
  data_type: string;
  action: 'delete' | 'archive' | 'anonymize';
  cutoff: string;
  records_affected: number;
}

export interface RetentionReport {
  timestamp: string;
  rules_applied: AppliedRule[];
  records_deleted: number;
  records_archived: number;
  records_anonymized: number;
  errors: string[];
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
    format?: 'json' | 'csv' | 'html' | 'excel';
    language?: 'en' | 'zh' | 'ja' | 'ko';
    tenant_id?: number;
    filters?: Record<string, unknown>;
  }): Promise<ComplianceReport | string> {
    const isCsv = data.format === 'csv';
    const isHtml = data.format === 'html';
    const isExcel = data.format === 'excel';

    if (isCsv) {
      // CSV format returns raw text
      return apiClient.post<string>('/api/compliance/reports', data, undefined, undefined, true);
    }

    if (isHtml) {
      // HTML format returns raw text
      return apiClient.post<string>('/api/compliance/reports', data, undefined, undefined, true);
    }

    if (isExcel) {
      // Excel format returns binary data - need special handling
      const response = await fetch('/api/compliance/reports', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error('Failed to generate report');
      }
      const blob = await response.blob();
      return blob;
    }

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

  async getSavedReport(
    reportId: string,
    format?: 'json' | 'csv' | 'html' | 'excel',
    language?: 'en' | 'zh' | 'ja' | 'ko'
  ): Promise<ComplianceReport | string | Blob> {
    const queryParams: Record<string, string> = {};
    if (format) queryParams.format = format;
    if (language) queryParams.language = language;

    const isCsv = format === 'csv';
    const isHtml = format === 'html';
    const isExcel = format === 'excel';

    if (isCsv) {
      // CSV format returns raw text
      return apiClient.get<string>(
        `/api/compliance/reports/${reportId}`,
        queryParams,
        undefined,
        true
      );
    }

    if (isHtml) {
      // HTML format returns raw text
      return apiClient.get<string>(
        `/api/compliance/reports/${reportId}`,
        queryParams,
        undefined,
        true
      );
    }

    if (isExcel) {
      // Excel format returns binary data
      const url = `/api/compliance/reports/${reportId}?${new URLSearchParams(queryParams).toString()}`;
      const response = await fetch(url, {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error('Failed to get report');
      }
      return response.blob();
    }

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

  async updateAnomalyStatus(
    anomalyType: string,
    affectedUsers: number[],
    status: 'processed' | 'ignored'
  ): Promise<{ success: boolean; status: string }> {
    return apiClient.post<{ success: boolean; status: string }>(
      '/api/compliance/audit/anomalies/status',
      { anomaly_type: anomalyType, affected_users: affectedUsers, status }
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

  // Audit Thresholds
  async getAuditThresholds(): Promise<AuditThresholds> {
    return apiClient.get<AuditThresholds>('/api/compliance/audit/thresholds');
  },

  async updateAuditThresholds(data: Partial<AuditThresholds>): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>('/api/compliance/audit/thresholds', data);
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
    action?: 'delete' | 'archive' | 'anonymize';
  }): Promise<RetentionRule> {
    const response = await apiClient.put<{ rule: RetentionRule }>(
      '/api/compliance/retention/rules',
      data
    );
    return response.rule;
  },

  async runCleanup(dryRun?: boolean): Promise<RetentionReport> {
    let url = '/api/compliance/retention/cleanup';
    if (dryRun) {
      url += '?dry_run=true';
    }
    return apiClient.post<RetentionReport>(url);
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

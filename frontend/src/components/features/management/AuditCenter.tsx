/**
 * AuditCenter Component - Combined Audit Log and Audit Analysis
 *
 * Features:
 * - Tab navigation between Log and Analysis views
 * - Audit Log: Log viewer with filters
 * - Audit Analysis: Security score, patterns, anomalies
 */

import React, { useState, useMemo, useEffect } from 'react';
import { cn } from '@/utils';
import { useAuditLogs, useUsers, useAuditActions } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
  PieChart,
  BarChart,
  PageRefreshControl,
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDate, formatDateTime, createMatcherConfig } from '@/utils';
import {
  complianceApi,
  type AuditPattern,
  type AuditAnomaly,
  type SecurityScore,
  type UserProfile,
} from '@/api';
import type { AuditLogFilters } from '@/api';
import { usePageRefresh } from '@/hooks';

type AuditLog = {
  id: number;
  timestamp: string;
  user_id: number | null;
  username: string | null;
  action: string;
  severity: string;
  resource_type: string;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  session_id: string | null;
  success: boolean | null;
  error_message: string | null;
};

const ITEMS_PER_PAGE = 20;
// Default audit-log filter window: the last 7 days. Used both on initial
// load and after Reset so the page always queries a bounded range instead
// of an unbounded one (issue #838). Mirrors the Messages page, which
// defaults startDate/endDate to today.
const DEFAULT_FILTER_RANGE_DAYS = 7;

const getDefaultAuditFilters = (): AuditLogFilters => ({
  start_date: formatDate(
    new Date(Date.now() - DEFAULT_FILTER_RANGE_DAYS * 24 * 60 * 60 * 1000),
    'iso'
  ),
  end_date: formatDate(new Date(), 'iso'),
});

const ACTION_COLORS: Record<string, BadgeVariant> = {
  login: 'primary',
  logout: 'secondary',
  create: 'success',
  update: 'warning',
  delete: 'danger',
  view: 'info',
};

// Map backend resource_type codes to i18n keys for human-readable display.
// Mirrors resourceTypeOptions; a new resource type must add an entry here, in
// resourceTypeOptions, and in i18n (en/zh/ja/ko). Codes reflect the backend's
// real set — never synthesize ids like "<type>_1".
const RESOURCE_TYPE_LABELS: Record<string, string> = {
  audit_logs: 'resourceAuditLogs',
  quota_alert: 'resourceQuotaAlert',
  content: 'resourceContent',
  content_filter: 'resourceContentFilter',
  filter_rule: 'resourceFilterRule',
  security_settings: 'resourceSecuritySettings',
  analytics_report: 'resourceAnalyticsReport',
  analytics: 'resourceAnalytics',
  ai_agent_settings: 'resourceAiAgentSettings',
  compliance_report: 'resourceComplianceReport',
  agent_token: 'resourceAgentToken',
  remote_machine: 'resourceRemoteMachine',
  data: 'resourceData',
  session: 'resourceSession',
  user: 'resourceUser',
};

type TabType = 'log' | 'analysis';

export const AuditCenter: React.FC = () => {
  const language = useLanguage();
  const { data: users, isLoading: usersLoading } = useUsers();
  const [activeTab, setActiveTab] = useState<TabType>('log');

  // --- Page Refresh Control ---
  const pageRefresh = usePageRefresh({
    page: '/manage/audit',
    refreshKey: createMatcherConfig(
      [
        ['admin', 'audit-logs'],
        ['admin', 'audit-thresholds'],
      ],
      'prefix'
    ),
    interval: 0, // No auto refresh - manual only for audit logs
    enabled: false,
  });

  // --- Audit Log State ---
  const [filters, setFilters] = useState<AuditLogFilters>(getDefaultAuditFilters);
  const [page, setPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  const { data, isLoading, isError, error, refetch } = useAuditLogs({
    ...filters,
    page,
    limit: ITEMS_PER_PAGE,
  });

  // --- Audit Analysis State ---
  const [patterns, setPatterns] = useState<AuditPattern | null>(null);
  const [anomalies, setAnomalies] = useState<AuditAnomaly[]>([]);
  const [anomalyPage, setAnomalyPage] = useState(1);
  const ANOMALY_PAGE_SIZE = 10;
  const [securityScore, setSecurityScore] = useState<SecurityScore | null>(null);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [days] = useState(30);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // --- Audit Actions Hook ---
  const { actions: auditActions, categories: auditCategories } = useAuditActions();

  const handleAnomalyStatusUpdate = async (
    anomalyType: string,
    affectedUsers: number[],
    status: 'processed' | 'ignored'
  ) => {
    try {
      await complianceApi.updateAnomalyStatus(anomalyType, affectedUsers, status);
      // Update local state
      setAnomalies((prev) =>
        prev.map((a) =>
          a.anomaly_type === anomalyType &&
          JSON.stringify([...(a.affected_users || [])].sort()) ===
            JSON.stringify([...affectedUsers].sort())
            ? { ...a, status, processed_at: new Date().toISOString() }
            : a
        )
      );
    } catch (err) {
      console.error('Failed to update anomaly status:', err);
    }
  };

  // Fetch analysis data
  const fetchAnalysisData = React.useCallback(async () => {
    setAnalysisLoading(true);
    setAnalysisError(null);
    try {
      const [patternsData, anomaliesData, scoreData] = await Promise.all([
        complianceApi.analyzePatterns(days),
        complianceApi.detectAnomalies(days),
        complianceApi.getSecurityScore(days),
      ]);
      setPatterns(patternsData);
      setAnomalies(anomaliesData.anomalies);
      setSecurityScore(scoreData);
    } catch (err) {
      const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch data';
      setAnalysisError(errorMessage);
    } finally {
      setAnalysisLoading(false);
    }
  }, [days]);

  // Fetch user profile when user is selected
  useEffect(() => {
    const fetchProfile = async () => {
      if (!selectedUserId) {
        setUserProfile(null);
        return;
      }
      try {
        const profile = await complianceApi.getUserProfile(selectedUserId, days);
        setUserProfile(profile);
      } catch (err) {
        console.error('Failed to fetch user profile:', err);
      }
    };
    fetchProfile();
  }, [selectedUserId, days]);

  // Load analysis data when tab is active
  useEffect(() => {
    if (activeTab === 'analysis') {
      fetchAnalysisData();
    }
  }, [activeTab, fetchAnalysisData]);

  // --- Audit Log Handlers ---
  // Grouped action options for optgroup display
  const groupedActionOptions = useMemo(() => {
    const groups: Array<{
      category: { key: string; label: string; i18n_key: string };
      actions: Array<{ value: string; label: string }>;
    }> = [];

    for (const category of auditCategories) {
      const categoryActions = auditActions
        .filter((a) => a.category === category.key)
        .map((a) => ({ value: a.value, label: t(a.i18n_key, language) }));

      groups.push({
        category: {
          key: category.key,
          label: t(category.i18n_key, language),
          i18n_key: category.i18n_key,
        },
        actions: categoryActions,
      });
    }

    return groups;
  }, [auditActions, auditCategories, language]);

  const resourceTypeOptions = useMemo(
    () => [
      { value: '', label: t('allResourceTypes', language) },
      { value: 'audit_logs', label: t('resourceAuditLogs', language) },
      { value: 'quota_alert', label: t('resourceQuotaAlert', language) },
      { value: 'content', label: t('resourceContent', language) },
      { value: 'content_filter', label: t('resourceContentFilter', language) },
      { value: 'filter_rule', label: t('resourceFilterRule', language) },
      { value: 'security_settings', label: t('resourceSecuritySettings', language) },
      { value: 'analytics_report', label: t('resourceAnalyticsReport', language) },
      { value: 'analytics', label: t('resourceAnalytics', language) },
      { value: 'ai_agent_settings', label: t('resourceAiAgentSettings', language) },
      { value: 'compliance_report', label: t('resourceComplianceReport', language) },
      { value: 'agent_token', label: t('resourceAgentToken', language) },
      { value: 'remote_machine', label: t('resourceRemoteMachine', language) },
      { value: 'data', label: t('resourceData', language) },
      { value: 'session', label: t('resourceSession', language) },
      { value: 'user', label: t('resourceUser', language) },
    ],
    [language]
  );

  const handleFilterChange = (key: keyof AuditLogFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters(getDefaultAuditFilters());
    setPage(1);
  };

  // Localized resource-type label, falling back to the raw code.
  const renderResourceType = (code: string | null | undefined): string => {
    if (!code) return '-';
    const key = RESOURCE_TYPE_LABELS[code];
    return key ? t(key, language) : code;
  };

  // details is normalized to an object by the backend; guard legacy rows.
  // Accepts the broadest shape (both the API AuditLog and the local type) so
  // it works on rows from useAuditLogs without an unsafe cast.
  const getDetails = (log: { details: Record<string, unknown> | null }): Record<string, unknown> =>
    log.details && typeof log.details === 'object' ? (log.details as Record<string, unknown>) : {};

  // Human-readable resource name (carried in details by the backend) for the
  // ID column tooltip; null when no name is available.
  const getResourceName = (log: { details: Record<string, unknown> | null }): string | null => {
    const name = getDetails(log).resource_name;
    return typeof name === 'string' && name ? name : null;
  };

  // --- Analysis Chart Data ---
  // Helper function to convert UTC hour to local hour
  const utcToLocalHour = (utcHour: number): number => {
    const offset = -new Date().getTimezoneOffset() / 60; // getTimezoneOffset returns minutes, negative for ahead of UTC
    return (utcHour + offset + 24) % 24;
  };

  const loginPatternData = useMemo(() => {
    if (!patterns?.login_hourly_distribution) return { labels: [], data: [] };
    const entries = Object.entries(patterns.login_hourly_distribution);
    // Convert UTC hours to local hours for display
    const localEntries = entries.map(([hour, count]) => [utcToLocalHour(parseInt(hour)), count]);
    // Sort by local hour
    const sortedEntries = localEntries.sort((a, b) => a[0] - b[0]);
    return {
      labels: sortedEntries.map(([hour]) => `${hour}:00`),
      data: sortedEntries.map(([, count]) => count),
    };
  }, [patterns]);

  const operationDistributionData = useMemo(() => {
    if (!patterns?.action_distribution) return { labels: [], data: [] };
    const entries = Object.entries(patterns.action_distribution);
    return {
      labels: entries.map(([op]) => op),
      data: entries.map(([, count]) => count),
    };
  }, [patterns]);

  const anomalyStats = useMemo(() => {
    const total = anomalies.length;
    const high = anomalies.filter((a) => a.severity === 'high').length;
    const medium = anomalies.filter((a) => a.severity === 'medium').length;
    const low = anomalies.filter((a) => a.severity === 'low').length;
    return { total, high, medium, low };
  }, [anomalies]);

  const getSeverityVariant = (severity: string) => {
    switch (severity) {
      case 'high':
        return 'danger';
      case 'medium':
        return 'warning';
      default:
        return 'info';
    }
  };

  // --- Render Audit Log Tab ---
  const renderLogTab = () => {
    if (isError) {
      return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
    }

    const logs = data?.logs ?? [];
    const total = data?.total ?? 0;
    const totalPages = Math.ceil(total / ITEMS_PER_PAGE);

    return (
      <>
        {/* Filters */}
        <Card className="mb-3">
          <div className="row g-3">
            <div className="col-md-3">
              <label className="form-label">{t('tableAction', language)}</label>
              <Select
                groupedOptions={[
                  { value: '', label: t('allActions', language) },
                  ...groupedActionOptions.map((group) => ({
                    label: group.category.label,
                    options: group.actions,
                  })),
                ]}
                value={filters.action ?? ''}
                onChange={(value) => handleFilterChange('action', value)}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('resourceType', language)}</label>
              <Select
                options={resourceTypeOptions}
                value={filters.resource_type ?? ''}
                onChange={(value) => handleFilterChange('resource_type', value)}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('startDate', language)}</label>
              <input
                type="date"
                className="form-control"
                value={filters.start_date ?? ''}
                onChange={(e) => handleFilterChange('start_date', e.target.value)}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('endDate', language)}</label>
              <input
                type="date"
                className="form-control"
                value={filters.end_date ?? ''}
                onChange={(e) => handleFilterChange('end_date', e.target.value)}
              />
            </div>
          </div>
          <div className="mt-3">
            <Button variant="secondary" size="sm" onClick={handleReset}>
              {t('reset', language)}
            </Button>
          </div>
        </Card>

        {/* Stats */}
        {total > 0 && (
          <div className="mb-3">
            <span className="text-muted">
              {t('total', language)}: {total.toLocaleString()} {t('records', language)}
            </span>
          </div>
        )}

        {/* Log List */}
        {isLoading ? (
          <Loading size="lg" text={t('loading', language)} />
        ) : logs.length === 0 ? (
          <EmptyState icon="bi-journal-text" title={t('noAuditLogs', language)} />
        ) : (
          <>
            <div className="table-responsive">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>{t('tableTimestamp', language)}</th>
                    <th>{t('tableUser', language)}</th>
                    <th>{t('tableAction', language)}</th>
                    <th>{t('resourceType', language)}</th>
                    <th>{t('resourceId', language)}</th>
                    <th>{t('tableIpAddress', language)}</th>
                    <th>{t('tableDetails', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id}>
                      <td>
                        <small>{formatDateTime(log.timestamp)}</small>
                      </td>
                      <td>{log.username ?? `User ${log.user_id}`}</td>
                      <td>
                        <Badge variant={ACTION_COLORS[log.action] ?? 'secondary'}>
                          {log.action}
                        </Badge>
                      </td>
                      <td>{renderResourceType(log.resource_type)}</td>
                      <td>
                        {log.resource_id ? (
                          <code title={getResourceName(log) ?? undefined}>{log.resource_id}</code>
                        ) : (
                          <span className="text-muted">-</span>
                        )}
                      </td>
                      <td>
                        <small className="text-muted">{log.ip_address ?? '-'}</small>
                      </td>
                      <td>
                        <button
                          className="btn btn-outline-primary btn-sm audit-detail-btn"
                          onClick={() => setSelectedLog(log as unknown as AuditLog)}
                        >
                          <i className="bi bi-eye me-1" />
                          {t('details', language)}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="d-flex justify-content-center mt-4">
                <nav>
                  <ul className="pagination">
                    <li className={`page-item ${page === 1 ? 'disabled' : ''}`}>
                      <button
                        className="page-link"
                        onClick={() => setPage(page - 1)}
                        disabled={page === 1}
                      >
                        {t('previous', language)}
                      </button>
                    </li>
                    {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                      const pageNum = i + 1;
                      return (
                        <li
                          key={pageNum}
                          className={`page-item ${page === pageNum ? 'active' : ''}`}
                        >
                          <button className="page-link" onClick={() => setPage(pageNum)}>
                            {pageNum}
                          </button>
                        </li>
                      );
                    })}
                    <li className={`page-item ${page === totalPages ? 'disabled' : ''}`}>
                      <button
                        className="page-link"
                        onClick={() => setPage(page + 1)}
                        disabled={page === totalPages}
                      >
                        {t('next', language)}
                      </button>
                    </li>
                  </ul>
                </nav>
              </div>
            )}

            {/* Detail Modal */}
            {selectedLog && (
              <div
                className="modal show d-block"
                tabIndex={-1}
                onClick={() => setSelectedLog(null)}
              >
                <div
                  className="modal-dialog modal-lg modal-dialog-scrollable"
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="modal-content">
                    <div className="modal-header">
                      <h5 className="modal-title">
                        <i className="bi bi-journal-text me-2" />
                        {t('details', language)} - {selectedLog.action}
                      </h5>
                      <button
                        type="button"
                        className="btn-close"
                        onClick={() => setSelectedLog(null)}
                      />
                    </div>
                    <div className="modal-body">
                      <table className="table table-borderless mb-0">
                        <tbody>
                          <tr>
                            <th style={{ width: '30%' }}>{t('tableTimestamp', language)}</th>
                            <td>{formatDateTime(selectedLog.timestamp)}</td>
                          </tr>
                          <tr>
                            <th>{t('tableUser', language)}</th>
                            <td>{selectedLog.username ?? `User ${selectedLog.user_id}`}</td>
                          </tr>
                          <tr>
                            <th>{t('tableAction', language)}</th>
                            <td>
                              <Badge variant={ACTION_COLORS[selectedLog.action] ?? 'secondary'}>
                                {selectedLog.action}
                              </Badge>
                            </td>
                          </tr>
                          <tr>
                            <th>{t('resourceType', language)}</th>
                            <td>{renderResourceType(selectedLog.resource_type)}</td>
                          </tr>
                          {selectedLog.resource_id && (
                            <tr>
                              <th>{t('resourceId', language)}</th>
                              <td>
                                <code>{selectedLog.resource_id}</code>
                              </td>
                            </tr>
                          )}
                          <tr>
                            <th>{t('tableIpAddress', language)}</th>
                            <td>{selectedLog.ip_address ?? '-'}</td>
                          </tr>
                          {selectedLog.session_id && (
                            <tr>
                              <th>Session ID</th>
                              <td>
                                <code>{selectedLog.session_id}</code>
                              </td>
                            </tr>
                          )}
                          <tr>
                            <th>{t('status', language) ?? 'Status'}</th>
                            <td>
                              <Badge variant={selectedLog.success !== false ? 'success' : 'danger'}>
                                {selectedLog.success !== false ? 'Success' : 'Failed'}
                              </Badge>
                            </td>
                          </tr>
                          {selectedLog.error_message && (
                            <tr>
                              <th>Error</th>
                              <td className="text-danger">{selectedLog.error_message}</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                      {selectedLog.details &&
                        typeof selectedLog.details === 'object' &&
                        Object.keys(selectedLog.details).length > 0 && (
                          <div className="mt-3">
                            <h6>{t('tableDetails', language)}</h6>
                            <pre
                              className="bg-light p-3 rounded"
                              style={{
                                maxHeight: '300px',
                                overflow: 'auto',
                                whiteSpace: 'pre-wrap',
                                wordBreak: 'break-word',
                              }}
                            >
                              {JSON.stringify(selectedLog.details, null, 2)}
                            </pre>
                          </div>
                        )}
                    </div>
                    <div className="modal-footer">
                      <Button variant="secondary" onClick={() => setSelectedLog(null)}>
                        {t('close', language) ?? 'Close'}
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            )}
            {selectedLog && <div className="modal-backdrop show" />}
          </>
        )}
      </>
    );
  };

  // --- Render Analysis Tab ---
  const renderAnalysisTab = () => {
    if (analysisLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (analysisError) {
      return <Error message={analysisError} onRetry={fetchAnalysisData} />;
    }

    return (
      <>
        {/* Security Score */}
        {securityScore && (
          <Card title={t('securityScore', language)} className="mb-4">
            <div className="row">
              <div className="col-md-4 text-center">
                <div
                  className={cn(
                    'security-score-circle',
                    securityScore.score >= 80
                      ? 'text-success'
                      : securityScore.score >= 60
                        ? 'text-warning'
                        : 'text-danger'
                  )}
                >
                  <div
                    className="display-1 fw-bold"
                    style={{
                      fontSize: '4rem',
                      color:
                        securityScore.score >= 80
                          ? '#198754'
                          : securityScore.score >= 60
                            ? '#ffc107'
                            : '#dc3545',
                    }}
                  >
                    {securityScore.score}
                  </div>
                  <div className="text-muted">
                    {t('overallScore', language)} ({securityScore.grade})
                  </div>
                </div>
              </div>
              <div className="col-md-8">
                <h6>{t('categoryScores', language) ?? 'Severity Breakdown'}</h6>
                {[
                  {
                    label: 'High Severity',
                    count: securityScore.high_severity_count,
                    max: 10,
                    variant: 'danger',
                  },
                  {
                    label: 'Medium Severity',
                    count: securityScore.medium_severity_count,
                    max: 10,
                    variant: 'warning',
                  },
                  {
                    label: 'Low Severity',
                    count: securityScore.low_severity_count,
                    max: 10,
                    variant: 'info',
                  },
                ].map((item) => {
                  const scorePercent = Math.max(0, 100 - (item.count / item.max) * 100);
                  return (
                    <div key={item.label} className="mb-2">
                      <div className="d-flex justify-content-between">
                        <span>{item.label}</span>
                        <span className={item.count > 0 ? `text-${item.variant}` : 'text-success'}>
                          {item.count} {t('totalAnomalies', language) ?? 'anomalies'}
                        </span>
                      </div>
                      <div className="progress" style={{ height: '8px' }}>
                        <div
                          className={cn(
                            'progress-bar',
                            `bg-${scorePercent >= 80 ? 'success' : scorePercent >= 60 ? 'warning' : 'danger'}`
                          )}
                          style={{ width: `${scorePercent}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
                <div className="mt-2">
                  <small className="text-muted">
                    {t('totalAnomalies', language) ?? 'Total'}: {securityScore.anomaly_count}
                  </small>
                </div>
              </div>
            </div>
            {securityScore.recommendations && securityScore.recommendations.length > 0 && (
              <div className="mt-3">
                <h6>{t('recommendations', language)}</h6>
                <ul className="list-unstyled">
                  {securityScore.recommendations.map((rec, index) => (
                    <li key={index} className="mb-1">
                      <i className="bi bi-lightbulb text-warning me-2" />
                      {rec}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        )}

        {/* Pattern Analysis */}
        {patterns && (
          <div className="row mb-4">
            <div className="col-md-6">
              <Card title={t('loginPatterns', language)}>
                {loginPatternData.labels.length > 0 ? (
                  <BarChart
                    labels={loginPatternData.labels}
                    datasets={[{ label: t('logins', language), data: loginPatternData.data }]}
                    height={200}
                  />
                ) : (
                  <EmptyState icon="bi-graph-up" title={t('noData', language)} />
                )}
              </Card>
            </div>
            <div className="col-md-6">
              <Card title={t('operationDistribution', language)}>
                {operationDistributionData.labels.length > 0 ? (
                  <PieChart
                    labels={operationDistributionData.labels}
                    data={operationDistributionData.data}
                    height={200}
                  />
                ) : (
                  <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>
        )}

        {/* Anomaly Detection */}
        <Card title={t('anomalyDetection', language)} className="mb-4">
          <div className="row g-3 mb-3">
            <div className="col-md-3">
              <StatCard
                label={t('totalAnomalies', language)}
                value={anomalyStats.total.toString()}
                icon={<i className="bi bi-exclamation-triangle fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('highSeverity', language)}
                value={anomalyStats.high.toString()}
                icon={<i className="bi bi-x-circle fs-4" />}
                variant="danger"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('mediumSeverity', language)}
                value={anomalyStats.medium.toString()}
                icon={<i className="bi bi-exclamation-circle fs-4" />}
                variant="warning"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('lowSeverity', language)}
                value={anomalyStats.low.toString()}
                icon={<i className="bi bi-info-circle fs-4" />}
                variant="info"
              />
            </div>
          </div>

          {anomalies.length > 0 ? (
            <div>
              <div className="table-responsive">
                <table className="table table-hover">
                  <thead>
                    <tr>
                      <th>{t('type', language)}</th>
                      <th>{t('description', language)}</th>
                      <th>{t('affectedUsers', language)}</th>
                      <th>{t('time', language)}</th>
                      <th>{t('severity', language)}</th>
                      <th>{t('status', language)}</th>
                      <th>{t('actions', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {anomalies
                      .slice((anomalyPage - 1) * ANOMALY_PAGE_SIZE, anomalyPage * ANOMALY_PAGE_SIZE)
                      .map((anomaly, index) => (
                        <tr
                          key={`${anomaly.anomaly_type}-${index}`}
                          className={anomaly.status === 'processed' ? 'opacity-50' : ''}
                        >
                          <td>
                            <span className="badge bg-secondary">{anomaly.anomaly_type}</span>
                          </td>
                          <td>{anomaly.description}</td>
                          <td>
                            {anomaly.affected_users?.length > 0
                              ? anomaly.affected_users
                                  .map(
                                    (uid) =>
                                      users?.find((u) => u.id === uid)?.username ?? `ID:${uid}`
                                  )
                                  .join(', ')
                              : '-'}
                          </td>
                          <td>
                            <small className="text-muted">
                              {formatDateTime(anomaly.first_seen)}
                            </small>
                          </td>
                          <td>
                            <span
                              className={cn('badge', `bg-${getSeverityVariant(anomaly.severity)}`)}
                            >
                              {anomaly.severity}
                            </span>
                          </td>
                          <td>
                            <span
                              className={cn('badge', {
                                'bg-success': anomaly.status === 'processed',
                                'bg-warning text-dark': anomaly.status === 'ignored',
                                'bg-secondary': !anomaly.status || anomaly.status === 'pending',
                              })}
                            >
                              {t(anomaly.status ?? 'pending', language)}
                            </span>
                          </td>
                          <td>
                            {(!anomaly.status || anomaly.status === 'pending') && (
                              <div className="btn-group btn-group-sm">
                                <button
                                  className="btn btn-outline-success btn-sm"
                                  title={t('markProcessed', language)}
                                  onClick={() =>
                                    handleAnomalyStatusUpdate(
                                      anomaly.anomaly_type,
                                      anomaly.affected_users || [],
                                      'processed'
                                    )
                                  }
                                >
                                  <i className="bi bi-check-lg" />
                                </button>
                                <button
                                  className="btn btn-outline-warning btn-sm"
                                  title={t('ignoreAnomaly', language)}
                                  onClick={() =>
                                    handleAnomalyStatusUpdate(
                                      anomaly.anomaly_type,
                                      anomaly.affected_users || [],
                                      'ignored'
                                    )
                                  }
                                >
                                  <i className="bi bi-eye-slash" />
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <div className="d-flex justify-content-between align-items-center mt-2">
                <small className="text-muted">
                  {t('total', language)}: {anomalies.length} {t('anomalies', language)}
                </small>
                {Math.ceil(anomalies.length / ANOMALY_PAGE_SIZE) > 1 && (
                  <nav>
                    <ul className="pagination pagination-sm mb-0">
                      <li className={`page-item ${anomalyPage === 1 ? 'disabled' : ''}`}>
                        <button
                          className="page-link"
                          onClick={() => setAnomalyPage(anomalyPage - 1)}
                          disabled={anomalyPage === 1}
                        >
                          {t('previous', language)}
                        </button>
                      </li>
                      {(() => {
                        const totalPages = Math.ceil(anomalies.length / ANOMALY_PAGE_SIZE);
                        const startPage = Math.max(1, anomalyPage - 2);
                        const endPage = Math.min(totalPages, startPage + 4);
                        return Array.from({ length: endPage - startPage + 1 }, (_, i) => {
                          const pageNum = startPage + i;
                          return (
                            <li
                              key={pageNum}
                              className={`page-item ${anomalyPage === pageNum ? 'active' : ''}`}
                            >
                              <button className="page-link" onClick={() => setAnomalyPage(pageNum)}>
                                {pageNum}
                              </button>
                            </li>
                          );
                        });
                      })()}
                      <li
                        className={`page-item ${
                          anomalyPage === Math.ceil(anomalies.length / ANOMALY_PAGE_SIZE)
                            ? 'disabled'
                            : ''
                        }`}
                      >
                        <button
                          className="page-link"
                          onClick={() => setAnomalyPage(anomalyPage + 1)}
                          disabled={anomalyPage === Math.ceil(anomalies.length / ANOMALY_PAGE_SIZE)}
                        >
                          {t('next', language)}
                        </button>
                      </li>
                    </ul>
                  </nav>
                )}
              </div>
            </div>
          ) : (
            <EmptyState icon="bi-check-circle" title={t('noAnomaliesDetected', language)} />
          )}
        </Card>

        {/* User Behavior Profile */}
        <Card title={t('userBehaviorProfile', language)}>
          <div className="row g-3 mb-3">
            <div className="col-md-4">
              <label className="form-label">{t('selectUser', language)}</label>
              <select
                className="form-select"
                value={selectedUserId ?? ''}
                onChange={(e) =>
                  setSelectedUserId(e.target.value ? parseInt(e.target.value) : null)
                }
                disabled={usersLoading}
              >
                <option value="">-- {t('selectUser', language)} --</option>
                {users?.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {userProfile ? (
            <div>
              <div className="row mb-3">
                <div className="col-md-3">
                  <StatCard
                    label={t('total', language) ?? 'Total Actions'}
                    value={String(userProfile.total_actions ?? 0)}
                    variant="primary"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('actionsPerDay', language)}
                    value={(userProfile.actions_per_day ?? 0).toFixed(1)}
                    variant="info"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('peakHour', language)}
                    value={`${userProfile.peak_activity_hour ?? '-'}:00`}
                    variant="warning"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('peakDay', language)}
                    value={userProfile.peak_activity_day ?? '-'}
                    variant="success"
                  />
                </div>
              </div>
              <div className="row">
                <div className="col-md-6">
                  <h6>{t('activeHours', language)}</h6>
                  {userProfile.hourly_distribution &&
                  Object.keys(userProfile.hourly_distribution).length > 0 ? (
                    <BarChart
                      labels={Object.keys(userProfile.hourly_distribution).map((h) => `${h}:00`)}
                      datasets={[
                        {
                          label: t('activity', language),
                          data: Object.values(userProfile.hourly_distribution),
                        },
                      ]}
                      height={150}
                    />
                  ) : (
                    <p className="text-muted">{t('noData', language)}</p>
                  )}
                </div>
                <div className="col-md-6">
                  <h6>{t('commonOperations', language)}</h6>
                  {userProfile.action_breakdown &&
                  Object.keys(userProfile.action_breakdown).length > 0 ? (
                    <ul className="list-group">
                      {Object.entries(userProfile.action_breakdown)
                        .sort(([, a], [, b]) => b - a)
                        .slice(0, 10)
                        .map(([op, count]) => (
                          <li key={op} className="list-group-item d-flex justify-content-between">
                            <span>{op}</span>
                            <Badge variant="secondary">{count}</Badge>
                          </li>
                        ))}
                    </ul>
                  ) : (
                    <p className="text-muted">{t('noData', language)}</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <EmptyState icon="bi-person" title={t('selectUserToViewProfile', language)} />
          )}
        </Card>
      </>
    );
  };

  const handleExportReport = () => {
    const rows: string[][] = [];

    // Security Score
    if (securityScore) {
      rows.push(['Section', 'Key', 'Value']);
      rows.push(['Security Score', 'Score', String(securityScore.score)]);
      rows.push(['Security Score', 'Grade', securityScore.grade]);
      rows.push(['Security Score', 'Anomaly Count', String(securityScore.anomaly_count)]);
      rows.push(['Security Score', 'High Severity', String(securityScore.high_severity_count)]);
      rows.push(['Security Score', 'Medium Severity', String(securityScore.medium_severity_count)]);
      rows.push(['Security Score', 'Low Severity', String(securityScore.low_severity_count)]);
    }

    // Anomalies
    if (anomalies.length > 0) {
      rows.push([]);
      rows.push(['Anomaly Type', 'Description', 'Severity', 'Affected Users', 'First Seen']);
      anomalies.forEach((a) => {
        const userNames =
          a.affected_users
            ?.map((uid) => users?.find((u) => u.id === uid)?.username ?? `ID:${uid}`)
            .join('; ') ?? '';
        rows.push([a.anomaly_type, a.description, a.severity, userNames, a.first_seen]);
      });
    }

    // Patterns
    if (patterns) {
      rows.push([]);
      rows.push(['Pattern', 'Value']);
      rows.push(['Total Events', String(patterns.total_events)]);
      rows.push(['Unique Users', String(patterns.unique_users)]);
      if (patterns.action_distribution) {
        Object.entries(patterns.action_distribution).forEach(([action, count]) => {
          rows.push([`Action: ${action}`, String(count)]);
        });
      }
      if (patterns.hourly_distribution) {
        Object.entries(patterns.hourly_distribution).forEach(([hour, count]) => {
          rows.push([`Hour ${hour}:00`, String(count)]);
        });
      }
    }

    // User Profile
    if (userProfile) {
      rows.push([]);
      rows.push(['User Profile', 'Value']);
      rows.push(['Total Actions', String(userProfile.total_actions ?? 0)]);
      rows.push(['Actions/Day', (userProfile.actions_per_day ?? 0).toFixed(1)]);
      rows.push(['Peak Hour', `${userProfile.peak_activity_hour ?? '-'}:00`]);
      rows.push(['Peak Day', userProfile.peak_activity_day ?? '-']);
    }

    const csv = rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-analysis-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="audit-center">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('auditCenter', language)}</h2>
        <div className="d-flex gap-2">
          {/* Page Refresh Control */}
          <PageRefreshControl refresh={pageRefresh} compact={true} showLastRefreshTime={true} />
          {activeTab === 'analysis' && (
            <Button variant="outline-success" size="sm" onClick={handleExportReport}>
              <i className="bi bi-download me-1" />
              {t('exportReport', language)}
            </Button>
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'log' && 'active')}
            onClick={() => setActiveTab('log')}
          >
            <i className="bi bi-journal-text me-1" />
            {t('auditLog', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'analysis' && 'active')}
            onClick={() => setActiveTab('analysis')}
          >
            <i className="bi bi-search me-1" />
            {t('auditAnalysis', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'log' ? renderLogTab() : renderAnalysisTab()}
    </div>
  );
};

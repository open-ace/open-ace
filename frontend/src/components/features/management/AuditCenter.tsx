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
import { useAuditLogs } from '@/hooks';
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
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDateTime } from '@/utils';
import {
  complianceApi,
  type AuditPattern,
  type AuditAnomaly,
  type SecurityScore,
  type UserProfile,
} from '@/api';
import type { AuditLogFilters } from '@/api';

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

const ACTION_COLORS: Record<string, BadgeVariant> = {
  login: 'primary',
  logout: 'secondary',
  create: 'success',
  update: 'warning',
  delete: 'danger',
  view: 'info',
};

type TabType = 'log' | 'analysis';

export const AuditCenter: React.FC = () => {
  const language = useLanguage();
  const [activeTab, setActiveTab] = useState<TabType>('log');

  // --- Audit Log State ---
  const [filters, setFilters] = useState<AuditLogFilters>({});
  const [page, setPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);

  const { data, isLoading, isFetching, isError, error, refetch } = useAuditLogs({
    ...filters,
    page,
    limit: ITEMS_PER_PAGE,
  });

  // --- Audit Analysis State ---
  const [patterns, setPatterns] = useState<AuditPattern | null>(null);
  const [anomalies, setAnomalies] = useState<AuditAnomaly[]>([]);
  const [securityScore, setSecurityScore] = useState<SecurityScore | null>(null);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [days] = useState(30);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

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
  const actionOptions = useMemo(
    () => [
      { value: '', label: t('allActions', language) },
      { value: 'login', label: 'Login' },
      { value: 'logout', label: 'Logout' },
      { value: 'create', label: 'Create' },
      { value: 'update', label: 'Update' },
      { value: 'delete', label: 'Delete' },
      { value: 'view', label: 'View' },
    ],
    [language]
  );

  const resourceTypeOptions = useMemo(
    () => [
      { value: '', label: t('allResourceTypes', language) },
      { value: 'user', label: 'User' },
      { value: 'session', label: 'Session' },
      { value: 'message', label: 'Message' },
      { value: 'quota', label: 'Quota' },
      { value: 'settings', label: 'Settings' },
    ],
    [language]
  );

  const handleFilterChange = (key: keyof AuditLogFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
  };

  // --- Analysis Chart Data ---
  const loginPatternData = useMemo(() => {
    if (!patterns?.hourly_distribution) return { labels: [], data: [] };
    const entries = Object.entries(patterns.hourly_distribution);
    return {
      labels: entries.map(([hour]) => `${hour}:00`),
      data: entries.map(([, count]) => count),
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
                options={actionOptions}
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
                      <td>{log.resource_type}</td>
                      <td>
                        <code>{log.resource_id}</code>
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
                            <td>{selectedLog.resource_type}</td>
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
            <div className="table-responsive">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>{t('type', language)}</th>
                    <th>{t('description', language)}</th>
                    <th>{t('time', language)}</th>
                    <th>{t('severity', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  {anomalies.slice(0, 10).map((anomaly, index) => (
                    <tr key={index}>
                      <td>
                        <span className="badge bg-secondary">{anomaly.anomaly_type}</span>
                      </td>
                      <td>{anomaly.description}</td>
                      <td>
                        <small className="text-muted">{formatDateTime(anomaly.first_seen)}</small>
                      </td>
                      <td>
                        <span className={cn('badge', `bg-${getSeverityVariant(anomaly.severity)}`)}>
                          {anomaly.severity}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
              <input
                type="number"
                className="form-control"
                placeholder={t('enterUserId', language)}
                value={selectedUserId ?? ''}
                onChange={(e) => setSelectedUserId(parseInt(e.target.value) || null)}
              />
            </div>
          </div>

          {userProfile ? (
            <div>
              <div className="row mb-3">
                <div className="col-md-3">
                  <StatCard
                    label={t('total', language) ?? 'Total Actions'}
                    value={String(userProfile.total_actions)}
                    variant="primary"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('actionsPerDay', language) ?? 'Actions/Day'}
                    value={userProfile.actions_per_day.toFixed(1)}
                    variant="info"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('peakHour', language) ?? 'Peak Hour'}
                    value={`${userProfile.peak_activity_hour}:00`}
                    variant="warning"
                  />
                </div>
                <div className="col-md-3">
                  <StatCard
                    label={t('peakDay', language) ?? 'Peak Day'}
                    value={userProfile.peak_activity_day}
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

  return (
    <div className="audit-center">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('auditCenter', language)}</h2>
        <Button
          variant="primary"
          size="sm"
          onClick={() => (activeTab === 'log' ? refetch() : fetchAnalysisData())}
          loading={isFetching}
        >
          {isFetching ? null : <i className="bi bi-arrow-clockwise me-1" />}
          {t('refresh', language)}
        </Button>
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

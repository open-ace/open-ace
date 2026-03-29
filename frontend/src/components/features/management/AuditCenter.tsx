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
    if (!patterns?.login_patterns) return { labels: [], data: [] };
    const entries = Object.entries(patterns.login_patterns);
    return {
      labels: entries.map(([hour]) => `${hour}:00`),
      data: entries.map(([, count]) => count),
    };
  }, [patterns]);

  const operationDistributionData = useMemo(() => {
    if (!patterns?.operation_distribution) return { labels: [], data: [] };
    const entries = Object.entries(patterns.operation_distribution);
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
                        {log.details && Object.keys(log.details).length > 0 && (
                          <button
                            className="btn btn-link btn-sm p-0"
                            onClick={() => window.alert(JSON.stringify(log.details, null, 2))}
                          >
                            <i className="bi bi-eye" />
                          </button>
                        )}
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
                    securityScore.overall_score >= 80
                      ? 'text-success'
                      : securityScore.overall_score >= 60
                        ? 'text-warning'
                        : 'text-danger'
                  )}
                >
                  <div
                    className="display-1 fw-bold"
                    style={{
                      fontSize: '4rem',
                      color:
                        securityScore.overall_score >= 80
                          ? '#198754'
                          : securityScore.overall_score >= 60
                            ? '#ffc107'
                            : '#dc3545',
                    }}
                  >
                    {securityScore.overall_score}
                  </div>
                  <div className="text-muted">{t('overallScore', language)}</div>
                </div>
              </div>
              <div className="col-md-8">
                <h6>{t('categoryScores', language)}</h6>
                {Object.entries(securityScore.categories || {}).map(([category, data]) => (
                  <div key={category} className="mb-2">
                    <div className="d-flex justify-content-between">
                      <span>{category}</span>
                      <span
                        className={cn(
                          (data as { status: string }).status === 'good'
                            ? 'text-success'
                            : (data as { status: string }).status === 'warning'
                              ? 'text-warning'
                              : 'text-danger'
                        )}
                      >
                        {(data as { score: number }).score}%
                      </span>
                    </div>
                    <div className="progress" style={{ height: '8px' }}>
                      <div
                        className={cn(
                          'progress-bar',
                          (data as { status: string }).status === 'good'
                            ? 'bg-success'
                            : (data as { status: string }).status === 'warning'
                              ? 'bg-warning'
                              : 'bg-danger'
                        )}
                        style={{ width: `${(data as { score: number }).score}%` }}
                      />
                    </div>
                  </div>
                ))}
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
                        <span className="badge bg-secondary">{anomaly.type}</span>
                      </td>
                      <td>{anomaly.description}</td>
                      <td>
                        <small className="text-muted">{formatDateTime(anomaly.timestamp)}</small>
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
              <div className="row">
                <div className="col-md-6">
                  <h6>{t('activeHours', language)}</h6>
                  {Object.keys(userProfile.active_hours || {}).length > 0 ? (
                    <BarChart
                      labels={Object.keys(userProfile.active_hours)}
                      datasets={[
                        {
                          label: t('activity', language),
                          data: Object.values(userProfile.active_hours),
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
                  {userProfile.common_operations && userProfile.common_operations.length > 0 ? (
                    <ul className="list-group">
                      {userProfile.common_operations.map((op, index) => (
                        <li key={index} className="list-group-item">
                          {op}
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
        <h5>{t('auditCenter', language)}</h5>
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

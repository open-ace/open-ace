/**
 * AuditAnalysis Component - Audit analysis page
 *
 * Features:
 * - Security score display
 * - Pattern analysis charts
 * - Anomaly detection
 * - User behavior profile
 */

import React, { useState, useEffect, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error,
  EmptyState,
  PieChart,
  BarChart,
} from '@/components/common';
import {
  complianceApi,
  type AuditPattern,
  type AuditAnomaly,
  type SecurityScore,
  type UserProfile,
} from '@/api';
import { formatDateTime } from '@/utils';

export const AuditAnalysis: React.FC = () => {
  const language = useLanguage();
  const [patterns, setPatterns] = useState<AuditPattern | null>(null);
  const [anomalies, setAnomalies] = useState<AuditAnomaly[]>([]);
  const [securityScore, setSecurityScore] = useState<SecurityScore | null>(null);
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [days, setDays] = useState(30);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // Fetch data
  const fetchData = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
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
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [days]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

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

  // Prepare chart data
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

  // Anomaly statistics
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

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchData} />;
  }

  return (
    <div className="audit-analysis">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('auditAnalysis', language)}</h2>
        <div className="d-flex gap-2 align-items-center">
          <select
            className="form-select form-select-sm"
            value={days}
            onChange={(e) => setDays(parseInt(e.target.value))}
            style={{ width: 'auto' }}
          >
            <option value={7}>7 {t('days', language)}</option>
            <option value={30}>30 {t('days', language)}</option>
            <option value={90}>90 {t('days', language)}</option>
          </select>
          <Button variant="primary" size="sm" onClick={fetchData}>
            <i className="bi bi-arrow-clockwise" />
          </Button>
        </div>
      </div>

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
                        {item.count}
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
                <small className="text-muted">Total anomalies: {securityScore.anomaly_count}</small>
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
                  label="Total Actions"
                  value={String(userProfile.total_actions)}
                  variant="primary"
                />
              </div>
              <div className="col-md-3">
                <StatCard
                  label="Actions/Day"
                  value={userProfile.actions_per_day.toFixed(1)}
                  variant="info"
                />
              </div>
              <div className="col-md-3">
                <StatCard
                  label="Peak Hour"
                  value={`${userProfile.peak_activity_hour}:00`}
                  variant="warning"
                />
              </div>
              <div className="col-md-3">
                <StatCard
                  label="Peak Day"
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
                          <span className="badge bg-secondary">{count}</span>
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
    </div>
  );
};

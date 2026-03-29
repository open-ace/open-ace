/**
 * AnomalyDetection Component - Anomaly detection and optimization recommendations
 *
 * Features:
 * - Anomaly statistics cards
 * - Anomaly trend chart
 * - Anomaly distribution pie chart
 * - Anomaly list table
 * - Optimization recommendations
 *
 * Performance optimized: Uses backend API for anomaly detection instead of client-side calculation.
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Select,
  Loading,
  Error,
  EmptyState,
  LineChart,
  PieChart,
} from '@/components/common';
import { formatTokens } from '@/utils';
import { useAnomalyDetection, useAnomalyTrend, useRecommendations, useHosts } from '@/hooks';

// Anomaly type options
const anomalyTypeOptions = [
  { value: '', label: 'All Types' },
  { value: 'spike', label: 'Usage Spike' },
  { value: 'drop', label: 'Usage Drop' },
];

// Severity options
const severityOptions = [
  { value: '', label: 'All Severities' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

export const AnomalyDetection: React.FC = () => {
  const language = useLanguage();
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [anomalyTypeFilter, setAnomalyTypeFilter] = useState<string>('');
  const [severityFilter, setSeverityFilter] = useState<string>('');

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = hostsData ?? [];

  // Quick date range options
  const [quickRange, setQuickRange] = useState<'7' | '30' | '90' | 'all'>('30');

  // Date range based on quick range selection
  const dateRange = useMemo(() => {
    const end = new Date();
    let start: Date;

    switch (quickRange) {
      case '7':
        start = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
        break;
      case '30':
        start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
        break;
      case '90':
        start = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000);
        break;
      case 'all':
      default:
        start = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
        break;
    }

    return {
      start: start.toISOString().split('T')[0],
      end: end.toISOString().split('T')[0],
    };
  }, [quickRange]);

  const [startDate, setStartDate] = useState(dateRange.start);
  const [endDate, setEndDate] = useState(dateRange.end);

  // Update date range when quick range changes
  React.useEffect(() => {
    setStartDate(dateRange.start);
    setEndDate(dateRange.end);
  }, [dateRange]);

  // Fetch anomaly data from backend API (optimized)
  const {
    data: anomalyData,
    isLoading: anomalyLoading,
    isError: anomalyError,
    error: anomalyErrorMsg,
  } = useAnomalyDetection(
    startDate,
    endDate,
    selectedHost || undefined,
    anomalyTypeFilter || undefined,
    severityFilter || undefined
  );

  // Fetch anomaly trend from backend API
  const { data: trendData } = useAnomalyTrend(startDate, endDate, selectedHost || undefined);

  // Fetch recommendations
  const { data: recommendations } = useRecommendations(selectedHost || undefined);

  // Host options
  const hostOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllHosts', language) },
      ...hosts.map((host) => ({ value: host, label: host })),
    ],
    [hosts, language]
  );

  const isLoading = anomalyLoading;

  // Get anomalies and summary from API response
  const anomalies = anomalyData?.anomalies ?? [];
  const stats = anomalyData?.summary ?? { total: 0, high: 0, medium: 0, low: 0 };

  // Anomaly distribution data for pie chart
  const anomalyDistributionData = useMemo(() => {
    const spikes = anomalies.filter((a) => a.type === 'spike').length;
    const drops = anomalies.filter((a) => a.type === 'drop').length;
    return [spikes, drops];
  }, [anomalies]);

  // Anomaly trend data from API
  const anomalyTrendData = useMemo(() => {
    const trend = trendData?.trend ?? [];
    return {
      labels: trend.map((t) => t.date),
      data: trend.map((t) => t.count),
    };
  }, [trendData]);

  return (
    <div className="anomaly-detection">
      {/* Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('anomalyDetection', language)}</h2>
      </div>

      {/* Filters */}
      <Card className="mb-4">
        <div className="row g-3">
          {/* Quick Date Range Buttons */}
          <div className="col-12">
            <label className="form-label">{t('quickDateRange', language)}</label>
            <div className="btn-group" role="group">
              <button
                type="button"
                className={cn('btn', quickRange === '7' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => setQuickRange('7')}
              >
                7 {t('days', language)}
              </button>
              <button
                type="button"
                className={cn('btn', quickRange === '30' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => setQuickRange('30')}
              >
                30 {t('days', language)}
              </button>
              <button
                type="button"
                className={cn('btn', quickRange === '90' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => setQuickRange('90')}
              >
                90 {t('days', language)}
              </button>
              <button
                type="button"
                className={cn('btn', quickRange === 'all' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => setQuickRange('all')}
              >
                {t('all', language)}
              </button>
            </div>
          </div>
          {/* Date Range */}
          <div className="col-md-3">
            <label className="form-label">{t('startDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={startDate}
              onChange={(e) => {
                setStartDate(e.target.value);
                setQuickRange('all');
              }}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={endDate}
              onChange={(e) => {
                setEndDate(e.target.value);
                setQuickRange('all');
              }}
            />
          </div>
          {/* Anomaly Type Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('anomalyType', language)}</label>
            <Select
              options={anomalyTypeOptions}
              value={anomalyTypeFilter}
              onChange={setAnomalyTypeFilter}
            />
          </div>
          {/* Host Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('tableHost', language)}</label>
            <Select options={hostOptions} value={selectedHost} onChange={setSelectedHost} />
          </div>
          {/* Severity Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('severity', language)}</label>
            <Select options={severityOptions} value={severityFilter} onChange={setSeverityFilter} />
          </div>
        </div>
      </Card>

      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : anomalyError ? (
        <Error message={anomalyErrorMsg?.message || t('error', language)} />
      ) : (
        <>
          {/* Statistics Cards */}
          <div className="row g-3 mb-4">
            <div className="col-md-3">
              <StatCard
                label={t('totalAnomalies', language)}
                value={stats.total.toString()}
                icon={<i className="bi bi-exclamation-triangle fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('highSeverity', language)}
                value={stats.high.toString()}
                icon={<i className="bi bi-x-circle fs-4" />}
                variant="danger"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('mediumSeverity', language)}
                value={stats.medium.toString()}
                icon={<i className="bi bi-exclamation-circle fs-4" />}
                variant="warning"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('lowSeverity', language)}
                value={stats.low.toString()}
                icon={<i className="bi bi-info-circle fs-4" />}
                variant="info"
              />
            </div>
          </div>

          {/* Charts Row */}
          <div className="row mb-4">
            <div className="col-md-8">
              <Card title={t('anomalyTrend', language)}>
                {anomalyTrendData.labels.length > 0 ? (
                  <LineChart
                    labels={anomalyTrendData.labels}
                    datasets={[
                      {
                        label: t('anomalies', language),
                        data: anomalyTrendData.data,
                        borderColor: 'rgba(255, 99, 132, 1)',
                        backgroundColor: 'rgba(255, 99, 132, 0.2)',
                      },
                    ]}
                    height={300}
                  />
                ) : (
                  <EmptyState icon="bi-graph-up" title={t('noAnomaliesDetected', language)} />
                )}
              </Card>
            </div>
            <div className="col-md-4">
              <Card title={t('anomalyDistribution', language)}>
                {anomalyDistributionData[0] + anomalyDistributionData[1] > 0 ? (
                  <PieChart
                    labels={[t('usageSpike', language), t('usageDrop', language)]}
                    data={anomalyDistributionData}
                    backgroundColor={['rgba(255, 99, 132, 0.8)', 'rgba(75, 192, 192, 0.8)']}
                    height={300}
                  />
                ) : (
                  <EmptyState icon="bi-pie-chart" title={t('noAnomaliesDetected', language)} />
                )}
              </Card>
            </div>
          </div>

          {/* Anomaly List & Recommendations */}
          <div className="row mb-4">
            {/* Anomaly List */}
            <div className="col-md-6">
              <Card title={t('anomalyList', language)} style={{ height: '100%' }}>
                {anomalies.length > 0 ? (
                  <div style={{ maxHeight: '400px', overflowY: 'auto', overflowX: 'hidden' }}>
                    <table
                      className="table table-sm table-hover mb-0"
                      style={{ tableLayout: 'fixed', width: '100%' }}
                    >
                      <thead>
                        <tr>
                          <th style={{ width: '35%' }}>{t('tableDate', language)}</th>
                          <th style={{ width: '25%' }}>{t('type', language)}</th>
                          <th style={{ width: '20%' }}>{t('tableTokens', language)}</th>
                          <th style={{ width: '20%' }}>{t('severity', language)}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {anomalies.map((anomaly, index) => (
                          <tr key={index}>
                            <td
                              style={{
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                              }}
                            >
                              {anomaly.date}
                            </td>
                            <td>
                              <span
                                className={cn(
                                  'badge',
                                  anomaly.type === 'spike' ? 'bg-danger' : 'bg-info'
                                )}
                              >
                                {anomaly.type === 'spike'
                                  ? t('usageSpike', language)
                                  : t('usageDrop', language)}
                              </span>
                            </td>
                            <td style={{ whiteSpace: 'nowrap' }}>{formatTokens(anomaly.tokens)}</td>
                            <td>
                              <span
                                className={cn(
                                  'badge',
                                  anomaly.severity === 'high'
                                    ? 'bg-danger'
                                    : anomaly.severity === 'medium'
                                      ? 'bg-warning'
                                      : 'bg-info'
                                )}
                              >
                                {anomaly.severity}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="text-center py-4">
                    <i className="bi bi-check-circle text-success fs-1" />
                    <p className="mb-0 text-muted mt-2">{t('noAnomaliesDetected', language)}</p>
                  </div>
                )}
              </Card>
            </div>

            {/* Recommendations */}
            <div className="col-md-6">
              <Card title={t('recommendations', language)} style={{ height: '100%' }}>
                {recommendations && recommendations.length > 0 ? (
                  <ul
                    className="list-group list-group-flush"
                    style={{ maxHeight: '400px', overflowY: 'auto' }}
                  >
                    {recommendations.map((rec, index) => (
                      <li key={index} className="list-group-item">
                        <div className="d-flex justify-content-between align-items-start">
                          <div>
                            <h6 className="mb-1">
                              <i className={cn('bi me-2', getRecommendationIcon(rec.type))} />
                              {rec.title}
                            </h6>
                            <p className="mb-1 text-muted small">{rec.description}</p>
                          </div>
                          <span className={cn('badge', getImpactBadge(rec.impact))}>
                            {rec.impact}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState icon="bi-lightbulb" title={t('noRecommendations', language)} />
                )}
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

/**
 * Helper Functions
 */
function getRecommendationIcon(type: string): string {
  const icons: Record<string, string> = {
    optimization: 'bi-lightning',
    cost: 'bi-currency-dollar',
    performance: 'bi-speedometer2',
    security: 'bi-shield-check',
    usage: 'bi-graph-up',
  };
  return icons[type] ?? 'bi-lightbulb';
}

function getImpactBadge(impact: string | undefined): string {
  if (!impact) return 'bg-secondary';
  const badges: Record<string, string> = {
    high: 'bg-danger',
    medium: 'bg-warning',
    low: 'bg-info',
  };
  return badges[impact.toLowerCase()] || 'bg-secondary';
}

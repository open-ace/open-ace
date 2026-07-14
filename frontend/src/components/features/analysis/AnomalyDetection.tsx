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

import React, { useState, useMemo, useRef } from 'react';
import { cn, createMatcherConfig } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Badge,
  type BadgeVariant,
  Card,
  StatCard,
  Select,
  Loading,
  Error,
  EmptyState,
  LineChart,
  PieChart,
  PageRefreshControl,
  DatePicker,
} from '@/components/common';
import { formatTokens } from '@/utils';
import {
  useAnomalyDetection,
  useAnomalyTrend,
  useRecommendations,
  useHosts,
  usePageRefresh,
  useDataRange,
} from '@/hooks';

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

  // Page refresh control - manual refresh for anomaly detection
  const pageRefresh = usePageRefresh({
    page: '/manage/analysis/anomaly',
    refreshKey: createMatcherConfig(
      [
        ['analysis', 'anomaly-detection'],
        ['analysis', 'anomaly-trend'],
      ],
      'prefix'
    ),
    interval: 0, // No auto refresh - manual only
    enabled: false,
  });

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = useMemo(() => hostsData ?? [], [hostsData]);

  // Quick date range options
  const [quickRange, setQuickRange] = useState<'7' | '30' | '90' | 'all'>('30');

  // Fetch the global data range so the "All" quick-range reflects the actual
  // data span instead of a hardcoded window (only requested when relevant).
  const { data: dataRangeData } = useDataRange(quickRange === 'all');

  // Stable reference to data_range to avoid useMemo thrash when the API returns
  // a new object with the same min/max values (mirrors TrendAnalysis).
  const dataRangeRef = useRef<{ min_date: string; max_date: string } | null>(null);
  if (
    dataRangeData &&
    (dataRangeRef.current?.min_date !== dataRangeData.min_date ||
      dataRangeRef.current?.max_date !== dataRangeData.max_date)
  ) {
    dataRangeRef.current = dataRangeData;
  }
  const dataRange = dataRangeRef.current;

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
        // Use the actual data range when available; fall back to 365 days.
        if (dataRange?.min_date && dataRange?.max_date) {
          start = new Date(dataRange.min_date);
          end.setTime(new Date(dataRange.max_date).getTime());
        } else {
          start = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000);
        }
        break;
    }

    return {
      start: start.toISOString().split('T')[0],
      end: end.toISOString().split('T')[0],
    };
  }, [quickRange, dataRange]);

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
  const { data: trendData } = useAnomalyTrend(
    startDate,
    endDate,
    selectedHost || undefined,
    anomalyTypeFilter || undefined,
    severityFilter || undefined
  );

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
  const anomalies = useMemo(() => anomalyData?.anomalies ?? [], [anomalyData?.anomalies]);
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
        <PageRefreshControl refresh={pageRefresh} compact={true} showLastRefreshTime={true} />
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
            <DatePicker
              value={startDate}
              onChange={(v) => {
                setStartDate(v);
                setQuickRange('all');
              }}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <DatePicker
              value={endDate}
              onChange={(v) => {
                setEndDate(v);
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
              <Card
                title={t('anomalyList', language)}
                style={{ height: '100%' }}
                helpTooltip={t('anomalyMethodText', language)}
              >
                {/* Baseline banner — surfaces the detection context (currently discarded) */}
                {anomalyData?.statistics && (
                  <div className="anomaly-baseline-banner mb-3 p-2 rounded">
                    <small className="fw-semibold text-muted d-block mb-1">
                      {t('anomalyMethodIntro', language)}
                    </small>
                    <small className="text-muted">
                      {t('anomalyBaseline', language, {
                        avg: formatTokens(anomalyData.statistics.average),
                        std: formatTokens(anomalyData.statistics.std_deviation),
                        n: anomalyData.statistics.data_points,
                      })}
                    </small>
                  </div>
                )}
                {anomalies.length > 0 ? (
                  <ul
                    className="list-group list-group-flush"
                    style={{ maxHeight: '400px', overflowY: 'auto', overflowX: 'hidden' }}
                  >
                    {anomalies.map((anomaly, index) => {
                      const contributor = getAnomalyTopContributor(anomaly, language);
                      return (
                        <li key={index} className="list-group-item px-0">
                          <div className="d-flex justify-content-between align-items-start">
                            <div className="me-2 flex-grow-1" style={{ minWidth: 0 }}>
                              <div className="d-flex align-items-center gap-2 mb-1 flex-wrap">
                                <Badge variant={anomaly.type === 'spike' ? 'danger' : 'info'}>
                                  {anomaly.type === 'spike'
                                    ? t('usageSpike', language)
                                    : t('usageDrop', language)}
                                </Badge>
                                <span className="text-muted small" style={{ whiteSpace: 'nowrap' }}>
                                  {anomaly.date}
                                </span>
                                <span
                                  className="ms-auto fw-semibold"
                                  style={{ whiteSpace: 'nowrap' }}
                                >
                                  {formatTokens(anomaly.tokens)}
                                </span>
                              </div>
                              <p className="mb-1 text-muted small">
                                {getAnomalyDescription(anomaly, language)}
                              </p>
                              {contributor && (
                                <p className="mb-1 text-muted small">{contributor}</p>
                              )}
                              <p className="mb-0 small text-primary">
                                {getAnomalySuggestion(anomaly.type, language)}
                              </p>
                            </div>
                            <Badge
                              className="ms-2"
                              variant={
                                anomaly.severity === 'high'
                                  ? 'danger'
                                  : anomaly.severity === 'medium'
                                    ? 'warning'
                                    : 'info'
                              }
                            >
                              {anomaly.severity}
                            </Badge>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
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
                              {getTranslatedMessage(rec, language)}
                            </h6>
                            {rec.details && (
                              <p className="mb-1 text-muted small">
                                {getTranslatedDetails(rec, language)}
                              </p>
                            )}
                          </div>
                          <Badge variant={getImpactBadgeVariant(rec.type)}>
                            {t(getImpactKey(rec.type), language)}
                          </Badge>
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

function getImpactBadgeVariant(type: string): BadgeVariant {
  const variants: Record<string, BadgeVariant> = {
    optimization: 'warning',
    cost: 'danger',
    performance: 'warning',
    security: 'danger',
    usage: 'info',
    info: 'info',
    success: 'success',
  };
  return variants[type] || 'secondary';
}

function getImpactKey(type: string): string {
  const keys: Record<string, string> = {
    optimization: 'recImpactMedium',
    cost: 'recImpactHigh',
    performance: 'recImpactMedium',
    security: 'recImpactHigh',
    usage: 'recImpactLow',
    info: 'recImpactInfo',
    success: 'recImpactGood',
  };
  return keys[type] || 'recImpactInfo';
}

function getTranslatedMessage(rec: { type: string; message: string }, language: string): string {
  // Match specific message patterns to avoid semantic errors
  if (rec.message.includes('optimizing prompts') || rec.message.includes('reduce input token')) {
    return t('recOptimizePrompts', language as Language);
  }
  if (rec.message.includes('High concentration of usage')) {
    return t('recHighToolConcentration', language as Language);
  }
  if (rec.message.includes('Usage patterns look healthy')) {
    return t('recHealthyUsage', language as Language);
  }
  // Return original message for unmatched patterns
  return rec.message;
}

function getTranslatedDetails(rec: { type: string; details?: string }, language: string): string {
  if (!rec.details) return '';

  // Parse and translate details
  // Pattern 1: "Current output/input ratio: 0.04"
  const ratioMatch = rec.details.match(/Current output\/input ratio:\s*([\d.]+)/);
  if (ratioMatch) {
    return `${t('recCurrentRatio', language as Language)}: ${ratioMatch[1]}`;
  }

  // Pattern 2: "qwen accounts for 100.0% of total tokens"
  const toolMatch = rec.details.match(/(.+)\s+accounts for\s+([\d.]+%)\s+of total tokens/);
  if (toolMatch) {
    return `${toolMatch[1]} ${t('recToolAccountsFor', language as Language)} ${toolMatch[2]}`;
  }

  return rec.details;
}

/**
 * Anomaly description helpers — shared shape so both the dedicated anomaly
 * page (backend-driven) and the Analysis overview table (client-driven) can
 * render identical, localized descriptions.
 */
interface AnomalyLike {
  date: string;
  tokens: number;
  expected?: number;
  deviation?: number;
  type: string;
  severity?: string;
  top_contributor?: { tool: string; share_pct: number };
}

/**
 * Build a human-readable description of why a day is anomalous.
 *
 * `deviation` from the backend is a percentage of the daily average (always a
 * positive number). The direction ("above"/"below") is derived from `type`,
 * never from the deviation sign — a spike is always "above", a drop always
 * "below". Falls back to the raw token count when the structured baseline
 * fields are missing (legacy cache / older deployments).
 */
export function getAnomalyDescription(anomaly: AnomalyLike, language: Language): string {
  const { expected, deviation, type, tokens } = anomaly;

  // Guard: no usable baseline → degrade to plain token count (never NaN/empty)
  if (
    expected === undefined ||
    expected === null ||
    deviation === undefined ||
    deviation === null ||
    !isFinite(expected) ||
    !isFinite(deviation) ||
    expected <= 0
  ) {
    return formatTokens(tokens);
  }

  const key = type === 'spike' ? 'anomalySpikeDesc' : 'anomalyDropDesc';
  return t(key, language, {
    tokens: formatTokens(tokens),
    avg: formatTokens(expected),
    pct: deviation,
  });
}

/** One-line handling suggestion keyed off the anomaly type. */
export function getAnomalySuggestion(type: string, language: Language): string {
  const key = type === 'spike' ? 'anomalySuggestionSpike' : 'anomalySuggestionDrop';
  return t(key, language);
}

/** Optional "driven by tool X (Y%)" line; empty string when unavailable. */
export function getAnomalyTopContributor(anomaly: AnomalyLike, language: Language): string {
  const tc = anomaly.top_contributor;
  if (!tc?.tool || tc.share_pct === undefined || tc.share_pct === null || !isFinite(tc.share_pct)) {
    return '';
  }
  return t('anomalyTopContributor', language, { tool: tc.tool, pct: tc.share_pct });
}

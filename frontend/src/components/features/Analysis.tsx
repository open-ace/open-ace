/**
 * Analysis Component - Data analysis and visualization with tabs
 */

import React, { useState, useMemo, useRef } from 'react';
import { cn } from '@/utils';
import { useLanguage, useTheme } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Select,
  Loading,
  Error,
  EmptyState,
  BarChart,
  PieChart,
  LineChart,
  DoughnutChart,
  SimpleTabs,
  DatePicker,
} from '@/components/common';
import { formatTokens, formatDate, formatToolName } from '@/utils';
import {
  useKeyMetrics,
  useDailyHourlyUsage,
  useToolComparison,
  usePeakUsage,
  useUserRanking,
  useConversationStats,
  useRecommendations,
  useHosts,
  useUserSegmentation,
  useTools,
  useDataRange,
} from '@/hooks';
import { ConversationHistory } from './ConversationHistory';
import {
  getAnomalyDescription,
  getAnomalySuggestion,
  getAnomalyTopContributor,
} from './analysis/AnomalyDetection';
import { SessionStatisticsCard, calculateHealthScore } from './analysis/SessionStatisticsCard';

export const Analysis: React.FC = () => {
  const language = useLanguage();
  const [groupBy, setGroupBy] = useState<'day' | 'week' | 'month'>('day');
  const [selectedTool, setSelectedTool] = useState<string>('');
  const [selectedHost, setSelectedHost] = useState<string>('');

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
        // Use the actual data range when available; fall back to last year.
        if (dataRange?.min_date && dataRange?.max_date) {
          start = new Date(dataRange.min_date);
          end.setTime(new Date(dataRange.max_date).getTime());
        } else {
          start = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000); // Last year as "all"
        }
        break;
    }

    return {
      start: formatDate(start, 'iso'),
      end: formatDate(end, 'iso'),
    };
  }, [quickRange, dataRange]);

  const [startDate, setStartDate] = useState(dateRange.start);
  const [endDate, setEndDate] = useState(dateRange.end);

  // Update date range when quick range changes
  React.useEffect(() => {
    setStartDate(dateRange.start);
    setEndDate(dateRange.end);
  }, [dateRange]);

  // Fetch data
  const {
    data: keyMetrics,
    isLoading: metricsLoading,
    isError: metricsError,
    error: metricsErrorMsg,
  } = useKeyMetrics(startDate, endDate, selectedHost || undefined);
  const { data: dailyHourly, isLoading: dailyLoading } = useDailyHourlyUsage(
    startDate,
    endDate,
    selectedHost || undefined
  );
  const { data: toolComparison, isLoading: toolsLoading } = useToolComparison(
    startDate,
    endDate,
    selectedHost || undefined
  );
  const { data: peakUsage } = usePeakUsage(startDate, endDate, selectedHost || undefined);
  const { data: userRanking } = useUserRanking(startDate, endDate, selectedHost || undefined, 10);
  const { data: conversationStats } = useConversationStats(
    startDate,
    endDate,
    selectedHost || undefined
  );
  const { data: recommendations } = useRecommendations(selectedHost || undefined);
  const { data: userSegmentation } = useUserSegmentation(
    startDate,
    endDate,
    selectedHost || undefined
  );

  // Group by options
  const groupByOptions = useMemo(
    () => [
      { value: 'day', label: 'Daily' },
      { value: 'week', label: 'Weekly' },
      { value: 'month', label: 'Monthly' },
    ],
    []
  );

  // Get tools for filter
  const { data: toolsData } = useTools();
  const dynamicTools = useMemo(() => toolsData ?? [], [toolsData]);

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      ...dynamicTools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ],
    [dynamicTools, language]
  );

  // Host options
  const hostOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllHosts', language) },
      ...hosts.map((host) => ({ value: host, label: host })),
    ],
    [hosts, language]
  );

  const isLoading = metricsLoading || dailyLoading || toolsLoading;

  // Prepare chart data
  const dailyTrend = dailyHourly?.daily ?? [];
  const hourlyData = dailyHourly?.hourly ?? [];
  const tools = toolComparison?.tools ?? [];

  // Calculate additional metrics
  const activeUsers = userRanking?.users?.length ?? 0;
  const activeTools = tools.length;
  const healthScoreResult = calculateHealthScore(keyMetrics, conversationStats);
  const anomalyCount = detectAnomalies(dailyTrend).length;

  // Overview tab content
  const OverviewContent = (
    <div className="analysis-overview">
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
          {/* Tool Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('tableTool', language)}</label>
            <Select options={toolOptions} value={selectedTool} onChange={setSelectedTool} />
          </div>
          {/* Host Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('tableHost', language)}</label>
            <Select options={hostOptions} value={selectedHost} onChange={setSelectedHost} />
          </div>
        </div>
      </Card>

      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : metricsError ? (
        <Error message={metricsErrorMsg?.message ?? t('error', language)} />
      ) : (
        <>
          {/* Key Metrics - 6 cards */}
          <div className="row g-3 mb-4">
            <div className="col-md-2">
              <StatCard
                label={t('totalTokens', language)}
                value={formatTokens(keyMetrics?.total_tokens ?? 0)}
                icon={<i className="bi bi-cpu fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('totalRequests', language)}
                value={(keyMetrics?.total_messages ?? 0).toLocaleString()}
                icon={<i className="bi bi-chat-dots fs-4" />}
                variant="success"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('activeUsers', language)}
                value={activeUsers.toString()}
                icon={<i className="bi bi-people fs-4" />}
                variant="info"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('activeTools', language)}
                value={activeTools.toString()}
                icon={<i className="bi bi-tools fs-4" />}
                variant="warning"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('anomalies', language)}
                value={anomalyCount.toString()}
                icon={<i className="bi bi-exclamation-triangle fs-4" />}
                variant="danger"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('healthScore', language)}
                value={
                  healthScoreResult.status === 'no_data'
                    ? t('healthScoreNoData', language)
                    : `${healthScoreResult.score}%`
                }
                icon={<i className="bi bi-heart-pulse fs-4" />}
                variant={
                  healthScoreResult.status === 'no_data'
                    ? 'secondary'
                    : healthScoreResult.status === 'healthy'
                      ? 'success'
                      : 'warning'
                }
                helpTooltip={t(`healthScoreTooltip_${healthScoreResult.status}`, language)}
              />
            </div>
          </div>

          {/* Usage Heatmap */}
          <div className="row mb-4">
            <div className="col-12">
              <Card title={t('usageHeatmap', language)}>
                {hourlyData.length > 0 ? (
                  <UsageHeatmap hourlyData={hourlyData} language={language} />
                ) : (
                  <EmptyState icon="bi-calendar3" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>

          {/* Charts Row */}
          <div className="row mb-4">
            <div className="col-md-8">
              <Card title={t('tokenTrend', language)}>
                {dailyTrend.length > 0 ? (
                  <LineChart
                    labels={dailyTrend.map((d) => d.date)}
                    datasets={[
                      {
                        label: t('tokens', language),
                        data: dailyTrend.map((d) => d.tokens),
                      },
                    ]}
                    height={300}
                    unit="M"
                  />
                ) : (
                  <EmptyState icon="bi-graph-up" title={t('noData', language)} />
                )}
              </Card>
            </div>
            <div className="col-md-4">
              <Card title={t('topTools', language)}>
                {tools.length > 0 ? (
                  <BarChart
                    labels={tools.map((t) => t.tool_name)}
                    datasets={[
                      {
                        label: t('usage', language),
                        data: tools.map((t) => t.total_tokens),
                      },
                    ]}
                    height={300}
                    horizontal
                    showLegend={false}
                  />
                ) : (
                  <EmptyState icon="bi-bar-chart" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>

          {/* Tables Row */}
          <div className="row mb-4">
            {/* Peak Usage Periods */}
            <div className="col-md-6">
              <Card title={t('peakUsagePeriods', language)}>
                {peakUsage?.peak_days && peakUsage.peak_days.length > 0 ? (
                  <div className="table-responsive">
                    <table className="table table-sm table-hover">
                      <thead>
                        <tr>
                          <th>{t('tableDate', language)}</th>
                          <th>{t('tableTokens', language)}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {peakUsage.peak_days.slice(0, 5).map((day, index) => (
                          <tr key={index}>
                            <td>{day.date}</td>
                            <td>{formatTokens(day.tokens)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState icon="bi-graph-up-arrow" title={t('noData', language)} />
                )}
              </Card>
            </div>

            {/* Top 10 Active Users */}
            <div className="col-md-6">
              <Card title={t('topActiveUsers', language)}>
                {userRanking?.users && userRanking.users.length > 0 ? (
                  <div className="table-responsive">
                    <table className="table table-sm table-hover">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>{t('tableUser', language)}</th>
                          <th>{t('tableMessages', language)}</th>
                          <th>{t('tableTokens', language)}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {userRanking.users.slice(0, 10).map((user, index) => (
                          <tr key={user.user_id ?? index}>
                            <td>{index + 1}</td>
                            <td>{user.username ?? `User ${user.user_id}`}</td>
                            <td>{user.requests.toLocaleString()}</td>
                            <td>{formatTokens(user.tokens)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <EmptyState icon="bi-people" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>

          {/* Anomaly Detection & Recommendations */}
          <div className="row mb-4">
            {/* Anomaly Detection */}
            <div className="col-md-6">
              <Card title={t('anomalyDetection', language)}>
                {dailyTrend.length > 0 ? (
                  <AnomalyTable anomalies={detectAnomalies(dailyTrend)} language={language} />
                ) : (
                  <EmptyState icon="bi-shield-exclamation" title={t('noData', language)} />
                )}
              </Card>
            </div>

            {/* Recommendations */}
            <div className="col-md-6">
              <Card title={t('recommendations', language)}>
                {recommendations && recommendations.length > 0 ? (
                  <ul className="list-group list-group-flush">
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
                          <span className={cn('badge', getImpactBadge(rec.type))}>
                            {t(getImpactKey(rec.type), language)}
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

          {/* Detailed Stats */}
          <div className="row mb-4">
            <div className="col-md-6">
              <SessionStatisticsCard conversationStats={conversationStats} />
            </div>
            <div className="col-md-6">
              <Card title={t('userSegmentation', language)}>
                {userSegmentation &&
                userSegmentation.high +
                  userSegmentation.medium +
                  userSegmentation.low +
                  userSegmentation.dormant >
                  0 ? (
                  <DoughnutChart
                    labels={['High (>10K)', 'Medium (1K-10K)', 'Low (<1K)', 'Dormant']}
                    data={[
                      userSegmentation.high ?? 0,
                      userSegmentation.medium ?? 0,
                      userSegmentation.low ?? 0,
                      userSegmentation.dormant ?? 0,
                    ]}
                    backgroundColor={[
                      'rgba(255, 99, 132, 0.8)',
                      'rgba(255, 206, 86, 0.8)',
                      'rgba(75, 192, 192, 0.8)',
                      'rgba(201, 203, 207, 0.8)',
                    ]}
                    height={200}
                  />
                ) : (
                  <EmptyState icon="bi-people" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>

          {/* Tool Comparison Chart */}
          <div className="row mb-4">
            <div className="col-md-6">
              <Card title={t('toolComparison', language)}>
                {tools.length > 0 ? (
                  <BarChart
                    labels={tools.map((tool) => tool.tool_name.toUpperCase())}
                    datasets={[
                      {
                        label: t('tableTokens', language),
                        data: tools.map((tool) => tool.total_tokens),
                        backgroundColor: 'rgba(54, 162, 235, 0.6)',
                      },
                    ]}
                    height={250}
                    showLegend={false}
                  />
                ) : (
                  <EmptyState icon="bi-bar-chart" title={t('noData', language)} />
                )}
              </Card>
            </div>
            <div className="col-md-6">
              <Card title={t('tokenDistribution', language)}>
                {keyMetrics?.top_tools && keyMetrics.top_tools.length > 0 ? (
                  <PieChart
                    labels={keyMetrics.top_tools.map((tool) => tool.tool)}
                    data={keyMetrics.top_tools.map((tool) => tool.count)}
                    height={250}
                  />
                ) : (
                  <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
                )}
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  );

  // Tabs configuration
  const tabs = [
    {
      id: 'overview',
      label: t('overview', language),
      icon: <i className="bi bi-graph-up" />,
      content: OverviewContent,
    },
    {
      id: 'conversation-history',
      label: t('conversationHistory', language),
      icon: <i className="bi bi-chat-square-text" />,
      content: <ConversationHistory />,
    },
  ];

  return (
    <div className="analysis">
      {/* Header */}
      <div className="analysis-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('analysis', language)}</h2>
        <div className="page-header-controls">
          <Select
            options={groupByOptions}
            value={groupBy}
            onChange={(value) => setGroupBy(value as 'day' | 'week' | 'month')}
            size="sm"
          />
        </div>
      </div>

      {/* Tabs */}
      <SimpleTabs tabs={tabs} defaultTab="overview" />
    </div>
  );
};

/**
 * Usage Heatmap Component
 * Issue #1641: theme-aware cell opacity and text color for dark mode.
 */
interface UsageHeatmapProps {
  hourlyData: Array<{ hour: number; tokens: number; requests: number }>;
  language: Language;
}

const UsageHeatmap: React.FC<UsageHeatmapProps> = ({ hourlyData, language }) => {
  const theme = useTheme();
  const isDark = theme === 'dark';
  const maxTokens = Math.max(...hourlyData.map((d) => d.tokens), 1);

  const minOpacity = isDark ? 0.15 : 0;
  const getCellBg = (intensity: number) => {
    const opacity = minOpacity + (intensity / 100) * (1 - minOpacity);
    return `rgba(13, 110, 253, ${opacity.toFixed(2)})`;
  };

  const legendOpacities = isDark ? [0.15, 0.35, 0.6, 1] : [0.1, 0.3, 0.6, 1];

  return (
    <div className="usage-heatmap">
      <div className="mb-3">
        <small className="text-muted">{t('usageHeatmapDescription', language)}</small>
      </div>
      <div className="d-flex flex-wrap gap-1">
        {Array.from({ length: 24 }, (_, hour) => {
          const data = hourlyData.find((d) => d.hour === hour);
          const intensity = data ? (data.tokens / maxTokens) * 100 : 0;

          return (
            <div
              key={hour}
              className="heatmap-cell"
              style={{
                width: 'calc(100% / 24 - 4px)',
                height: '40px',
                backgroundColor: getCellBg(intensity),
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '4px',
                fontSize: '10px',
                color: isDark ? 'white' : intensity > 50 ? 'white' : 'black',
              }}
              title={`${hour}:00 - ${data?.tokens ?? 0} tokens`}
            >
              {hour}
            </div>
          );
        })}
      </div>
      <div className="mt-2 d-flex justify-content-between">
        <small className="text-muted">0:00</small>
        <small className="text-muted">12:00</small>
        <small className="text-muted">23:00</small>
      </div>
      <div className="mt-2 d-flex align-items-center justify-content-end gap-2">
        <small className="text-muted">{t('less', language)}</small>
        {legendOpacities.map((opacity, i) => (
          <div
            key={i}
            style={{
              width: '20px',
              height: '12px',
              backgroundColor: `rgba(13, 110, 253, ${opacity})`,
              borderRadius: '2px',
              border: isDark ? '1px solid rgba(255,255,255,0.15)' : undefined,
            }}
          />
        ))}
        <small className="text-muted">{t('more', language)}</small>
      </div>
    </div>
  );
};

/**
 * Anomaly Table Component
 */
interface AnomalyTableProps {
  anomalies: Array<{
    date: string;
    tokens: number;
    expected?: number;
    deviation?: number;
    type: string;
    severity: string;
    top_contributor?: { tool: string; share_pct: number };
  }>;
  language: Language;
}

const AnomalyTable: React.FC<AnomalyTableProps> = ({ anomalies, language }) => {
  if (anomalies.length === 0) {
    return (
      <div className="text-center py-3">
        <i className="bi bi-check-circle text-success fs-4" />
        <p className="mb-0 text-muted">{t('noAnomaliesDetected', language)}</p>
      </div>
    );
  }

  return (
    <div className="table-responsive">
      <table className="table table-sm table-hover mb-0">
        <thead>
          <tr>
            <th>{t('tableDate', language)}</th>
            <th>{t('type', language)}</th>
            <th>{t('tableTokens', language)}</th>
            <th>{t('severity', language)}</th>
          </tr>
        </thead>
        <tbody>
          {anomalies.slice(0, 5).map((anomaly, index) => {
            const contributor = getAnomalyTopContributor(anomaly, language);
            return (
              <React.Fragment key={index}>
                <tr>
                  <td>{anomaly.date}</td>
                  <td>
                    <span
                      className={cn('badge', anomaly.type === 'spike' ? 'bg-danger' : 'bg-info')}
                    >
                      {anomaly.type === 'spike'
                        ? t('usageSpike', language)
                        : t('usageDrop', language)}
                    </span>
                  </td>
                  <td>{formatTokens(anomaly.tokens)}</td>
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
                <tr>
                  <td colSpan={4} className="border-0 py-0">
                    <p className="mb-1 text-muted small">
                      {getAnomalyDescription(anomaly, language)}
                    </p>
                    {contributor && <p className="mb-1 text-muted small">{contributor}</p>}
                    <p className="mb-0 small text-primary">
                      {getAnomalySuggestion(anomaly.type, language)}
                    </p>
                  </td>
                </tr>
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

/**
 * Helper Functions
 *
 * `calculateHealthScore` and the session-statistics card are shared from
 * `./analysis/SessionStatisticsCard` so the Analysis overview and Token Trend
 * pages stay in sync.
 */

function detectAnomalies(dailyTrend: Array<{ date: string; tokens: number }>): Array<{
  date: string;
  tokens: number;
  expected: number;
  deviation: number;
  type: string;
  severity: string;
}> {
  if (dailyTrend.length < 3) return [];

  const anomalies: Array<{
    date: string;
    tokens: number;
    expected: number;
    deviation: number;
    type: string;
    severity: string;
  }> = [];
  const avgTokens = dailyTrend.reduce((sum, d) => sum + d.tokens, 0) / dailyTrend.length;
  const stdDev = Math.sqrt(
    dailyTrend.reduce((sum, d) => sum + Math.pow(d.tokens - avgTokens, 2), 0) / dailyTrend.length
  );

  dailyTrend.forEach((day) => {
    // stdDistance drives detection (matches the >2σ / >3σ rule); deviation is
    // the percentage off the daily average. NOTE: only the `deviation` *field's*
    // value is aligned with the backend here so descriptions read identically —
    // the drop *detection rule* itself still differs (this client flags any day
    // below the mean at >2σ as a drop; the backend requires <50% of the mean).
    // That divergence predates this change and is out of scope.
    const stdDistance = stdDev > 0 ? Math.abs(day.tokens - avgTokens) / stdDev : 0;
    const deviationPct =
      avgTokens > 0 ? Math.round((Math.abs(day.tokens - avgTokens) / avgTokens) * 1000) / 10 : 0;

    if (stdDistance > 2) {
      anomalies.push({
        date: day.date,
        tokens: day.tokens,
        expected: Math.round(avgTokens),
        deviation: deviationPct,
        type: day.tokens > avgTokens ? 'spike' : 'drop',
        severity: stdDistance > 3 ? 'high' : 'medium',
      });
    }
  });

  return anomalies;
}

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

function getImpactBadge(type: string): string {
  const badges: Record<string, string> = {
    optimization: 'bg-warning',
    cost: 'bg-danger',
    performance: 'bg-warning',
    security: 'bg-danger',
    usage: 'bg-info',
    info: 'bg-info',
    success: 'bg-success',
  };
  return badges[type] || 'bg-secondary';
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

/**
 * Analysis Component - Data analysis and visualization with tabs
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  BarChart,
  PieChart,
  LineChart,
  DoughnutChart,
  SimpleTabs,
} from '@/components/common';
import { formatTokens, formatDate } from '@/utils';
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
} from '@/hooks';
import { ConversationHistory } from './ConversationHistory';

export const Analysis: React.FC = () => {
  const language = useLanguage();
  const [groupBy, setGroupBy] = useState<'day' | 'week' | 'month'>('day');
  const [selectedTool, setSelectedTool] = useState<string>('');
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [autoRefresh, setAutoRefresh] = useState<boolean>(false);

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = hostsData || [];

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
        start = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000); // Last year as "all"
        break;
    }

    return {
      start: formatDate(start, 'iso'),
      end: formatDate(end, 'iso'),
    };
  }, [quickRange]);

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
    isFetching: metricsFetching,
    isError: metricsError,
    error: metricsErrorMsg,
    refetch: refetchMetrics,
  } = useKeyMetrics(startDate, endDate, selectedHost || undefined);
  const { data: dailyHourly, isLoading: dailyLoading } = useDailyHourlyUsage(startDate, endDate, selectedHost || undefined);
  const { data: toolComparison, isLoading: toolsLoading } = useToolComparison(startDate, endDate, selectedHost || undefined);
  const { data: peakUsage } = usePeakUsage(startDate, endDate, selectedHost || undefined);
  const { data: userRanking } = useUserRanking(startDate, endDate, selectedHost || undefined, 10);
  const { data: conversationStats } = useConversationStats(startDate, endDate, selectedHost || undefined);
  const { data: recommendations } = useRecommendations(selectedHost || undefined);
  const { data: userSegmentation } = useUserSegmentation(startDate, endDate, selectedHost || undefined);

  // Auto-refresh effect
  React.useEffect(() => {
    if (autoRefresh) {
      const interval = setInterval(() => {
        refetchMetrics();
      }, 60000); // 60 seconds
      return () => clearInterval(interval);
    }
    return undefined;
  }, [autoRefresh, refetchMetrics]);

  // Group by options
  const groupByOptions = useMemo(
    () => [
      { value: 'day', label: 'Daily' },
      { value: 'week', label: 'Weekly' },
      { value: 'month', label: 'Monthly' },
    ],
    []
  );

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude', label: 'Claude' },
      { value: 'qwen', label: 'Qwen' },
    ],
    [language]
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
  const dailyTrend = dailyHourly?.daily || [];
  const hourlyData = dailyHourly?.hourly || [];
  const tools = toolComparison?.tools || [];

  // Calculate additional metrics
  const activeUsers = userRanking?.users?.length || 0;
  const activeTools = tools.length;
  const healthScore = calculateHealthScore(keyMetrics, conversationStats);
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
          {/* Tool Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('tableTool', language)}</label>
            <Select
              options={toolOptions}
              value={selectedTool}
              onChange={setSelectedTool}
            />
          </div>
          {/* Host Filter */}
          <div className="col-md-3">
            <label className="form-label">{t('tableHost', language)}</label>
            <Select
              options={hostOptions}
              value={selectedHost}
              onChange={setSelectedHost}
            />
          </div>
        </div>
      </Card>

      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : metricsError ? (
        <Error
          message={metricsErrorMsg?.message || t('error', language)}
          onRetry={() => refetchMetrics()}
        />
      ) : (
        <>
          {/* Key Metrics - 6 cards */}
          <div className="row g-3 mb-4">
            <div className="col-md-2">
              <StatCard
                label={t('totalTokens', language)}
                value={formatTokens(keyMetrics?.total_tokens || 0)}
                icon={<i className="bi bi-cpu fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-2">
              <StatCard
                label={t('totalRequests', language)}
                value={(keyMetrics?.total_messages || 0).toLocaleString()}
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
                value={`${healthScore}%`}
                icon={<i className="bi bi-heart-pulse fs-4" />}
                variant={healthScore >= 80 ? 'success' : healthScore >= 60 ? 'warning' : 'danger'}
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
                          <tr key={user.user_id || index}>
                            <td>{index + 1}</td>
                            <td>{user.username || `User ${user.user_id}`}</td>
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

          {/* Detailed Stats */}
          <div className="row mb-4">
            <div className="col-md-6">
              <Card title={t('sessionStatistics', language)}>
                <table className="table table-sm">
                  <tbody>
                    <tr>
                      <td>{t('avgMessagesPerSession', language)}</td>
                      <td className="text-end">
                        {keyMetrics?.avg_messages_per_session?.toFixed(1) || '0'}
                      </td>
                    </tr>
                    <tr>
                      <td>{t('avgTokensPerSession', language)}</td>
                      <td className="text-end">
                        {formatTokens(keyMetrics?.avg_tokens_per_session || 0)}
                      </td>
                    </tr>
                    <tr>
                      <td>{t('totalSessions', language)}</td>
                      <td className="text-end">
                        {keyMetrics?.total_sessions?.toLocaleString() || '0'}
                      </td>
                    </tr>
                    <tr>
                      <td>{t('totalConversations', language)}</td>
                      <td className="text-end">
                        {conversationStats?.total_conversations?.toLocaleString() || '0'}
                      </td>
                    </tr>
                    <tr>
                      <td>{t('multiTurnRatio', language)}</td>
                      <td className="text-end">
                        {conversationStats?.avg_conversation_length?.toFixed(1) || '0'}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </Card>
            </div>
            <div className="col-md-6">
              <Card title={t('userSegmentation', language)}>
                {userSegmentation && (userSegmentation.high + userSegmentation.medium + userSegmentation.low + userSegmentation.dormant) > 0 ? (
                  <DoughnutChart
                    labels={['High (>10K)', 'Medium (1K-10K)', 'Low (<1K)', 'Dormant']}
                    data={[
                      userSegmentation.high || 0,
                      userSegmentation.medium || 0,
                      userSegmentation.low || 0,
                      userSegmentation.dormant || 0,
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
      icon: <i className="bi bi-chat-history" />,
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
          {/* Auto-refresh toggle */}
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              id="analysisAutoRefreshSwitch"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <label className="form-check-label" htmlFor="analysisAutoRefreshSwitch">
              {t('autoRefresh', language)}
            </label>
          </div>
          <Button
            variant="primary"
            size="sm"
            icon={metricsFetching ? undefined : <i className="bi bi-arrow-clockwise" />}
            onClick={() => refetchMetrics()}
            loading={metricsFetching}
          >
            {t('refresh', language)}
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <SimpleTabs tabs={tabs} defaultTab="overview" />
    </div>
  );
};

/**
 * Usage Heatmap Component
 */
interface UsageHeatmapProps {
  hourlyData: Array<{ hour: number; tokens: number; requests: number }>;
  language: Language;
}

const UsageHeatmap: React.FC<UsageHeatmapProps> = ({ hourlyData, language }) => {
  const maxTokens = Math.max(...hourlyData.map((d) => d.tokens), 1);

  return (
    <div className="usage-heatmap">
      <div className="mb-3">
        <small className="text-muted">
          {t('usageHeatmapDescription', language)}
        </small>
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
                backgroundColor: `rgba(13, 110, 253, ${intensity / 100})`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '4px',
                fontSize: '10px',
                color: intensity > 50 ? 'white' : 'black',
              }}
              title={`${hour}:00 - ${data?.tokens || 0} tokens`}
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
        <div style={{ width: '20px', height: '12px', backgroundColor: 'rgba(13, 110, 253, 0.1)', borderRadius: '2px' }} />
        <div style={{ width: '20px', height: '12px', backgroundColor: 'rgba(13, 110, 253, 0.3)', borderRadius: '2px' }} />
        <div style={{ width: '20px', height: '12px', backgroundColor: 'rgba(13, 110, 253, 0.6)', borderRadius: '2px' }} />
        <div style={{ width: '20px', height: '12px', backgroundColor: 'rgba(13, 110, 253, 1)', borderRadius: '2px' }} />
        <small className="text-muted">{t('more', language)}</small>
      </div>
    </div>
  );
};

/**
 * Anomaly Table Component
 */
interface AnomalyTableProps {
  anomalies: Array<{ date: string; tokens: number; type: string; severity: string }>;
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
      <table className="table table-sm table-hover">
        <thead>
          <tr>
            <th>{t('tableDate', language)}</th>
            <th>{t('type', language)}</th>
            <th>{t('tableTokens', language)}</th>
            <th>{t('severity', language)}</th>
          </tr>
        </thead>
        <tbody>
          {anomalies.slice(0, 5).map((anomaly, index) => (
            <tr key={index}>
              <td>{anomaly.date}</td>
              <td>{anomaly.type}</td>
              <td>{formatTokens(anomaly.tokens)}</td>
              <td>
                <span
                  className={cn(
                    'badge',
                    anomaly.severity === 'high' ? 'bg-danger' :
                    anomaly.severity === 'medium' ? 'bg-warning' : 'bg-info'
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
  );
};

/**
 * Helper Functions
 */
function calculateHealthScore(
  keyMetrics: { total_sessions?: number; avg_tokens_per_session?: number } | undefined,
  conversationStats: { avg_conversation_length?: number } | undefined
): number {
  // Simple health score calculation
  let score = 100;

  // Deduct points for low engagement
  if (keyMetrics?.avg_tokens_per_session && keyMetrics.avg_tokens_per_session < 1000) {
    score -= 20;
  }

  // Deduct points for short conversations
  if (conversationStats?.avg_conversation_length && conversationStats.avg_conversation_length < 2) {
    score -= 15;
  }

  return Math.max(0, Math.min(100, score));
}

function detectAnomalies(
  dailyTrend: Array<{ date: string; tokens: number }>
): Array<{ date: string; tokens: number; type: string; severity: string }> {
  if (dailyTrend.length < 3) return [];

  const anomalies: Array<{ date: string; tokens: number; type: string; severity: string }> = [];
  const avgTokens = dailyTrend.reduce((sum, d) => sum + d.tokens, 0) / dailyTrend.length;
  const stdDev = Math.sqrt(
    dailyTrend.reduce((sum, d) => sum + Math.pow(d.tokens - avgTokens, 2), 0) / dailyTrend.length
  );

  dailyTrend.forEach((day) => {
    const deviation = Math.abs(day.tokens - avgTokens) / stdDev;

    if (deviation > 2) {
      anomalies.push({
        date: day.date,
        tokens: day.tokens,
        type: day.tokens > avgTokens ? 'spike' : 'drop',
        severity: deviation > 3 ? 'high' : 'medium',
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
  return icons[type] || 'bi-lightbulb';
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
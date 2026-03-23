/**
 * TrendAnalysis Component - Token usage trend analysis page
 *
 * Features:
 * - Key metrics cards
 * - Usage heatmap
 * - Token trend chart
 * - Tool comparison
 * - Peak usage periods
 * - Active users ranking
 * - User segmentation
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
} from '@/components/common';
import { formatTokens } from '@/utils';
import {
  useKeyMetrics,
  useDailyHourlyUsage,
  useToolComparison,
  usePeakUsage,
  useUserRanking,
  useConversationStats,
  useHosts,
  useUserSegmentation,
} from '@/hooks';

export const TrendAnalysis: React.FC = () => {
  const language = useLanguage();
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
  const { data: userSegmentation } = useUserSegmentation(startDate, endDate, selectedHost || undefined);

  // Auto-refresh effect
  React.useEffect(() => {
    if (autoRefresh) {
      const interval = setInterval(() => {
        refetchMetrics();
      }, 60000);
      return () => clearInterval(interval);
    }
    return undefined;
  }, [autoRefresh, refetchMetrics]);

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
  const healthScore = calculateHealthScore(keyMetrics, conversationStats);

  return (
    <div className="trend-analysis">
      {/* Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('tokenTrend', language)}</h2>
        <div className="page-header-controls">
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              id="trendAutoRefreshSwitch"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <label className="form-check-label" htmlFor="trendAutoRefreshSwitch">
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
          {/* Key Metrics - 4 cards */}
          <div className="row g-3 mb-4">
            <div className="col-md-3">
              <StatCard
                label={t('totalTokens', language)}
                value={formatTokens(keyMetrics?.total_tokens || 0)}
                icon={<i className="bi bi-cpu fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('totalRequests', language)}
                value={(keyMetrics?.total_messages || 0).toLocaleString()}
                icon={<i className="bi bi-chat-dots fs-4" />}
                variant="success"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('activeUsers', language)}
                value={activeUsers.toString()}
                icon={<i className="bi bi-people fs-4" />}
                variant="info"
              />
            </div>
            <div className="col-md-3">
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
 * Helper Functions
 */
function calculateHealthScore(
  keyMetrics: { total_sessions?: number; avg_tokens_per_session?: number } | undefined,
  conversationStats: { avg_conversation_length?: number } | undefined
): number {
  let score = 100;

  if (keyMetrics?.avg_tokens_per_session && keyMetrics.avg_tokens_per_session < 1000) {
    score -= 20;
  }

  if (conversationStats?.avg_conversation_length && conversationStats.avg_conversation_length < 2) {
    score -= 15;
  }

  return Math.max(0, Math.min(100, score));
}
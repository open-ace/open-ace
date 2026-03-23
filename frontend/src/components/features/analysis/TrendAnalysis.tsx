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
 *
 * Performance optimizations:
 * - Uses batch API to fetch all data in a single request
 * - Skeleton loading for better perceived performance
 */

import React, { useState, useMemo, useRef, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Select,
  Error,
  EmptyState,
  BarChart,
  PieChart,
  LineChart,
  DoughnutChart,
  Skeleton,
} from '@/components/common';
import { formatTokens } from '@/utils';
import {
  useBatchAnalysis,
  useHosts,
} from '@/hooks';

// Skeleton components
const MetricsSkeleton: React.FC = () => (
  <div className="row g-3 mb-4">
    {[1, 2, 3, 4].map((i) => (
      <div key={i} className="col-md-3">
        <div className="card">
          <div className="card-body">
            <Skeleton height={16} width="60%" className="mb-2" />
            <Skeleton height={32} width="80%" className="mb-1" />
            <Skeleton height={12} width="40%" />
          </div>
        </div>
      </div>
    ))}
  </div>
);

const ChartSkeleton: React.FC<{ height?: number }> = ({ height = 300 }) => (
  <div style={{ height }}>
    <Skeleton variant="rectangular" height={height} className="w-100" />
  </div>
);

export const TrendAnalysis: React.FC = () => {
  const language = useLanguage();
  const [selectedTool, setSelectedTool] = useState<string>('');
  const [selectedHost, setSelectedHost] = useState<string>('');

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

  // Track if this is the initial load
  const isInitialLoad = useRef(true);

  // Update date range when quick range changes
  useEffect(() => {
    setStartDate(dateRange.start);
    setEndDate(dateRange.end);
  }, [dateRange]);

  // Fetch all data in a single batch request
  const {
    data: batchData,
    isLoading,
    isError,
    error,
  } = useBatchAnalysis(startDate, endDate, selectedHost || undefined);

  // Mark initial load complete
  useEffect(() => {
    if (!isLoading && batchData) {
      isInitialLoad.current = false;
    }
  }, [isLoading, batchData]);

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

  // Extract data from batch response
  const keyMetrics = batchData?.key_metrics;
  const dailyHourly = batchData?.daily_hourly_usage;
  const peakUsage = batchData?.peak_usage;
  const userRanking = batchData?.user_ranking;
  const conversationStats = batchData?.conversation_stats;
  const toolComparison = batchData?.tool_comparison;
  const userSegmentation = batchData?.user_segmentation;

  // Prepare chart data
  const dailyTrend = dailyHourly?.daily || [];
  const hourlyData = dailyHourly?.hourly || [];
  const tools = toolComparison?.tools || [];

  // Calculate additional metrics
  const activeUsers = userRanking?.users?.length || 0;
  const healthScore = calculateHealthScore(keyMetrics, conversationStats);

  // Show skeleton on initial load
  if (isLoading && isInitialLoad.current) {
    return (
      <div className="trend-analysis">
        <div className="page-header d-flex justify-content-between align-items-center mb-4">
          <Skeleton height={32} width={200} />
          <Skeleton height={32} width={100} />
        </div>

        <Card className="mb-4">
          <div className="row g-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="col-md-3">
                <Skeleton height={38} />
              </div>
            ))}
          </div>
        </Card>

        <MetricsSkeleton />

        <div className="row mb-4">
          <div className="col-12">
            <Card title={t('usageHeatmap', language)}>
              <ChartSkeleton height={100} />
            </Card>
          </div>
        </div>

        <div className="row mb-4">
          <div className="col-md-8">
            <Card title={t('tokenTrend', language)}>
              <ChartSkeleton height={300} />
            </Card>
          </div>
          <div className="col-md-4">
            <Card title={t('topTools', language)}>
              <ChartSkeleton height={300} />
            </Card>
          </div>
        </div>
      </div>
    );
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} />;
  }

  return (
    <div className="trend-analysis">
      {/* Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('tokenTrend', language)}</h2>
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

      {/* Token Trend Chart */}
      <div className="row mb-4">
        <div className="col-12">
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
      </div>

      {/* Tables Row */}
      <div className="row mb-4">
        {/* Peak Usage Periods */}
        <div className="col-md-6">
          <Card title={t('peakUsagePeriods', language)}>
            {peakUsage?.peak_days && peakUsage.peak_days.length > 0 ? (
              <div style={{ minHeight: 380 }}>
                <table className="table table-sm table-hover" style={{ tableLayout: 'fixed', width: '100%' }}>
                  <thead>
                    <tr>
                      <th style={{ width: '10%' }}>#</th>
                      <th style={{ width: '45%' }}>{t('tableDate', language)}</th>
                      <th style={{ width: '45%' }} className="text-end">{t('tableTokens', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {peakUsage.peak_days.slice(0, 10).map((day, index) => (
                      <tr key={index}>
                        <td>{index + 1}</td>
                        <td>{day.date}</td>
                        <td className="text-end">{formatTokens(day.tokens)}</td>
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
              <div style={{ minHeight: 380 }}>
                <table className="table table-sm table-hover" style={{ tableLayout: 'fixed', width: '100%' }}>
                  <thead>
                    <tr>
                      <th style={{ width: '10%' }}>#</th>
                      <th style={{ width: '40%' }}>{t('tableUser', language)}</th>
                      <th style={{ width: '25%' }} className="text-end">{t('tableMessages', language)}</th>
                      <th style={{ width: '25%' }} className="text-end">{t('tableTokens', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {userRanking.users.slice(0, 10).map((user, index) => (
                      <tr key={user.user_id || index}>
                        <td>{index + 1}</td>
                        <td className="text-truncate">{user.username || `User ${user.user_id}`}</td>
                        <td className="text-end">{user.requests.toLocaleString()}</td>
                        <td className="text-end">{formatTokens(user.tokens)}</td>
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
            <div style={{ height: 200 }}>
              <table className="table table-sm">
                <tbody>
                  <tr>
                    <td>{t('avgMessagesPerSession', language)}</td>
                    <td className="text-end">
                      {Math.round(keyMetrics?.avg_messages_per_session || 0)}
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
            </div>
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
                unit="M"
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
                labels={keyMetrics.top_tools.map((tool) => tool.tool.toUpperCase())}
                data={keyMetrics.top_tools.map((tool) => tool.count)}
                height={250}
              />
            ) : (
              <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>
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
/**
 * RequestDashboard Component - Request statistics dashboard for administrators
 *
 * Features:
 * - Today's request total and by-tool breakdown
 * - Request trend chart with date range selector
 * - Request by user statistics
 * - Real-time request monitoring
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { usePageRefresh } from '@/hooks';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error,
  EmptyState,
  Badge,
  TextInput,
  PageRefreshControl,
} from '@/components/common';
import { LazyLineChart, LazyBarChart } from '@/components/common/LazyCharts';
import { getToolColor } from '@/components/common/chartColors';
import {
  requestApi,
  type RequestTodayStats,
  type RequestTrendByToolData,
  type RequestStatsByUser,
} from '@/api/request';
import { formatNumber, createMatcherConfig } from '@/utils';

// Date range preset values for the selector
const DATE_RANGE_PRESET_VALUES = [
  { value: '7', labelKey: 'dateRangeLast7Days' },
  { value: '14', labelKey: 'dateRangeLast14Days' },
  { value: '30', labelKey: 'dateRangeLast30Days' },
  { value: '60', labelKey: 'dateRangeLast60Days' },
  { value: '90', labelKey: 'dateRangeLast90Days' },
] as const;

// Helper to get date string N days ago
const getDaysAgo = (days: number): string => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().split('T')[0];
};

const getToday = (): string => {
  return new Date().toISOString().split('T')[0];
};

export const RequestDashboard: React.FC = () => {
  const language = useLanguage();

  // State
  const [todayStats, setTodayStats] = useState<RequestTodayStats | null>(null);
  const [trendData, setTrendData] = useState<RequestTrendByToolData[]>([]);
  const [userStats, setUserStats] = useState<RequestStatsByUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Date range
  const [dateRange, setDateRange] = useState('30');
  const [customStartDate, setCustomStartDate] = useState(getDaysAgo(30));
  const [customEndDate, setCustomEndDate] = useState(getToday());
  const [useCustomRange, setUseCustomRange] = useState(false);

  // Fetch data
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const startDate = useCustomRange ? customStartDate : getDaysAgo(parseInt(dateRange));
      const endDate = useCustomRange ? customEndDate : getToday();

      const [todayData, trendResult, userResult] = await Promise.all([
        requestApi.getTodayStats(),
        requestApi.getTrendByTool(startDate, endDate),
        requestApi.getStatsByUser(),
      ]);

      setTodayStats(todayData);
      setTrendData(trendResult);
      setUserStats(userResult);
    } catch (err) {
      const error = err as Error;
      const errorMessage = error?.message || 'Failed to fetch request statistics';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [dateRange, useCustomRange, customStartDate, customEndDate]);

  // Page refresh control - manages auto refresh for request dashboard queries
  const pageRefresh = usePageRefresh({
    page: '/manage/analysis/request-dashboard',
    refreshKey: createMatcherConfig([['analysis', 'request-dashboard']], 'prefix'),
    interval: 60000, // 1 minute default
    enabled: true, // Enable by default for real-time data
    onRefresh: fetchData,
  });

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Date range presets with translated labels
  const dateRangePresets = useMemo(
    () =>
      DATE_RANGE_PRESET_VALUES.map((preset) => ({
        value: preset.value,
        label: t(preset.labelKey, language),
      })),
    [language]
  );

  // Aggregate user stats
  const aggregatedUserStats = useMemo(() => {
    const userMap = new Map<string, { requests: number; tokens: number }>();

    userStats.forEach((stat) => {
      const existing = userMap.get(stat.user) ?? { requests: 0, tokens: 0 };
      existing.requests += stat.requests;
      existing.tokens += stat.tokens;
      userMap.set(stat.user, existing);
    });

    return Array.from(userMap.entries())
      .map(([user, data]) => ({ user, ...data }))
      .sort((a, b) => b.requests - a.requests);
  }, [userStats]);

  // Prepare trend chart data
  const trendChartData = useMemo(() => {
    const dates = [...new Set(trendData.map((d) => d.date))].sort();
    const tools = [...new Set(trendData.map((d) => d.tool))];

    const dataMap = new Map<string, number>();
    trendData.forEach((d) => {
      dataMap.set(`${d.date}-${d.tool}`, d.requests);
    });

    return {
      labels: dates,
      datasets: tools.map((tool, index) => {
        const colors = getToolColor(tool, index);
        return {
          label: tool.toUpperCase(),
          data: dates.map((date) => dataMap.get(`${date}-${tool}`) ?? 0),
          borderColor: colors.border,
          backgroundColor: colors.background,
          fill: false,
          tension: 0.2,
        };
      }),
    };
  }, [trendData]);

  // Prepare user bar chart data
  // Y-axis shows ranking (1, 2, 3...), X-axis shows request count
  // Tooltip shows username + request count
  const userChartData = useMemo(() => {
    const topUsers = aggregatedUserStats.slice(0, 10);
    return {
      labels: topUsers.map((_, index) => String(index + 1)), // 排序号从1开始
      usernames: topUsers.map((u) => u.user), // 保存用户名用于 tooltip
      datasets: [
        {
          label: t('requests', language),
          data: topUsers.map((u) => u.requests),
          backgroundColor: 'rgba(37, 99, 235, 0.6)',
        },
      ],
    };
  }, [aggregatedUserStats, language]);

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchData} />;
  }

  return (
    <div className="request-dashboard">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('requestStatistics', language)}</h2>
        <PageRefreshControl
          refresh={pageRefresh}
          compact={true}
          showLastRefreshTime={true}
          showNextRefreshTime={false}
        />
      </div>

      {/* Today's Stats */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('todayRequests', language)}
            value={formatNumber(todayStats?.total_requests ?? 0)}
            icon={<i className="bi bi-lightning fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('activeUsers', language)}
            value={formatNumber(aggregatedUserStats.length)}
            icon={<i className="bi bi-people fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('avgRequestsPerUser', language)}
            value={
              aggregatedUserStats.length > 0
                ? formatNumber(
                    Math.round(
                      aggregatedUserStats.reduce((sum, u) => sum + u.requests, 0) /
                        aggregatedUserStats.length
                    )
                  )
                : '0'
            }
            icon={<i className="bi bi-calculator fs-4" />}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('peakTool', language)}
            value={
              todayStats?.by_tool
                ? Object.entries(todayStats.by_tool)
                    .sort((a, b) => b[1] - a[1])[0]?.[0]
                    ?.toUpperCase() || '-'
                : '-'
            }
            icon={<i className="bi bi-bar-chart fs-4" />}
            variant="warning"
          />
        </div>
      </div>

      {/* Today's By Tool */}
      <div className="row g-4 mb-4">
        <div className="col-12">
          <Card title={t('todayByTool', language)}>
            {todayStats?.by_tool && Object.keys(todayStats.by_tool).length > 0 ? (
              <div className="table-responsive">
                <table className="table table-sm">
                  <thead>
                    <tr>
                      <th>{t('tool', language)}</th>
                      <th className="text-end">{t('requests', language)}</th>
                      <th className="text-end">{t('percentage', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(todayStats.by_tool)
                      .sort((a, b) => b[1] - a[1])
                      .map(([tool, count]) => {
                        const percentage =
                          todayStats.total_requests > 0
                            ? ((count / todayStats.total_requests) * 100).toFixed(1)
                            : '0';
                        return (
                          <tr key={tool}>
                            <td>
                              <Badge variant="info">{tool.toUpperCase()}</Badge>
                            </td>
                            <td className="text-end">{formatNumber(count)}</td>
                            <td className="text-end">{percentage}%</td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>

      {/* Date Range Selector */}
      <Card className="mb-4">
        <div className="d-flex flex-wrap gap-2 align-items-center">
          <span className="me-2">{t('dateRange', language)}:</span>
          {dateRangePresets.map((preset) => (
            <Button
              key={preset.value}
              variant={
                !useCustomRange && dateRange === preset.value ? 'primary' : 'outline-secondary'
              }
              size="sm"
              onClick={() => {
                setUseCustomRange(false);
                setDateRange(preset.value);
              }}
            >
              {preset.label}
            </Button>
          ))}
          <Button
            variant={useCustomRange ? 'primary' : 'outline-secondary'}
            size="sm"
            onClick={() => setUseCustomRange(true)}
          >
            {t('custom', language)}
          </Button>
          {useCustomRange && (
            <div className="d-flex gap-2 align-items-center ms-2">
              <TextInput
                type="date"
                value={customStartDate}
                onChange={setCustomStartDate}
                placeholder={t('startDate', language)}
              />
              <span>-</span>
              <TextInput
                type="date"
                value={customEndDate}
                onChange={setCustomEndDate}
                placeholder={t('endDate', language)}
              />
            </div>
          )}
        </div>
      </Card>

      {/* Request Trend Chart */}
      <div className="row g-4 mb-4">
        <div className="col-12">
          <Card title={t('requestTrend', language)}>
            {trendData.length > 0 ? (
              <LazyLineChart
                labels={trendChartData.labels}
                datasets={trendChartData.datasets}
                height={300}
              />
            ) : (
              <EmptyState icon="bi-graph-up" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>

      {/* User Statistics */}
      <div className="row g-4">
        {/* Request by User Chart */}
        <div className="col-lg-6">
          <Card title={t('requestsByUser', language)}>
            {aggregatedUserStats.length > 0 ? (
              <LazyBarChart
                labels={userChartData.labels}
                datasets={userChartData.datasets}
                height={300}
                horizontal
                usernames={userChartData.usernames}
                language={language}
              />
            ) : (
              <EmptyState icon="bi-people" title={t('noData', language)} />
            )}
          </Card>
        </div>

        {/* User Details Table */}
        <div className="col-lg-6">
          <Card title={t('userDetails', language)}>
            {aggregatedUserStats.length > 0 ? (
              <div className="table-responsive" style={{ maxHeight: '400px' }}>
                <table className="table table-sm table-hover">
                  <thead>
                    <tr>
                      <th>{t('user', language)}</th>
                      <th className="text-end">{t('requests', language)}</th>
                      <th className="text-end">{t('tokens', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {aggregatedUserStats.map((stat) => (
                      <tr key={stat.user}>
                        <td>{stat.user}</td>
                        <td className="text-end">{formatNumber(stat.requests)}</td>
                        <td className="text-end">{formatNumber(stat.tokens)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState icon="bi-table" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};

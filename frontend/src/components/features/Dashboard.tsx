/**
 * Dashboard Component - Main dashboard with usage statistics
 */

import React, { useState, useMemo, startTransition } from 'react';
import { cn } from '@/utils';
import { useDashboard, useTools, usePageRefresh } from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Select,
  Error,
  EmptyState,
  TokenTrendChart,
  TokenDistributionChart,
  DashboardSkeleton,
  TextInput,
  PageRefreshControl,
} from '@/components/common';
import { formatTokens, TOOL_DISPLAY_NAMES, formatToolName, createMatcherConfig } from '@/utils';
import type { ToolUsage, ToolSummary } from '@/types';

// Color palette for each tool
const TOOL_COLORS: Record<string, { border: string; background: string; card: string }> = {
  openclaw: {
    border: 'rgba(255, 99, 132, 1)',
    background: 'rgba(255, 99, 132, 0.2)',
    card: 'bg-danger',
  },
  claude: {
    border: 'rgba(75, 192, 192, 1)',
    background: 'rgba(75, 192, 192, 0.2)',
    card: 'bg-success',
  },
  qwen: { border: 'rgba(54, 162, 235, 1)', background: 'rgba(54, 162, 235, 0.2)', card: 'bg-info' },
};

// Date range preset values (labels will be internationalized)
const DATE_RANGE_PRESET_VALUES = [
  { value: '7', labelKey: 'dateRangeLast7Days' },
  { value: '30', labelKey: 'dateRangeLast30Days' },
  { value: 'month', labelKey: 'dateRangeThisMonth' },
  { value: 'last_month', labelKey: 'dateRangeLastMonth' },
  { value: 'custom', labelKey: 'dateRangeCustom' },
] as const;

// Helper to get date string N days ago
const getDaysAgo = (days: number): string => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().split('T')[0];
};

// Helper to get first day of current month
const getFirstDayOfMonth = (): string => {
  const date = new Date();
  return new Date(date.getFullYear(), date.getMonth(), 1).toISOString().split('T')[0];
};

// Helper to get first day of last month
const getFirstDayOfLastMonth = (): string => {
  const date = new Date();
  return new Date(date.getFullYear(), date.getMonth() - 1, 1).toISOString().split('T')[0];
};

// Helper to get last day of last month
const getLastDayOfLastMonth = (): string => {
  const date = new Date();
  return new Date(date.getFullYear(), date.getMonth(), 0).toISOString().split('T')[0];
};

const getToday = (): string => {
  return new Date().toISOString().split('T')[0];
};

// Date validation error type
type DateErrorType = 'invalid_range' | 'future_date' | null;

// Sort configuration type
type SortKey =
  | 'total_tokens'
  | 'total_requests'
  | 'avg_tokens'
  | 'total_input_tokens'
  | 'total_output_tokens'
  | 'days_count';
type SortDirection = 'asc' | 'desc';

export const Dashboard: React.FC = () => {
  const language = useLanguage();
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [selectedTool, setSelectedTool] = useState<string>('');
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  // Date range state
  const [dateRangePreset, setDateRangePreset] = useState('30');
  const [customStartDate, setCustomStartDate] = useState(getDaysAgo(30));
  const [customEndDate, setCustomEndDate] = useState(getToday());
  const [useCustomRange, setUseCustomRange] = useState(false);
  const [dateError, setDateError] = useState<DateErrorType>(null);

  // Validate date range
  const validateDateRange = (start: string, end: string): DateErrorType => {
    const today = getToday();
    const startDateObj = new Date(start);
    const endDateObj = new Date(end);
    const todayDateObj = new Date(today);

    // Check if start date is after end date
    if (startDateObj > endDateObj) {
      return 'invalid_range';
    }

    // Check if any date is in the future
    if (startDateObj > todayDateObj || endDateObj > todayDateObj) {
      return 'future_date';
    }

    return null;
  };

  // Handle date change with validation
  const handleStartDateChange = (value: string) => {
    setCustomStartDate(value);
    if (useCustomRange) {
      const error = validateDateRange(value, customEndDate);
      setDateError(error);
    }
  };

  const handleEndDateChange = (value: string) => {
    setCustomEndDate(value);
    if (useCustomRange) {
      const error = validateDateRange(customStartDate, value);
      setDateError(error);
    }
  };

  // Compute actual date range based on preset or custom
  const { startDate, endDate } = useMemo(() => {
    if (useCustomRange) {
      return { startDate: customStartDate, endDate: customEndDate };
    }
    switch (dateRangePreset) {
      case '7':
        return { startDate: getDaysAgo(7), endDate: getToday() };
      case '30':
        return { startDate: getDaysAgo(30), endDate: getToday() };
      case 'month':
        return { startDate: getFirstDayOfMonth(), endDate: getToday() };
      case 'last_month':
        return { startDate: getFirstDayOfLastMonth(), endDate: getLastDayOfLastMonth() };
      default:
        return { startDate: getDaysAgo(30), endDate: getToday() };
    }
  }, [dateRangePreset, useCustomRange, customStartDate, customEndDate]);

  // Handle preset change
  const handlePresetChange = (value: string) => {
    if (value === 'custom') {
      setUseCustomRange(true);
      // Validate current custom dates when switching to custom mode
      const error = validateDateRange(customStartDate, customEndDate);
      setDateError(error);
    } else {
      setUseCustomRange(false);
      setDateRangePreset(value);
      setDateError(null); // Clear error when switching to preset
    }
  };

  // Use combined dashboard hook with trend data for parallel fetching
  // Note: autoRefresh parameter is deprecated - use usePageRefresh instead
  const { todayData, summaryData, hosts, trendData, isLoading, isError, error, refetch } =
    useDashboard({
      tool: selectedTool || undefined,
      host: selectedHost || undefined,
      startDate,
      endDate,
      // DEPRECATED: autoRefresh parameter - will be removed in future versions
      // Use PageRefreshControl component for refresh management
      autoRefresh: false,
    });

  // Page refresh control - manages auto refresh for dashboard queries
  const pageRefresh = usePageRefresh({
    page: '/manage/dashboard',
    refreshKey: createMatcherConfig([['dashboard']], 'prefix', [['dashboard', 'hosts']]),
    interval: 60000, // 1 minute default
    enabled: true, // Enable by default for real-time data
  });

  // Sort handler - use startTransition for non-urgent updates (rerender-transitions optimization)
  const handleSort = (key: SortKey) => {
    startTransition(() => {
      if (sortKey === key) {
        setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
      } else {
        setSortKey(key);
        setSortDirection('desc');
      }
    });
  };

  // Sorted summary data
  const sortedSummaryData = useMemo(() => {
    if (!sortKey) return summaryData;
    const entries = Object.entries(summaryData);
    entries.sort(([, a], [, b]) => {
      const aVal = a[sortKey] ?? 0;
      const bVal = b[sortKey] ?? 0;
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
    });
    return Object.fromEntries(entries);
  }, [summaryData, sortKey, sortDirection]);

  // Host options for select
  const hostOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllHosts', language) },
      ...hosts.map((host) => ({ value: host, label: host })),
    ],
    [hosts, language]
  );

  // Get tools for filter
  const { data: toolsData } = useTools();
  const dynamicTools = useMemo(() => toolsData ?? [], [toolsData]);

  // Tool options for select
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      ...dynamicTools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ],
    [dynamicTools, language]
  );

  // Date range preset options with internationalized labels
  const dateRangeOptions = useMemo(
    () =>
      DATE_RANGE_PRESET_VALUES.map((preset) => ({
        value: preset.value,
        label: t(preset.labelKey, language),
      })),
    [language]
  );

  if (isLoading) {
    return <DashboardSkeleton />;
  }

  if (isError) {
    return <Error message={error?.message ?? t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="dashboard">
      {/* Header */}
      <div className="dashboard-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('dashboardTitle', language)}</h2>
        <div className="page-header-controls d-flex gap-2 align-items-center flex-wrap">
          {/* Page Refresh Control - compact mode for better layout */}
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showLastRefreshTime={true}
            showNextRefreshTime={false}
          />

          {/* Date Range Selector */}
          <Select
            options={dateRangeOptions}
            value={useCustomRange ? 'custom' : dateRangePreset}
            onChange={handlePresetChange}
            size="sm"
            className="select-narrow"
          />
          {useCustomRange && (
            <>
              {/* Visually hidden labels for accessibility */}
              <label htmlFor="date-start-input" className="visually-hidden">
                {t('dateRangeStartDate', language)}
              </label>
              <TextInput
                id="date-start-input"
                type="date"
                value={customStartDate}
                onChange={handleStartDateChange}
                className="date-input-narrow"
                aria-describedby={dateError ? 'date-range-error' : undefined}
              />
              {/* Separator with aria-hidden to avoid duplicate announcement */}
              <span aria-hidden="true" className="text-muted">
                {t('dateRangeSeparator', language)}
              </span>
              <label htmlFor="date-end-input" className="visually-hidden">
                {t('dateRangeEndDate', language)}
              </label>
              <TextInput
                id="date-end-input"
                type="date"
                value={customEndDate}
                onChange={handleEndDateChange}
                className="date-input-narrow"
                aria-describedby={dateError ? 'date-range-error' : undefined}
              />
              {/* Error message container with aria-live for screen readers */}
              {dateError && (
                <div
                  id="date-range-error"
                  className="text-danger small"
                  role="alert"
                  aria-live="polite"
                >
                  {dateError === 'invalid_range'
                    ? t('dateRangeErrorInvalid', language)
                    : t('dateRangeErrorFuture', language)}
                </div>
              )}
            </>
          )}
          <Select
            options={hostOptions}
            value={selectedHost}
            onChange={setSelectedHost}
            size="sm"
            className="select-narrow"
          />
          <Select
            options={toolOptions}
            value={selectedTool}
            onChange={setSelectedTool}
            size="sm"
            className="select-narrow"
          />
        </div>
      </div>

      {/* Today's Usage */}
      <section className="dashboard-section mb-4">
        <h5 className="mb-3">{t('todayUsage', language)}</h5>
        {todayData.length === 0 ? (
          <EmptyState icon="bi-calendar-x" title={t('noData', language)} />
        ) : (
          <div className="row g-3">
            {todayData.map((item) => (
              <TodayCard key={item.tool_name} item={item} language={language} />
            ))}
          </div>
        )}
      </section>

      {/* Total Overview */}
      <section className="dashboard-section mb-4">
        <h5 className="mb-3">{t('totalOverview', language)}</h5>
        {Object.keys(summaryData).length === 0 ? (
          <EmptyState icon="bi-bar-chart" title={t('noData', language)} />
        ) : (
          <div className="row g-3">
            {Object.entries(summaryData).map(([tool, stats]) => (
              <SummaryCard key={tool} tool={tool} stats={stats} language={language} />
            ))}
          </div>
        )}
      </section>

      {/* Charts */}
      <section className="dashboard-section mb-4">
        <div className="row">
          <div className="col-md-8 mb-4">
            <Card title={t('trendChart', language)}>
              {trendData && trendData.length > 0 ? (
                <TokenTrendChart
                  data={trendData}
                  startDate={startDate}
                  endDate={endDate}
                  height={300}
                />
              ) : (
                <EmptyState icon="bi-graph-up" title={t('noData', language)} />
              )}
            </Card>
          </div>
          <div className="col-md-4 mb-4">
            <Card title={t('tokenDistribution', language)}>
              {trendData && trendData.length > 0 ? (
                <TokenDistributionChart
                  data={Object.values(
                    trendData.reduce(
                      (acc, item) => {
                        const tool = item.tool;
                        if (!acc[tool]) {
                          acc[tool] = { tool, tokens: 0 };
                        }
                        acc[tool].tokens += item.tokens;
                        return acc;
                      },
                      {} as Record<string, { tool: string; tokens: number }>
                    )
                  ).sort((a, b) => b.tokens - a.tokens)}
                  height={300}
                />
              ) : (
                <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
              )}
            </Card>
          </div>
        </div>
      </section>

      {/* Tools Info Table */}
      <section className="dashboard-section">
        <Card title={t('toolsInfo', language)}>
          {Object.keys(summaryData).length === 0 ? (
            <EmptyState icon="bi-tools" title={t('noData', language)} />
          ) : (
            <div className="table-responsive">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>{t('tableTool', language)}</th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableTokens', language)}
                      {sortKey === 'total_tokens' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_requests')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableRequests', language)}
                      {sortKey === 'total_requests' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('avg_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableAverage', language)}
                      {sortKey === 'avg_tokens' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_input_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableInput', language)}
                      {sortKey === 'total_input_tokens' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_output_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableOutput', language)}
                      {sortKey === 'total_output_tokens' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th className="text-end">{t('tableRatio', language) || 'Ratio'}</th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('days_count')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('days_tracked', language)}
                      {sortKey === 'days_count' && (
                        <i
                          className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                        />
                      )}
                    </th>
                    <th>{t('date_range', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(sortedSummaryData).map(([tool, stats]) => (
                    <tr key={tool}>
                      <td>
                        <span className={cn('badge', TOOL_COLORS[tool]?.card || 'bg-secondary')}>
                          {tool.toUpperCase()}
                        </span>
                      </td>
                      <td className="text-end">{formatTokens(stats.total_tokens)}</td>
                      <td className="text-end">{stats.total_requests ?? '-'}</td>
                      <td className="text-end">{(stats.avg_tokens / 1000000).toFixed(2)} M/day</td>
                      <td className="text-end">{formatTokens(stats.total_input_tokens ?? 0)}</td>
                      <td className="text-end">{formatTokens(stats.total_output_tokens ?? 0)}</td>
                      <td className="text-end">
                        {(stats.total_output_tokens ?? 0) > 0
                          ? (
                              (stats.total_input_tokens ?? 0) / (stats.total_output_tokens ?? 1)
                            ).toFixed(1)
                          : '-'}
                      </td>
                      <td className="text-end">{stats.days_count}</td>
                      <td>
                        <small className="text-muted">
                          {stats.first_date} ~ {stats.last_date}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </section>
    </div>
  );
};

/**
 * Today's Usage Card
 * Memoized for performance (rerender-memo optimization)
 */
interface TodayCardProps {
  item: ToolUsage;
  language: Language;
}

const TodayCard = React.memo<TodayCardProps>(({ item, language }) => {
  const colors = TOOL_COLORS[item.tool_name] || { card: 'bg-secondary' };

  return (
    <div className="col-md-4">
      <div className={cn('card usage-card text-white', colors.card)}>
        <div className="card-body">
          <h6 className="card-subtitle mb-3 text-white-50">
            {(TOOL_DISPLAY_NAMES[item.tool_name] ?? item.tool_name).toUpperCase()}
          </h6>
          {item.tokens_used > 0 ? (
            <h3 className="card-title mb-3">
              {formatTokens(item.tokens_used)}{' '}
              <small className="fs-6">{t('tokens', language)}</small>
            </h3>
          ) : item.request_count > 0 ? (
            <>
              <h3 className="card-title mb-3">
                {item.request_count} <small className="fs-6">{t('tableRequests', language)}</small>
              </h3>
              <p className="card-text mb-1 text-warning">
                <small>
                  <i className="bi bi-exclamation-triangle" />{' '}
                  {t('tokenDataNotAvailable', language)}
                </small>
              </p>
            </>
          ) : (
            <h3 className="card-title mb-3">{t('noDataAvailable', language)}</h3>
          )}
          <p className="card-text mb-1">
            <strong>{t('tableDate', language)}:</strong> {item.date}
          </p>
          {item.tokens_used > 0 && item.request_count > 0 && (
            <p className="card-text mb-1">
              <strong>{t('tableRequests', language)}:</strong> {item.request_count}
            </p>
          )}
          {item.input_tokens > 0 && (
            <p className="card-text mb-1">
              <strong>{t('tableInput', language)}:</strong> {formatTokens(item.input_tokens)}{' '}
              <small className="fs-6">{t('tokens', language)}</small>
            </p>
          )}
          {item.output_tokens > 0 && (
            <p className="card-text mb-1">
              <strong>{t('tableOutput', language)}:</strong> {formatTokens(item.output_tokens)}{' '}
              <small className="fs-6">{t('tokens', language)}</small>
            </p>
          )}
        </div>
      </div>
    </div>
  );
});

/**
 * Summary Card
 * Memoized for performance (rerender-memo optimization)
 */
interface SummaryCardProps {
  tool: string;
  stats: ToolSummary;
  language: Language;
}

const SummaryCard = React.memo<SummaryCardProps>(({ tool, stats, language }) => {
  const colors = TOOL_COLORS[tool] || { card: 'bg-secondary' };

  return (
    <div className="col-md-4">
      <div className={cn('card usage-card text-white', colors.card)}>
        <div className="card-body">
          <h5 className="card-title">{tool.toUpperCase()}</h5>
          <p className="card-text">
            <strong>{formatTokens(stats.total_tokens)}</strong> {t('tokens', language)}
            <br />
            {stats.days_count} {t('days_tracked', language)}
            <br />
            {t('avg', language)}: {formatTokens(stats.avg_tokens)}/day
            <br />
            <strong>{t('tableRequests', language)}:</strong>{' '}
            {stats.total_requests > 0 ? stats.total_requests : t('noDataAvailable', language)}
          </p>
          <small>
            {t('date_range', language)}: {stats.first_date} - {stats.last_date}
          </small>
        </div>
      </div>
    </div>
  );
});

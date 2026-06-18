/**
 * Dashboard Component - Main dashboard with usage statistics
 *
 * Layout structure:
 * 1. Header - Title
 * 2. Today's Usage - Real-time tool usage cards
 * 3. Time Range Statistics - Summary stats + Tool summary cards + Date filter
 * 4. Trend Chart + Distribution Chart - Shared tool filter
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useDashboard } from '@/hooks';
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
  StatCard,
} from '@/components/common';
import { formatTokens, TOOL_DISPLAY_NAMES } from '@/utils';
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

export const Dashboard: React.FC = () => {
  const language = useLanguage();

  // Tool filter for trend chart
  const [selectedTool, setSelectedTool] = useState('all');

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

    if (startDateObj > endDateObj) {
      return 'invalid_range';
    }

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

  // Compute actual date range
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
      const error = validateDateRange(customStartDate, customEndDate);
      setDateError(error);
    } else {
      setUseCustomRange(false);
      setDateRangePreset(value);
      setDateError(null);
    }
  };

  // Use combined dashboard hook
  const { todayData, summaryData, trendData, isLoading, isError, error, refetch } = useDashboard({
    startDate,
    endDate,
    autoRefresh: false,
  });

  // Compute summary statistics for the selected time range
  const summaryStats = useMemo(() => {
    const tools = Object.keys(summaryData);
    const totalTokens = tools.reduce(
      (sum, tool) => sum + (summaryData[tool]?.total_tokens ?? 0),
      0
    );
    const totalRequests = tools.reduce(
      (sum, tool) => sum + (summaryData[tool]?.total_requests ?? 0),
      0
    );
    const activeTools = tools.length;
    const totalInputTokens = tools.reduce(
      (sum, tool) => sum + (summaryData[tool]?.total_input_tokens ?? 0),
      0
    );
    const totalOutputTokens = tools.reduce(
      (sum, tool) => sum + (summaryData[tool]?.total_output_tokens ?? 0),
      0
    );

    return {
      totalTokens,
      totalRequests,
      activeTools,
      totalInputTokens,
      totalOutputTokens,
    };
  }, [summaryData]);

  // Date range preset options
  const dateRangeOptions = useMemo(
    () =>
      DATE_RANGE_PRESET_VALUES.map((preset) => ({
        value: preset.value,
        label: t(preset.labelKey, language),
      })),
    [language]
  );

  // Tool filter options for trend chart
  const toolOptions = useMemo(() => {
    const tools = Object.keys(summaryData);
    const options = [{ value: 'all', label: t('dashboardFilterAllTools', language) }];
    tools.forEach((tool) => {
      options.push({
        value: tool,
        label: TOOL_DISPLAY_NAMES[tool] ?? tool,
      });
    });
    return options;
  }, [summaryData, language]);

  // Filtered trend data based on selected tool
  const filteredTrendData = useMemo(() => {
    if (selectedTool === 'all' || !trendData) {
      return trendData;
    }
    // Filter trend data points for the selected tool
    return trendData.filter((point) => point.tool === selectedTool);
  }, [trendData, selectedTool]);

  // Distribution data for chart - filtered by selected tool
  const distributionData = useMemo(() => {
    const tools = Object.keys(summaryData);
    if (selectedTool === 'all') {
      return tools.map((tool) => ({
        tool,
        tokens: summaryData[tool]?.total_tokens ?? 0,
      }));
    }
    // Filter for selected tool only
    if (tools.includes(selectedTool)) {
      return [{ tool: selectedTool, tokens: summaryData[selectedTool]?.total_tokens ?? 0 }];
    }
    return [];
  }, [summaryData, selectedTool]);

  if (isLoading) {
    return <DashboardSkeleton />;
  }

  if (isError) {
    return <Error message={error?.message ?? t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="dashboard">
      {/* Header - simple title row */}
      <div className="dashboard-header mb-4">
        <h2>{t('dashboardTitle', language)}</h2>
      </div>

      {/* Today's Usage - Real-time data */}
      <section className="dashboard-section mb-4">
        <Card title={t('todayUsage', language)}>
          {todayData.length === 0 ? (
            <EmptyState icon="bi-calendar-x" title={t('noData', language)} />
          ) : (
            <div className="row g-3">
              {todayData.map((item) => (
                <TodayCard key={item.tool_name} item={item} language={language} />
              ))}
            </div>
          )}
        </Card>
      </section>

      {/* Time Range Statistics - Summary + Date filter */}
      <section className="dashboard-section mb-4">
        <Card
          title={t('timeRangeStatistics', language) || '时间范围统计'}
          actions={
            <div className="d-flex gap-2 align-items-center page-header-controls">
              <Select
                options={dateRangeOptions}
                value={useCustomRange ? 'custom' : dateRangePreset}
                onChange={handlePresetChange}
                size="sm"
                className="select-narrow"
              />
              {useCustomRange && (
                <>
                  <TextInput
                    type="date"
                    value={customStartDate}
                    onChange={handleStartDateChange}
                    className="date-input-narrow"
                    aria-label={t('startDate', language)}
                  />
                  <span className="text-muted" aria-hidden="true">
                    {t('dateRangeSeparator', language)}
                  </span>
                  <TextInput
                    type="date"
                    value={customEndDate}
                    onChange={handleEndDateChange}
                    className="date-input-narrow"
                    aria-label={t('endDate', language)}
                  />
                  {dateError && (
                    <small className="text-danger" role="alert">
                      {dateError === 'invalid_range'
                        ? t('dateRangeErrorInvalid', language)
                        : t('dateRangeErrorFuture', language)}
                    </small>
                  )}
                </>
              )}
            </div>
          }
        >
          {/* Summary Stat Cards */}
          <div className="row g-3 mb-4">
            <div className="col-md-4">
              <StatCard
                label={t('totalTokens', language) || '总 Token'}
                value={formatTokens(summaryStats.totalTokens)}
                icon={<i className="bi bi-bar-chart fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-4">
              <StatCard
                label={t('totalRequests', language) || '总请求'}
                value={summaryStats.totalRequests.toLocaleString()}
                icon={<i className="bi bi-lightning fs-4" />}
                variant="info"
              />
            </div>
            <div className="col-md-4">
              <StatCard
                label={t('activeTools', language) || '活跃工具'}
                value={summaryStats.activeTools.toString()}
                icon={<i className="bi bi-tools fs-4" />}
                variant="success"
              />
            </div>
          </div>
          {/* Tool Summary Cards */}
          {Object.keys(summaryData).length === 0 ? (
            <EmptyState icon="bi-bar-chart" title={t('noData', language)} />
          ) : (
            <div className="row g-3">
              {Object.entries(summaryData).map(([tool, stats]) => (
                <SummaryCard key={tool} tool={tool} stats={stats} language={language} />
              ))}
            </div>
          )}
        </Card>
      </section>

      {/* Trend Chart + Distribution Chart - Two columns with shared filter */}
      <section className="dashboard-section mb-4">
        {/* Global chart filter bar - controls both trend and distribution charts */}
        <div className="chart-filter-bar bg-light rounded p-2 mb-3">
          <div className="d-flex align-items-center page-header-controls">
            <span className="text-muted small me-2" style={{ whiteSpace: 'nowrap' }}>
              {t('tool', language)}:
            </span>
            <Select
              options={toolOptions}
              value={selectedTool}
              onChange={setSelectedTool}
              size="sm"
              className="select-narrow"
            />
          </div>
        </div>
        <div className="row g-3">
          {/* Trend Chart - Left */}
          <div className="col-lg-8">
            <Card title={t('trendChart', language)}>
              {filteredTrendData && filteredTrendData.length > 0 ? (
                <TokenTrendChart
                  data={filteredTrendData}
                  startDate={startDate}
                  endDate={endDate}
                  height={300}
                />
              ) : (
                <EmptyState icon="bi-graph-up" title={t('noData', language)} />
              )}
            </Card>
          </div>
          {/* Distribution Chart - Right */}
          <div className="col-lg-4">
            <Card title={t('tokenDistribution', language)}>
              {distributionData.length > 0 ? (
                <TokenDistributionChart data={distributionData} height={300} />
              ) : (
                <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
              )}
            </Card>
          </div>
        </div>
      </section>
    </div>
  );
};

/**
 * Today's Usage Card
 */
interface TodayCardProps {
  item: ToolUsage;
  language: Language;
}

const TodayCard = React.memo<TodayCardProps>(({ item, language }) => {
  const colors = TOOL_COLORS[item.tool_name] || { card: 'bg-secondary' };

  return (
    <div className="col-md-3 col-sm-6">
      <div className={cn('card usage-card text-white', colors.card)}>
        <div className="card-body">
          <h6 className="card-subtitle mb-2 text-white-50">
            {(TOOL_DISPLAY_NAMES[item.tool_name] ?? item.tool_name).toUpperCase()}
          </h6>
          {item.tokens_used > 0 ? (
            <>
              <h4 className="card-title mb-2">
                {formatTokens(item.tokens_used)}{' '}
                <small className="fs-6">{t('tokens', language)}</small>
              </h4>
              {item.input_tokens > 0 && (
                <p className="card-text mb-1">
                  <small>
                    <strong>{t('tableInput', language)}:</strong> {formatTokens(item.input_tokens)}
                  </small>
                </p>
              )}
              {item.output_tokens > 0 && (
                <p className="card-text mb-1">
                  <small>
                    <strong>{t('tableOutput', language)}:</strong>{' '}
                    {formatTokens(item.output_tokens)}
                  </small>
                </p>
              )}
            </>
          ) : item.request_count > 0 ? (
            <>
              <h4 className="card-title mb-2">
                {item.request_count} <small className="fs-6">{t('tableRequests', language)}</small>
              </h4>
              <p className="card-text mb-1 text-warning">
                <small>
                  <i className="bi bi-exclamation-triangle" />{' '}
                  {t('tokenDataNotAvailable', language)}
                </small>
              </p>
            </>
          ) : (
            <h4 className="card-title mb-2">{t('noDataAvailable', language)}</h4>
          )}
          {item.tokens_used > 0 && item.request_count > 0 && (
            <p className="card-text mb-0">
              <small>
                {t('tableRequests', language)}: {item.request_count}
              </small>
            </p>
          )}
        </div>
      </div>
    </div>
  );
});

/**
 * Summary Card - Shows tool statistics for selected time range
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
          <h5 className="card-title">{(TOOL_DISPLAY_NAMES[tool] || tool).toUpperCase()}</h5>
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

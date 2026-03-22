/**
 * Dashboard Component - Main dashboard with usage statistics
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useDashboard, useTrendData } from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  TokenTrendChart,
  TokenDistributionChart,
} from '@/components/common';
import { formatTokens, formatDate } from '@/utils';
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

// Sort configuration type
type SortKey = 'total_tokens' | 'total_requests' | 'avg_tokens' | 'total_input_tokens' | 'total_output_tokens' | 'days_count';
type SortDirection = 'asc' | 'desc';

export const Dashboard: React.FC = () => {
  const language = useLanguage();
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [selectedTool, setSelectedTool] = useState<string>('');
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  const { todayData, summaryData, hosts, isLoading, isFetching, isError, error, refetch } = useDashboard({
    tool: selectedTool || undefined,
    host: selectedHost || undefined,
    autoRefresh,
    refreshInterval: 60000,
  });

  // Get date range for trend data (last 30 days)
  const { startDate, endDate } = useMemo(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    return {
      startDate: formatDate(start, 'iso'),
      endDate: formatDate(end, 'iso'),
    };
  }, []);

  const trendQuery = useTrendData(startDate, endDate, selectedHost || undefined);

  // Sort handler
  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDirection('desc');
    }
  };

  // Sorted summary data
  const sortedSummaryData = useMemo(() => {
    if (!sortKey) return summaryData;
    const entries = Object.entries(summaryData);
    entries.sort(([, a], [, b]) => {
      const aVal = a[sortKey] || 0;
      const bVal = b[sortKey] || 0;
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

  // Tool options for select
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      { value: 'openclaw', label: t('dashboardFilterOpenclaw', language) },
      { value: 'claude', label: t('dashboardFilterClaude', language) },
      { value: 'qwen', label: t('dashboardFilterQwen', language) },
    ],
    [language]
  );

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="dashboard">
      {/* Header */}
      <div className="dashboard-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('dashboardTitle', language)}</h2>
        <div className="page-header-controls">
          <Select options={hostOptions} value={selectedHost} onChange={setSelectedHost} size="sm" />
          <Select options={toolOptions} value={selectedTool} onChange={setSelectedTool} size="sm" />
          {/* Auto-refresh toggle */}
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              id="autoRefreshSwitch"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <label className="form-check-label" htmlFor="autoRefreshSwitch">
              {t('autoRefresh', language)}
            </label>
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => refetch()}
            loading={isFetching}
            icon={isFetching ? undefined : <i className="bi bi-arrow-clockwise" />}
          >
            {t('refresh', language)}
          </Button>
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
              {trendQuery.isLoading ? (
                <Loading />
              ) : trendQuery.data && trendQuery.data.length > 0 ? (
                <TokenTrendChart data={trendQuery.data} height={300} />
              ) : (
                <EmptyState icon="bi-graph-up" title={t('noData', language)} />
              )}
            </Card>
          </div>
          <div className="col-md-4 mb-4">
            <Card title={t('tokenDistribution', language)}>
              {trendQuery.data && trendQuery.data.length > 0 ? (
                <TokenDistributionChart
                  data={Object.values(
                    trendQuery.data.reduce((acc, item) => {
                      const tool = item.tool;
                      if (!acc[tool]) {
                        acc[tool] = { tool, tokens: 0 };
                      }
                      acc[tool].tokens += item.tokens;
                      return acc;
                    }, {} as Record<string, { tool: string; tokens: number }>)
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
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_requests')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableRequests', language)}
                      {sortKey === 'total_requests' && (
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('avg_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableAverage', language)}
                      {sortKey === 'avg_tokens' && (
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_input_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableInput', language)}
                      {sortKey === 'total_input_tokens' && (
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('total_output_tokens')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('tableOutput', language)}
                      {sortKey === 'total_output_tokens' && (
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
                      )}
                    </th>
                    <th
                      className="text-end sortable"
                      onClick={() => handleSort('days_count')}
                      style={{ cursor: 'pointer' }}
                    >
                      {t('days_tracked', language)}
                      {sortKey === 'days_count' && (
                        <i className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`} />
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
                      <td className="text-end">{stats.total_requests || '-'}</td>
                      <td className="text-end">{(stats.avg_tokens / 1000000).toFixed(2)} M/day</td>
                      <td className="text-end">{formatTokens(stats.total_input_tokens || 0)}</td>
                      <td className="text-end">{formatTokens(stats.total_output_tokens || 0)}</td>
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
 */
interface TodayCardProps {
  item: ToolUsage;
  language: Language;
}

const TodayCard: React.FC<TodayCardProps> = ({ item, language }) => {
  const colors = TOOL_COLORS[item.tool_name] || { card: 'bg-secondary' };

  return (
    <div className="col-md-4">
      <div className={cn('card usage-card text-white', colors.card)}>
        <div className="card-body">
          <h6 className="card-subtitle mb-3 text-white-50">{item.tool_name.toUpperCase()}</h6>
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
};

/**
 * Summary Card
 */
interface SummaryCardProps {
  tool: string;
  stats: ToolSummary;
  language: Language;
}

const SummaryCard: React.FC<SummaryCardProps> = ({ tool, stats, language }) => {
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
};

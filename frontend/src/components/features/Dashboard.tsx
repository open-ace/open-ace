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

export const Dashboard: React.FC = () => {
  const language = useLanguage();
  const [selectedHost, setSelectedHost] = useState<string>('');
  const [selectedTool, setSelectedTool] = useState<string>('');

  const { todayData, summaryData, hosts, isLoading, isFetching, isError, error, refetch } = useDashboard({
    tool: selectedTool || undefined,
    host: selectedHost || undefined,
    autoRefresh: true,
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
        <div className="dashboard-controls d-flex gap-2">
          <Select options={hostOptions} value={selectedHost} onChange={setSelectedHost} size="sm" />
          <Select options={toolOptions} value={selectedTool} onChange={setSelectedTool} size="sm" />
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
      <section className="dashboard-section">
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
              {todayData.length > 0 ? (
                <TokenDistributionChart
                  data={{
                    input: todayData.reduce((sum, item) => sum + item.input_tokens, 0),
                    output: todayData.reduce((sum, item) => sum + item.output_tokens, 0),
                  }}
                  height={300}
                />
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

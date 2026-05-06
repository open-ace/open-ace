/**
 * Report Component - Personal usage report for regular users
 */

import React, { useState, useMemo } from 'react';
import { useMyUsage } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error,
  EmptyState,
  TokenTrendChart,
  TokenDistributionChart,
} from '@/components/common';
import { formatTokens, formatDate, formatToolName } from '@/utils';

export const Report: React.FC = () => {
  const language = useLanguage();
  const [startDate, setStartDate] = useState<string>(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    return formatDate(date, 'iso');
  });
  const [endDate, setEndDate] = useState<string>(() => formatDate(new Date(), 'iso'));

  const {
    data: report,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useMyUsage(startDate, endDate);

  // Prepare chart data - by tool
  const chartData = useMemo(() => {
    if (!report?.daily_usage) return [];

    // Aggregate by date and tool
    const aggregated: Record<string, { date: string; tool: string; tokens: number }> = {};

    report.daily_usage.forEach((item) => {
      const toolName = item.tool_name ?? 'unknown';
      const key = `${item.date}-${toolName}`;
      if (!aggregated[key]) {
        aggregated[key] = { date: item.date, tool: toolName, tokens: 0 };
      }
      aggregated[key].tokens += item.tokens_used ?? 0;
    });

    return Object.values(aggregated).sort((a, b) => a.date.localeCompare(b.date));
  }, [report?.daily_usage]);

  // Prepare token distribution data - by tool
  const tokenDistributionData = useMemo(() => {
    if (!report?.daily_usage) return [];

    // Aggregate by tool
    const aggregated: Record<string, { tool: string; tokens: number }> = {};

    report.daily_usage.forEach((item) => {
      const toolName = item.tool_name ?? 'unknown';
      if (!aggregated[toolName]) {
        aggregated[toolName] = { tool: toolName, tokens: 0 };
      }
      aggregated[toolName].tokens += item.tokens_used ?? 0;
    });

    return Object.values(aggregated).sort((a, b) => b.tokens - a.tokens);
  }, [report?.daily_usage]);

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message ?? t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="report">
      {/* Header */}
      <div className="report-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('myUsageReport', language)}</h2>
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

      {/* Date Range Filter */}
      <Card className="mb-4">
        <div className="row g-3">
          <div className="col-md-4">
            <label className="form-label">{t('startDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div className="col-md-4">
            <label className="form-label">{t('endDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
          <div className="col-md-4 d-flex align-items-end">
            <Button variant="primary" onClick={() => refetch()}>
              {t('apply', language)}
            </Button>
          </div>
        </div>
      </Card>

      {/* User Info */}
      {report && (
        <div className="mb-4">
          <p className="text-muted mb-1">
            {t('user', language)}: <strong>{report.username}</strong>
          </p>
          <p className="text-muted mb-0">
            {t('dateRange', language)}: {report.date_range.start} - {report.date_range.end}
          </p>
        </div>
      )}

      {/* Stats Overview */}
      {report && (
        <div className="row g-3 mb-4">
          <div className="col-md-3">
            <StatCard
              label={t('totalTokens', language)}
              value={formatTokens(report.totals.tokens)}
              icon={<i className="bi bi-cpu fs-4" />}
              variant="primary"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('inputTokens', language)}
              value={formatTokens(report.totals.input_tokens)}
              icon={<i className="bi bi-box-arrow-in-left fs-4" />}
              variant="info"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('outputTokens', language)}
              value={formatTokens(report.totals.output_tokens)}
              icon={<i className="bi bi-box-arrow-right fs-4" />}
              variant="success"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('totalRequests', language)}
              value={report.totals.requests.toLocaleString()}
              icon={<i className="bi bi-arrow-repeat fs-4" />}
              variant="warning"
            />
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="row">
        <div className="col-md-8 mb-4">
          <Card title={t('tokenTrend', language)}>
            {chartData.length > 0 ? (
              <TokenTrendChart data={chartData} startDate={startDate} endDate={endDate} height={300} />
            ) : (
              <EmptyState icon="bi-graph-up" title={t('noData', language)} />
            )}
          </Card>
        </div>
        <div className="col-md-4 mb-4">
          <Card title={t('tokenDistribution', language)}>
            {tokenDistributionData.length > 0 ? (
              <TokenDistributionChart data={tokenDistributionData} height={300} />
            ) : (
              <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>

      {/* Daily Usage Table */}
      {report && report.daily_usage.length > 0 && (
        <Card title={t('dailyUsage', language)}>
          <div className="table-responsive">
            <table className="table table-sm table-hover">
              <thead>
                <tr>
                  <th>{t('tableDate', language)}</th>
                  <th>{t('tableTool', language)}</th>
                  <th className="text-end">{t('tableInput', language)}</th>
                  <th className="text-end">{t('tableOutput', language)}</th>
                  <th className="text-end">{t('tableTokens', language)}</th>
                  <th className="text-end">{t('tableRequests', language)}</th>
                </tr>
              </thead>
              <tbody>
                {report.daily_usage.slice(0, 20).map((item, index) => (
                  <tr key={`${item.date}-${item.tool_name ?? index}`}>
                    <td>{item.date}</td>
                    <td>{formatToolName(item.tool_name ?? '')}</td>
                    <td className="text-end">{formatTokens(item.input_tokens)}</td>
                    <td className="text-end">{formatTokens(item.output_tokens)}</td>
                    <td className="text-end">{formatTokens(item.tokens_used)}</td>
                    <td className="text-end">{item.request_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.daily_usage.length > 20 && (
            <p className="text-muted small mt-2">
              {t('showingFirst', language)} 20 {t('of', language)} {report.daily_usage.length}{' '}
              {t('records', language)}
            </p>
          )}
        </Card>
      )}
    </div>
  );
};

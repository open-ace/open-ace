/**
 * Analysis Component - Data analysis and visualization with tabs
 */

import React, { useState, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
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
  SimpleTabs,
} from '@/components/common';
import { formatTokens, formatDate } from '@/utils';
import { useKeyMetrics, useDailyHourlyUsage, useToolComparison } from '@/hooks';
import { ConversationHistory } from './ConversationHistory';

export const Analysis: React.FC = () => {
  const language = useLanguage();
  const [groupBy, setGroupBy] = useState<'day' | 'week' | 'month'>('day');

  // Date range (default: last 30 days)
  const dateRange = useMemo(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    return {
      start: formatDate(start, 'iso'),
      end: formatDate(end, 'iso'),
    };
  }, []);

  const [startDate, setStartDate] = useState(dateRange.start);
  const [endDate, setEndDate] = useState(dateRange.end);

  // Fetch data
  const {
    data: keyMetrics,
    isLoading: metricsLoading,
    isFetching: metricsFetching,
    isError: metricsError,
    error: metricsErrorMsg,
    refetch: refetchMetrics,
  } = useKeyMetrics(startDate, endDate);
  const { data: dailyHourly, isLoading: dailyLoading } = useDailyHourlyUsage(startDate, endDate);
  const { data: toolComparison, isLoading: toolsLoading } = useToolComparison(startDate, endDate);

  // Group by options
  const groupByOptions = useMemo(
    () => [
      { value: 'day', label: 'Daily' },
      { value: 'week', label: 'Weekly' },
      { value: 'month', label: 'Monthly' },
    ],
    []
  );

  const isLoading = metricsLoading || dailyLoading || toolsLoading;

  // Prepare chart data
  const dailyTrend = dailyHourly?.daily || [];
  const tools = toolComparison?.tools || [];

  // Overview tab content
  const OverviewContent = (
    <div className="analysis-overview">
      {/* Date Range */}
      <div className="mb-4">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('startDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
        </div>
      </div>

      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : metricsError ? (
        <Error
          message={metricsErrorMsg?.message || t('error', language)}
          onRetry={() => refetchMetrics()}
        />
      ) : (
        <>
          {/* Stats Overview */}
          <div className="row g-3 mb-4">
            <div className="col-md-3">
              <StatCard
                label={t('totalSessions', language)}
                value={keyMetrics?.total_sessions?.toLocaleString() || '0'}
                icon={<i className="bi bi-collection fs-4" />}
                variant="primary"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('totalMessages', language)}
                value={keyMetrics?.total_messages?.toLocaleString() || '0'}
                icon={<i className="bi bi-chat-dots fs-4" />}
                variant="success"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('totalTokens', language)}
                value={formatTokens(keyMetrics?.total_tokens || 0)}
                icon={<i className="bi bi-cpu fs-4" />}
                variant="info"
              />
            </div>
            <div className="col-md-3">
              <StatCard
                label={t('avgTokensPerSession', language)}
                value={formatTokens(keyMetrics?.avg_tokens_per_session || 0)}
                icon={<i className="bi bi-graph-up fs-4" />}
                variant="warning"
              />
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

          {/* Detailed Stats */}
          <div className="row">
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
                  </tbody>
                </table>
              </Card>
            </div>
            <div className="col-md-6">
              <Card title={t('tokenDistribution', language)}>
                {keyMetrics?.top_tools && keyMetrics.top_tools.length > 0 ? (
                  <PieChart
                    labels={keyMetrics.top_tools.map((t) => t.tool)}
                    data={keyMetrics.top_tools.map((t) => t.count)}
                    height={200}
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
        <div className="d-flex gap-2">
          <Select
            options={groupByOptions}
            value={groupBy}
            onChange={(value) => setGroupBy(value as 'day' | 'week' | 'month')}
            size="sm"
          />
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
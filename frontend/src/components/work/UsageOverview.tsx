/**
 * UsageOverview Component - User's own usage overview in Work mode
 *
 * Features:
 * - Daily and monthly token quota and usage
 * - Daily and monthly request quota and usage
 * - Usage trend chart
 * - Quota warnings
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  Loading,
  Error,
  Progress,
  Badge,
} from '@/components/common';
import { LineChart } from '@/components/common/Charts';
import { requestApi, type QuotaStatusResponse, type UserUsageResponse } from '@/api/request';
import { formatNumber, formatTokens } from '@/utils';

export const UsageOverview: React.FC = () => {
  const language = useLanguage();

  // State
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatusResponse | null>(null);
  const [usageData, setUsageData] = useState<UserUsageResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch data
  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [status, usage] = await Promise.all([
        requestApi.getQuotaStatus(),
        requestApi.getMyUsage(),
      ]);

      setQuotaStatus(status);
      setUsageData(usage);
    } catch (err) {
      const error = err as Error;
      const errorMessage = error?.message || 'Failed to fetch usage data';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Calculate usage percentage
  const getPercentage = (used: number, limit: number | null): number => {
    if (!limit || limit === 0) return 0;
    return Math.min((used / limit) * 100, 100);
  };

  // Get progress variant based on percentage
  const getVariant = (percentage: number): 'success' | 'warning' | 'danger' => {
    if (percentage >= 95) return 'danger';
    if (percentage >= 80) return 'warning';
    return 'success';
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchData} />;
  }

  // Prepare token trend chart data
  const tokenTrendChartData = usageData?.usage.trend
    ? {
        labels: usageData.usage.trend.map((d) => d.date),
        datasets: [
          {
            label: t('tokens', language),
            data: usageData.usage.trend.map((d) => d.tokens),
            borderColor: 'rgba(16, 185, 129, 1)',
            backgroundColor: 'rgba(16, 185, 129, 0.2)',
            fill: false,
            tension: 0.2,
          },
        ],
      }
    : null;

  // Prepare request trend chart data
  const trendChartData = usageData?.usage.trend
    ? {
        labels: usageData.usage.trend.map((d) => d.date),
        datasets: [
          {
            label: t('requests', language),
            data: usageData.usage.trend.map((d) => d.requests),
            borderColor: 'rgba(37, 99, 235, 1)',
            backgroundColor: 'rgba(37, 99, 235, 0.2)',
            fill: false,
            tension: 0.2,
          },
        ],
      }
    : null;

  return (
    <div className="usage-overview">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('myUsage', language)}</h2>
        <Button variant="outline-primary" size="sm" onClick={fetchData}>
          <i className="bi bi-arrow-clockwise me-1" />
          {t('refresh', language)}
        </Button>
      </div>

      {/* Quota Warning Banner */}
      {quotaStatus?.over_quota.any && (
        <Card className="mb-4 border-danger">
          <div className="d-flex align-items-center text-danger">
            <i className="bi bi-exclamation-triangle-fill fs-4 me-3" />
            <div>
              <strong>{t('quotaExceeded', language)}</strong>
              <p className="mb-0 small">
                {quotaStatus.over_quota.daily_request && t('dailyRequestQuotaExceeded', language)}
                {quotaStatus.over_quota.monthly_request && t('monthlyRequestQuotaExceeded', language)}
                {quotaStatus.over_quota.daily_token && t('dailyTokenQuotaExceeded', language)}
                {quotaStatus.over_quota.monthly_token && t('monthlyTokenQuotaExceeded', language)}
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Daily Stats */}
      <h5 className="mb-3">{t('daily', language)}</h5>
      <div className="row g-3 mb-4">
        {/* Daily Token */}
        <div className="col-md-6">
          <Card>
            <div className="d-flex justify-content-between align-items-center mb-2">
              <span className="fw-medium">{t('tokenUsage', language)}</span>
              {quotaStatus?.over_quota.daily_token && (
                <Badge variant="danger">{t('exceeded', language)}</Badge>
              )}
            </div>
            <div className="mb-2">
              <div className="d-flex justify-content-between mb-1">
                <small className="text-muted">
                  {formatTokens(quotaStatus?.daily.tokens.used ?? 0)}
                </small>
                <small className="text-muted">
                  / {quotaStatus?.daily.tokens.limit
                    ? formatTokens(quotaStatus.daily.tokens.limit)
                    : '∞'}
                </small>
              </div>
              <Progress
                value={getPercentage(
                  quotaStatus?.daily.tokens.used ?? 0,
                  quotaStatus?.daily.tokens.limit ?? null
                )}
                variant={getVariant(
                  getPercentage(
                    quotaStatus?.daily.tokens.used ?? 0,
                    quotaStatus?.daily.tokens.limit ?? null
                  )
                )}
              />
            </div>
          </Card>
        </div>

        {/* Daily Request */}
        <div className="col-md-6">
          <Card>
            <div className="d-flex justify-content-between align-items-center mb-2">
              <span className="fw-medium">{t('requests', language)}</span>
              {quotaStatus?.over_quota.daily_request && (
                <Badge variant="danger">{t('exceeded', language)}</Badge>
              )}
            </div>
            <div className="mb-2">
              <div className="d-flex justify-content-between mb-1">
                <small className="text-muted">
                  {formatNumber(quotaStatus?.daily.requests.used ?? 0)}
                </small>
                <small className="text-muted">
                  / {quotaStatus?.daily.requests.limit
                    ? formatNumber(quotaStatus.daily.requests.limit)
                    : '∞'}
                </small>
              </div>
              <Progress
                value={getPercentage(
                  quotaStatus?.daily.requests.used ?? 0,
                  quotaStatus?.daily.requests.limit ?? null
                )}
                variant={getVariant(
                  getPercentage(
                    quotaStatus?.daily.requests.used ?? 0,
                    quotaStatus?.daily.requests.limit ?? null
                  )
                )}
              />
            </div>
          </Card>
        </div>
      </div>

      {/* Monthly Stats */}
      <h5 className="mb-3">{t('monthly', language)}</h5>
      <div className="row g-3 mb-4">
        {/* Monthly Token */}
        <div className="col-md-6">
          <Card>
            <div className="d-flex justify-content-between align-items-center mb-2">
              <span className="fw-medium">{t('tokenUsage', language)}</span>
              {quotaStatus?.over_quota.monthly_token && (
                <Badge variant="danger">{t('exceeded', language)}</Badge>
              )}
            </div>
            <div className="mb-2">
              <div className="d-flex justify-content-between mb-1">
                <small className="text-muted">
                  {formatTokens(quotaStatus?.monthly.tokens.used ?? 0)}
                </small>
                <small className="text-muted">
                  / {quotaStatus?.monthly.tokens.limit
                    ? formatTokens(quotaStatus.monthly.tokens.limit)
                    : '∞'}
                </small>
              </div>
              <Progress
                value={getPercentage(
                  quotaStatus?.monthly.tokens.used ?? 0,
                  quotaStatus?.monthly.tokens.limit ?? null
                )}
                variant={getVariant(
                  getPercentage(
                    quotaStatus?.monthly.tokens.used ?? 0,
                    quotaStatus?.monthly.tokens.limit ?? null
                  )
                )}
              />
            </div>
          </Card>
        </div>

        {/* Monthly Request */}
        <div className="col-md-6">
          <Card>
            <div className="d-flex justify-content-between align-items-center mb-2">
              <span className="fw-medium">{t('requests', language)}</span>
              {quotaStatus?.over_quota.monthly_request && (
                <Badge variant="danger">{t('exceeded', language)}</Badge>
              )}
            </div>
            <div className="mb-2">
              <div className="d-flex justify-content-between mb-1">
                <small className="text-muted">
                  {formatNumber(quotaStatus?.monthly.requests.used ?? 0)}
                </small>
                <small className="text-muted">
                  / {quotaStatus?.monthly.requests.limit
                    ? formatNumber(quotaStatus.monthly.requests.limit)
                    : '∞'}
                </small>
              </div>
              <Progress
                value={getPercentage(
                  quotaStatus?.monthly.requests.used ?? 0,
                  quotaStatus?.monthly.requests.limit ?? null
                )}
                variant={getVariant(
                  getPercentage(
                    quotaStatus?.monthly.requests.used ?? 0,
                    quotaStatus?.monthly.requests.limit ?? null
                  )
                )}
              />
            </div>
          </Card>
        </div>
      </div>

      {/* Token Trend Chart */}
      {tokenTrendChartData && tokenTrendChartData.labels.length > 0 && (
        <Card title={t('tokenTrend', language)} className="mb-4">
          <LineChart
            labels={tokenTrendChartData.labels}
            datasets={tokenTrendChartData.datasets}
            height={250}
            unit="M"
          />
        </Card>
      )}

      {/* Request Trend Chart */}
      {trendChartData && trendChartData.labels.length > 0 && (
        <Card title={t('requestTrend', language)} className="mb-4">
          <LineChart
            labels={trendChartData.labels}
            datasets={trendChartData.datasets}
            height={250}
          />
        </Card>
      )}

      {/* Help Text */}
      <Card className="bg-light">
        <div className="d-flex align-items-start">
          <i className="bi bi-info-circle text-muted me-2" />
          <div className="small text-muted">
            <p className="mb-1">
              <strong>{t('quotaLimitsHelp', language)}</strong>
            </p>
            <p className="mb-0">
              {t('quotaLimitsHelpDesc', language)}
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
};
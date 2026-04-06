/**
 * UsageOverview Component - User's own usage overview in Work mode
 *
 * Features:
 * - Daily and monthly token quota and usage
 * - Daily and monthly request quota and usage
 * - Usage trend chart
 * - Quota warnings
 * - Optimized loading with skeleton screens
 * - Lazy chart loading for better performance
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
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

  // Chart refs for lazy loading
  const tokenChartRef = useRef<HTMLDivElement>(null);
  const requestChartRef = useRef<HTMLDivElement>(null);
  const [loadTokenChart, setLoadTokenChart] = useState(false);
  const [loadRequestChart, setLoadRequestChart] = useState(false);

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
      // Charts can start loading after data is fetched
      setTimeout(() => {
        setLoadTokenChart(true);
        setLoadRequestChart(true);
      }, 100);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Lazy load charts using Intersection Observer
  useEffect(() => {
    const observerOptions = {
      root: null,
      rootMargin: '100px',
      threshold: 0.1,
    };

    const observerCallback: IntersectionObserverCallback = (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          if (entry.target === tokenChartRef.current) {
            setLoadTokenChart(true);
          } else if (entry.target === requestChartRef.current) {
            setLoadRequestChart(true);
          }
        }
      });
    };

    const observer = new IntersectionObserver(observerCallback, observerOptions);

    if (tokenChartRef.current) {
      observer.observe(tokenChartRef.current);
    }
    if (requestChartRef.current) {
      observer.observe(requestChartRef.current);
    }

    return () => observer.disconnect();
  }, []);

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
    return (
      <div className="usage-overview">
        {/* Skeleton Loading State */}
        <div className="d-flex justify-content-between align-items-center mb-4">
          <div className="skeleton-title" style={{ width: '200px', height: '32px', background: '#e0e0e0', borderRadius: '4px' }} />
          <div className="skeleton-button" style={{ width: '100px', height: '36px', background: '#e0e0e0', borderRadius: '4px' }} />
        </div>
        
        {/* Skeleton Cards */}
        <h5 className="mb-3" style={{ visibility: 'hidden' }}>{t('daily', language)}</h5>
        <div className="row g-3 mb-4">
          <div className="col-md-6">
            <Card>
              <div style={{ width: '120px', height: '20px', background: '#e0e0e0', borderRadius: '4px', marginBottom: '12px' }} />
              <div style={{ width: '100%', height: '8px', background: '#f0f0f0', borderRadius: '4px' }} />
            </Card>
          </div>
          <div className="col-md-6">
            <Card>
              <div style={{ width: '120px', height: '20px', background: '#e0e0e0', borderRadius: '4px', marginBottom: '12px' }} />
              <div style={{ width: '100%', height: '8px', background: '#f0f0f0', borderRadius: '4px' }} />
            </Card>
          </div>
        </div>
        
        <h5 className="mb-3" style={{ visibility: 'hidden' }}>{t('monthly', language)}</h5>
        <div className="row g-3 mb-4">
          <div className="col-md-6">
            <Card>
              <div style={{ width: '120px', height: '20px', background: '#e0e0e0', borderRadius: '4px', marginBottom: '12px' }} />
              <div style={{ width: '100%', height: '8px', background: '#f0f0f0', borderRadius: '4px' }} />
            </Card>
          </div>
          <div className="col-md-6">
            <Card>
              <div style={{ width: '120px', height: '20px', background: '#e0e0e0', borderRadius: '4px', marginBottom: '12px' }} />
              <div style={{ width: '100%', height: '8px', background: '#f0f0f0', borderRadius: '4px' }} />
            </Card>
          </div>
        </div>
        
        {/* Skeleton Charts */}
        <Card className="mb-4">
          <div style={{ width: '150px', height: '24px', background: '#e0e0e0', borderRadius: '4px', marginBottom: '16px' }} />
          <div style={{ width: '100%', height: '250px', background: '#f5f5f5', borderRadius: '4px' }} />
        </Card>
      </div>
    );
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
          <div ref={tokenChartRef}>
            {loadTokenChart ? (
              <LineChart
                labels={tokenTrendChartData.labels}
                datasets={tokenTrendChartData.datasets}
                height={250}
                unit="M"
              />
            ) : (
              <div style={{ width: '100%', height: '250px', background: '#f5f5f5', borderRadius: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Loading size="sm" />
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Request Trend Chart */}
      {trendChartData && trendChartData.labels.length > 0 && (
        <Card title={t('requestTrend', language)} className="mb-4">
          <div ref={requestChartRef}>
            {loadRequestChart ? (
              <LineChart
                labels={trendChartData.labels}
                datasets={trendChartData.datasets}
                height={250}
              />
            ) : (
              <div style={{ width: '100%', height: '250px', background: '#f5f5f5', borderRadius: '4px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <Loading size="sm" />
              </div>
            )}
          </div>
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
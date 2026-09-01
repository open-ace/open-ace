/**
 * UsageForecast Component - Usage forecast page
 *
 * Features:
 * - Forecast key metrics cards
 * - Historical + forecast trend chart
 * - Day selector (7/14/30 days)
 * - Method explanation card
 * - Quality metrics display (replaces deprecated confidence)
 *
 * Error handling priority:
 * 1. If forecast API fails: Show Error component
 * 2. If historical API fails but forecast succeeds: Show forecast data with warning
 * 3. If both succeed: Show combined data
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, StatCard, Error, EmptyState, LineChart, Loading } from '@/components/common';
import { formatTokens } from '@/utils';
import { useUsageForecast, useDailyHourlyUsage } from '@/hooks';

/**
 * Quality level configuration
 */
type QualityLevel = 'quality' | 'satisfactory' | 'fair' | 'poor' | 'unavailable';

interface QualityConfig {
  label: string;
  variant: 'success' | 'primary' | 'warning' | 'danger' | 'secondary';
  color: string;
}

const QUALITY_CONFIG: Record<QualityLevel, QualityConfig> = {
  quality: { label: 'qualityLevelQuality', variant: 'success', color: '#198754' },
  satisfactory: { label: 'qualityLevelSatisfactory', variant: 'primary', color: '#0d6efd' },
  fair: { label: 'qualityLevelFair', variant: 'warning', color: '#ffc107' },
  poor: { label: 'qualityLevelPoor', variant: 'danger', color: '#dc3545' },
  unavailable: { label: 'qualityLevelUnavailable', variant: 'secondary', color: '#6c757d' },
};

/**
 * Format WAPE value as percentage string
 */
function formatWape(wape: number | null | undefined): string {
  if (wape === null || wape === undefined) return '--';
  return `${Math.round(wape * 100)}%`;
}

export const UsageForecast: React.FC = () => {
  const language = useLanguage();

  // Day selector state (7/14/30)
  const [forecastDays, setForecastDays] = useState<7 | 14 | 30>(7);

  // Fetch forecast data
  const {
    data: forecastData,
    isLoading: forecastLoading,
    isError: forecastError,
    error: forecastErrorMsg,
  } = useUsageForecast(forecastDays);

  // Fetch historical data (past 7 days, matching backend moving average window)
  const historicalDateRange = useMemo(() => {
    const end = new Date();
    const start = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    return {
      start: start.toISOString().split('T')[0],
      end: end.toISOString().split('T')[0],
    };
  }, []);

  const {
    data: historicalData,
    isLoading: historicalLoading,
    isError: historicalError,
  } = useDailyHourlyUsage(historicalDateRange.start, historicalDateRange.end);

  // Combined loading state
  const isLoading = forecastLoading || historicalLoading;

  // Check if forecast is available
  const forecastAvailable = forecastData?.forecast_available === true;

  // Get quality configuration
  const qualityLevel = (forecastData?.quality_level as QualityLevel) || 'unavailable';
  const qualityConfig = QUALITY_CONFIG[qualityLevel] || QUALITY_CONFIG.unavailable;

  // Chart colors
  const historicalColor = 'rgba(13, 110, 253, 1)';
  const forecastColor = 'rgba(25, 135, 84, 1)';

  // Prepare chart data: historical + forecast
  const chartData = useMemo(() => {
    if (!forecastAvailable || !forecastData) {
      return null;
    }

    const historical = historicalData?.daily ?? [];
    const forecastDates = forecastData.forecast_dates ?? [];
    const dailyForecast = forecastData.daily_forecast;

    // Build labels: historical dates + forecast dates
    const historicalDates = historical.map((d) => d.date);
    const labels = [...historicalDates, ...forecastDates];

    // Build historical data array
    const historicalTokens = historical.map((d) => d.tokens);

    // Build forecast data array (repeated daily forecast for each forecast day)
    const forecastTokens = forecastDates.map(() => dailyForecast.tokens);

    // Build datasets
    const datasets = [
      {
        label: t('historicalData', language),
        data: [...historicalTokens, ...Array(forecastDates.length).fill(null)],
        borderColor: historicalColor,
        tension: 0.4,
        pointRadius: 3,
      },
      {
        label: t('predictedData', language),
        data: [...Array(historicalDates.length).fill(null), ...forecastTokens],
        borderColor: forecastColor,
        tension: 0.4,
        pointRadius: 3,
        // Note: borderDash is supported by Chart.js but may need LineChart component extension
        // For now, we use different color to distinguish
      },
    ];

    return { labels, datasets };
  }, [forecastData, historicalData, forecastAvailable, language, historicalColor, forecastColor]);

  // Loading state
  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  // Forecast API error
  if (forecastError) {
    return <Error message={forecastErrorMsg?.message ?? t('error', language)} />;
  }

  // Forecast not available
  if (!forecastAvailable) {
    return (
      <div className="usage-forecast">
        <div className="page-header mb-4">
          <h2>{t('usageForecast', language)}</h2>
        </div>
        <Card>
          <EmptyState
            icon="bi-graph-up-arrow"
            title={t('insufficientHistoricalData', language)}
            description={forecastData && 'reason' in forecastData ? forecastData.reason : undefined}
          />
        </Card>
      </div>
    );
  }

  // Get quality metrics
  const qualityMetrics = forecastData.quality_metrics;
  const backtestWape = qualityMetrics?.backtest_wape;
  const adjustedWape = qualityMetrics?.horizon_adjusted_wape;
  const showAdjustedWape = forecastDays > 7 && adjustedWape !== null && adjustedWape !== undefined;

  // Forecast available, render content
  return (
    <div className="usage-forecast">
      {/* Header */}
      <div className="page-header mb-4">
        <h2>{t('usageForecast', language)}</h2>
      </div>

      {/* Day Selector */}
      <Card className="mb-4">
        <div className="d-flex align-items-center gap-3">
          <label className="form-label mb-0">{t('forecastDays', language)}:</label>
          <div className="btn-group" role="group">
            {[7, 14, 30].map((days) => (
              <button
                key={days}
                type="button"
                className={cn('btn', forecastDays === days ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => setForecastDays(days as 7 | 14 | 30)}
              >
                {days} {t('days', language)}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* Historical data unavailable warning */}
      {historicalError && (
        <div className="alert alert-warning mb-4" role="alert">
          <i className="bi bi-exclamation-triangle me-2" />
          {t('historicalDataUnavailable', language)}
        </div>
      )}

      {/* Key Metrics Cards */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('forecastTotalTokens', language)}
            value={formatTokens(forecastData.total_forecast.tokens)}
            icon={<i className="bi bi-cpu fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('forecastTotalRequests', language)}
            value={forecastData.total_forecast.requests.toLocaleString()}
            icon={<i className="bi bi-chat-dots fs-4" />}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('forecastDailyAvg', language)}
            value={formatTokens(forecastData.daily_forecast.tokens)}
            icon={<i className="bi bi-graph-up fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('forecastQuality', language)}
            value={t(qualityConfig.label, language)}
            icon={<i className="bi bi-check-circle fs-4" />}
            variant={qualityConfig.variant}
            helpTooltip={forecastData.quality_description}
          />
        </div>
      </div>

      {/* Quality Details Card */}
      {qualityMetrics && (
        <div className="row mb-4">
          <div className="col-12">
            <Card title={t('qualityMetrics', language)}>
              <div className="row">
                <div className="col-md-4">
                  <div className="d-flex align-items-center mb-2">
                    <i className="bi bi-activity me-2 text-primary" />
                    <span className="text-muted">{t('backtestError', language)}:</span>
                  </div>
                  <span className="fs-5 fw-bold">{formatWape(backtestWape)}</span>
                </div>
                {showAdjustedWape && (
                  <div className="col-md-4">
                    <div className="d-flex align-items-center mb-2">
                      <i className="bi bi-graph-up-arrow me-2 text-warning" />
                      <span className="text-muted">{t('adjustedError', language)}:</span>
                    </div>
                    <span className="fs-5 fw-bold">{formatWape(adjustedWape)}</span>
                    <small className="text-muted d-block">
                      {t('forDays', language).replace('{days}', String(forecastDays))}
                    </small>
                  </div>
                )}
                <div className="col-md-4">
                  <div className="d-flex align-items-center mb-2">
                    <i className="bi bi-calendar3 me-2 text-info" />
                    <span className="text-muted">{t('sampleDays', language)}:</span>
                  </div>
                  <span className="fs-5 fw-bold">{qualityMetrics.sample_days}</span>
                  {qualityMetrics.missing_days > 0 && (
                    <small className="text-muted d-block">
                      {t('missingDaysCount', language).replace(
                        '{count}',
                        String(qualityMetrics.missing_days)
                      )}
                    </small>
                  )}
                </div>
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* Trend Chart */}
      <div className="row mb-4">
        <div className="col-12">
          <Card title={t('tokenTrend', language)}>
            {chartData ? (
              <LineChart
                labels={chartData.labels}
                datasets={chartData.datasets}
                height={300}
                unit="M"
              />
            ) : (
              <EmptyState icon="bi-graph-up" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>

      {/* Method Explanation Card */}
      <div className="row mb-4">
        <div className="col-12">
          <Card title={t('forecastMethod', language)}>
            <div className="mb-3">
              <strong>{t('movingAverage', language)}</strong>
            </div>
            <p className="text-muted mb-0">{t('forecastExplanation', language)}</p>
          </Card>
        </div>
      </div>
    </div>
  );
};

/**
 * TenantUsageChart Component - Usage trend charts for tenant detail page
 *
 * Issue #3197: Displays Token and Request usage trends over 30 days
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { LineChart } from '@/components/common';
import type { TenantUsage } from '@/api';
import { TOKEN_QUOTA_MULTIPLIER } from '@/constants/quota';

interface TenantUsageChartProps {
  usage: TenantUsage[];
  loading?: boolean;
}

export const TenantUsageChart: React.FC<TenantUsageChartProps> = ({ usage, loading }) => {
  const language = useLanguage();

  if (loading) {
    return (
      <div className="text-center py-4">
        <div className="spinner-border spinner-border-sm" role="status">
          <span className="visually-hidden">Loading...</span>
        </div>
      </div>
    );
  }

  if (!usage || usage.length === 0) {
    return (
      <div className="text-center py-4 text-muted">
        <i className="bi bi-graph-up fs-3 d-block mb-2" />
        <span>{t('tenantNoData', language)}</span>
      </div>
    );
  }

  // Sort usage by date (ascending) for the chart
  const sortedUsage = [...usage].sort((a, b) => a.date.localeCompare(b.date));

  // Extract labels (dates) and data
  const labels = sortedUsage.map((u) => u.date.slice(5)); // Show MM-DD format

  // Token data (convert to millions for better readability)
  const tokenData = sortedUsage.map((u) => u.tokens / TOKEN_QUOTA_MULTIPLIER);

  // Request data
  const requestData = sortedUsage.map((u) => u.requests);

  return (
    <div className="tenant-usage-chart">
      <div className="row g-4">
        <div className="col-md-6">
          <h6 className="text-muted mb-3">{t('tenantTokenUsage', language)}</h6>
          <LineChart
            labels={labels}
            datasets={[
              {
                label: t('tenantTokenUsage', language),
                data: tokenData,
                fill: true,
                tension: 0.4,
              },
            ]}
            height={250}
            unit="M"
            showLegend={false}
          />
        </div>
        <div className="col-md-6">
          <h6 className="text-muted mb-3">{t('tenantRequestUsage', language)}</h6>
          <LineChart
            labels={labels}
            datasets={[
              {
                label: t('tenantRequestUsage', language),
                data: requestData,
                fill: true,
                tension: 0.4,
              },
            ]}
            height={250}
            showLegend={false}
          />
        </div>
      </div>
    </div>
  );
};

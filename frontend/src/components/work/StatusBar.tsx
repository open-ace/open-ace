/**
 * StatusBar Component - Status bar for Work Mode
 *
 * Features:
 * - Today's token usage and quota
 * - Today's request usage and quota
 */

import React, { useState, useEffect } from 'react';
import { cn, formatTokens } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface StatusBarProps {
  tokensUsed?: number;
  tokensLimit?: number;
  requestsUsed?: number;
  requestsLimit?: number;
}

interface WorkspaceStatus {
  tokens_used: number;
  tokens_limit: number;
  requests_used: number;
  requests_limit: number;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  tokensUsed: propTokensUsed,
  tokensLimit: propTokensLimit,
  requestsUsed: propRequestsUsed,
  requestsLimit: propRequestsLimit,
}) => {
  const language = useLanguage();
  const [status, setStatus] = useState<WorkspaceStatus | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch workspace status from API
  useEffect(() => {
    // If props are provided, use them
    if (propTokensUsed !== undefined) {
      setStatus({
        tokens_used: propTokensUsed ?? 0,
        tokens_limit: propTokensLimit ?? 100000,
        requests_used: propRequestsUsed ?? 0,
        requests_limit: propRequestsLimit ?? 1000,
      });
      return;
    }

    // Otherwise fetch from API
    const fetchStatus = async () => {
      setLoading(true);
      try {
        const response = await fetch('/api/workspace/status', {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setStatus(data);
        }
      } catch (error) {
        console.error('Failed to fetch workspace status:', error);
        // Set default values
        setStatus({
          tokens_used: 0,
          tokens_limit: 100000,
          requests_used: 0,
          requests_limit: 1000,
        });
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();

    // Refresh every 30 seconds
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [propTokensUsed, propTokensLimit, propRequestsUsed, propRequestsLimit]);

  // Calculate usage percentages
  const tokenPercentage = status
    ? Math.min(100, (status.tokens_used / status.tokens_limit) * 100)
    : 0;
  const requestPercentage = status
    ? Math.min(100, (status.requests_used / status.requests_limit) * 100)
    : 0;

  // Determine progress bar color based on percentage
  const getProgressVariant = (percentage: number) => {
    if (percentage >= 90) return 'danger';
    if (percentage >= 70) return 'warning';
    return 'success';
  };

  // Format number with commas
  const formatNumber = (num: number) => {
    return num.toLocaleString();
  };

  return (
    <footer className="work-status-bar">
      <div className="status-center">
        <span className="status-item status-token-usage" title={t('todayTokenUsage', language)}>
          <i className="bi bi-bar-chart" />
          <span className="status-label">{t('token', language)}:</span>
          <span className="status-tokens">
            {formatTokens(status?.tokens_used ?? 0)} / {formatTokens(status?.tokens_limit ?? 100000)}
          </span>
          <div className="status-progress">
            <div
              className={cn('status-progress-bar', `bg-${getProgressVariant(tokenPercentage)}`)}
              style={{ width: `${tokenPercentage}%` }}
            />
          </div>
        </span>
        <span className="status-separator">|</span>
        <span className="status-item status-request-usage" title={t('todayRequestUsage', language)}>
          <i className="bi bi-arrow-up-circle" />
          <span className="status-label">{t('request', language)}:</span>
          <span className="status-requests">
            {formatNumber(status?.requests_used ?? 0)} / {formatNumber(status?.requests_limit ?? 1000)}
          </span>
          <div className="status-progress">
            <div
              className={cn('status-progress-bar', `bg-${getProgressVariant(requestPercentage)}`)}
              style={{ width: `${requestPercentage}%` }}
            />
          </div>
        </span>
        {loading && (
          <span className="status-loading">
            <i className="bi bi-arrow-repeat spin" />
          </span>
        )}
      </div>
    </footer>
  );
};

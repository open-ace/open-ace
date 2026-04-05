/**
 * StatusBar Component - Status bar for Work Mode
 *
 * Features:
 * - Display current model info
 * - Today's token usage and quota
 * - Today's request usage and quota
 * - Response latency
 */

import React, { useState, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface StatusBarProps {
  model?: string;
  tokensUsed?: number;
  tokensLimit?: number;
  requestsUsed?: number;
  requestsLimit?: number;
  latency?: number;
}

interface WorkspaceStatus {
  model: string;
  tokens_used: number;
  tokens_limit: number;
  requests_used: number;
  requests_limit: number;
  latency: number;
  last_request: string | null;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  model: propModel,
  tokensUsed: propTokensUsed,
  tokensLimit: propTokensLimit,
  requestsUsed: propRequestsUsed,
  requestsLimit: propRequestsLimit,
  latency: propLatency,
}) => {
  const language = useLanguage();
  const [status, setStatus] = useState<WorkspaceStatus | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch workspace status from API
  useEffect(() => {
    // If props are provided, use them
    if (propModel || propTokensUsed !== undefined) {
      setStatus({
        model: propModel ?? 'GPT-4',
        tokens_used: propTokensUsed ?? 0,
        tokens_limit: propTokensLimit ?? 100000,
        requests_used: propRequestsUsed ?? 0,
        requests_limit: propRequestsLimit ?? 1000,
        latency: propLatency ?? 0,
        last_request: null,
      });
      return;
    }

    // Otherwise fetch from API
    const fetchStatus = async () => {
      setLoading(true);
      try {
        const response = await fetch('/api/workspace/status');
        if (response.ok) {
          const data = await response.json();
          setStatus(data);
        }
      } catch (error) {
        console.error('Failed to fetch workspace status:', error);
        // Set default values
        setStatus({
          model: 'GPT-4',
          tokens_used: 0,
          tokens_limit: 100000,
          requests_used: 0,
          requests_limit: 1000,
          latency: 0,
          last_request: null,
        });
      } finally {
        setLoading(false);
      }
    };

    fetchStatus();

    // Refresh every 30 seconds
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [propModel, propTokensUsed, propTokensLimit, propRequestsUsed, propRequestsLimit, propLatency]);

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

  // Format latency
  const formatLatency = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  // Format number with commas
  const formatNumber = (num: number) => {
    return num.toLocaleString();
  };

  return (
    <footer className="work-status-bar">
      <div className="status-left">
        <span className="status-item" title={t('currentModel', language)}>
          <i className="bi bi-cpu" />
          <span className="status-model">{status?.model ?? 'GPT-4'}</span>
        </span>
      </div>

      <div className="status-center">
        <span className="status-item status-token-usage" title={t('todayTokenUsage', language)}>
          <i className="bi bi-lightning" />
          <span className="status-label">Token:</span>
          <span className="status-tokens">
            {formatNumber(status?.tokens_used ?? 0)} / {formatNumber(status?.tokens_limit ?? 100000)}
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
          <i className="bi bi-arrow-repeat" />
          <span className="status-label">Request:</span>
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
      </div>

      <div className="status-right">
        <span className="status-item" title={t('responseLatency', language)}>
          <i className="bi bi-clock" />
          <span className="status-latency">{formatLatency(status?.latency ?? 0)}</span>
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

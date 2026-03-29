/**
 * StatusBar Component - Status bar for Work Mode
 *
 * Features:
 * - Display current model info
 * - Token usage display
 * - Response latency
 */

import React, { useState, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { formatTokens } from '@/utils';

interface StatusBarProps {
  model?: string;
  tokensUsed?: number;
  tokensLimit?: number;
  latency?: number;
}

interface WorkspaceStatus {
  model: string;
  tokens_used: number;
  tokens_limit: number;
  latency: number;
  last_request: string | null;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  model: propModel,
  tokensUsed: propTokensUsed,
  tokensLimit: propTokensLimit,
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
        tokens_limit: propTokensLimit ?? 10000,
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
          tokens_limit: 10000,
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
  }, [propModel, propTokensUsed, propTokensLimit, propLatency]);

  // Calculate token usage percentage
  const tokenPercentage = status
    ? Math.min(100, (status.tokens_used / status.tokens_limit) * 100)
    : 0;

  // Determine progress bar color
  const getProgressVariant = () => {
    if (tokenPercentage >= 90) return 'danger';
    if (tokenPercentage >= 70) return 'warning';
    return 'success';
  };

  // Format latency
  const formatLatency = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
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
        <span className="status-item status-token-usage" title={t('tokenUsage', language)}>
          <i className="bi bi-lightning" />
          <span className="status-tokens">
            {formatTokens(status?.tokens_used ?? 0)} / {formatTokens(status?.tokens_limit ?? 10000)}
          </span>
          <div className="status-progress">
            <div
              className={cn('status-progress-bar', `bg-${getProgressVariant()}`)}
              style={{ width: `${tokenPercentage}%` }}
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

/**
 * PageRefreshControl Component - Page-level refresh control UI
 *
 * Features:
 * - Auto refresh toggle
 * - Interval selector
 * - Manual refresh button with debounce
 * - Refresh status indicators (last/next refresh time)
 * - Error indicator
 * - Compact mode for mobile
 */

import React, { useState, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/hooks';
import { t, type Language } from '@/i18n';
import type { UsePageRefreshReturn } from '@/hooks/usePageRefresh';

/**
 * Refresh interval options
 */
export interface IntervalOption {
  value: number; // milliseconds
  label: string; // i18n key
}

/**
 * Standard interval options
 */
export const STANDARD_INTERVALS: IntervalOption[] = [
  { value: 30000, label: '30s' }, // 30 seconds
  { value: 60000, label: '1min' }, // 1 minute
  { value: 300000, label: '5min' }, // 5 minutes
];

/**
 * PageRefreshControl props
 */
export interface PageRefreshControlProps {
  refresh: UsePageRefreshReturn;
  intervalOptions?: IntervalOption[];
  showAutoRefreshToggle?: boolean;
  showIntervalSelector?: boolean;
  compact?: boolean;
  position?: 'top-right' | 'bottom-right' | 'inline';
  showLastRefreshTime?: boolean;
  showNextRefreshTime?: boolean;
  showErrorIndicator?: boolean;
  className?: string;
}

/**
 * Format timestamp to readable string
 */
function formatRefreshTime(timestamp: number | null, language: Language): string {
  if (!timestamp) return '';

  const now = Date.now();
  const diff = Math.abs(now - timestamp);

  if (diff < 60000) {
    // Less than 1 minute
    const seconds = Math.floor(diff / 1000);
    return `${seconds} ${t('secondsAgo', language)}`;
  } else if (diff < 3600000) {
    // Less than 1 hour
    const minutes = Math.floor(diff / 60000);
    return `${minutes} ${t('minutesAgo', language)}`;
  } else {
    // More than 1 hour
    const date = new Date(timestamp);
    return date.toLocaleTimeString(language === 'zh' ? 'zh-CN' : 'en-US', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}

/**
 * Calculate next refresh countdown
 */
function getNextRefreshCountdown(nextRefreshTime: number | null): string {
  if (!nextRefreshTime) return '';

  const now = Date.now();
  const diff = nextRefreshTime - now;

  if (diff <= 0) return '...';

  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

/**
 * PageRefreshControl Component
 */
export const PageRefreshControl: React.FC<PageRefreshControlProps> = ({
  refresh,
  intervalOptions = STANDARD_INTERVALS,
  showAutoRefreshToggle = true,
  showIntervalSelector = true,
  compact = false,
  position = 'top-right',
  showLastRefreshTime = true,
  showNextRefreshTime = false,
  showErrorIndicator = true,
  className,
}) => {
  const language = useLanguage();
  const [isButtonDisabled, setIsButtonDisabled] = useState(false);
  const [countdown, setCountdown] = useState('');

  const {
    isRefreshing,
    refresh: handleRefresh,
    autoRefresh,
    setAutoRefresh,
    interval,
    setInterval,
    lastRefreshTime,
    nextRefreshTime,
    error,
    errorCount,
  } = refresh;

  // Disable button after click for debounce
  useEffect(() => {
    if (isRefreshing) {
      setIsButtonDisabled(true);
      return;
    }
    // Re-enable after debounce time
    const timeout = setTimeout(() => {
      setIsButtonDisabled(false);
    }, 1000);
    return () => clearTimeout(timeout);
  }, [isRefreshing]);

  // Update countdown for next refresh
  useEffect(() => {
    if (!showNextRefreshTime || !autoRefresh || !nextRefreshTime) {
      setCountdown('');
      return;
    }

    const updateCountdown = () => {
      setCountdown(getNextRefreshCountdown(nextRefreshTime));
    };

    updateCountdown();
    const intervalId = window.setInterval(updateCountdown, 1000);

    return () => window.clearInterval(intervalId);
  }, [showNextRefreshTime, autoRefresh, nextRefreshTime]);

  // Build tooltip content
  const buildTooltip = () => {
    const parts: string[] = [];

    if (showLastRefreshTime && lastRefreshTime) {
      parts.push(`${t('lastRefresh', language)}: ${formatRefreshTime(lastRefreshTime, language)}`);
    }

    if (showNextRefreshTime && autoRefresh && nextRefreshTime) {
      parts.push(`${t('nextRefresh', language)}: ${countdown}`);
    }

    return parts.join('\n');
  };

  // Position styles
  const positionStyles = {
    'top-right': 'position-absolute top-0 end-0',
    'bottom-right': 'position-absolute bottom-0 end-0',
    inline: '',
  };

  // Error indicator
  const hasError = showErrorIndicator && error && errorCount > 0;

  // Check if dropdown has any content (auto refresh toggle or interval selector)
  const hasDropdownContent = showAutoRefreshToggle || (showIntervalSelector && autoRefresh);

  if (compact) {
    // Compact mode: just icon buttons with dropdown
    return (
      <div
        className={cn(
          'page-refresh-control-compact',
          'd-flex align-items-center gap-1',
          positionStyles[position],
          className
        )}
      >
        {/* Error indicator */}
        {hasError && (
          <i
            className="bi bi-exclamation-triangle-fill text-warning"
            title={error}
            data-testid="refresh-error-indicator"
          />
        )}

        {/* Dropdown for settings - only render if there's content */}
        {hasDropdownContent ? (
          <div className="dropdown">
            <button
              className="btn btn-link btn-sm p-0"
              type="button"
              data-bs-toggle="dropdown"
              aria-expanded="false"
              title={buildTooltip()}
              data-testid="dropdown-toggle"
            >
              <i className={cn('bi', autoRefresh ? 'bi-clock' : 'bi-clock-history')} />
            </button>
            <ul className="dropdown-menu dropdown-menu-end">
              {showAutoRefreshToggle && (
                <li>
                  <div className="dropdown-item-text">
                    <div className="form-check form-switch">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        id={`${position}-auto-refresh`}
                        checked={autoRefresh}
                        onChange={(e) => setAutoRefresh(e.target.checked)}
                      />
                      <label className="form-check-label" htmlFor={`${position}-auto-refresh`}>
                        {t('autoRefresh', language)}
                      </label>
                    </div>
                  </div>
                </li>
              )}
              {showIntervalSelector && autoRefresh && (
                <>
                  <li>
                    <hr className="dropdown-divider" />
                  </li>
                  <li>
                    <span className="dropdown-item-text small text-muted">
                      {t('refreshInterval', language)}
                    </span>
                  </li>
                  {intervalOptions.map((option) => (
                    <li key={option.value}>
                      <button
                        className={cn('dropdown-item', interval === option.value && 'active')}
                        onClick={() => setInterval(option.value)}
                      >
                        {t(option.label, language)}
                      </button>
                    </li>
                  ))}
                </>
              )}
            </ul>
          </div>
        ) : (
          /* Static clock icon when no dropdown content - shows last refresh time tooltip */
          <i
            className={cn('bi bi-clock-history', 'text-muted')}
            title={buildTooltip()}
            data-testid="refresh-clock-icon"
          />
        )}

        {/* Manual refresh button */}
        <button
          className="btn btn-link btn-sm p-0"
          onClick={handleRefresh}
          disabled={isButtonDisabled || isRefreshing}
          title={t('refresh', language)}
          data-testid="manual-refresh-button"
        >
          <i
            className={cn(
              'bi bi-arrow-clockwise',
              isRefreshing && 'spinner-border spinner-border-sm'
            )}
          />
        </button>
      </div>
    );
  }

  // Full mode: all controls visible
  return (
    <div
      className={cn(
        'page-refresh-control',
        'd-flex align-items-center gap-2',
        positionStyles[position],
        className
      )}
      data-testid="page-refresh-control"
    >
      {/* Error indicator */}
      {hasError && (
        <div className="d-flex align-items-center gap-1 text-warning">
          <i
            className="bi bi-exclamation-triangle-fill"
            title={error}
            data-testid="refresh-error-indicator"
          />
          <small className="text-warning">{errorCount}</small>
        </div>
      )}

      {/* Auto refresh toggle */}
      {showAutoRefreshToggle && (
        <div className="form-check form-switch d-flex align-items-center mb-0">
          <input
            className="form-check-input"
            type="checkbox"
            id="page-auto-refresh"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          <label className="form-check-label small" htmlFor="page-auto-refresh">
            {t('autoRefresh', language)}
          </label>
        </div>
      )}

      {/* Interval selector */}
      {showIntervalSelector && autoRefresh && (
        <select
          className="form-select form-select-sm"
          value={interval}
          onChange={(e) => setInterval(Number(e.target.value))}
          aria-label={t('refreshInterval', language)}
          data-testid="interval-selector"
        >
          {intervalOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {t(option.label, language)}
            </option>
          ))}
        </select>
      )}

      {/* Last refresh time */}
      {showLastRefreshTime && lastRefreshTime && (
        <small className="text-muted" title={buildTooltip()}>
          {formatRefreshTime(lastRefreshTime, language)}
        </small>
      )}

      {/* Next refresh countdown */}
      {showNextRefreshTime && autoRefresh && countdown && (
        <small className="text-muted">{countdown}</small>
      )}

      {/* Manual refresh button */}
      <button
        className="btn btn-outline-secondary btn-sm"
        onClick={handleRefresh}
        disabled={isButtonDisabled || isRefreshing}
        title={buildTooltip()}
        data-testid="manual-refresh-button"
      >
        {isRefreshing ? (
          <>
            <span
              className="spinner-border spinner-border-sm me-1"
              role="status"
              aria-hidden="true"
            />
            {t('refreshing', language) || 'Refreshing...'}
          </>
        ) : (
          <>
            <i className="bi bi-arrow-clockwise me-1" />
            {t('refresh', language)}
          </>
        )}
      </button>
    </div>
  );
};

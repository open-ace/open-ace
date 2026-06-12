/**
 * usePageRefresh Hook - Page-level refresh management
 *
 * Features:
 * - Query key filtering with exact/prefix matching and exclusion
 * - Request deduplication
 * - Refresh throttling/debouncing
 * - Concurrent refresh handling
 * - Error handling and fallback
 * - Integration with pageRefreshStore
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePageRefreshStore } from '@/store';
import {
  matchQueryKey,
  hashQueryKey,
  type QueryKeyMatcherConfig,
} from '@/utils';

/**
 * UsePageRefreshOptions - Hook configuration
 */
export interface UsePageRefreshOptions {
  page: string; // Page identifier for state management
  refreshKey: QueryKeyMatcherConfig; // Query key matching config
  interval?: number; // Auto refresh interval (ms), 0 = disabled
  enabled?: boolean; // Enable auto refresh
  onRefresh?: () => void; // Callback on refresh
  onError?: (error: Error) => void; // Callback on error
  maxRetries?: number; // Max retry count (default: 3)
  fallbackInterval?: number; // Fallback interval on error (default: 5 min)
  dedupeTime?: number; // Deduplication window (default: 1000ms)
}

/**
 * UsePageRefreshReturn - Hook return value
 */
export interface UsePageRefreshReturn {
  isRefreshing: boolean;
  refresh: () => Promise<void>;
  autoRefresh: boolean;
  setAutoRefresh: (enabled: boolean) => void;
  interval: number;
  setInterval: (ms: number) => void;
  lastRefreshTime: number | null;
  nextRefreshTime: number | null;
  error: string | null;
  errorCount: number;
}

/**
 * Default options
 */
const DEFAULT_OPTIONS = {
  interval: 60000, // 1 minute
  enabled: false,
  maxRetries: 3,
  fallbackInterval: 300000, // 5 minutes
  dedupeTime: 1000, // 1 second
};

/**
 * usePageRefresh Hook
 */
export function usePageRefresh(options: UsePageRefreshOptions): UsePageRefreshReturn {
  const {
    page,
    refreshKey,
    interval = DEFAULT_OPTIONS.interval,
    enabled = DEFAULT_OPTIONS.enabled,
    onRefresh,
    onError,
    maxRetries = DEFAULT_OPTIONS.maxRetries,
    fallbackInterval = DEFAULT_OPTIONS.fallbackInterval,
    dedupeTime = DEFAULT_OPTIONS.dedupeTime,
  } = options;

  const queryClient = useQueryClient();

  // Store actions
  const setConfig = usePageRefreshStore((state) => state.setConfig);
  const recordRefresh = usePageRefreshStore((state) => state.recordRefresh);
  const globalPaused = usePageRefreshStore((state) => state.globalPaused);

  // Local state
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Refs for deduplication and timing
  const lastRefreshTimestampsRef = useRef<Map<string, number>>(new Map());
  const refreshTimeoutRef = useRef<number | null>(null);
  const debounceTimeoutRef = useRef<number | null>(null);
  const refreshQueueRef = useRef<Array<() => Promise<void>>>([]);

  // Get config from store
  const config = usePageRefreshStore((state) => state.configs[page]);

  // Use store values if available, otherwise use options
  const autoRefresh = config?.autoRefresh ?? enabled;
  const refreshInterval = config?.interval ?? interval;
  const lastRefreshTime = config?.lastRefreshTime ?? null;
  const nextRefreshTime = config?.nextRefreshTime ?? null;
  const errorCount = config?.errorCount ?? 0;

  /**
   * Check if a refresh should be deduplicated
   */
  const shouldDedupe = useCallback((key: string): boolean => {
    const lastTime = lastRefreshTimestampsRef.current.get(key);
    if (!lastTime) return false;
    return Date.now() - lastTime < dedupeTime;
  }, [dedupeTime]);

  /**
   * Record refresh timestamp for deduplication
   */
  const recordDedupeTimestamp = useCallback((key: string) => {
    lastRefreshTimestampsRef.current.set(key, Date.now());
  }, []);

  /**
   * Get all matching query keys from cache
   */
  const getMatchingQueryKeys = useCallback(() => {
    const cache = queryClient.getQueryCache();
    const allQueries = cache.getAll();
    return allQueries
      .map((query) => query.queryKey)
      .filter((key) => matchQueryKey(key, refreshKey));
  }, [queryClient, refreshKey]);

  /**
   * Execute refresh with error handling
   */
  const executeRefresh = useCallback(async () => {
    // Check if global paused
    if (globalPaused) {
      return;
    }

    // Check if already refreshing
    if (isRefreshing) {
      // Add to queue
      refreshQueueRef.current.push(executeRefresh);
      return;
    }

    setIsRefreshing(true);
    setError(null);

    try {
      const matchingKeys = getMatchingQueryKeys();

      // Deduplicate requests
      const keysToRefresh = matchingKeys.filter((key) => {
        const hash = hashQueryKey(key);
        return !shouldDedupe(hash);
      });

      if (keysToRefresh.length > 0) {
        // Invalidate matching queries
        await Promise.all(
          keysToRefresh.map((key) => {
            const hash = hashQueryKey(key);
            recordDedupeTimestamp(hash);
            return queryClient.invalidateQueries({ queryKey: key });
          })
        );

        // Record success
        recordRefresh(page, true);
        onRefresh?.();
      }
    } catch (err) {
      const errorObj = err instanceof Error ? err : new Error('Refresh failed');
      setError(errorObj.message);

      // Record failure
      recordRefresh(page, false, errorObj.message);

      // Call error callback
      onError?.(errorObj);

      // Check if should use fallback interval
      const currentErrorCount = usePageRefreshStore.getState().configs[page]?.errorCount ?? 0;
      if (currentErrorCount >= maxRetries) {
        // Switch to fallback interval
        setConfig(page, { interval: fallbackInterval });
      }
    } finally {
      setIsRefreshing(false);

      // Process queued refreshes
      if (refreshQueueRef.current.length > 0) {
        const nextRefresh = refreshQueueRef.current.shift();
        if (nextRefresh) {
          // Delay to prevent immediate execution
          window.setTimeout(nextRefresh, 100);
        }
      }
    }
  }, [
    globalPaused,
    isRefreshing,
    getMatchingQueryKeys,
    shouldDedupe,
    recordDedupeTimestamp,
    queryClient,
    page,
    recordRefresh,
    onRefresh,
    onError,
    maxRetries,
    fallbackInterval,
    setConfig,
  ]);

  /**
   * Manual refresh with debounce
   */
  const refresh = useCallback(async () => {
    // Clear any pending debounce
    if (debounceTimeoutRef.current) {
      window.clearTimeout(debounceTimeoutRef.current);
    }

    // Debounce for 1-2 seconds
    await new Promise<void>((resolve) => {
      debounceTimeoutRef.current = window.setTimeout(() => {
        resolve();
      }, 1000);
    });

    await executeRefresh();
  }, [executeRefresh]);

  /**
   * Set auto refresh state
   */
  const setAutoRefresh = useCallback((enabled: boolean) => {
    setConfig(page, {
      autoRefresh: enabled,
      interval: refreshInterval,
    });
  }, [page, refreshInterval, setConfig]);

  /**
   * Set refresh interval
   */
  const setInterval = useCallback((ms: number) => {
    setConfig(page, {
      autoRefresh: autoRefresh,
      interval: ms,
    });
  }, [page, autoRefresh, setConfig]);

  /**
   * Auto refresh effect
   */
  useEffect(() => {
    // Clear existing timeout
    if (refreshTimeoutRef.current) {
      window.clearTimeout(refreshTimeoutRef.current);
      refreshTimeoutRef.current = null;
    }

    // Skip if not enabled or global paused
    if (!autoRefresh || globalPaused || refreshInterval === 0) {
      return;
    }

    // Use fallback interval if error count exceeds max
    const effectiveInterval =
      errorCount >= maxRetries ? fallbackInterval : refreshInterval;

    // Schedule next refresh
    refreshTimeoutRef.current = window.setTimeout(() => {
      executeRefresh();
    }, effectiveInterval);

    // Cleanup on unmount or when conditions change
    return () => {
      if (refreshTimeoutRef.current) {
        window.clearTimeout(refreshTimeoutRef.current);
        refreshTimeoutRef.current = null;
      }
    };
  }, [
    autoRefresh,
    globalPaused,
    refreshInterval,
    errorCount,
    maxRetries,
    fallbackInterval,
    executeRefresh,
  ]);

  /**
   * Cleanup on unmount
   */
  useEffect(() => {
    return () => {
      // Clear all timeouts
      if (refreshTimeoutRef.current) {
        window.clearTimeout(refreshTimeoutRef.current);
      }
      if (debounceTimeoutRef.current) {
        window.clearTimeout(debounceTimeoutRef.current);
      }
      // Clear queue
      refreshQueueRef.current = [];
    };
  }, []);

  /**
   * Initialize config in store on mount
   */
  useEffect(() => {
    const existingConfig = usePageRefreshStore.getState().configs[page];
    if (!existingConfig) {
      setConfig(page, {
        autoRefresh: enabled,
        interval: interval,
      });
    }
  }, [page, enabled, interval, setConfig]);

  return {
    isRefreshing,
    refresh,
    autoRefresh,
    setAutoRefresh,
    interval: refreshInterval,
    setInterval,
    lastRefreshTime,
    nextRefreshTime,
    error,
    errorCount,
  };
}
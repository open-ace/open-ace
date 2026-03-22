/**
 * usePerformance Hook
 *
 * React hook for performance monitoring in components.
 */

import { useEffect, useCallback, useRef } from 'react';
import {
  startMeasure,
  trackRender,
  trackApiCall,
  getPerformanceSummary,
  onMetric,
} from '@/utils/performance';
import type { PerformanceMetric } from '@/utils/performance';

/**
 * Hook to track component render performance
 */
export function useRenderPerformance(componentName: string, enabled = true) {
  const renderStart = useRef<number>(0);

  useEffect(() => {
    if (!enabled) return;

    renderStart.current = performance.now();

    return () => {
      const duration = performance.now() - renderStart.current;
      trackRender(componentName, duration);
    };
  });
}

/**
 * Hook to track custom performance measurements
 */
export function usePerformance() {
  const startMeasureCallback = useCallback((name: string) => {
    return startMeasure(name);
  }, []);

  const trackApi = useCallback((endpoint: string, duration: number, success: boolean) => {
    trackApiCall(endpoint, duration, success);
  }, []);

  const getSummary = useCallback(() => {
    return getPerformanceSummary();
  }, []);

  return {
    startMeasure: startMeasureCallback,
    trackApi,
    getSummary,
  };
}

/**
 * Hook to subscribe to performance metrics
 */
export function usePerformanceMetrics(callback: (metric: PerformanceMetric) => void) {
  useEffect(() => {
    return onMetric(callback);
  }, [callback]);
}

/**
 * Hook to measure async operation performance
 */
export function useAsyncPerformance(operationName: string): {
  measure: <R>(promise: Promise<R>) => Promise<R>;
} {
  const measure = useCallback(
    async <R>(promise: Promise<R>): Promise<R> => {
      const endMeasure = startMeasure(operationName);
      try {
        const result = await promise;
        endMeasure();
        return result;
      } catch (error) {
        endMeasure();
        throw error;
      }
    },
    [operationName]
  );

  return { measure };
}

export default usePerformance;

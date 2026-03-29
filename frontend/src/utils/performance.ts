/**
 * Performance Monitoring Module
 *
 * Tracks Web Vitals and custom performance metrics for Open ACE frontend.
 */

// Types
export interface PerformanceMetric {
  name: string;
  value: number;
  rating: 'good' | 'needs-improvement' | 'poor';
  timestamp: number;
  id: string;
}

// Web Vitals thresholds (based on Google's recommendations)
const THRESHOLDS = {
  // Core Web Vitals
  LCP: { good: 2500, poor: 4000 }, // Largest Contentful Paint
  FID: { good: 100, poor: 300 }, // First Input Delay
  CLS: { good: 0.1, poor: 0.25 }, // Cumulative Layout Shift
  INP: { good: 200, poor: 500 }, // Interaction to Next Paint

  // Other metrics
  FCP: { good: 1800, poor: 3000 }, // First Contentful Paint
  TTFB: { good: 800, poor: 1800 }, // Time to First Byte
  SI: { good: 3400, poor: 5800 }, // Speed Index
  TTI: { good: 3800, poor: 7300 }, // Time to Interactive
};

// Rating helper
function getRating(name: string, value: number): 'good' | 'needs-improvement' | 'poor' {
  const threshold = THRESHOLDS[name as keyof typeof THRESHOLDS];
  if (!threshold) return 'good';

  if (value <= threshold.good) return 'good';
  if (value <= threshold.poor) return 'needs-improvement';
  return 'poor';
}

// Generate unique ID
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

// Store metrics
const metrics: PerformanceMetric[] = [];

// Callbacks for metric reporting
type MetricCallback = (metric: PerformanceMetric) => void;
const callbacks: MetricCallback[] = [];

/**
 * Report a metric to all registered callbacks
 */
function reportMetric(metric: PerformanceMetric): void {
  metrics.push(metric);
  callbacks.forEach((callback) => callback(metric));
}

/**
 * Register a callback for metric reporting
 */
export function onMetric(callback: MetricCallback): () => void {
  callbacks.push(callback);
  return () => {
    const index = callbacks.indexOf(callback);
    if (index > -1) callbacks.splice(index, 1);
  };
}

/**
 * Get all collected metrics
 */
export function getMetrics(): PerformanceMetric[] {
  return [...metrics];
}

/**
 * Clear all collected metrics
 */
export function clearMetrics(): void {
  metrics.length = 0;
}

/**
 * Observe Largest Contentful Paint (LCP)
 */
function observeLCP(): void {
  if (!('PerformanceObserver' in window)) return;

  try {
    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();
      const lastEntry = entries[entries.length - 1];

      if (lastEntry) {
        reportMetric({
          name: 'LCP',
          value: lastEntry.startTime,
          rating: getRating('LCP', lastEntry.startTime),
          timestamp: Date.now(),
          id: generateId(),
        });
      }
    });

    observer.observe({ type: 'largest-contentful-paint', buffered: true });
  } catch {
    // LCP not supported
  }
}

/**
 * Observe First Input Delay (FID)
 */
function observeFID(): void {
  if (!('PerformanceObserver' in window)) return;

  try {
    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();

      entries.forEach((entry: any) => {
        if (entry.processingStart) {
          const fid = entry.processingStart - entry.startTime;
          reportMetric({
            name: 'FID',
            value: fid,
            rating: getRating('FID', fid),
            timestamp: Date.now(),
            id: generateId(),
          });
        }
      });
    });

    observer.observe({ type: 'first-input', buffered: true });
  } catch {
    // FID not supported
  }
}

/**
 * Observe Cumulative Layout Shift (CLS)
 */
function observeCLS(): void {
  if (!('PerformanceObserver' in window)) return;

  let clsValue = 0;
  const clsEntries: any[] = [];

  try {
    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();

      entries.forEach((entry: any) => {
        if (!entry.hadRecentInput) {
          clsValue += entry.value;
          clsEntries.push(entry);
        }
      });
    });

    observer.observe({ type: 'layout-shift', buffered: true });

    // Report CLS on page hide
    const reportCLS = () => {
      if (clsValue > 0) {
        reportMetric({
          name: 'CLS',
          value: clsValue,
          rating: getRating('CLS', clsValue),
          timestamp: Date.now(),
          id: generateId(),
        });
      }
    };

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        reportCLS();
      }
    });

    window.addEventListener('pagehide', reportCLS);
  } catch {
    // CLS not supported
  }
}

/**
 * Observe Interaction to Next Paint (INP)
 */
function observeINP(): void {
  if (!('PerformanceObserver' in window)) return;

  try {
    let maxINP = 0;

    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();

      entries.forEach((entry: any) => {
        if (entry.interactionId) {
          const inp = entry.duration;
          if (inp > maxINP) {
            maxINP = inp;
          }
        }
      });
    });

    observer.observe({ type: 'event', buffered: true });

    // Report INP on page hide
    const reportINP = () => {
      if (maxINP > 0) {
        reportMetric({
          name: 'INP',
          value: maxINP,
          rating: getRating('INP', maxINP),
          timestamp: Date.now(),
          id: generateId(),
        });
      }
    };

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        reportINP();
      }
    });

    window.addEventListener('pagehide', reportINP);
  } catch {
    // INP not supported
  }
}

/**
 * Get First Contentful Paint (FCP)
 */
function getFCP(): void {
  if (!('performance' in window)) return;

  const paintEntries = window.performance.getEntriesByType('paint');
  const fcpEntry = paintEntries.find((entry) => entry.name === 'first-contentful-paint');

  if (fcpEntry) {
    reportMetric({
      name: 'FCP',
      value: fcpEntry.startTime,
      rating: getRating('FCP', fcpEntry.startTime),
      timestamp: Date.now(),
      id: generateId(),
    });
  }
}

/**
 * Get Time to First Byte (TTFB)
 */
function getTTFB(): void {
  if (!('performance' in window)) return;

  const navigationEntries = window.performance.getEntriesByType(
    'navigation'
  ) as PerformanceNavigationTiming[];
  const navigationEntry = navigationEntries[0];

  if (navigationEntry) {
    const ttfb = navigationEntry.responseStart - navigationEntry.requestStart;
    reportMetric({
      name: 'TTFB',
      value: ttfb,
      rating: getRating('TTFB', ttfb),
      timestamp: Date.now(),
      id: generateId(),
    });
  }
}

/**
 * Track custom timing
 */
export function trackTiming(name: string, duration: number): void {
  reportMetric({
    name,
    value: duration,
    rating: getRating(name, duration),
    timestamp: Date.now(),
    id: generateId(),
  });
}

/**
 * Start a timing measurement
 */
export function startMeasure(name: string): () => number {
  const start = window.performance.now();

  return () => {
    const duration = window.performance.now() - start;
    trackTiming(name, duration);
    return duration;
  };
}

/**
 * Track API call performance
 */
export function trackApiCall(endpoint: string, duration: number, success: boolean): void {
  reportMetric({
    name: `API_${endpoint.replace(/[^a-zA-Z0-9]/g, '_')}`,
    value: duration,
    rating: success ? 'good' : 'poor',
    timestamp: Date.now(),
    id: generateId(),
  });
}

/**
 * Track component render time
 */
export function trackRender(componentName: string, duration: number): void {
  reportMetric({
    name: `RENDER_${componentName}`,
    value: duration,
    rating: duration < 16 ? 'good' : duration < 100 ? 'needs-improvement' : 'poor',
    timestamp: Date.now(),
    id: generateId(),
  });
}

/**
 * Get performance summary
 */
export function getPerformanceSummary(): {
  metrics: PerformanceMetric[];
  summary: Record<string, { count: number; avg: number; min: number; max: number }>;
} {
  const summary: Record<string, { count: number; avg: number; min: number; max: number }> = {};

  metrics.forEach((metric) => {
    if (!summary[metric.name]) {
      summary[metric.name] = { count: 0, avg: 0, min: Infinity, max: -Infinity };
    }

    const s = summary[metric.name];
    s.count++;
    s.min = Math.min(s.min, metric.value);
    s.max = Math.max(s.max, metric.value);
    s.avg = (s.avg * (s.count - 1) + metric.value) / s.count;
  });

  return { metrics, summary };
}

/**
 * Initialize performance monitoring
 */
export function initPerformanceMonitoring(): void {
  // Wait for page load
  if (document.readyState === 'complete') {
    initObservers();
  } else {
    window.addEventListener('load', initObservers);
  }
}

function initObservers(): void {
  // Observe Core Web Vitals
  observeLCP();
  observeFID();
  observeCLS();
  observeINP();

  // Get other metrics
  getFCP();
  getTTFB();

  // Log metrics in development
  if (import.meta.env.DEV) {
    onMetric((metric) => {
      console.log(`[Performance] ${metric.name}: ${metric.value.toFixed(2)}ms (${metric.rating})`);
    });
  }
}

// Auto-initialize
if (typeof window !== 'undefined') {
  initPerformanceMonitoring();
}

export default {
  onMetric,
  getMetrics,
  clearMetrics,
  trackTiming,
  startMeasure,
  trackApiCall,
  trackRender,
  getPerformanceSummary,
  initPerformanceMonitoring,
};

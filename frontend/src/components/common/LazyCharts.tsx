/**
 * LazyCharts - Lazy-loaded chart components
 *
 * Uses React.lazy to dynamically import Chart.js (~60KB) only when needed.
 * This reduces the initial bundle size and improves page load performance.
 *
 * Usage:
 *   import { LazyLineChart, LazyBarChart } from '@/components/common/LazyCharts';
 *
 *   <LazyLineChart labels={...} datasets={...} />
 */

import React, { Suspense } from 'react';
import { Skeleton } from './Loading';
import type { Language } from '@/i18n';

// Loading fallback component
const ChartSkeleton: React.FC<{ height?: number }> = ({ height = 300 }) => (
  <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
    <Skeleton height={height - 40} width="100%" />
  </div>
);

// Type definitions for chart props
export interface LineChartProps {
  labels: string[];
  datasets: Array<{
    label: string;
    data: number[];
    borderColor?: string;
    backgroundColor?: string;
    fill?: boolean;
    tension?: number;
    pointRadius?: number;
    pointHoverRadius?: number;
  }>;
  title?: string;
  height?: number;
  showLegend?: boolean;
  className?: string;
  unit?: 'none' | 'K' | 'M' | 'B';
}

export interface BarChartProps {
  labels: string[];
  datasets: Array<{
    label: string;
    data: number[];
    backgroundColor?: string | string[];
    borderColor?: string | string[];
    borderWidth?: number;
  }>;
  title?: string;
  height?: number;
  showLegend?: boolean;
  horizontal?: boolean;
  stacked?: boolean;
  className?: string;
  unit?: 'none' | 'K' | 'M' | 'B';
  usernames?: string[];
  /**
   * Explicit per-bar tooltip titles. When provided, the tooltip title uses
   * these instead of the (possibly compact) axis label — e.g. full YYYY-MM-DD
   * dates on hover while the axis shows a short format. Optional; omitting it
   * keeps the default behavior so existing callers are unaffected.
   */
  tooltipLabels?: string[];
  /** Language for tooltip translation */
  language?: Language;
}

export interface PieChartProps {
  labels: string[];
  data: number[];
  backgroundColor?: string[];
  borderColor?: string;
  title?: string;
  height?: number;
  showLegend?: boolean;
  className?: string;
}

export interface DoughnutChartProps {
  labels: string[];
  data: number[];
  backgroundColor?: string[];
  borderColor?: string;
  title?: string;
  height?: number;
  showLegend?: boolean;
  cutout?: string;
  className?: string;
  /** Descriptions for each segment, displayed in tooltip */
  descriptions?: string[];
  /** Show percentage in tooltip */
  showPercentage?: boolean;
}

export interface TokenTrendChartProps {
  data: Array<{
    date: string;
    tool: string;
    tokens: number;
  }>;
  startDate?: string;
  endDate?: string;
  height?: number;
  className?: string;
}

export interface ToolUsageChartProps {
  data: Array<{
    tool: string;
    count: number;
  }>;
  height?: number;
  className?: string;
}

export interface TokenDistributionChartProps {
  data: Array<{
    tool: string;
    tokens: number;
  }>;
  height?: number;
  className?: string;
}

// Lazy load the entire Charts module
// This creates a separate chunk for Chart.js that is loaded on demand
const LazyLineChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.LineChart };
});

const LazyBarChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.BarChart };
});

const LazyPieChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.PieChart };
});

const LazyDoughnutChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.DoughnutChart };
});

const LazyTokenTrendChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.TokenTrendChart };
});

const LazyToolUsageChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.ToolUsageChart };
});

const LazyTokenDistributionChart = React.lazy(async () => {
  const module = await import('./Charts');
  return { default: module.TokenDistributionChart };
});

// Wrapper components with Suspense
export const LazyLineChartWithSuspense: React.FC<LineChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyLineChart {...props} />
  </Suspense>
);

export const LazyBarChartWithSuspense: React.FC<BarChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyBarChart {...props} />
  </Suspense>
);

export const LazyPieChartWithSuspense: React.FC<PieChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyPieChart {...props} />
  </Suspense>
);

export const LazyDoughnutChartWithSuspense: React.FC<DoughnutChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyDoughnutChart {...props} />
  </Suspense>
);

export const LazyTokenTrendChartWithSuspense: React.FC<TokenTrendChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyTokenTrendChart {...props} />
  </Suspense>
);

export const LazyToolUsageChartWithSuspense: React.FC<ToolUsageChartProps> = (props) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyToolUsageChart {...props} />
  </Suspense>
);

export const LazyTokenDistributionChartWithSuspense: React.FC<TokenDistributionChartProps> = (
  props
) => (
  <Suspense fallback={<ChartSkeleton height={props.height} />}>
    <LazyTokenDistributionChart {...props} />
  </Suspense>
);

// Export with shorter names for convenience
export {
  LazyLineChartWithSuspense as LazyLineChart,
  LazyBarChartWithSuspense as LazyBarChart,
  LazyPieChartWithSuspense as LazyPieChart,
  LazyDoughnutChartWithSuspense as LazyDoughnutChart,
  LazyTokenTrendChartWithSuspense as LazyTokenTrendChart,
  LazyToolUsageChartWithSuspense as LazyToolUsageChart,
  LazyTokenDistributionChartWithSuspense as LazyTokenDistributionChart,
};

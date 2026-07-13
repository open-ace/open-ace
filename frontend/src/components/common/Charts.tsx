/**
 * Charts Component - Chart.js integration with react-chartjs-2
 */

import React, { useMemo } from 'react';
import { COLORS, TOOL_COLORS, getToolColor } from './chartColors';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  type TooltipItem,
} from 'chart.js';
import { Line, Bar, Pie, Doughnut } from 'react-chartjs-2';
import { cn } from '@/utils';
import { t, type Language } from '@/i18n';
import { useTheme } from '@/store';

// Register Chart.js components
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  ArcElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

// Theme-aware chart colors (Issue #1630)
const getChartThemeColors = (theme: string) => {
  const isDark = theme === 'dark';
  return {
    text: isDark ? '#cbd5e1' : '#475569', // --text-secondary equivalent
    textMuted: isDark ? '#a1a1aa' : '#64748b', // --text-muted equivalent
    grid: isDark ? 'rgba(148, 163, 184, 0.1)' : 'rgba(15, 23, 42, 0.1)',
    tooltipBg: isDark ? '#1e293b' : '#ffffff',
    tooltipBorder: isDark ? '#334155' : '#e2e8f0',
    tooltipText: isDark ? '#f8fafc' : '#0f172a',
  };
};

// Default chart options factory (theme-aware)
const getDefaultOptions = (theme: string) => {
  const colors = getChartThemeColors(theme);
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          color: colors.text,
        },
      },
      title: {
        color: colors.text,
      },
      tooltip: {
        backgroundColor: colors.tooltipBg,
        titleColor: colors.tooltipText,
        bodyColor: colors.tooltipText,
        borderColor: colors.tooltipBorder,
        borderWidth: 1,
      },
    },
    scales: {
      x: {
        ticks: { color: colors.textMuted },
        grid: { color: colors.grid },
        title: { color: colors.text },
      },
      y: {
        ticks: { color: colors.textMuted },
        grid: { color: colors.grid },
        title: { color: colors.text },
      },
    },
  };
};

// Color palette
// Re-export for backward compatibility
export { COLORS, TOOL_COLORS, getToolColor };

const COLOR_ARRAY = [
  COLORS.primary,
  COLORS.success,
  COLORS.warning,
  COLORS.danger,
  COLORS.info,
  COLORS.purple,
  COLORS.cyan,
];

const COLOR_ARRAY_LIGHT = [
  COLORS.primaryLight,
  COLORS.successLight,
  COLORS.warningLight,
  COLORS.dangerLight,
  COLORS.infoLight,
  COLORS.purpleLight,
  COLORS.cyanLight,
];

// ===== Line Chart =====
interface LineChartProps {
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
  /** Format large numbers (e.g., 'M' for millions) */
  unit?: 'none' | 'K' | 'M' | 'B';
}

export const LineChart: React.FC<LineChartProps> = ({
  labels,
  datasets,
  title,
  height = 300,
  showLegend = true,
  className,
  unit = 'none',
}) => {
  const theme = useTheme();

  // Helper function to format values based on unit
  const formatValue = (value: number): number => {
    switch (unit) {
      case 'K':
        return value / 1_000;
      case 'M':
        return value / 1_000_000;
      case 'B':
        return value / 1_000_000_000;
      default:
        return value;
    }
  };

  // Check if we have only one data point
  const hasSinglePoint = labels.length === 1;

  const chartData = {
    labels,
    datasets: datasets.map((dataset, index) => ({
      ...dataset,
      data: dataset.data.map(formatValue),
      borderColor: dataset.borderColor ?? COLOR_ARRAY[index % COLOR_ARRAY.length],
      backgroundColor:
        dataset.backgroundColor ?? COLOR_ARRAY_LIGHT[index % COLOR_ARRAY_LIGHT.length],
      fill: dataset.fill ?? true,
      tension: dataset.tension ?? 0.4,
      // For single point, use pointRadius to make it visible
      pointRadius: hasSinglePoint ? 8 : dataset.pointRadius,
      pointHoverRadius: hasSinglePoint ? 10 : dataset.pointHoverRadius,
      // For single point, disable line display but show the point
      showLine: !hasSinglePoint,
    })),
  };

  // Calculate max value for y-axis
  const allValues = datasets.flatMap((d) => d.data.map(formatValue));
  const maxValue = Math.max(...allValues, 1);
  // Add 10% padding to y-axis max so the highest point doesn't go outside
  const yMax = unit !== 'none' ? Math.ceil(maxValue * 1.1) : undefined;

  const themeDefaults = getDefaultOptions(theme);
  const options = {
    ...themeDefaults,
    plugins: {
      ...themeDefaults.plugins,
      legend: {
        display: showLegend,
        position: 'top' as const,
        labels: { color: themeDefaults.plugins.legend.labels.color },
      },
      title: {
        display: !!title,
        text: title,
        color: themeDefaults.plugins.title.color,
      },
      tooltip: {
        ...themeDefaults.plugins.tooltip,
        callbacks: {
          label: (context: TooltipItem<'line'>) => {
            const label = context.dataset?.label ?? '';
            const value = context.parsed?.y;
            if (unit !== 'none') {
              return `${label}: ${value?.toFixed(2) ?? 0}${unit}`;
            }
            return `${label}: ${value?.toLocaleString() ?? 0}`;
          },
        },
      },
    },
    scales: {
      x: {
        ...themeDefaults.scales.x,
        // Center the label when there's only one data point
        offset: hasSinglePoint,
        ticks: {
          ...themeDefaults.scales.x.ticks,
          display: true,
        },
      },
      y: {
        ...themeDefaults.scales.y,
        beginAtZero: true,
        max: yMax,
        ticks:
          unit !== 'none'
            ? {
                ...themeDefaults.scales.y.ticks,
                stepSize: 1,
                callback: (value: string | number) => `${value}${unit}`,
              }
            : themeDefaults.scales.y.ticks,
        title:
          unit !== 'none'
            ? {
                display: true,
                text: `Tokens (${unit})`,
                color: themeDefaults.scales.y.title.color,
              }
            : undefined,
      },
    },
  };

  return (
    <div className={cn('chart-container', className)} style={{ height }}>
      <Line data={chartData} options={options} />
    </div>
  );
};

// ===== Bar Chart =====
interface BarChartProps {
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
  /** Format large numbers (e.g., 'M' for millions) */
  unit?: 'none' | 'K' | 'M' | 'B';
  /** User names for tooltip display (used when labels are ranking numbers) */
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

export const BarChart: React.FC<BarChartProps> = ({
  labels,
  datasets,
  title,
  height = 300,
  showLegend = true,
  horizontal = false,
  stacked = false,
  className,
  unit = 'none',
  usernames,
  tooltipLabels,
  language,
}) => {
  const theme = useTheme();

  // Helper function to format values based on unit
  const formatValue = (value: number): number => {
    switch (unit) {
      case 'K':
        return value / 1_000;
      case 'M':
        return value / 1_000_000;
      case 'B':
        return value / 1_000_000_000;
      default:
        return value;
    }
  };

  const chartData = {
    labels,
    datasets: datasets.map((dataset, index) => ({
      ...dataset,
      data: dataset.data.map(formatValue),
      backgroundColor:
        dataset.backgroundColor ?? COLOR_ARRAY_LIGHT[index % COLOR_ARRAY_LIGHT.length],
      borderColor: dataset.borderColor ?? COLOR_ARRAY[index % COLOR_ARRAY.length],
      borderWidth: dataset.borderWidth ?? 1,
    })),
  };

  // Calculate max value for y-axis
  const allValues = datasets.flatMap((d) => d.data.map(formatValue));
  const maxValue = Math.max(...allValues, 1);
  const yMax = unit !== 'none' ? Math.ceil(maxValue * 1.1) : undefined;

  const themeDefaults = getDefaultOptions(theme);
  const options = {
    ...themeDefaults,
    indexAxis: horizontal ? ('y' as const) : ('x' as const),
    plugins: {
      ...themeDefaults.plugins,
      legend: {
        display: showLegend,
        position: 'top' as const,
        labels: { color: themeDefaults.plugins.legend.labels.color },
      },
      title: {
        display: !!title,
        text: title,
        color: themeDefaults.plugins.title.color,
      },
      tooltip: {
        ...themeDefaults.plugins.tooltip,
        callbacks: {
          title: (context: TooltipItem<'bar'>[]) => {
            // For horizontal bar charts with usernames, show username as title
            if (horizontal && usernames && context[0]) {
              const dataIndex = context[0].dataIndex ?? 0;
              return usernames[dataIndex] ?? '';
            }
            // Prefer explicit tooltip labels (e.g. full YYYY-MM-DD dates) over
            // the compact axis label when both are available.
            if (tooltipLabels && context[0]) {
              const dataIndex = context[0].dataIndex ?? 0;
              return tooltipLabels[dataIndex] ?? '';
            }
            // Default: use label (ranking number or category)
            return context[0]?.label ?? '';
          },
          label: (context: TooltipItem<'bar'>) => {
            // For horizontal bar charts with usernames, show translated "Requests: value"
            if (horizontal && usernames) {
              const value = context.parsed?.x;
              return `${t('requests', language)}: ${value?.toLocaleString() ?? 0}`;
            }
            // Default format
            const label = context.dataset?.label ?? '';
            const value = context.parsed?.y;
            if (unit !== 'none') {
              return `${label}: ${value?.toFixed(2) ?? 0}${unit}`;
            }
            return `${label}: ${value?.toLocaleString() ?? 0}`;
          },
        },
      },
    },
    scales: {
      // For horizontal bar charts: X is value axis, Y is category axis
      // For vertical bar charts: X is category axis, Y is value axis
      x: horizontal
        ? {
            ...themeDefaults.scales.x,
            stacked,
            beginAtZero: true,
            max: yMax,
          }
        : { ...themeDefaults.scales.x, stacked },
      y: horizontal
        ? { ...themeDefaults.scales.y, stacked }
        : {
            ...themeDefaults.scales.y,
            stacked,
            beginAtZero: true,
            max: yMax,
            ticks:
              unit !== 'none'
                ? {
                    ...themeDefaults.scales.y.ticks,
                    stepSize: 1,
                    callback: (value: string | number) => `${value}${unit}`,
                  }
                : themeDefaults.scales.y.ticks,
            title:
              unit !== 'none'
                ? {
                    display: true,
                    text: `Tokens (${unit})`,
                    color: themeDefaults.scales.y.title.color,
                  }
                : undefined,
          },
    },
  };

  return (
    <div className={cn('chart-container', className)} style={{ height }}>
      <Bar data={chartData} options={options} />
    </div>
  );
};

// ===== Pie Chart =====
interface PieChartProps {
  labels: string[];
  data: number[];
  backgroundColor?: string[];
  borderColor?: string;
  title?: string;
  height?: number;
  showLegend?: boolean;
  className?: string;
}

export const PieChart: React.FC<PieChartProps> = ({
  labels,
  data,
  backgroundColor,
  borderColor = '#fff',
  title,
  height = 300,
  showLegend = true,
  className,
}) => {
  const theme = useTheme();
  const chartData = {
    labels,
    datasets: [
      {
        data,
        backgroundColor: backgroundColor ?? COLOR_ARRAY_LIGHT,
        borderColor,
        borderWidth: 2,
      },
    ],
  };

  const themeDefaults = getDefaultOptions(theme);
  const options = {
    ...themeDefaults,
    plugins: {
      ...themeDefaults.plugins,
      legend: {
        display: showLegend,
        position: 'right' as const,
        labels: { color: themeDefaults.plugins.legend.labels.color },
      },
      title: {
        display: !!title,
        text: title,
        color: themeDefaults.plugins.title.color,
      },
    },
  };

  return (
    <div className={cn('chart-container', className)} style={{ height }}>
      <Pie data={chartData} options={options} />
    </div>
  );
};

// ===== Doughnut Chart =====
interface DoughnutChartProps {
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

export const DoughnutChart: React.FC<DoughnutChartProps> = ({
  labels,
  data,
  backgroundColor,
  borderColor = '#fff',
  title,
  height = 300,
  showLegend = true,
  cutout = '60%',
  className,
  descriptions,
  showPercentage = false,
}) => {
  const theme = useTheme();

  // Calculate total for percentage
  const total = data.reduce((sum, val) => sum + val, 0);

  const chartData = {
    labels,
    datasets: [
      {
        data,
        backgroundColor: backgroundColor ?? COLOR_ARRAY_LIGHT,
        borderColor,
        borderWidth: 2,
      },
    ],
  };

  const themeDefaults = getDefaultOptions(theme);
  const options = {
    ...themeDefaults,
    cutout,
    plugins: {
      ...themeDefaults.plugins,
      legend: {
        display: showLegend,
        position: 'right' as const,
        labels: { color: themeDefaults.plugins.legend.labels.color },
      },
      title: {
        display: !!title,
        text: title,
        color: themeDefaults.plugins.title.color,
      },
      tooltip: {
        ...themeDefaults.plugins.tooltip,
        callbacks: {
          label: (context: TooltipItem<'doughnut'>) => {
            const label = context.label ?? '';
            const value = context.parsed ?? 0;
            const dataIndex = context.dataIndex ?? 0;

            // Build multi-line tooltip content
            const lines: string[] = [];

            // Line 1: Label with user count
            lines.push(`${label}: ${value}`);

            // Line 2: Percentage (if showPercentage is true)
            if (showPercentage && total > 0) {
              const percentage = ((value / total) * 100).toFixed(1);
              lines.push(`${percentage}%`);
            }

            // Line 3: Description (if provided)
            if (descriptions?.[dataIndex]) {
              lines.push(descriptions[dataIndex]);
            }

            return lines;
          },
        },
      },
    },
  };

  return (
    <div className={cn('chart-container', className)} style={{ height }}>
      <Doughnut data={chartData} options={options} />
    </div>
  );
};

// ===== Token Trend Chart =====
interface TokenTrendChartProps {
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

export const TokenTrendChart: React.FC<TokenTrendChartProps> = ({
  data,
  startDate,
  endDate,
  height = 300,
  className,
}) => {
  const theme = useTheme();

  // Generate complete date range if startDate and endDate are provided
  const dates = useMemo(() => {
    if (startDate && endDate) {
      // Parse dates using local time to avoid timezone offset issues
      const parseLocalDate = (dateStr: string): Date => {
        const [year, month, day] = dateStr.split('-').map(Number);
        return new Date(year, month - 1, day);
      };

      const formatDate = (d: Date): string => {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
      };

      const start = parseLocalDate(startDate);
      const end = parseLocalDate(endDate);

      if (isNaN(start.getTime()) || isNaN(end.getTime())) {
        return [...new Set(data.map((d) => d.date))].sort();
      }

      const dateArray: string[] = [];
      const currentDate = new Date(start);

      while (currentDate <= end) {
        dateArray.push(formatDate(currentDate));
        currentDate.setDate(currentDate.getDate() + 1);
      }

      return dateArray;
    }

    // Fallback: use unique dates from data
    return [...new Set(data.map((d) => d.date))].sort();
  }, [data, startDate, endDate]);

  const tools = [...new Set(data.map((d) => d.tool))];

  // Create a map for quick lookup
  const dataMap = new Map<string, number>();
  data.forEach((d) => {
    dataMap.set(`${d.date}-${d.tool}`, d.tokens);
  });

  // Convert tokens to millions
  const toMillions = (tokens: number) => tokens / 1_000_000;

  // Build datasets for each tool using unified colors
  const datasets = tools.map((tool, index) => {
    const colors = getToolColor(tool, index);

    return {
      label: tool.toUpperCase(),
      data: dates.map((date) => toMillions(dataMap.get(`${date}-${tool}`) ?? 0)),
      borderColor: colors.border,
      backgroundColor: colors.background,
      fill: false,
      tension: 0.2,
    };
  });

  // Calculate max value for y-axis
  const maxTokens = Math.max(...data.map((d) => toMillions(d.tokens)));
  const yMax = Math.ceil(maxTokens) ?? 1;

  const themeDefaults = getDefaultOptions(theme);
  const options = {
    ...themeDefaults,
    plugins: {
      ...themeDefaults.plugins,
      legend: {
        display: true,
        position: 'top' as const,
        labels: { color: themeDefaults.plugins.legend.labels.color },
      },
      tooltip: {
        ...themeDefaults.plugins.tooltip,
        callbacks: {
          label: (context: TooltipItem<'line'>) => {
            const label = context.dataset?.label ?? '';
            const value = context.parsed?.y;
            return `${label}: ${value?.toFixed(2) ?? 0}M tokens`;
          },
        },
      },
    },
    scales: {
      x: {
        ...themeDefaults.scales.x,
        display: true,
      },
      y: {
        ...themeDefaults.scales.y,
        display: true,
        beginAtZero: true,
        max: yMax,
        ticks: {
          ...themeDefaults.scales.y.ticks,
          stepSize: 1,
          callback: (value: string | number) => `${value}M`,
        },
        title: {
          display: true,
          text: 'Tokens',
          color: themeDefaults.scales.y.title.color,
        },
      },
    },
  };

  const chartData = {
    labels: dates,
    datasets,
  };

  return (
    <div className={cn('chart-container', className)} style={{ height }}>
      <Line data={chartData} options={options} />
    </div>
  );
};

// ===== Tool Usage Chart =====
interface ToolUsageChartProps {
  data: Array<{
    tool: string;
    count: number;
  }>;
  height?: number;
  className?: string;
}

export const ToolUsageChart: React.FC<ToolUsageChartProps> = ({
  data,
  height = 300,
  className,
}) => {
  const labels = data.map((d) => d.tool);
  const values = data.map((d) => d.count);

  return (
    <BarChart
      labels={labels}
      datasets={[
        {
          label: 'Usage',
          data: values,
        },
      ]}
      height={height}
      className={className}
    />
  );
};

// ===== Token Distribution Chart =====
interface TokenDistributionChartProps {
  data: Array<{
    tool: string;
    tokens: number;
  }>;
  height?: number;
  className?: string;
}

export const TokenDistributionChart: React.FC<TokenDistributionChartProps> = ({
  data,
  height = 250,
  className,
}) => {
  const labels = data.map((d) => d.tool.toUpperCase());
  const values = data.map((d) => d.tokens);
  const backgroundColors = data.map((d, index) => getToolColor(d.tool, index).solid);

  return (
    <DoughnutChart
      labels={labels}
      data={values}
      backgroundColor={backgroundColors}
      height={height}
      className={className}
    />
  );
};

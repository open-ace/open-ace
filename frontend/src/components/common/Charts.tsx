/**
 * Charts Component - Chart.js integration with react-chartjs-2
 */

import React from 'react';
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
} from 'chart.js';
import { Line, Bar, Pie, Doughnut } from 'react-chartjs-2';
import { cn } from '@/utils';

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

// Default chart options
const defaultOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      position: 'top' as const,
    },
  },
};

// Color palette
const COLORS = {
  primary: 'rgba(37, 99, 235, 1)',
  primaryLight: 'rgba(37, 99, 235, 0.2)',
  success: 'rgba(34, 197, 94, 1)',
  successLight: 'rgba(34, 197, 94, 0.2)',
  warning: 'rgba(245, 158, 11, 1)',
  warningLight: 'rgba(245, 158, 11, 0.2)',
  danger: 'rgba(239, 68, 68, 1)',
  dangerLight: 'rgba(239, 68, 68, 0.2)',
  info: 'rgba(59, 130, 246, 1)',
  infoLight: 'rgba(59, 130, 246, 0.2)',
  purple: 'rgba(139, 92, 246, 1)',
  purpleLight: 'rgba(139, 92, 246, 0.2)',
  cyan: 'rgba(6, 182, 212, 1)',
  cyanLight: 'rgba(6, 182, 212, 0.2)',
};

// Unified tool colors - used across all charts
export const TOOL_COLORS: Record<string, { border: string; background: string; solid: string }> = {
  openclaw: {
    border: 'rgba(255, 99, 132, 1)',
    background: 'rgba(255, 99, 132, 0.2)',
    solid: 'rgba(255, 99, 132, 0.8)',
  },
  claude: {
    border: 'rgba(75, 192, 192, 1)',
    background: 'rgba(75, 192, 192, 0.2)',
    solid: 'rgba(75, 192, 192, 0.8)',
  },
  qwen: {
    border: 'rgba(54, 162, 235, 1)',
    background: 'rgba(54, 162, 235, 0.2)',
    solid: 'rgba(54, 162, 235, 0.8)',
  },
};

// Get tool color, fallback to default colors
export const getToolColor = (tool: string, index: number) => {
  const toolColors = TOOL_COLORS[tool.toLowerCase()];
  if (toolColors) return toolColors;

  const solidColors = [
    'rgba(37, 99, 235, 0.8)',
    'rgba(34, 197, 94, 0.8)',
    'rgba(245, 158, 11, 0.8)',
    'rgba(239, 68, 68, 0.8)',
    'rgba(139, 92, 246, 0.8)',
    'rgba(6, 182, 212, 0.8)',
  ];
  return {
    border: [
      COLORS.primary,
      COLORS.success,
      COLORS.warning,
      COLORS.danger,
      COLORS.purple,
      COLORS.cyan,
    ][index % 6],
    background: [
      COLORS.primaryLight,
      COLORS.successLight,
      COLORS.warningLight,
      COLORS.dangerLight,
      COLORS.purpleLight,
      COLORS.cyanLight,
    ][index % 6],
    solid: solidColors[index % 6],
  };
};

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
      borderColor: dataset.borderColor || COLOR_ARRAY[index % COLOR_ARRAY.length],
      backgroundColor:
        dataset.backgroundColor || COLOR_ARRAY_LIGHT[index % COLOR_ARRAY_LIGHT.length],
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

  const options = {
    ...defaultOptions,
    plugins: {
      ...defaultOptions.plugins,
      legend: {
        display: showLegend,
        position: 'top' as const,
      },
      title: {
        display: !!title,
        text: title,
      },
      tooltip: {
        callbacks: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          label: (context: any) => {
            const label = context.dataset?.label || '';
            const value = context.parsed?.y;
            if (unit !== 'none') {
              return `${label}: ${value?.toFixed(2) || 0}${unit}`;
            }
            return `${label}: ${value?.toLocaleString() || 0}`;
          },
        },
      },
    },
    scales: {
      x: {
        // Center the label when there's only one data point
        offset: hasSinglePoint,
        ticks: {
          display: true,
        },
      },
      y: {
        beginAtZero: true,
        max: yMax,
        ticks:
          unit !== 'none'
            ? {
                stepSize: 1,

                callback: (value: string | number) => `${value}${unit}`,
              }
            : undefined,
        title:
          unit !== 'none'
            ? {
                display: true,
                text: `Tokens (${unit})`,
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
}) => {
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
        dataset.backgroundColor || COLOR_ARRAY_LIGHT[index % COLOR_ARRAY_LIGHT.length],
      borderColor: dataset.borderColor || COLOR_ARRAY[index % COLOR_ARRAY.length],
      borderWidth: dataset.borderWidth ?? 1,
    })),
  };

  // Calculate max value for y-axis
  const allValues = datasets.flatMap((d) => d.data.map(formatValue));
  const maxValue = Math.max(...allValues, 1);
  const yMax = unit !== 'none' ? Math.ceil(maxValue * 1.1) : undefined;

  const options = {
    ...defaultOptions,
    indexAxis: horizontal ? ('y' as const) : ('x' as const),
    plugins: {
      ...defaultOptions.plugins,
      legend: {
        display: showLegend,
        position: 'top' as const,
      },
      title: {
        display: !!title,
        text: title,
      },
      tooltip: {
        callbacks: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          label: (context: any) => {
            const label = context.dataset?.label || '';
            const value = context.parsed?.y;
            if (unit !== 'none') {
              return `${label}: ${value?.toFixed(2) || 0}${unit}`;
            }
            return `${label}: ${value?.toLocaleString() || 0}`;
          },
        },
      },
    },
    scales: {
      x: {
        stacked,
      },
      y: {
        stacked,
        beginAtZero: true,
        max: yMax,
        ticks:
          unit !== 'none'
            ? {
                stepSize: 1,
                callback: (value: string | number) => `${value}${unit}`,
              }
            : undefined,
        title:
          unit !== 'none'
            ? {
                display: true,
                text: `Tokens (${unit})`,
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
  const chartData = {
    labels,
    datasets: [
      {
        data,
        backgroundColor: backgroundColor || COLOR_ARRAY_LIGHT,
        borderColor,
        borderWidth: 2,
      },
    ],
  };

  const options = {
    ...defaultOptions,
    plugins: {
      ...defaultOptions.plugins,
      legend: {
        display: showLegend,
        position: 'right' as const,
      },
      title: {
        display: !!title,
        text: title,
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
}) => {
  const chartData = {
    labels,
    datasets: [
      {
        data,
        backgroundColor: backgroundColor || COLOR_ARRAY_LIGHT,
        borderColor,
        borderWidth: 2,
      },
    ],
  };

  const options = {
    ...defaultOptions,
    cutout,
    plugins: {
      ...defaultOptions.plugins,
      legend: {
        display: showLegend,
        position: 'right' as const,
      },
      title: {
        display: !!title,
        text: title,
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
  height?: number;
  className?: string;
}

export const TokenTrendChart: React.FC<TokenTrendChartProps> = ({
  data,
  height = 300,
  className,
}) => {
  // Get unique dates and tools
  const dates = [...new Set(data.map((d) => d.date))].sort();
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
      data: dates.map((date) => toMillions(dataMap.get(`${date}-${tool}`) || 0)),
      borderColor: colors.border,
      backgroundColor: colors.background,
      fill: false,
      tension: 0.2,
    };
  });

  // Calculate max value for y-axis
  const maxTokens = Math.max(...data.map((d) => toMillions(d.tokens)));
  const yMax = Math.ceil(maxTokens) || 1;

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: true,
        position: 'top' as const,
      },
      tooltip: {
        callbacks: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          label: (context: any) => {
            const label = context.dataset?.label || '';
            const value = context.parsed?.y;
            return `${label}: ${value?.toFixed(2) || 0}M tokens`;
          },
        },
      },
    },
    scales: {
      x: {
        display: true,
      },
      y: {
        display: true,
        beginAtZero: true,
        max: yMax,
        ticks: {
          stepSize: 1,

          callback: (value: string | number) => `${value}M`,
        },
        title: {
          display: true,
          text: 'Tokens',
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

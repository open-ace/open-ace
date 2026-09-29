/**
 * EnterpriseReport Component Tests
 *
 * Issue #3078: Management UI for enterprise analytics report
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { registerLocale } from 'react-datepicker';
import zhCN from 'date-fns/locale/zh-CN';
import { EnterpriseReport } from './EnterpriseReport';

// The app renders DatePicker with locale="zh-CN"; register it so calendar
// popups in these tests do not warn "A locale object was not found".
registerLocale('zh-CN', zhCN);

// Mock API
vi.mock('@/api/analysis', () => ({
  analysisApi: {
    getEnterpriseReport: vi.fn(),
    getEfficiencyMetrics: vi.fn(),
    exportReport: vi.fn(),
  },
}));

// Mock auth store
vi.mock('@/store', () => ({
  useLanguage: vi.fn(() => 'zh'),
  useUser: vi.fn(() => ({ role: 'admin', id: 1, username: 'testuser' })),
}));

// Mock permissions
vi.mock('@/utils/permissions', () => ({
  isAdmin: vi.fn(() => true),
}));

// Mock hooks
vi.mock('@/hooks', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/hooks')>();
  return {
    ...original,
    useEnterpriseReport: vi.fn(() => ({
      data: null,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })),
    useEfficiencyMetrics: vi.fn(() => ({
      data: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    })),
    useAuth: vi.fn(() => ({
      user: { role: 'admin', id: 1, username: 'testuser' },
    })),
  };
});

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
};

describe('EnterpriseReport Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders loading state initially', async () => {
    const { useEnterpriseReport } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: null,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as ReturnType<typeof useEnterpriseReport>);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    // Check for skeleton loading elements
    expect(screen.getByText('企业分析报告')).toBeInTheDocument();
  });

  it('renders error state when API fails', async () => {
    const { useEnterpriseReport } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: null,
      isLoading: false,
      isError: true,
      error: new Error('API Error'),
      refetch: vi.fn(),
    } as ReturnType<typeof useEnterpriseReport>);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    // Check for error message display
    expect(screen.getByText('API Error')).toBeInTheDocument();
    expect(screen.getByText('Retry')).toBeInTheDocument();
  });

  it('renders report data successfully', async () => {
    const mockReportData = {
      period: {
        start: '2024-01-01',
        end: '2024-01-31',
      },
      summary: {
        total_tokens: 100000,
        total_input_tokens: 50000,
        total_output_tokens: 50000,
        total_requests: 1000,
        unique_tools: 10,
        unique_hosts: 5,
        daily_average_tokens: 3333,
        daily_average_requests: 33,
        peak_day: '2024-01-15',
        peak_tokens: 5000,
      },
      trends: [
        {
          metric: 'tokens',
          direction: 'up' as const,
          change_percentage: 15.5,
          current_value: 100000,
          previous_value: 86500,
          period_days: 30,
          confidence: 0.85,
        },
      ],
      anomalies: [],
      breakdown_by_tool: {
        'claude-3-opus': {
          tokens: 50000,
          input_tokens: 25000,
          output_tokens: 25000,
          requests: 500,
          days_active: 30,
        },
      },
      breakdown_by_host: {
        'host1.example.com': {
          tokens: 30000,
          requests: 300,
          days_active: 30,
        },
      },
    };

    const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: mockReportData,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as ReturnType<typeof useEnterpriseReport>);

    vi.mocked(useEfficiencyMetrics).mockReturnValue({
      data: {
        efficiency_available: true,
        output_ratio: 50.0,
        tokens_per_request: 100,
        output_per_request: 50,
        input_output_ratio: 1.0,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as ReturnType<typeof useEfficiencyMetrics>);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    // Check for summary cards (use actual rendered text)
    await waitFor(() => {
      expect(screen.getByText('总 Tokens')).toBeInTheDocument();
    });

    // Check for efficiency metrics
    expect(screen.getByText('效率指标')).toBeInTheDocument();

    // Check for trends table
    expect(screen.getByText('趋势分析')).toBeInTheDocument();

    // Check for breakdown tables
    expect(screen.getByText('工具维度统计')).toBeInTheDocument();
    expect(screen.getByText('主机维度统计')).toBeInTheDocument();
  });

  it('renders export buttons for admin users', async () => {
    const mockReportData = {
      period: { start: '2024-01-01', end: '2024-01-31' },
      summary: {
        total_tokens: 100000,
        total_input_tokens: 50000,
        total_output_tokens: 50000,
        total_requests: 1000,
        unique_tools: 10,
        unique_hosts: 5,
        daily_average_tokens: 3333,
        daily_average_requests: 33,
        peak_day: null,
        peak_tokens: 0,
      },
      trends: [],
      anomalies: [],
      breakdown_by_tool: {},
      breakdown_by_host: {},
    };

    const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: mockReportData,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.mocked(useEfficiencyMetrics).mockReturnValue({
      data: { efficiency_available: false },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('导出报告')).toBeInTheDocument();
    });
  });

  it('renders cost/ROI placeholder notice', async () => {
    const mockReportData = {
      period: { start: '2024-01-01', end: '2024-01-31' },
      summary: {
        total_tokens: 100000,
        total_input_tokens: 50000,
        total_output_tokens: 50000,
        total_requests: 1000,
        unique_tools: 10,
        unique_hosts: 5,
        daily_average_tokens: 3333,
        daily_average_requests: 33,
        peak_day: null,
        peak_tokens: 0,
      },
      trends: [],
      anomalies: [],
      breakdown_by_tool: {},
      breakdown_by_host: {},
    };

    const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: mockReportData,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.mocked(useEfficiencyMetrics).mockReturnValue({
      data: { efficiency_available: false },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('成本和 ROI 暂不可用')).toBeInTheDocument();
    });
  });

  it('renders date range controls', async () => {
    const mockReportData = {
      period: { start: '2024-01-01', end: '2024-01-31' },
      summary: {
        total_tokens: 100000,
        total_input_tokens: 50000,
        total_output_tokens: 50000,
        total_requests: 1000,
        unique_tools: 10,
        unique_hosts: 5,
        daily_average_tokens: 3333,
        daily_average_requests: 33,
        peak_day: null,
        peak_tokens: 0,
      },
      trends: [],
      anomalies: [],
      breakdown_by_tool: {},
      breakdown_by_host: {},
    };

    const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
    vi.mocked(useEnterpriseReport).mockReturnValue({
      data: mockReportData,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    vi.mocked(useEfficiencyMetrics).mockReturnValue({
      data: { efficiency_available: false },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as any);

    render(<EnterpriseReport />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText('快速日期范围')).toBeInTheDocument();
      expect(screen.getByText('开始日期')).toBeInTheDocument();
      expect(screen.getByText('结束日期')).toBeInTheDocument();
    });
  });

  describe('peak_tokens subtitle', () => {
    it('should display peak_tokens subtitle when peak_tokens > 0', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: '2024-01-15',
          peak_tokens: 5000,
        },
        trends: [],
        anomalies: [],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        // Should display the peak-day card label (zh i18n key peakDay)
        expect(screen.getByText('高峰日')).toBeInTheDocument();
        // Should display peak day value
        expect(screen.getByText('2024-01-15')).toBeInTheDocument();
        // Should display peak tokens subtitle with label
        expect(screen.getByText(/峰值 Tokens:/)).toBeInTheDocument();
        expect(screen.getByText(/5.00K/)).toBeInTheDocument();
      });
    });

    it('should not display peak_tokens subtitle when peak_tokens is 0', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        // Should display the peak-day card label (zh i18n key peakDay)
        expect(screen.getByText('高峰日')).toBeInTheDocument();
        // Should display dash when no peak day
        expect(screen.getByText('-')).toBeInTheDocument();
      });

      // Should NOT display peak tokens subtitle
      expect(screen.queryByText(/峰值 Tokens:/)).not.toBeInTheDocument();
    });
  });

  describe('Efficiency Metrics Error Handling', () => {
    it('displays error when efficiency metrics API fails', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Efficiency API Error'),
        refetch: vi.fn(),
      } as any);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      // Wait for main report to load
      await waitFor(() => {
        expect(screen.getByText('总 Tokens')).toBeInTheDocument();
      });

      // Should display error for efficiency metrics
      expect(screen.getByText('Efficiency API Error')).toBeInTheDocument();
      expect(screen.getByText('Retry')).toBeInTheDocument();
    });

    it('retries efficiency metrics when retry button is clicked', async () => {
      const refetchMock = vi.fn();
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as any);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('API Error'),
        refetch: refetchMock,
      } as any);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText('总 Tokens')).toBeInTheDocument();
      });

      // Click retry button
      const retryButton = screen.getByText('Retry');
      fireEvent.click(retryButton);

      // Verify refetch was called
      expect(refetchMock).toHaveBeenCalledTimes(1);
    });
  });

  describe('Anomaly Table', () => {
    it('renders spike anomaly with danger color and up arrow', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [
          {
            type: 'spike' as const,
            metric: 'tokens',
            date: '2024-01-15',
            expected_value: 1000,
            actual_value: 5000,
            deviation_percentage: 400.0,
            severity: 'high' as const,
            description: 'Token usage spike on 2024-01-15',
          },
        ],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEnterpriseReport>);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEfficiencyMetrics>);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText('异常检测')).toBeInTheDocument();
      });

      // Check for spike badge with danger color
      const spikeBadge = screen.getByText('突增'); // Translated 'spike'
      expect(spikeBadge).toHaveClass('bg-danger');

      // Check for up arrow and danger text color in deviation
      const deviationSpan = screen.getByText(
        (content) => content.includes('↑') && content.includes('400.0%')
      );
      expect(deviationSpan).toHaveClass('text-danger');
    });

    it('renders drop anomaly with info color and down arrow', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [
          {
            type: 'drop' as const,
            metric: 'tokens',
            date: '2024-01-20',
            expected_value: 1000,
            actual_value: 200,
            deviation_percentage: 80.0,
            severity: 'low' as const,
            description: 'Token usage drop on 2024-01-20',
          },
        ],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEnterpriseReport>);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEfficiencyMetrics>);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText('异常检测')).toBeInTheDocument();
      });

      // Check for drop badge with info color
      const dropBadge = screen.getByText('骤降'); // Translated 'drop'
      expect(dropBadge).toHaveClass('bg-info');

      // Check for down arrow and info text color in deviation
      const deviationSpan = screen.getByText(
        (content) => content.includes('↓') && content.includes('80.0%')
      );
      expect(deviationSpan).toHaveClass('text-info');
    });

    it('renders unusual_pattern anomaly with warning color and no arrow', async () => {
      const mockReportData = {
        period: { start: '2024-01-01', end: '2024-01-31' },
        summary: {
          total_tokens: 100000,
          total_input_tokens: 50000,
          total_output_tokens: 50000,
          total_requests: 1000,
          unique_tools: 10,
          unique_hosts: 5,
          daily_average_tokens: 3333,
          daily_average_requests: 33,
          peak_day: null,
          peak_tokens: 0,
        },
        trends: [],
        anomalies: [
          {
            type: 'unusual_pattern' as const,
            metric: 'tokens',
            date: '2024-01-25',
            expected_value: 1000,
            actual_value: 500,
            deviation_percentage: 50.0,
            severity: 'medium' as const,
            description: 'Unusual pattern detected on 2024-01-25',
          },
        ],
        breakdown_by_tool: {},
        breakdown_by_host: {},
      };

      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEnterpriseReport>);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEfficiencyMetrics>);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText('异常检测')).toBeInTheDocument();
      });

      // Check for unusual_pattern badge with warning color
      const patternBadge = screen.getByText('异常模式'); // Translated 'unusual_pattern'
      expect(patternBadge).toHaveClass('bg-warning');

      // Check for no arrow and warning text color in deviation
      const deviationSpan = screen.getByText('50.0%');
      expect(deviationSpan).toHaveClass('text-warning');
      expect(deviationSpan.textContent).not.toContain('↑');
      expect(deviationSpan.textContent).not.toContain('↓');
    });
  });

  describe('Quick range highlight (Issue #3255)', () => {
    // Fixed "today" (local): 2026-06-15 noon, so quick ranges are stable:
    // 7 days -> 2026-06-09..2026-06-15, 30 days -> 2026-05-17..2026-06-15
    const TODAY = new Date(2026, 5, 15, 12, 0, 0);

    const mockReportData = {
      period: { start: '2026-05-17', end: '2026-06-15' },
      summary: {
        total_tokens: 100000,
        total_input_tokens: 50000,
        total_output_tokens: 50000,
        total_requests: 1000,
        unique_tools: 10,
        unique_hosts: 5,
        daily_average_tokens: 3333,
        daily_average_requests: 33,
        peak_day: null,
        peak_tokens: 0,
      },
      trends: [],
      anomalies: [],
      breakdown_by_tool: {},
      breakdown_by_host: {},
    };

    beforeEach(() => {
      // Fake timers keep Date-based quick range derivation deterministic.
      // shouldAdvanceTime lets waitFor/user interactions still resolve.
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.setSystemTime(TODAY);
      // react-datepicker's popper (floating-ui) instantiates ResizeObserver,
      // which the global jsdom mock in test setup is not constructible for.
      vi.stubGlobal(
        'ResizeObserver',
        class ResizeObserverStub {
          observe() {}
          unobserve() {}
          disconnect() {}
        }
      );
    });

    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    const renderLoadedReport = async () => {
      const { useEnterpriseReport, useEfficiencyMetrics } = await import('@/hooks');
      vi.mocked(useEnterpriseReport).mockReturnValue({
        data: mockReportData,
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEnterpriseReport>);

      vi.mocked(useEfficiencyMetrics).mockReturnValue({
        data: { efficiency_available: false },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useEfficiencyMetrics>);

      render(<EnterpriseReport />, { wrapper: createWrapper() });

      await waitFor(() => {
        expect(screen.getByText('快速日期范围')).toBeInTheDocument();
      });
    };

    const getQuickRangeButton = (days: '7' | '30' | '90') =>
      screen.getByRole('button', { name: `${days} 天` });

    /** The DatePicker renders its value inside a custom button input. */
    const getDatePickerInput = (labelText: string) => {
      const container = screen.getByText(labelText).parentElement;
      if (!container) {
        throw new Error(`No container found for label ${labelText}`);
      }
      return within(container).getByRole('button');
    };

    /** Opens the calendar popup and clicks the in-month day with the given number. */
    const pickDay = (input: HTMLElement, day: number) => {
      fireEvent.click(input);
      const dayCell = Array.from(
        document.querySelectorAll(
          '.react-datepicker__day:not(.react-datepicker__day--outside-month)'
        )
      ).find((el) => el.textContent?.trim() === String(day));
      if (!dayCell) {
        throw new Error(`Day ${day} not found in the opened calendar`);
      }
      fireEvent.click(dayCell);
    };

    it('highlights the "30 天" button on initial render (default range)', async () => {
      await renderLoadedReport();

      const sevenDaysButton = getQuickRangeButton('7');
      const thirtyDaysButton = getQuickRangeButton('30');
      const ninetyDaysButton = getQuickRangeButton('90');

      expect(thirtyDaysButton).toHaveAttribute('aria-pressed', 'true');
      expect(thirtyDaysButton).toHaveClass('btn-primary');
      expect(sevenDaysButton).toHaveAttribute('aria-pressed', 'false');
      expect(ninetyDaysButton).toHaveAttribute('aria-pressed', 'false');
    });

    it('clears all quick range highlights after manually picking a custom date', async () => {
      await renderLoadedReport();

      // Initial state: 30 天 highlighted (2026-05-17..2026-06-15)
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'true');

      // Manually change the start date to a non-quick-range value (2026-05-20)
      const startInput = getDatePickerInput('开始日期');
      pickDay(startInput, 20);

      // Custom period (2026-05-20..2026-06-15) matches no quick range:
      // no button should stay highlighted
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('90')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('7')).not.toHaveClass('btn-primary');
      expect(getQuickRangeButton('30')).not.toHaveClass('btn-primary');
      expect(getQuickRangeButton('90')).not.toHaveClass('btn-primary');
    });

    it('switches to the last 7 days and highlights "7 天" when clicked', async () => {
      await renderLoadedReport();

      fireEvent.click(getQuickRangeButton('7'));

      // Dates become exactly the last 7 days: 2026-06-09..2026-06-15
      expect(getDatePickerInput('开始日期')).toHaveTextContent('2026/06/09');
      expect(getDatePickerInput('结束日期')).toHaveTextContent('2026/06/15');

      // Only the 7 天 button is highlighted
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'true');
      expect(getQuickRangeButton('7')).toHaveClass('btn-primary');
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('90')).toHaveAttribute('aria-pressed', 'false');
    });

    it('restores the "30 天" highlight when dates are manually set back to exactly the last 30 days', async () => {
      await renderLoadedReport();

      // First create a custom period (2026-05-20..2026-06-15): no highlight
      const startInput = getDatePickerInput('开始日期');
      pickDay(startInput, 20);
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'false');

      // Then set the start date back to exactly the 30-day range start (2026-05-17)
      pickDay(startInput, 17);

      // The period now equals getDefaultDateRange(30), so 30 天 is active again
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'true');
      expect(getQuickRangeButton('30')).toHaveClass('btn-primary');
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('90')).toHaveAttribute('aria-pressed', 'false');
    });

    it('clears all quick range highlights after manually changing only the end date', async () => {
      await renderLoadedReport();

      // Initial state: 30 天 highlighted (2026-05-17..2026-06-15)
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'true');

      // Manually change the end date to a non-quick-range value (2026-06-10)
      const endInput = getDatePickerInput('结束日期');
      pickDay(endInput, 10);

      // Custom period (2026-05-17..2026-06-10) matches no quick range
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('90')).toHaveAttribute('aria-pressed', 'false');
    });

    it('restores the "7 天" highlight when dates are manually set back to exactly the last 7 days', async () => {
      await renderLoadedReport();

      // Start from the 7-day quick range (2026-06-09..2026-06-15)
      fireEvent.click(getQuickRangeButton('7'));
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'true');

      // Break it: start date 2026-06-10 makes a custom 6-day period
      const startInput = getDatePickerInput('开始日期');
      pickDay(startInput, 10);
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'false');

      // Set the start date back to exactly the 7-day range start (2026-06-09)
      pickDay(startInput, 9);

      // The period now equals getDefaultDateRange(7), so 7 天 is active again
      expect(getQuickRangeButton('7')).toHaveAttribute('aria-pressed', 'true');
      expect(getQuickRangeButton('7')).toHaveClass('btn-primary');
      expect(getQuickRangeButton('30')).toHaveAttribute('aria-pressed', 'false');
      expect(getQuickRangeButton('90')).toHaveAttribute('aria-pressed', 'false');
    });
  });
});

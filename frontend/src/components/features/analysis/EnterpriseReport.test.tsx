/**
 * EnterpriseReport Component Tests
 *
 * Issue #3078: Management UI for enterprise analytics report
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { EnterpriseReport } from './EnterpriseReport';

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
});

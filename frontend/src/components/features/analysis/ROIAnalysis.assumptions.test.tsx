import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@/test/utils';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ROIAnalysis } from './ROIAnalysis';

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/hooks', () => ({
  useTools: () => ({ data: ['qwen'] }),
  usePageRefresh: () => ({
    isRefreshing: false,
    lastRefreshTime: null,
    refresh: vi.fn(),
    canRefresh: true,
    refreshCount: 0,
  }),
}));

vi.mock('@/components/common', () => ({
  Card: ({ title, children }: { title?: string; children: ReactNode }) => (
    <section>
      {title ? <h3>{title}</h3> : null}
      {children}
    </section>
  ),
  StatCard: ({ label, value }: { label: string; value: string }) => (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  ),
  Select: ({
    options,
    value,
    onChange,
  }: {
    options: Array<{ value: string; label: string }>;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <select aria-label="tool-filter" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
  TextInput: ({
    label,
    value,
    onChange,
    type,
  }: {
    label?: string;
    value?: string;
    onChange?: (value: string) => void;
    type?: string;
  }) => (
    <label>
      <span>{label}</span>
      <input
        aria-label={label}
        type={type ?? 'text'}
        value={value ?? ''}
        onChange={(e) => onChange?.(e.target.value)}
      />
    </label>
  ),
  Error: ({ message }: { message: string }) => <div>{message}</div>,
  EmptyState: ({ title }: { title: string }) => <div>{title}</div>,
  LineChart: () => <div>line-chart</div>,
  PieChart: () => <div>pie-chart</div>,
  BarChart: () => <div>bar-chart</div>,
  Skeleton: () => <div>skeleton</div>,
  SkeletonCard: () => <div>skeleton-card</div>,
  PageRefreshControl: () => <div>refresh-control</div>,
  DatePicker: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <input
      aria-label="date-picker"
      type="date"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

const defaultAssumptions = {
  hourly_labor_cost: 50,
  productivity_multiplier: 10,
  avg_time_saved_per_request: 5,
  currency: 'USD',
};

const mockedGetROI = vi.fn();
const mockedGetROITrend = vi.fn();
const mockedGetCostBreakdown = vi.fn();
const mockedGetDailyCosts = vi.fn();
const mockedGetOptimizationSuggestions = vi.fn();
const mockedGetEfficiencyReport = vi.fn();

vi.mock('@/api', () => ({
  roiApi: {
    getROI: (...args: unknown[]) => mockedGetROI(...args),
    getROITrend: (...args: unknown[]) => mockedGetROITrend(...args),
    getCostBreakdown: (...args: unknown[]) => mockedGetCostBreakdown(...args),
    getDailyCosts: (...args: unknown[]) => mockedGetDailyCosts(...args),
    getOptimizationSuggestions: (...args: unknown[]) => mockedGetOptimizationSuggestions(...args),
    getEfficiencyReport: (...args: unknown[]) => mockedGetEfficiencyReport(...args),
  },
}));

describe('ROIAnalysis assumptions', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockedGetROI.mockImplementation(
      async (params?: { assumptions?: typeof defaultAssumptions }) => ({
        period: '2026-06-01 to 2026-06-30',
        start_date: '2026-06-01',
        end_date: '2026-06-30',
        total_cost: 120.5,
        tokens_used: 5000,
        input_tokens: 3000,
        output_tokens: 2000,
        input_cost: 60.25,
        output_cost: 60.25,
        requests_made: 25,
        estimated_hours_saved: 2.08,
        estimated_savings: 104.0,
        productivity_gain: 900,
        roi_percentage: -13.7,
        cost_per_request: 4.82,
        cost_per_token: 0.0241,
        efficiency_score: 75,
        assumptions: params?.assumptions ?? defaultAssumptions,
      })
    );
    mockedGetROITrend.mockResolvedValue([]);
    mockedGetCostBreakdown.mockResolvedValue({ breakdown: [], total_cost: 120.5 });
    mockedGetDailyCosts.mockResolvedValue([]);
    mockedGetOptimizationSuggestions.mockResolvedValue([]);
    mockedGetEfficiencyReport.mockResolvedValue({
      period_days: 30,
      total_tokens: 5000,
      total_requests: 25,
      avg_tokens_per_request: 200,
      output_ratio: 40,
      input_output_ratio: 1.5,
      model_distribution: {},
      unique_models: 1,
      unique_tools: 1,
      overall_efficiency: 75,
      avg_cost_per_request: 4.82,
      waste_percentage: 12,
      recommendation_items: [],
    });
  });

  it('shows baseline assumptions, applies overrides, and resets the draft', async () => {
    render(<ROIAnalysis />);

    expect(await screen.findByText('ROI Assumptions')).toBeInTheDocument();
    expect(
      screen.getByText(
        'ROI is a configurable planning estimate, not verified realized savings or universal productivity truth.'
      )
    ).toBeInTheDocument();

    const hourlyLaborCost = screen.getByLabelText('Hourly labor cost');
    const avgTimeSaved = screen.getByLabelText('Avg time saved / request');
    const productivityMultiplier = screen.getByLabelText('Productivity multiplier');
    const currency = screen.getByLabelText('Currency');

    await waitFor(() => {
      expect(hourlyLaborCost).toHaveValue(50);
      expect(avgTimeSaved).toHaveValue(5);
      expect(productivityMultiplier).toHaveValue(10);
      expect(currency).toHaveValue('USD');
    });

    mockedGetROI.mockClear();
    mockedGetROITrend.mockClear();

    fireEvent.change(hourlyLaborCost, { target: { value: '88' } });
    fireEvent.change(avgTimeSaved, { target: { value: '12' } });
    fireEvent.change(productivityMultiplier, { target: { value: '6' } });
    fireEvent.change(currency, { target: { value: 'cny' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => {
      expect(mockedGetROI).toHaveBeenCalledWith(
        expect.objectContaining({
          assumptions: {
            hourly_labor_cost: 88,
            avg_time_saved_per_request: 12,
            productivity_multiplier: 6,
            currency: 'CNY',
          },
        })
      );
      expect(mockedGetROITrend).toHaveBeenCalledWith(6, undefined, {
        hourly_labor_cost: 88,
        avg_time_saved_per_request: 12,
        productivity_multiplier: 6,
        currency: 'CNY',
      });
    });

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    await waitFor(() => {
      expect(hourlyLaborCost).toHaveValue(50);
      expect(avgTimeSaved).toHaveValue(5);
      expect(productivityMultiplier).toHaveValue(10);
      expect(currency).toHaveValue('USD');
    });
  });
});

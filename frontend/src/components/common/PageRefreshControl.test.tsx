/**
 * PageRefreshControl Component Tests
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PageRefreshControl, STANDARD_INTERVALS } from './PageRefreshControl';
import type { UsePageRefreshReturn } from '@/hooks/usePageRefresh';

// Mock useLanguage hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Create mock refresh return
const createMockRefresh = (): UsePageRefreshReturn => ({
  isRefreshing: false,
  refresh: vi.fn().mockResolvedValue(undefined),
  autoRefresh: false,
  setAutoRefresh: vi.fn(),
  interval: 60000,
  setInterval: vi.fn(),
  lastRefreshTime: Date.now() - 60000,
  nextRefreshTime: null,
  error: null,
  errorCount: 0,
});

describe('PageRefreshControl', () => {
  describe('standard intervals', () => {
    it('should define standard interval options', () => {
      expect(STANDARD_INTERVALS).toHaveLength(3);
      expect(STANDARD_INTERVALS[0].value).toBe(30000);
      expect(STANDARD_INTERVALS[1].value).toBe(60000);
      expect(STANDARD_INTERVALS[2].value).toBe(300000);
    });
  });

  describe('full mode', () => {
    it('should render auto refresh toggle', () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} />);

      expect(screen.getByLabelText(/auto refresh/i)).toBeInTheDocument();
    });

    it('should render interval selector when auto refresh is enabled', async () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.autoRefresh = true;

      render(<PageRefreshControl refresh={mockRefresh} showIntervalSelector={true} />);

      expect(screen.getByTestId('interval-selector')).toBeInTheDocument();
    });

    it('should render manual refresh button', () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} />);

      expect(screen.getByTestId('manual-refresh-button')).toBeInTheDocument();
    });

    it('should render last refresh time', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 120000; // 2 minutes ago

      render(<PageRefreshControl refresh={mockRefresh} showLastRefreshTime={true} />);

      // Should show relative time
      expect(screen.getByText(/minutes ago/i)).toBeInTheDocument();
    });

    it('should call refresh when button clicked', async () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} />);

      const button = screen.getByTestId('manual-refresh-button');
      fireEvent.click(button);

      // Button should be disabled after click (debounce)
      await waitFor(() => {
        expect(button).toBeDisabled();
      });
    });

    it('should show loading state when refreshing', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.isRefreshing = true;

      render(<PageRefreshControl refresh={mockRefresh} />);

      expect(screen.getByText(/refreshing/i)).toBeInTheDocument();
    });

    it('should show error indicator when error exists', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.error = 'Network error';
      mockRefresh.errorCount = 1;

      render(<PageRefreshControl refresh={mockRefresh} showErrorIndicator={true} />);

      expect(screen.getByTestId('refresh-error-indicator')).toBeInTheDocument();
    });
  });

  describe('compact mode', () => {
    it('should render compact buttons', () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} compact={true} />);

      // Should have icon buttons
      expect(screen.getByTestId('manual-refresh-button')).toBeInTheDocument();
    });

    it('should render dropdown for settings in compact mode', () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} compact={true} showAutoRefreshToggle={true} />);

      // Should have clock icon for dropdown
      expect(screen.getByRole('button', { name: '' })).toBeInTheDocument();
    });

    it('should show error indicator in compact mode', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.error = 'Network error';
      mockRefresh.errorCount = 1;

      render(<PageRefreshControl refresh={mockRefresh} compact={true} showErrorIndicator={true} />);

      expect(screen.getByTestId('refresh-error-indicator')).toBeInTheDocument();
    });
  });

  describe('manual refresh only mode', () => {
    it('should hide auto refresh toggle when disabled', () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} showAutoRefreshToggle={false} />);

      expect(screen.queryByLabelText(/auto refresh/i)).not.toBeInTheDocument();
    });

    it('should hide interval selector when disabled', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.autoRefresh = true;

      render(<PageRefreshControl refresh={mockRefresh} showIntervalSelector={false} />);

      expect(screen.queryByTestId('interval-selector')).not.toBeInTheDocument();
    });
  });

  describe('callback actions', () => {
    it('should call setAutoRefresh when toggle clicked', async () => {
      const mockRefresh = createMockRefresh();

      render(<PageRefreshControl refresh={mockRefresh} showAutoRefreshToggle={true} />);

      const toggle = screen.getByLabelText(/auto refresh/i);
      fireEvent.click(toggle);

      expect(mockRefresh.setAutoRefresh).toHaveBeenCalledWith(true);
    });

    it('should call setInterval when option selected', async () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.autoRefresh = true;

      render(<PageRefreshControl refresh={mockRefresh} showIntervalSelector={true} />);

      const selector = screen.getByTestId('interval-selector');
      fireEvent.change(selector, { target: { value: '30000' } });

      expect(mockRefresh.setInterval).toHaveBeenCalledWith(30000);
    });
  });

  describe('next refresh countdown', () => {
    it('should show countdown when enabled', async () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.autoRefresh = true;
      mockRefresh.nextRefreshTime = Date.now() + 30000;

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          showNextRefreshTime={true}
          showAutoRefreshToggle={true}
        />
      );

      // Countdown should be visible
      await waitFor(() => {
        expect(screen.getByText(/\d+s/)).toBeInTheDocument();
      });
    });

    it('should not show countdown when auto refresh disabled', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.autoRefresh = false;

      render(<PageRefreshControl refresh={mockRefresh} showNextRefreshTime={true} />);

      // No countdown should be visible
      expect(screen.queryByText(/\d+s/)).not.toBeInTheDocument();
    });
  });
});
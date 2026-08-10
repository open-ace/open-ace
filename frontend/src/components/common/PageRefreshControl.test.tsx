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

      // Should call refresh function
      expect(mockRefresh.refresh).toHaveBeenCalled();
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

      render(
        <PageRefreshControl refresh={mockRefresh} compact={true} showAutoRefreshToggle={true} />
      );

      // Should have dropdown toggle button with clock icon
      const dropdownToggle = screen.getByTestId('dropdown-toggle');
      expect(dropdownToggle).toBeInTheDocument();
      expect(dropdownToggle.querySelector('i.bi-clock, i.bi-clock-history')).toBeTruthy();
    });

    it('should show error indicator in compact mode', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.error = 'Network error';
      mockRefresh.errorCount = 1;

      render(<PageRefreshControl refresh={mockRefresh} compact={true} showErrorIndicator={true} />);

      expect(screen.getByTestId('refresh-error-indicator')).toBeInTheDocument();
    });

    it('should display last refresh time as text when no dropdown content in compact mode', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 120000; // 2 minutes ago

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      );

      // Regression guard: ensure removed static clock icon does not return
      expect(screen.queryByTestId('refresh-clock-icon')).not.toBeInTheDocument();
      // Should render refresh time as text
      expect(screen.getByText(/minutes ago/i)).toBeInTheDocument();
    });

    it('should render time text with Tooltip wrapper in compact mode without dropdown', async () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 120000; // 2 minutes ago

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      );

      // Should render time text
      const timeText = screen.getByText(/minutes ago/i);
      expect(timeText).toBeInTheDocument();
      // Issue #2397: Should have tabIndex for keyboard accessibility
      expect(timeText).toHaveAttribute('tabindex', '0');
    });

    it('should render multi-line JSX in tooltip content', async () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 120000; // 2 minutes ago

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      );

      const timeText = screen.getByText(/minutes ago/i);
      fireEvent.mouseOver(timeText);

      await waitFor(() => {
        const tooltip = screen.getByRole('tooltip');
        // Verify multi-line structure (div > div)
        expect(tooltip.querySelector('div > div')).toBeInTheDocument();
        // Verify guidance text is present
        expect(tooltip.textContent).toContain('Click the refresh button');
      });
    });

    it('should render refresh button with outline-secondary class in compact mode', () => {
      const mockRefresh = createMockRefresh();

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
        />
      );

      // Issue #2397: Button should use outline-secondary for visual consistency
      const button = screen.getByTestId('manual-refresh-button');
      expect(button.className).toContain('btn-outline-secondary');
      expect(button.className).toContain('btn-sm');
    });

    it('should not render clock icon or extra element when lastRefreshTime is null', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = null;

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      );

      // Regression guard: ensure removed static clock icon does not return
      expect(screen.queryByTestId('refresh-clock-icon')).not.toBeInTheDocument();
      // Should NOT render the time text small element
      expect(screen.queryByText(/ago/i)).not.toBeInTheDocument();
    });

    it('should not show extra elements when showLastRefreshTime is false', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 60000;

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={false}
        />
      );

      // Regression guard: ensure removed static clock icon does not return
      expect(screen.queryByTestId('refresh-clock-icon')).not.toBeInTheDocument();
      // Should NOT render the time text
      expect(screen.queryByText(/ago/i)).not.toBeInTheDocument();
    });

    it('should display seconds ago format for very recent refresh', () => {
      const mockRefresh = createMockRefresh();
      mockRefresh.lastRefreshTime = Date.now() - 30000; // 30 seconds ago

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      );

      // Should render seconds ago format
      expect(screen.getByText(/seconds ago/i)).toBeInTheDocument();
    });

    it('should still render manual refresh button when no dropdown content', () => {
      const mockRefresh = createMockRefresh();

      render(
        <PageRefreshControl
          refresh={mockRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
        />
      );

      // Manual refresh button should always be present
      expect(screen.getByTestId('manual-refresh-button')).toBeInTheDocument();
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

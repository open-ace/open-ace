/**
 * Pagination Component Tests
 *
 * Tests cover:
 * - getVisiblePages algorithm
 * - Props validation
 * - User interactions
 * - Jump input validation
 * - Accessibility
 * - Responsive design
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi } from 'vitest';
import { Pagination, getVisiblePages } from './Pagination';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n - note: the component does .replace('{page}', num) on goToPage result for page buttons
// but uses the raw t() value for input/button aria-labels
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      navigation: 'Navigation',
      previous: 'Previous',
      next: 'Next',
      goToPage: 'Go to page {page}',
      pageInfo: 'Page {current} of {total}',
      invalidPageNumber: 'Please enter a valid page number (1-{total})',
      previousPage: 'Previous page',
      nextPage: 'Next page',
    };
    return translations[key] || key;
  },
}));

// Helper: get the desktop nav (first one rendered)
function getDesktopNav() {
  const navs = screen.getAllByRole('navigation');
  return navs[0];
}

// Helper: get desktop input (first spinbutton)
function getDesktopInput() {
  const inputs = screen.getAllByRole('spinbutton');
  return inputs[0];
}

// Helper: get the desktop alert (first one)
function getFirstAlert() {
  const alerts = screen.getAllByRole('alert');
  return alerts[0];
}

describe('getVisiblePages', () => {
  describe('Edge cases', () => {
    it('should return empty array for totalPages <= 0', () => {
      expect(getVisiblePages(1, 0, 5)).toEqual([]);
      expect(getVisiblePages(1, -1, 5)).toEqual([]);
    });

    it('should return all pages when totalPages <= maxVisible', () => {
      expect(getVisiblePages(1, 5, 5)).toEqual([1, 2, 3, 4, 5]);
      expect(getVisiblePages(3, 4, 5)).toEqual([1, 2, 3, 4]);
    });
  });

  describe('Current page in middle', () => {
    it('should show current page centered with ellipsis', () => {
      const result = getVisiblePages(12, 50, 5);
      expect(result).toEqual([1, 'ellipsis-start', 10, 11, 12, 13, 14, 'ellipsis-end', 50]);
    });

    it('should handle even maxVisible correctly', () => {
      const result = getVisiblePages(12, 50, 6);
      // halfVisible=3, start=9, end=15
      expect(result).toEqual([1, 'ellipsis-start', 9, 10, 11, 12, 13, 14, 15, 'ellipsis-end', 50]);
    });
  });

  describe('Current page near start', () => {
    it('should show first pages without start ellipsis', () => {
      const result = getVisiblePages(2, 50, 5);
      expect(result).toEqual([1, 2, 3, 4, 5, 'ellipsis-end', 50]);
    });

    it('should handle currentPage=1', () => {
      const result = getVisiblePages(1, 50, 5);
      expect(result).toEqual([1, 2, 3, 4, 5, 'ellipsis-end', 50]);
    });
  });

  describe('Current page near end', () => {
    it('should show last pages without end ellipsis', () => {
      const result = getVisiblePages(48, 50, 5);
      // endPage >= totalPages → endPage=49, startPage=50-5=45
      expect(result).toEqual([1, 'ellipsis-start', 45, 46, 47, 48, 49, 50]);
    });

    it('should handle currentPage=totalPages', () => {
      const result = getVisiblePages(50, 50, 5);
      expect(result).toEqual([1, 'ellipsis-start', 45, 46, 47, 48, 49, 50]);
    });
  });
});

describe('Pagination Component', () => {
  const mockOnPageChange = vi.fn();

  beforeEach(() => {
    mockOnPageChange.mockClear();
  });

  describe('Props validation', () => {
    it('should not render when totalPages <= 1', () => {
      const { container } = render(
        <Pagination currentPage={1} totalPages={1} onPageChange={mockOnPageChange} />
      );
      expect(container.firstChild).toBeNull();
    });

    it('should render with default props', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const navs = screen.getAllByRole('navigation');
      expect(navs.length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Previous').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Next').length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('User interactions', () => {
    it('should call onPageChange when clicking page button', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const page2Button = within(nav).getByRole('button', { name: 'Go to page 2' });
      fireEvent.click(page2Button);

      expect(mockOnPageChange).toHaveBeenCalledWith(2);
    });

    it('should call onPageChange when clicking Previous button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const prevButton = within(nav).getByRole('button', { name: 'Previous page' });
      fireEvent.click(prevButton);

      expect(mockOnPageChange).toHaveBeenCalledWith(4);
    });

    it('should call onPageChange when clicking Next button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const nextButton = within(nav).getByRole('button', { name: 'Next page' });
      fireEvent.click(nextButton);

      expect(mockOnPageChange).toHaveBeenCalledWith(6);
    });

    it('should disable Previous button when on first page', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const prevButton = within(nav).getByRole('button', { name: 'Previous page' });
      expect(prevButton).toBeDisabled();
      expect(prevButton.closest('li')).toHaveClass('disabled');
    });

    it('should disable Next button when on last page', () => {
      render(<Pagination currentPage={10} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const nextButton = within(nav).getByRole('button', { name: 'Next page' });
      expect(nextButton).toBeDisabled();
      expect(nextButton.closest('li')).toHaveClass('disabled');
    });

    it('should not call onPageChange when clicking disabled buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const prevButton = within(nav).getByRole('button', { name: 'Previous page' });
      fireEvent.click(prevButton);

      expect(mockOnPageChange).not.toHaveBeenCalled();
    });
  });

  describe('Jump input', () => {
    it('should allow entering page number', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '5' } });

      expect(input).toHaveValue(5);
    });

    it('should call onPageChange when pressing Enter with valid input', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '5' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(mockOnPageChange).toHaveBeenCalledWith(5);
    });

    it('should show error for invalid input (non-number)', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: 'abc' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
      });
    });

    it('should show error for out-of-range input', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
      });
    });

    it('should show error for input less than 1', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '0' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
      });
    });

    it('should clear error when valid input is entered', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);
      });

      // Enter valid input - clears error
      fireEvent.change(input, { target: { value: '5' } });

      await waitFor(() => {
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      });
    });

    it('should auto-clear error after 3 seconds', async () => {
      vi.useFakeTimers();

      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      // Error should be visible (desktop + mobile)
      expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(1);

      // Fast-forward 3 seconds and flush React updates
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });

      // Error should be cleared
      expect(screen.queryAllByRole('alert')).toHaveLength(0);

      vi.useRealTimers();
    });
  });

  describe('Accessibility', () => {
    it('should have aria-label on navigation elements', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const navs = screen.getAllByRole('navigation');
      navs.forEach((nav) => {
        expect(nav).toHaveAttribute('aria-label', 'Navigation');
      });
    });

    it('should have aria-current on current page button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const currentPageButton = within(nav).getByRole('button', { name: 'Go to page 5' });
      expect(currentPageButton).toHaveAttribute('aria-current', 'page');
    });

    it('should not have aria-current on other page buttons', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      // When on page 5 with 10 total, visible pages include 3 (not current)
      const page3Button = within(nav).getByRole('button', { name: 'Go to page 3' });
      expect(page3Button).not.toHaveAttribute('aria-current');
    });

    it('should have aria-label on page buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const page2Button = within(nav).getByRole('button', { name: 'Go to page 2' });
      expect(page2Button).toHaveAttribute('aria-label');
    });

    it('should have aria-disabled on disabled buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const prevButton = within(nav).getByRole('button', { name: 'Previous page' });
      expect(prevButton).toHaveAttribute('aria-disabled', 'true');
    });

    it('should have role="alert" with aria-live on error message', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const input = getDesktopInput();
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      // Error should appear (both desktop and mobile alerts)
      const alerts = screen.getAllByRole('alert');
      expect(alerts.length).toBeGreaterThanOrEqual(1);
      expect(alerts[0]).toHaveAttribute('aria-live', 'polite');
    });

    it('should support keyboard navigation (Tab)', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const nav = getDesktopNav();
      const buttons = within(nav).getAllByRole('button');
      buttons.forEach((button) => {
        if (!button.hasAttribute('disabled')) {
          expect(button).not.toHaveAttribute('tabindex', '-1');
        }
      });
    });
  });

  describe('Responsive design', () => {
    it('should render desktop layout with full pagination', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      expect(screen.getAllByText('Previous').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('Next').length).toBeGreaterThanOrEqual(1);
    });

    it('should show page info by default', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      expect(screen.getByText(/Page 5 of 10/i)).toBeInTheDocument();
    });

    it('should hide page info when showPageInfo=false', () => {
      render(
        <Pagination
          currentPage={5}
          totalPages={10}
          onPageChange={mockOnPageChange}
          showPageInfo={false}
        />
      );

      expect(screen.queryByText(/Page 5 of 10/i)).not.toBeInTheDocument();
    });

    it('should show jump input by default', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      expect(screen.getAllByRole('spinbutton').length).toBeGreaterThanOrEqual(1);
    });

    it('should hide jump input when showPageInput=false', () => {
      render(
        <Pagination
          currentPage={1}
          totalPages={10}
          onPageChange={mockOnPageChange}
          showPageInput={false}
        />
      );

      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    });
  });

  describe('Props behavior', () => {
    it('should use custom maxVisiblePages', () => {
      render(
        <Pagination
          currentPage={10}
          totalPages={50}
          onPageChange={mockOnPageChange}
          maxVisiblePages={7}
        />
      );

      const nav = getDesktopNav();
      const buttons = within(nav).getAllByRole('button');
      const pageButtons = buttons.filter((btn) =>
        btn.getAttribute('aria-label')?.startsWith('Go to page')
      );

      expect(pageButtons.length).toBeGreaterThan(7);
    });

    it('should sync input value with currentPage prop changes', () => {
      const { rerender } = render(
        <Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />
      );

      const input = getDesktopInput();
      expect(input).toHaveValue(1);

      rerender(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      expect(input).toHaveValue(5);
    });

    it('should apply custom className', () => {
      const { container } = render(
        <Pagination
          currentPage={1}
          totalPages={10}
          onPageChange={mockOnPageChange}
          className="custom-pagination"
        />
      );

      expect(container.firstChild).toHaveClass('custom-pagination');
    });
  });
});

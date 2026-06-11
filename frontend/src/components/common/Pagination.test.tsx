/**
 * Pagination Component Tests
 *
 * Tests cover:
 * - Props validation
 * - Page algorithm
 * - User interactions
 * - Jump input validation
 * - Accessibility
 * - Responsive design
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { Pagination } from './Pagination';
import { getVisiblePages } from './Pagination';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      navigation: 'Navigation',
      previous: 'Previous',
      next: 'Next',
      goToPage: 'Go to page',
      pageInfo: 'Page {current} of {total}',
      invalidPageNumber: 'Please enter a valid page number (1-{total})',
      previousPage: 'Previous page',
      nextPage: 'Next page',
    };
    return translations[key] || key;
  },
}));

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
      // currentPage=12, totalPages=50, maxVisible=5
      // Expected: [1, '...', 10, 11, 12, 13, 14, '...', 50]
      const result = getVisiblePages(12, 50, 5);
      expect(result).toEqual([1, 'ellipsis-start', 10, 11, 12, 13, 14, 'ellipsis-end', 50]);
    });

    it('should handle even maxVisible correctly', () => {
      // currentPage=12, totalPages=50, maxVisible=6
      // halfVisible = 3, so range is 9-15 (6 pages centered around 12)
      const result = getVisiblePages(12, 50, 6);
      expect(result).toEqual([1, 'ellipsis-start', 9, 10, 11, 12, 13, 14, 15, 'ellipsis-end', 50]);
    });
  });

  describe('Current page near start', () => {
    it('should show first pages without start ellipsis', () => {
      // currentPage=2, totalPages=50, maxVisible=5
      // Expected: [1, 2, 3, 4, 5, '...', 50]
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
      // currentPage=48, totalPages=50, maxVisible=5
      // When near end: startPage = totalPages - maxVisible = 45, endPage = 49
      const result = getVisiblePages(48, 50, 5);
      expect(result).toEqual([1, 'ellipsis-start', 45, 46, 47, 48, 49, 50]);
    });

    it('should handle currentPage=totalPages', () => {
      // currentPage=50, totalPages=50, maxVisible=5
      // Same as near end: startPage = 45, endPage = 49
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

      // Should show page buttons (both desktop and mobile versions)
      expect(screen.getAllByRole('navigation').length).toBeGreaterThan(0);
      expect(screen.getByText('Previous')).toBeInTheDocument();
      expect(screen.getByText('Next')).toBeInTheDocument();
    });
  });

  describe('User interactions', () => {
    it('should call onPageChange when clicking page button', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version page button
      const buttons = screen.getAllByRole('button', { name: /Go to page 2/i });
      const page2Button = buttons[0];
      fireEvent.click(page2Button);

      expect(mockOnPageChange).toHaveBeenCalledWith(2);
    });

    it('should call onPageChange when clicking Previous button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version previous button (has aria-disabled attribute)
      const prevButtons = screen.getAllByRole('button', { name: /Previous page/i });
      const prevButton = prevButtons.find((btn) => btn.textContent !== '');
      fireEvent.click(prevButton!);

      expect(mockOnPageChange).toHaveBeenCalledWith(4);
    });

    it('should call onPageChange when clicking Next button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version next button (has text content)
      const nextButtons = screen.getAllByRole('button', { name: /Next page/i });
      const nextButton = nextButtons.find((btn) => btn.textContent !== '');
      fireEvent.click(nextButton!);

      expect(mockOnPageChange).toHaveBeenCalledWith(6);
    });

    it('should disable Previous button when on first page', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version previous button (has aria-disabled)
      const prevButtons = screen.getAllByRole('button', { name: /Previous page/i });
      const prevButton = prevButtons.find((btn) => btn.hasAttribute('aria-disabled'));
      expect(prevButton).toBeDisabled();
      expect(prevButton!.closest('li')).toHaveClass('disabled');
    });

    it('should disable Next button when on last page', () => {
      render(<Pagination currentPage={10} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version next button
      const nextButtons = screen.getAllByRole('button', { name: /Next page/i });
      const nextButton = nextButtons.find((btn) => btn.hasAttribute('aria-disabled'));
      expect(nextButton).toBeDisabled();
      expect(nextButton!.closest('li')).toHaveClass('disabled');
    });

    it('should not call onPageChange when clicking disabled buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version previous button (has aria-disabled)
      const prevButtons = screen.getAllByRole('button', { name: /Previous page/i });
      const prevButton = prevButtons.find((btn) => btn.hasAttribute('aria-disabled'));
      fireEvent.click(prevButton!);

      expect(mockOnPageChange).not.toHaveBeenCalled();
    });
  });

  describe('Jump input', () => {
    it('should allow entering page number', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '5' } });

      expect(input).toHaveValue(5);
    });

    it('should call onPageChange when pressing Enter', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '5' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(mockOnPageChange).toHaveBeenCalledWith(5);
    });

    it('should show error for invalid input (non-number)', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: 'abc' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        // Desktop version error message
        const alerts = screen.getAllByRole('alert');
        expect(alerts.length).toBeGreaterThan(0);
      });
    });

    it('should show error for out-of-range input', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        const alerts = screen.getAllByRole('alert');
        expect(alerts.length).toBeGreaterThan(0);
      });
    });

    it('should show error for input less than 1', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '0' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        const alerts = screen.getAllByRole('alert');
        expect(alerts.length).toBeGreaterThan(0);
      });
    });

    it('should clear error when valid input is entered', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(() => {
        const alerts = screen.getAllByRole('alert');
        expect(alerts.length).toBeGreaterThan(0);
      });

      // Enter valid input
      fireEvent.change(input, { target: { value: '5' } });

      await waitFor(() => {
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      });
    });

    it('should auto-clear error after 3 seconds', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      // Wait for error to appear
      await waitFor(
        () => {
          const alerts = screen.getAllByRole('alert');
          expect(alerts.length).toBeGreaterThan(0);
        },
        { timeout: 3000 }
      );

      // Fast-forward 3 seconds using act
      vi.useFakeTimers();
      vi.advanceTimersByTime(3100);
      vi.useRealTimers();

      // Wait for error to disappear
      await waitFor(
        () => {
          const alerts = screen.queryAllByRole('alert');
          expect(alerts.length).toBe(0);
        },
        { timeout: 3000 }
      );
    });
  });

  describe('Accessibility', () => {
    it('should have aria-label on navigation', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Both desktop and mobile have navigation
      const navs = screen.getAllByRole('navigation');
      expect(navs[0]).toHaveAttribute('aria-label', 'Navigation');
    });

    it('should have aria-current on current page button', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version page button with aria-current
      const buttons = screen.getAllByRole('button', { name: /Go to page 5/i });
      const currentPageButton = buttons.find((btn) => btn.hasAttribute('aria-current'));
      expect(currentPageButton).toHaveAttribute('aria-current', 'page');
    });

    it('should not have aria-current on other page buttons', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version page button without aria-current (page 3 is visible, not current)
      const buttons = screen.getAllByRole('button', { name: /Go to page 3/i });
      const page3Button = buttons[0]; // Desktop version
      expect(page3Button).not.toHaveAttribute('aria-current');
    });

    it('should have aria-label on page buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version page button
      const buttons = screen.getAllByRole('button', { name: /Go to page 2/i });
      const page2Button = buttons[0];
      expect(page2Button).toHaveAttribute('aria-label');
    });

    it('should have aria-disabled on disabled buttons', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop version previous button
      const prevButtons = screen.getAllByRole('button', { name: /Previous page/i });
      const prevButton = prevButtons.find((btn) => btn.hasAttribute('aria-disabled'));
      expect(prevButton).toHaveAttribute('aria-disabled', 'true');
    });

    it('should have role="alert" on error message', async () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      fireEvent.change(input, { target: { value: '15' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      await waitFor(
        () => {
          // Desktop version error message
          const alerts = screen.getAllByRole('alert');
          const errorMessage = alerts[0];
          expect(errorMessage).toHaveAttribute('aria-live', 'polite');
        },
        { timeout: 3000 }
      );
    });

    it('should support keyboard navigation (Tab)', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      const buttons = screen.getAllByRole('button');
      // Verify buttons are focusable
      buttons.forEach((button) => {
        if (!button.hasAttribute('disabled')) {
          expect(button).not.toHaveAttribute('tabindex', '-1');
        }
      });
    });
  });

  describe('Responsive design', () => {
    it('should render desktop layout on larger screens', () => {
      render(<Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />);

      // Desktop layout should have full pagination
      expect(screen.getByText('Previous')).toBeInTheDocument();
      expect(screen.getByText('Next')).toBeInTheDocument();
    });

    it('should show page info by default', () => {
      render(<Pagination currentPage={5} totalPages={10} onPageChange={mockOnPageChange} />);

      // Should show "Page 5 of 10"
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

      // Both desktop and mobile have spinbutton inputs
      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs.length).toBeGreaterThan(0);
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

      const inputs = screen.queryAllByRole('spinbutton');
      expect(inputs.length).toBe(0);
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

      // Should show 7 visible pages around current page
      // currentPage=10, totalPages=50, maxVisible=7
      // Expected: [1, '...', 7, 8, 9, 10, 11, 12, 13, '...', 50]
      const buttons = screen.getAllByRole('button');
      const pageButtons = buttons.filter((btn) =>
        btn.getAttribute('aria-label')?.includes('Go to page')
      );

      // Should have visible pages + first + last
      expect(pageButtons.length).toBeGreaterThan(7);
    });

    it('should sync input value with currentPage prop changes', () => {
      const { rerender } = render(
        <Pagination currentPage={1} totalPages={10} onPageChange={mockOnPageChange} />
      );

      const inputs = screen.getAllByRole('spinbutton', { name: /Go to page/i });
      const input = inputs[0]; // Desktop version input
      expect(input).toHaveValue(1);

      // Update currentPage prop
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

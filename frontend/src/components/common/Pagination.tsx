/**
 * Pagination Component - Reusable pagination with smart page display
 *
 * Features:
 * - Smart page number display (current page centered)
 * - Page jump input with validation
 * - Total pages display
 * - Accessibility support (ARIA attributes)
 * - Responsive design
 * - Keyboard navigation
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface PaginationProps {
  /** Current page number (1-based) */
  currentPage: number;
  /** Total number of pages */
  totalPages: number;
  /** Callback when page changes */
  onPageChange: (page: number) => void;
  /** Whether to show jump input, default true */
  showPageInput?: boolean;
  /**
   * Whether to show page info (deprecated - total pages now displayed separately)
   * @deprecated Total pages are now always displayed at the start
   */
  showPageInfo?: boolean;
  /** Maximum visible page numbers, default 5 */
  maxVisiblePages?: number;
  /** Additional CSS class */
  className?: string;
}

/**
 * Generate visible page numbers with smart ellipsis
 *
 * Strategy: Current page centered, show first and last page when needed
 *
 * Examples:
 * - currentPage=12, totalPages=50, maxVisible=5 → [1, '...', 10, 11, 12, 13, 14, '...', 50]
 * - currentPage=2, totalPages=50, maxVisible=5 → [1, 2, 3, 4, 5, '...', 50]
 * - currentPage=48, totalPages=50, maxVisible=5 → [1, '...', 46, 47, 48, 49, 50]
 */
export function getVisiblePages(
  currentPage: number,
  totalPages: number,
  maxVisible: number
): (number | 'ellipsis-start' | 'ellipsis-end')[] {
  // Edge cases
  if (totalPages <= 0) return [];
  if (totalPages <= maxVisible) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const pages: (number | 'ellipsis-start' | 'ellipsis-end')[] = [];

  // Always show first page
  pages.push(1);

  // Calculate range around current page
  const halfVisible = Math.floor(maxVisible / 2);
  let startPage = currentPage - halfVisible;
  let endPage = currentPage + halfVisible;

  // Adjust boundaries
  if (startPage <= 1) {
    startPage = 2;
    endPage = maxVisible;
  }
  if (endPage >= totalPages) {
    endPage = totalPages - 1;
    startPage = totalPages - maxVisible;
  }

  // Ensure startPage is at least 2 (after first page)
  startPage = Math.max(2, startPage);

  // Add ellipsis before middle range if needed
  if (startPage > 2) {
    pages.push('ellipsis-start');
  }

  // Add middle range pages
  for (let i = startPage; i <= endPage; i++) {
    pages.push(i);
  }

  // Add ellipsis after middle range if needed
  if (endPage < totalPages - 1) {
    pages.push('ellipsis-end');
  }

  // Always show last page
  if (totalPages > 1) {
    pages.push(totalPages);
  }

  return pages;
}

export const Pagination: React.FC<PaginationProps> = ({
  currentPage,
  totalPages,
  onPageChange,
  showPageInput = true,
  showPageInfo: _showPageInfo = true,
  maxVisiblePages = 5,
  className,
}) => {
  const language = useLanguage();
  const [inputValue, setInputValue] = useState<string>(String(currentPage));
  const [error, setError] = useState<string | null>(null);

  // Sync input value with currentPage prop changes
  useEffect(() => {
    setInputValue(String(currentPage));
    setError(null);
  }, [currentPage]);

  // Generate visible pages
  const visiblePages = useMemo(
    () => getVisiblePages(currentPage, totalPages, maxVisiblePages),
    [currentPage, totalPages, maxVisiblePages]
  );

  // Handle page change
  const handlePageChange = useCallback(
    (page: number) => {
      if (page >= 1 && page <= totalPages && page !== currentPage) {
        onPageChange(page);
      }
    },
    [currentPage, totalPages, onPageChange]
  );

  // Handle previous/next
  const handlePrevious = useCallback(() => {
    handlePageChange(currentPage - 1);
  }, [currentPage, handlePageChange]);

  const handleNext = useCallback(() => {
    handlePageChange(currentPage + 1);
  }, [currentPage, handlePageChange]);

  // Handle jump input submit
  const handleJumpSubmit = useCallback(() => {
    const pageNum = parseInt(inputValue, 10);

    if (isNaN(pageNum)) {
      setError(t('invalidPageNumber', language).replace('{total}', String(totalPages)));
      return;
    }

    if (pageNum < 1 || pageNum > totalPages) {
      setError(t('invalidPageNumber', language).replace('{total}', String(totalPages)));
      return;
    }

    setError(null);
    handlePageChange(pageNum);
  }, [inputValue, totalPages, language, handlePageChange]);

  // Handle input change
  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setInputValue(value);

      // Clear error when input is empty or valid
      if (!value) {
        setError(null);
        return;
      }

      const pageNum = parseInt(value, 10);
      if (!isNaN(pageNum) && pageNum >= 1 && pageNum <= totalPages) {
        setError(null);
      }
    },
    [totalPages]
  );

  // Handle Enter key
  const handleInputKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        handleJumpSubmit();
      }
    },
    [handleJumpSubmit]
  );

  // Handle keyboard navigation on pagination buttons
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, page: number) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handlePageChange(page);
      }
    },
    [handlePageChange]
  );

  // Clear error after 3 seconds
  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 3000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [error]);

  // Don't render if only one page
  if (totalPages <= 1) {
    return null;
  }

  return (
    <div className={cn('pagination-container', className)}>
      {/* Desktop layout - horizontal row: total pages | pagination buttons | jump input */}
      <div className="d-none d-sm-flex align-items-center justify-content-center gap-3">
        {/* Total pages display */}
        <span className="text-muted small">
          {t('totalPages', language).replace('{total}', String(totalPages))}
        </span>

        {/* Main pagination nav */}
        <nav aria-label={t('navigation', language)}>
          <ul className="pagination mb-0">
            {/* Previous button */}
            <li className={cn('page-item', currentPage === 1 && 'disabled')}>
              <button
                className="page-link"
                onClick={handlePrevious}
                disabled={currentPage === 1}
                aria-label={t('previousPage', language)}
                aria-disabled={currentPage === 1}
              >
                {t('previous', language) ?? 'Previous'}
              </button>
            </li>

            {/* Page numbers */}
            {visiblePages.map((page, index) => {
              if (page === 'ellipsis-start' || page === 'ellipsis-end') {
                return (
                  <li key={`ellipsis-${index}`} className="page-item disabled">
                    <span className="page-link">...</span>
                  </li>
                );
              }

              const isActive = page === currentPage;
              return (
                <li key={page} className={cn('page-item', isActive && 'active')}>
                  <button
                    className="page-link"
                    onClick={() => handlePageChange(page)}
                    onKeyDown={(e) => handleKeyDown(e, page)}
                    aria-label={t('goToPage', language).replace('{page}', String(page))}
                    aria-current={isActive ? 'page' : undefined}
                  >
                    {page}
                  </button>
                </li>
              );
            })}

            {/* Next button */}
            <li className={cn('page-item', currentPage === totalPages && 'disabled')}>
              <button
                className="page-link"
                onClick={handleNext}
                disabled={currentPage === totalPages}
                aria-label={t('nextPage', language)}
                aria-disabled={currentPage === totalPages}
              >
                {t('next', language) ?? 'Next'}
              </button>
            </li>
          </ul>
        </nav>

        {/* Jump input */}
        {showPageInput && (
          <div className="d-flex align-items-center gap-1">
            <span className="text-muted small">
              {t('goToPage', language).replace('{page}', '')}:
            </span>
            <input
              type="number"
              className="form-control form-control-sm"
              style={{ width: '60px' }}
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              min={1}
              max={totalPages}
              aria-label={t('goToPage', language).replace('{page}', '')}
            />
          </div>
        )}

        {/* Error message */}
        {error && (
          <span
            className="text-danger small"
            role="alert"
            aria-live="polite"
            style={{ animation: 'fadeIn 0.3s ease-in' }}
          >
            {error}
          </span>
        )}
      </div>

      {/* Mobile layout (simplified) */}
      <div className="d-sm-none d-flex flex-column align-items-center gap-2">
        {/* Total pages display */}
        <span className="text-muted small">
          {t('totalPages', language).replace('{total}', String(totalPages))}
        </span>

        {/* Simplified pagination */}
        <nav aria-label={t('navigation', language)}>
          <ul className="pagination mb-0">
            <li className={cn('page-item', currentPage === 1 && 'disabled')}>
              <button
                className="page-link"
                onClick={handlePrevious}
                disabled={currentPage === 1}
                aria-label={t('previousPage', language)}
              >
                <i className="bi bi-chevron-left" />
              </button>
            </li>
            <li className="page-item active">
              <span className="page-link">
                {currentPage} / {totalPages}
              </span>
            </li>
            <li className={cn('page-item', currentPage === totalPages && 'disabled')}>
              <button
                className="page-link"
                onClick={handleNext}
                disabled={currentPage === totalPages}
                aria-label={t('nextPage', language)}
              >
                <i className="bi bi-chevron-right" />
              </button>
            </li>
          </ul>
        </nav>

        {/* Mobile jump input */}
        {showPageInput && (
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('goToPage', language).replace('{page}', '')}:</small>
            <input
              type="number"
              className="form-control form-control-sm"
              style={{ width: '50px' }}
              value={inputValue}
              onChange={handleInputChange}
              onKeyDown={handleInputKeyDown}
              min={1}
              max={totalPages}
              aria-label={t('goToPage', language).replace('{page}', '')}
            />
            {error && (
              <span className="text-danger small" role="alert" aria-live="polite">
                {error}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Inline styles for animations */}
      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
};

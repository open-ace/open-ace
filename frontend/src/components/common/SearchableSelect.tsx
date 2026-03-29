/**
 * SearchableSelect Component - Select dropdown with search functionality
 */

import React, { useState, useRef, useEffect } from 'react';
import { cn } from '@/utils';
import type { SelectOption } from '@/types';

interface SearchableSelectProps {
  options: SelectOption[];
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  disabled?: boolean;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  id?: string;
  'data-testid'?: string;
  style?: React.CSSProperties;
}

const sizeClasses: Record<string, string> = {
  sm: 'form-select-sm',
  md: '',
  lg: 'form-select-lg',
};

export const SearchableSelect: React.FC<SearchableSelectProps> = ({
  options,
  value,
  onChange,
  placeholder = 'Select...',
  searchPlaceholder = 'Search...',
  disabled = false,
  size = 'md',
  className,
  id,
  'data-testid': testId,
  style,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Find selected option label
  const selectedOption = options.find((opt) => opt.value === value);
  const selectedLabel = selectedOption?.label ?? placeholder;

  // Filter options based on search
  const filteredOptions = options.filter((opt) =>
    (opt.label ?? '').toLowerCase().includes(search.toLowerCase())
  );

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  const handleSelect = (optionValue: string) => {
    onChange(optionValue);
    setIsOpen(false);
    setSearch('');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
      setSearch('');
    } else if (e.key === 'Enter' && filteredOptions.length > 0) {
      handleSelect(filteredOptions[0].value);
    }
  };

  return (
    <div
      ref={containerRef}
      className={cn('searchable-select position-relative', className)}
      id={id}
      data-testid={testId}
      style={style}
    >
      {/* Trigger Button */}
      <button
        type="button"
        className={cn(
          'form-select text-start d-flex justify-content-between align-items-center',
          sizeClasses[size],
          disabled && 'disabled'
        )}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        disabled={disabled}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
      >
        <span className={cn(!selectedOption && 'text-muted')}>{selectedLabel}</span>
        <i className={cn('bi', isOpen ? 'bi-chevron-up' : 'bi-chevron-down')} />
      </button>

      {/* Dropdown Menu */}
      {isOpen && (
        <div
          className="position-absolute top-100 start-0 mt-1 bg-white border rounded shadow-sm"
          style={{ minWidth: '100%', maxHeight: '300px', overflow: 'hidden', zIndex: 1050 }}
          role="listbox"
        >
          {/* Search Input */}
          <div className="p-2 border-bottom">
            <input
              ref={inputRef}
              type="text"
              className="form-control form-control-sm"
              placeholder={searchPlaceholder}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>

          {/* Options List */}
          <div className="overflow-auto" style={{ maxHeight: '250px' }}>
            {filteredOptions.length === 0 ? (
              <div className="p-2 text-muted text-center small">No options found</div>
            ) : (
              filteredOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={cn(
                    'text-start px-3 py-2 border-0 bg-transparent',
                    'hover:bg-light cursor-pointer text-nowrap',
                    option.value === value && 'bg-primary bg-opacity-10',
                    option.disabled && 'text-muted'
                  )}
                  style={{ minWidth: '100%' }}
                  onClick={() => !option.disabled && handleSelect(option.value)}
                  disabled={option.disabled}
                  role="option"
                  aria-selected={option.value === value}
                >
                  {option.label}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
};

/**
 * DatePicker Component
 *
 * A theme-aware date picker that wraps react-datepicker.
 * Supports dark mode via the app's [data-theme='dark'] system,
 * replacing the native <input type="date"> whose calendar popup
 * cannot be styled in Firefox.
 */

import React, { forwardRef, useCallback } from 'react';
import ReactDatePicker from 'react-datepicker';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import type { DatePickerProps } from '@/types';
import 'react-datepicker/dist/react-datepicker.css';

/**
 * Custom input that mimics Bootstrap's .form-control appearance.
 */
const DateInput = forwardRef<
  HTMLButtonElement,
  {
    value?: string;
    onClick?: () => void;
    disabled?: boolean;
    placeholder?: string;
    className?: string;
  }
>(({ value, onClick, disabled, placeholder, className }, ref) => (
  <button
    type="button"
    ref={ref}
    onClick={onClick}
    disabled={disabled}
    className={cn(
      'form-control text-start d-flex align-items-center justify-content-between gap-2',
      className
    )}
    style={{ cursor: disabled ? 'not-allowed' : 'pointer' }}
  >
    <span className={cn(!value && 'text-muted', 'flex-grow-1 text-truncate')}>
      {value || placeholder || 'Select date'}
    </span>
    <i className="bi bi-calendar3 text-muted flex-shrink-0" />
  </button>
));
DateInput.displayName = 'DateInput';

export const DatePicker: React.FC<DatePickerProps> = ({
  value,
  onChange,
  min,
  max,
  placeholder,
  disabled = false,
  className,
}) => {
  const language = useLanguage();

  const selectedDate = value ? new Date(value + 'T00:00:00') : null;
  const minDate = min ? new Date(min + 'T00:00:00') : undefined;
  const maxDate = max ? new Date(max + 'T00:00:00') : undefined;

  const handleChange = useCallback(
    (date: Date | null) => {
      if (!date) return;
      // Format as YYYY-MM-DD
      const y = date.getFullYear();
      const m = String(date.getMonth() + 1).padStart(2, '0');
      const d = String(date.getDate()).padStart(2, '0');
      onChange(`${y}-${m}-${d}`);
    },
    [onChange]
  );

  // Locale-aware month/day names
  const locale = language === 'zh' ? 'zh-CN' : language === 'ja' ? 'ja' : 'en-US';

  return (
    <div className={cn('open-ace-datepicker', className)}>
      <ReactDatePicker
        selected={selectedDate}
        onChange={handleChange as unknown as (date: Date | null) => void}
        minDate={minDate}
        maxDate={maxDate}
        dateFormat="yyyy/MM/dd"
        locale={locale}
        disabled={disabled}
        placeholderText={placeholder}
        customInput={<DateInput disabled={disabled} placeholder={placeholder} />}
        popperPlacement="bottom-start"
        calendarClassName="open-ace-datepicker-calendar"
      />
    </div>
  );
};

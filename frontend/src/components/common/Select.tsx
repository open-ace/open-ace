/**
 * Select Component - Reusable select dropdown
 */

import React from 'react';
import { cn } from '@/utils';
import type { SelectProps, SelectOption } from '@/types';

const sizeClasses: Record<string, string> = {
  sm: 'form-select-sm',
  md: '',
  lg: 'form-select-lg',
};

export const Select: React.FC<SelectProps> = ({
  options,
  value,
  onChange,
  placeholder,
  disabled = false,
  size = 'md',
  className,
  id,
  'data-testid': testId,
  style,
}) => {
  return (
    <select
      id={id}
      data-testid={testId}
      className={cn('form-select', sizeClasses[size], className)}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      style={style}
    >
      {placeholder && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((option) => (
        <option key={option.value} value={option.value} disabled={option.disabled}>
          {option.label}
        </option>
      ))}
    </select>
  );
};

/**
 * Helper function to create options from string array
 */
export function createOptions(values: string[], labels?: Record<string, string>): SelectOption[] {
  return values.map((value) => ({
    value,
    label: labels?.[value] || value,
  }));
}

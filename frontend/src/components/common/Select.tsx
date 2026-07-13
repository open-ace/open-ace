/**
 * Select Component - Reusable select dropdown with optgroup support
 */

import React from 'react';
import { cn } from '@/utils';
import type { SelectProps, SelectOption, GroupedSelectOption, SelectOptionOrGroup } from '@/types';

const sizeClasses: Record<string, string> = {
  sm: 'form-select-sm',
  md: '',
  lg: 'form-select-lg',
};

/**
 * Check if an option is a group
 */
function isGroupedOption(option: SelectOptionOrGroup): option is GroupedSelectOption {
  return 'options' in option && Array.isArray(option.options);
}

type CombinedSelectProps = SelectProps & {
  groupedOptions?: SelectOptionOrGroup[];
};

export const Select: React.FC<CombinedSelectProps> = ({
  options,
  groupedOptions,
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
  // Use groupedOptions if provided, otherwise use flat options
  const renderOptions: SelectOptionOrGroup[] =
    groupedOptions ?? options?.map((o) => o as SelectOptionOrGroup) ?? [];

  return (
    <select
      id={id}
      data-testid={testId}
      className={cn('form-select', sizeClasses[size], className)}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      style={style}
    >
      {placeholder && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {renderOptions.map((option: SelectOptionOrGroup, index: number) => {
        if (isGroupedOption(option)) {
          return (
            <optgroup key={`group-${index}`} label={option.label}>
              {(option.options ?? []).map((subOption: SelectOption) => (
                <option key={subOption.value} value={subOption.value} disabled={subOption.disabled}>
                  {subOption.label}
                </option>
              ))}
            </optgroup>
          );
        } else {
          const flatOption = option as SelectOption;
          return (
            <option key={flatOption.value} value={flatOption.value} disabled={flatOption.disabled}>
              {flatOption.label}
            </option>
          );
        }
      })}
    </select>
  );
};

/**
 * Helper function to create options from string array
 */
export function createOptions(values: string[], labels?: Record<string, string>): SelectOption[] {
  return values.map((value) => ({
    value,
    label: labels?.[value] ?? value,
  }));
}

/**
 * MultiValueInput Component - Dynamic multi-value input with add/remove
 *
 * Issue #2572: Used for category key_patterns editing
 */

import React, { useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { cn } from '@/utils';
import { Button } from './Button';

export interface MultiValueInputProps {
  /** Current values */
  value: string[];
  /** Change handler */
  onChange: (value: string[]) => void;
  /** Placeholder for each input */
  placeholder?: string;
  /** Maximum number of items */
  maxItems?: number;
  /** Maximum length per item */
  maxLength?: number;
  /** Whether items can be empty */
  allowEmpty?: boolean;
  /** Label for the field */
  label?: string;
  /** Error message */
  error?: string;
  /** Hint text */
  hint?: string;
  /** Whether field is required */
  required?: boolean;
  /** Whether field is disabled */
  disabled?: boolean;
  /** Additional class name */
  className?: string;
}

/**
 * Multi-value input component for editing arrays of strings
 * Supports dynamic add/remove of input items
 */
export const MultiValueInput: React.FC<MultiValueInputProps> = ({
  value = [],
  onChange,
  placeholder = 'Enter value...',
  maxItems = 20,
  maxLength = 128,
  allowEmpty = false,
  label,
  error,
  hint,
  required,
  disabled,
  className,
}) => {
  const language = useLanguage();

  const handleAdd = useCallback(() => {
    if (value.length < maxItems && !disabled) {
      onChange([...value, '']);
    }
  }, [value, maxItems, disabled, onChange]);

  const handleRemove = useCallback(
    (index: number) => {
      if (!disabled) {
        const newValue = [...value];
        newValue.splice(index, 1);
        onChange(newValue);
      }
    },
    [value, disabled, onChange]
  );

  const handleChange = useCallback(
    (index: number, newValue: string) => {
      if (!disabled) {
        // Enforce max length
        const truncatedValue = maxLength > 0 ? newValue.slice(0, maxLength) : newValue;
        const updatedValue = [...value];
        updatedValue[index] = truncatedValue;
        onChange(updatedValue);
      }
    },
    [value, disabled, maxLength, onChange]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>, index: number) => {
      // Remove empty item on backspace
      if (e.key === 'Backspace' && value[index] === '' && index > 0) {
        handleRemove(index);
        e.preventDefault();
      }
      // Add new item on Enter
      if (e.key === 'Enter' && index === value.length - 1 && value[index] !== '') {
        handleAdd();
        e.preventDefault();
      }
    },
    [value, handleRemove, handleAdd]
  );

  const canAdd = value.length < maxItems && !disabled;
  const hasError = error ?? (!allowEmpty && value.some((v) => v.trim() === ''));

  return (
    <div className={cn('form-group', className)}>
      {label && (
        <label className="form-label">
          {label}
          {required && <span className="text-danger ms-1">*</span>}
        </label>
      )}
      <div className="multi-value-input">
        {value.map((item, index) => (
          <div key={index} className="multi-value-item input-group mb-2">
            <input
              type="text"
              className={cn('form-control', hasError && 'is-invalid')}
              value={item}
              onChange={(e) => handleChange(index, e.target.value)}
              onKeyDown={(e) => handleKeyDown(e, index)}
              placeholder={placeholder}
              disabled={disabled}
              maxLength={maxLength}
            />
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => handleRemove(index)}
              disabled={disabled}
              aria-label={`Remove item ${index + 1}`}
            >
              <i className="bi bi-x-lg" />
            </Button>
          </div>
        ))}
        {canAdd && (
          <Button variant="outline-primary" size="sm" onClick={handleAdd} disabled={disabled}>
            <i className="bi bi-plus-lg me-1" />
            {t('add', language)}
          </Button>
        )}
      </div>
      {error && <div className="invalid-feedback d-block">{error}</div>}
      {!error && hint && <small className="form-text text-muted">{hint}</small>}
    </div>
  );
};

export default MultiValueInput;

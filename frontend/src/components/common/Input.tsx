/**
 * Input Components - Form inputs with validation
 */

import React, { forwardRef } from 'react';
import { cn } from '@/utils';

// Base input props
interface BaseInputProps {
  label?: string;
  error?: string;
  hint?: string;
  required?: boolean;
  disabled?: boolean;
  className?: string;
}

// Text Input
interface TextInputProps extends BaseInputProps {
  type?: 'text' | 'email' | 'password' | 'number' | 'url' | 'tel' | 'date';
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  icon?: React.ReactNode;
  iconPosition?: 'left' | 'right';
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(
  (
    {
      type = 'text',
      label,
      placeholder,
      value,
      onChange,
      error,
      hint,
      required,
      disabled,
      icon,
      iconPosition = 'left',
      className,
    },
    ref
  ) => {
    return (
      <div className={cn('form-group', className)}>
        {label && (
          <label className="form-label">
            {label}
            {required && <span className="text-danger ms-1">*</span>}
          </label>
        )}
        <div className={cn('input-group', icon && `has-icon-${iconPosition}`)}>
          {icon && iconPosition === 'left' && <span className="input-icon">{icon}</span>}
          <input
            ref={ref}
            type={type}
            className={cn('form-control', error && 'is-invalid')}
            placeholder={placeholder}
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            disabled={disabled}
            required={required}
          />
          {icon && iconPosition === 'right' && <span className="input-icon">{icon}</span>}
          {error && <div className="invalid-feedback">{error}</div>}
        </div>
        {hint && !error && <small className="form-text text-muted">{hint}</small>}
      </div>
    );
  }
);

TextInput.displayName = 'TextInput';

// Textarea
interface TextareaProps extends BaseInputProps {
  placeholder?: string;
  value?: string;
  onChange?: (value: string) => void;
  rows?: number;
  maxLength?: number;
  showCount?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  (
    {
      label,
      placeholder,
      value = '',
      onChange,
      rows = 4,
      error,
      hint,
      required,
      disabled,
      maxLength,
      showCount = false,
      className,
    },
    ref
  ) => {
    return (
      <div className={cn('form-group', className)}>
        {label && (
          <label className="form-label">
            {label}
            {required && <span className="text-danger ms-1">*</span>}
          </label>
        )}
        <textarea
          ref={ref}
          className={cn('form-control', error && 'is-invalid')}
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          rows={rows}
          disabled={disabled}
          required={required}
          maxLength={maxLength}
        />
        <div className="d-flex justify-content-between">
          {error ? (
            <div className="invalid-feedback d-block">{error}</div>
          ) : hint ? (
            <small className="form-text text-muted">{hint}</small>
          ) : (
            <span />
          )}
          {showCount && maxLength && (
            <small className="text-muted">
              {value.length}/{maxLength}
            </small>
          )}
        </div>
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';

// Checkbox
interface CheckboxProps {
  label: string;
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  indeterminate?: boolean;
  className?: string;
}

export const Checkbox: React.FC<CheckboxProps> = ({
  label,
  checked = false,
  onChange,
  disabled,
  indeterminate,
  className,
}) => {
  return (
    <div className={cn('form-check', className)}>
      <input
        type="checkbox"
        className="form-check-input"
        checked={checked}
        onChange={(e) => onChange?.(e.target.checked)}
        disabled={disabled}
        ref={(el) => {
          if (el) el.indeterminate = indeterminate ?? false;
        }}
      />
      <label className="form-check-label">{label}</label>
    </div>
  );
};

// Radio
interface RadioOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface RadioGroupProps {
  name: string;
  options: RadioOption[];
  value?: string;
  onChange?: (value: string) => void;
  inline?: boolean;
  className?: string;
}

export const RadioGroup: React.FC<RadioGroupProps> = ({
  name,
  options,
  value,
  onChange,
  inline = false,
  className,
}) => {
  return (
    <div className={cn('radio-group', inline && 'd-flex gap-3', className)}>
      {options.map((option) => (
        <div key={option.value} className="form-check">
          <input
            type="radio"
            className="form-check-input"
            name={name}
            id={`${name}-${option.value}`}
            value={option.value}
            checked={value === option.value}
            onChange={(e) => onChange?.(e.target.value)}
            disabled={option.disabled}
          />
          <label className="form-check-label" htmlFor={`${name}-${option.value}`}>
            {option.label}
          </label>
        </div>
      ))}
    </div>
  );
};

// Switch
interface SwitchProps {
  label: string;
  checked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export const Switch: React.FC<SwitchProps> = ({
  label,
  checked = false,
  onChange,
  disabled,
  size = 'md',
  className,
}) => {
  const sizeClasses = {
    sm: 'form-switch-sm',
    md: '',
    lg: 'form-switch-lg',
  };

  return (
    <div className={cn('form-check form-switch', sizeClasses[size], className)}>
      <input
        type="checkbox"
        className="form-check-input"
        checked={checked}
        onChange={(e) => onChange?.(e.target.checked)}
        disabled={disabled}
        role="switch"
      />
      <label className="form-check-label">{label}</label>
    </div>
  );
};

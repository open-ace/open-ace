/**
 * Button Component - Reusable button with variants and loading state
 */

import React from 'react';
import { cn } from '@/utils';
import type { ButtonProps } from '@/types';

const variantClasses: Record<string, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  success: 'btn-success',
  danger: 'btn-danger',
  warning: 'btn-warning',
  info: 'btn-info',
  light: 'btn-light',
  dark: 'btn-dark',
  link: 'btn-link',
  'outline-primary': 'btn-outline-primary',
  'outline-secondary': 'btn-outline-secondary',
  'outline-success': 'btn-outline-success',
  'outline-danger': 'btn-outline-danger',
  'outline-warning': 'btn-outline-warning',
  'outline-info': 'btn-outline-info',
  'outline-light': 'btn-outline-light',
  'outline-dark': 'btn-outline-dark',
};

const sizeClasses: Record<string, string> = {
  sm: 'btn-sm',
  md: '',
  lg: 'btn-lg',
};

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  onClick,
  type = 'button',
  children,
  icon,
  fullWidth = false,
  className,
  id,
  'data-testid': testId,
}) => {
  return (
    <button
      type={type}
      id={id}
      data-testid={testId}
      className={cn(
        'btn',
        variantClasses[variant],
        sizeClasses[size],
        fullWidth && 'w-100',
        className
      )}
      disabled={disabled || loading}
      onClick={onClick}
    >
      {loading ? (
        <>
          <span
            className="spinner-border spinner-border-sm me-2"
            role="status"
            aria-hidden="true"
          />
          {children}
        </>
      ) : (
        <>
          {icon && <span className="me-2">{icon}</span>}
          {children}
        </>
      )}
    </button>
  );
};

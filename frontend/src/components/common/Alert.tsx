/**
 * Alert Component - Bootstrap-style alert with variant support
 */

import React from 'react';
import { cn } from '@/utils';

export type AlertVariant = 'info' | 'success' | 'warning' | 'danger';

export interface AlertProps {
  variant?: AlertVariant;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  id?: string;
  'data-testid'?: string;
}

const variantClasses: Record<AlertVariant, string> = {
  info: 'alert-info',
  success: 'alert-success',
  warning: 'alert-warning',
  danger: 'alert-danger',
};

const variantIcons: Record<AlertVariant, string> = {
  info: 'bi-info-circle-fill',
  success: 'bi-check-circle-fill',
  warning: 'bi-exclamation-triangle-fill',
  danger: 'bi-exclamation-circle-fill',
};

export const Alert: React.FC<AlertProps> = ({
  variant = 'info',
  children,
  className,
  style,
  id,
  'data-testid': testId,
}) => {
  return (
    <div
      id={id}
      data-testid={testId}
      className={cn('alert', variantClasses[variant], className)}
      style={style}
      role="alert"
    >
      <i className={cn('bi', variantIcons[variant], 'me-2')} />
      {children}
    </div>
  );
};

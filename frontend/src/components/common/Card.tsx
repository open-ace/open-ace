/**
 * Card Component - Reusable card container
 */

import React from 'react';
import { cn } from '@/utils';
import type { CardProps } from '@/types';

const variantClasses: Record<string, string> = {
  default: '',
  primary: 'border-primary',
  success: 'border-success',
  warning: 'border-warning',
  danger: 'border-danger',
  info: 'border-info',
};

export const Card: React.FC<CardProps> = ({
  title,
  subtitle,
  icon,
  variant = 'default',
  children,
  footer,
  className,
  id,
  'data-testid': testId,
}) => {
  return (
    <div id={id} data-testid={testId} className={cn('card', variantClasses[variant], className)}>
      {(title || subtitle || icon) && (
        <div className="card-header d-flex align-items-center">
          {icon && <span className="me-2">{icon}</span>}
          <div>
            {title && <h5 className="card-title mb-0">{title}</h5>}
            {subtitle && <small className="text-muted">{subtitle}</small>}
          </div>
        </div>
      )}
      <div className="card-body">{children}</div>
      {footer && <div className="card-footer">{footer}</div>}
    </div>
  );
};

/**
 * StatCard Component - Card for displaying statistics
 */
export interface StatCardProps {
  label: string;
  value: number | string;
  icon?: React.ReactNode;
  trend?: {
    value: number;
    isPositive: boolean;
  };
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';
  className?: string;
}

const statVariantClasses: Record<string, string> = {
  default: 'bg-light',
  primary: 'bg-primary text-white',
  success: 'bg-success text-white',
  warning: 'bg-warning',
  danger: 'bg-danger text-white',
  info: 'bg-info text-white',
};

export const StatCard: React.FC<StatCardProps> = ({
  label,
  value,
  icon,
  trend,
  variant = 'default',
  className,
}) => {
  return (
    <div className={cn('card stat-card', statVariantClasses[variant], className)}>
      <div className="card-body">
        <div className="d-flex justify-content-between align-items-start">
          <div>
            <h6 className="card-subtitle mb-2 text-muted">{label}</h6>
            <h3 className="card-title mb-0">{value}</h3>
            {trend && (
              <small
                className={cn(
                  'd-flex align-items-center mt-1',
                  trend.isPositive ? 'text-success' : 'text-danger'
                )}
              >
                <i className={cn('bi me-1', trend.isPositive ? 'bi-arrow-up' : 'bi-arrow-down')} />
                {Math.abs(trend.value)}%
              </small>
            )}
          </div>
          {icon && <div className="stat-icon">{icon}</div>}
        </div>
      </div>
    </div>
  );
};

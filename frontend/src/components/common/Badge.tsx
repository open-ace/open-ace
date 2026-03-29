/**
 * Badge Component - Status badges and labels
 */

import React from 'react';
import { cn } from '@/utils';

export type BadgeVariant =
  | 'primary'
  | 'secondary'
  | 'success'
  | 'danger'
  | 'warning'
  | 'info'
  | 'light'
  | 'dark';

interface BadgeProps {
  variant?: BadgeVariant;
  pill?: boolean;
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
  dismissible?: boolean;
  onDismiss?: () => void;
}

const variantClasses: Record<BadgeVariant, string> = {
  primary: 'bg-primary',
  secondary: 'bg-secondary',
  success: 'bg-success',
  danger: 'bg-danger',
  warning: 'bg-warning text-dark',
  info: 'bg-info text-dark',
  light: 'bg-light text-dark',
  dark: 'bg-dark',
};

export const Badge: React.FC<BadgeProps> = ({
  variant = 'primary',
  pill = false,
  children,
  className,
  onClick,
  dismissible = false,
  onDismiss,
}) => {
  const Component = onClick ? 'button' : 'span';

  return (
    <Component
      className={cn(
        'badge',
        variantClasses[variant],
        pill && 'rounded-pill',
        onClick && 'btn',
        className
      )}
      onClick={onClick}
      style={onClick ? { border: 'none', cursor: 'pointer' } : undefined}
    >
      {children}
      {dismissible && onDismiss && (
        <button
          type="button"
          className="btn-close btn-close-white ms-2"
          style={{ fontSize: '0.65em' }}
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          aria-label="Remove"
        />
      )}
    </Component>
  );
};

/**
 * Status Badge - Pre-built status indicators
 */
interface StatusBadgeProps {
  status: 'online' | 'offline' | 'busy' | 'away' | 'active' | 'inactive' | 'pending' | 'error';
  label?: string;
  pulse?: boolean;
  className?: string;
}

const statusConfig: Record<string, { color: string; variant: BadgeVariant }> = {
  online: { color: 'bg-success', variant: 'success' },
  offline: { color: 'bg-secondary', variant: 'secondary' },
  busy: { color: 'bg-danger', variant: 'danger' },
  away: { color: 'bg-warning', variant: 'warning' },
  active: { color: 'bg-success', variant: 'success' },
  inactive: { color: 'bg-secondary', variant: 'secondary' },
  pending: { color: 'bg-info', variant: 'info' },
  error: { color: 'bg-danger', variant: 'danger' },
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  label,
  pulse = false,
  className,
}) => {
  const config = statusConfig[status] || statusConfig.offline;

  return (
    <span className={cn('d-inline-flex align-items-center', className)}>
      <span
        className={cn('rounded-circle me-1', config.color, pulse && 'animate-pulse')}
        style={{ width: 8, height: 8 }}
      />
      <Badge variant={config.variant}>{label ?? status}</Badge>
    </span>
  );
};

/**
 * Count Badge - For notifications or counts
 */
interface CountBadgeProps {
  count: number;
  max?: number;
  variant?: BadgeVariant;
  className?: string;
}

export const CountBadge: React.FC<CountBadgeProps> = ({
  count,
  max = 99,
  variant = 'danger',
  className,
}) => {
  if (count <= 0) return null;

  const displayCount = count > max ? `${max}+` : count;

  return (
    <Badge
      variant={variant}
      pill
      className={cn('position-absolute top-0 start-100 translate-middle', className)}
    >
      {displayCount}
    </Badge>
  );
};

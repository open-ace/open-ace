/**
 * Error Component - Error display component
 */

import React from 'react';
import { cn } from '@/utils';
import { Button } from './Button';
import type { ErrorProps } from '@/types';

export const Error: React.FC<ErrorProps> = ({
  message,
  onRetry,
  className,
  id,
  'data-testid': testId,
}) => {
  return (
    <div
      id={id}
      data-testid={testId}
      className={cn('alert alert-danger d-flex align-items-center', className)}
      role="alert"
    >
      <i className="bi bi-exclamation-triangle-fill me-2" />
      <div className="flex-grow-1">{message}</div>
      {onRetry && (
        <Button variant="outline-danger" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
};

/**
 * EmptyState Component - Display when no data is available
 */
export interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon = 'bi-inbox',
  title,
  description,
  action,
  className,
}) => {
  return (
    <div className={cn('text-center py-5', className)}>
      <i className={cn('bi', icon, 'fs-1 text-muted d-block mb-3')} />
      <h5 className="text-muted">{title}</h5>
      {description && <p className="text-muted">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
};

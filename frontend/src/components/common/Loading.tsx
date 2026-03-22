/**
 * Loading Component - Loading spinner and skeleton
 */

import React from 'react';
import { cn } from '@/utils';
import type { LoadingProps } from '@/types';

const sizeClasses: Record<string, string> = {
  sm: 'spinner-border-sm',
  md: '',
  lg: 'spinner-border-lg',
};

export const Loading: React.FC<LoadingProps> = ({
  size = 'md',
  text,
  className,
  id,
  'data-testid': testId,
}) => {
  return (
    <div
      id={id}
      data-testid={testId}
      className={cn('d-flex align-items-center justify-content-center', className)}
    >
      <div className={cn('spinner-border', sizeClasses[size])} role="status">
        <span className="visually-hidden">{text || 'Loading...'}</span>
      </div>
      {text && <span className="ms-2">{text}</span>}
    </div>
  );
};

/**
 * LoadingOverlay Component - Full page loading overlay
 */
export const LoadingOverlay: React.FC<{ text?: string }> = ({ text }) => {
  return (
    <div className="loading-overlay position-fixed top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-white bg-opacity-75">
      <Loading size="lg" text={text} />
    </div>
  );
};

/**
 * Skeleton Component - Loading placeholder
 */
export interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  className?: string;
  variant?: 'text' | 'circular' | 'rectangular';
}

export const Skeleton: React.FC<SkeletonProps> = ({
  width,
  height,
  className,
  variant = 'text',
}) => {
  const style: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
  };

  return (
    <div
      className={cn(
        'skeleton',
        variant === 'circular' && 'rounded-circle',
        variant === 'rectangular' && 'rounded',
        className
      )}
      style={style}
    />
  );
};

/**
 * SkeletonCard Component - Card skeleton for loading state
 */
export const SkeletonCard: React.FC = () => {
  return (
    <div className="card">
      <div className="card-body">
        <Skeleton width="60%" height={20} className="mb-2" />
        <Skeleton width="100%" height={40} className="mb-2" />
        <Skeleton width="40%" height={16} />
      </div>
    </div>
  );
};

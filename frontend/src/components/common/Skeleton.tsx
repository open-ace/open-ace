/**
 * Skeleton Component - Loading placeholder with animation
 */

import React from 'react';
import { cn } from '@/utils';

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  variant?: 'text' | 'circular' | 'rectangular' | 'rounded';
  animation?: 'pulse' | 'wave' | 'none';
  className?: string;
}

export const Skeleton: React.FC<SkeletonProps> = ({
  width,
  height,
  variant = 'text',
  animation = 'pulse',
  className,
}) => {
  const variantClasses: Record<string, string> = {
    text: 'skeleton-text',
    circular: 'skeleton-circular rounded-circle',
    rectangular: 'skeleton-rectangular',
    rounded: 'skeleton-rounded rounded',
  };

  const animationClasses: Record<string, string> = {
    pulse: 'animate-pulse',
    wave: 'animate-wave',
    none: '',
  };

  return (
    <div
      className={cn('skeleton', variantClasses[variant], animationClasses[animation], className)}
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
      }}
    />
  );
};

/**
 * SkeletonText - Multiple text lines
 */
interface SkeletonTextProps {
  lines?: number;
  lastLineWidth?: string;
  className?: string;
}

export const SkeletonText: React.FC<SkeletonTextProps> = ({
  lines = 3,
  lastLineWidth = '60%',
  className,
}) => {
  return (
    <div className={cn('skeleton-text-container', className)}>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton
          key={index}
          variant="text"
          height={16}
          width={index === lines - 1 ? lastLineWidth : '100%'}
          className="mb-2"
        />
      ))}
    </div>
  );
};

/**
 * SkeletonCard - Card placeholder
 */
interface SkeletonCardProps {
  hasImage?: boolean;
  hasHeader?: boolean;
  lines?: number;
  className?: string;
}

export const SkeletonCard: React.FC<SkeletonCardProps> = ({
  hasImage = false,
  hasHeader = true,
  lines = 3,
  className,
}) => {
  return (
    <div className={cn('card skeleton-card', className)}>
      {hasImage && <Skeleton variant="rectangular" height={200} className="card-img-top" />}
      <div className="card-body">
        {hasHeader && (
          <div className="d-flex align-items-center mb-3">
            <Skeleton variant="circular" width={40} height={40} />
            <div className="ms-3 flex-grow-1">
              <Skeleton variant="text" height={16} width="40%" className="mb-1" />
              <Skeleton variant="text" height={12} width="60%" />
            </div>
          </div>
        )}
        <SkeletonText lines={lines} />
      </div>
    </div>
  );
};

/**
 * SkeletonTable - Table placeholder
 */
interface SkeletonTableProps {
  rows?: number;
  columns?: number;
  className?: string;
}

export const SkeletonTable: React.FC<SkeletonTableProps> = ({
  rows = 5,
  columns = 4,
  className,
}) => {
  return (
    <div className={cn('skeleton-table', className)}>
      {/* Header */}
      <div className="skeleton-table-header d-flex gap-3 mb-3 pb-2 border-bottom">
        {Array.from({ length: columns }).map((_, index) => (
          <Skeleton key={index} variant="text" height={14} className="flex-grow-1" />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="skeleton-table-row d-flex gap-3 mb-3">
          {Array.from({ length: columns }).map((_, colIndex) => (
            <Skeleton key={colIndex} variant="text" height={14} className="flex-grow-1" />
          ))}
        </div>
      ))}
    </div>
  );
};

/**
 * SkeletonList - List placeholder
 */
interface SkeletonListProps {
  items?: number;
  hasAvatar?: boolean;
  className?: string;
}

export const SkeletonList: React.FC<SkeletonListProps> = ({
  items = 5,
  hasAvatar = true,
  className,
}) => {
  return (
    <div className={cn('skeleton-list', className)}>
      {Array.from({ length: items }).map((_, index) => (
        <div key={index} className="skeleton-list-item d-flex align-items-center mb-3">
          {hasAvatar && <Skeleton variant="circular" width={40} height={40} className="me-3" />}
          <div className="flex-grow-1">
            <Skeleton variant="text" height={14} width="30%" className="mb-1" />
            <Skeleton variant="text" height={12} width="80%" />
          </div>
        </div>
      ))}
    </div>
  );
};

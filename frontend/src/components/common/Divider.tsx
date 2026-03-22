/**
 * Divider Component - Visual separators
 */

import React from 'react';
import { cn } from '@/utils';

interface DividerProps {
  orientation?: 'horizontal' | 'vertical';
  variant?: 'solid' | 'dashed' | 'dotted';
  text?: string;
  className?: string;
}

export const Divider: React.FC<DividerProps> = ({
  orientation = 'horizontal',
  variant = 'solid',
  text,
  className,
}) => {
  const variantStyles: Record<string, string> = {
    solid: 'solid',
    dashed: 'dashed',
    dotted: 'dotted',
  };

  if (orientation === 'vertical') {
    return (
      <div
        className={cn('divider-vertical', className)}
        style={{
          width: 1,
          height: '100%',
          borderLeft: `1px ${variantStyles[variant]} var(--border-color)`,
        }}
        role="separator"
        aria-orientation="vertical"
      />
    );
  }

  if (text) {
    return (
      <div className={cn('divider-text d-flex align-items-center', className)} role="separator">
        <div
          className="flex-grow-1"
          style={{ borderTop: `1px ${variantStyles[variant]} var(--border-color)` }}
        />
        <span className="px-3 text-muted small">{text}</span>
        <div
          className="flex-grow-1"
          style={{ borderTop: `1px ${variantStyles[variant]} var(--border-color)` }}
        />
      </div>
    );
  }

  return (
    <div
      className={cn('divider-horizontal', className)}
      style={{ borderTop: `1px ${variantStyles[variant]} var(--border-color)` }}
      role="separator"
      aria-orientation="horizontal"
    />
  );
};

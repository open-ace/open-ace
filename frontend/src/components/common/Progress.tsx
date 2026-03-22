/**
 * Progress Component - Progress bars and indicators
 */

import React from 'react';
import { cn } from '@/utils';

interface ProgressProps {
  value: number;
  max?: number;
  variant?: 'primary' | 'secondary' | 'success' | 'danger' | 'warning' | 'info';
  size?: 'sm' | 'md' | 'lg';
  striped?: boolean;
  animated?: boolean;
  label?: string;
  showValue?: boolean;
  className?: string;
}

const variantClasses: Record<string, string> = {
  primary: 'bg-primary',
  secondary: 'bg-secondary',
  success: 'bg-success',
  danger: 'bg-danger',
  warning: 'bg-warning',
  info: 'bg-info',
};

const sizeClasses: Record<string, string> = {
  sm: 'progress-sm',
  md: '',
  lg: 'progress-lg',
};

export const Progress: React.FC<ProgressProps> = ({
  value,
  max = 100,
  variant = 'primary',
  size = 'md',
  striped = false,
  animated = false,
  label,
  showValue = false,
  className,
}) => {
  const percentage = Math.min(100, Math.max(0, (value / max) * 100));

  return (
    <div className={cn('progress', sizeClasses[size], className)}>
      <div
        className={cn(
          'progress-bar',
          variantClasses[variant],
          striped && 'progress-bar-striped',
          animated && 'progress-bar-animated'
        )}
        role="progressbar"
        style={{ width: `${percentage}%` }}
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
      >
        {(label || showValue) && (
          <span className="progress-label">{label || `${Math.round(percentage)}%`}</span>
        )}
      </div>
    </div>
  );
};

/**
 * CircularProgress - Circular progress indicator
 */
interface CircularProgressProps {
  value: number;
  max?: number;
  size?: number;
  strokeWidth?: number;
  variant?: 'primary' | 'secondary' | 'success' | 'danger' | 'warning' | 'info';
  showValue?: boolean;
  className?: string;
}

const colorMap: Record<string, string> = {
  primary: 'var(--color-primary)',
  secondary: 'var(--color-secondary)',
  success: 'var(--color-success)',
  danger: 'var(--color-danger)',
  warning: 'var(--color-warning)',
  info: 'var(--color-info)',
};

export const CircularProgress: React.FC<CircularProgressProps> = ({
  value,
  max = 100,
  size = 100,
  strokeWidth = 8,
  variant = 'primary',
  showValue = true,
  className,
}) => {
  const percentage = Math.min(100, Math.max(0, (value / max) * 100));
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (percentage / 100) * circumference;

  return (
    <div className={cn('circular-progress', className)} style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--border-color)"
          strokeWidth={strokeWidth}
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={colorMap[variant]}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{
            transform: 'rotate(-90deg)',
            transformOrigin: '50% 50%',
            transition: 'stroke-dashoffset 0.5s ease',
          }}
        />
      </svg>
      {showValue && (
        <div className="circular-progress-value">
          <span>{Math.round(percentage)}%</span>
        </div>
      )}
    </div>
  );
};

/**
 * Steps Progress - Step indicator for multi-step processes
 */
interface Step {
  id: string;
  label: string;
  description?: string;
}

interface StepsProgressProps {
  steps: Step[];
  currentStep: string;
  onStepClick?: (stepId: string) => void;
  className?: string;
}

export const StepsProgress: React.FC<StepsProgressProps> = ({
  steps,
  currentStep,
  onStepClick,
  className,
}) => {
  const currentIndex = steps.findIndex((s) => s.id === currentStep);

  return (
    <div className={cn('steps-progress', className)}>
      {steps.map((step, index) => {
        const isCompleted = index < currentIndex;
        const isCurrent = index === currentIndex;
        const isClickable = onStepClick && (isCompleted || isCurrent);

        return (
          <div
            key={step.id}
            className={cn('step', isCompleted && 'completed', isCurrent && 'current')}
            onClick={() => isClickable && onStepClick(step.id)}
            style={{ cursor: isClickable ? 'pointer' : 'default' }}
          >
            <div className={cn('step-indicator', (isCompleted || isCurrent) && 'active')}>
              {isCompleted ? <i className="bi bi-check" /> : <span>{index + 1}</span>}
            </div>
            <div className="step-content">
              <div className="step-label">{step.label}</div>
              {step.description && <div className="step-description">{step.description}</div>}
            </div>
            {index < steps.length - 1 && (
              <div className={cn('step-connector', isCompleted && 'completed')} />
            )}
          </div>
        );
      })}
    </div>
  );
};

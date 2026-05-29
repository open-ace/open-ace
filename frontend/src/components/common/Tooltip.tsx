/**
 * Tooltip Component - Hover tooltip with animations
 */

import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/utils';

export type TooltipPlacement = 'top' | 'bottom' | 'left' | 'right';

interface TooltipProps {
  content: React.ReactNode;
  placement?: TooltipPlacement;
  children: React.ReactElement;
  delay?: number;
  disabled?: boolean;
}

interface TooltipPosition {
  top: number;
  left: number;
}

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  placement = 'top',
  children,
  delay = 200,
  disabled = false,
}) => {
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<TooltipPosition>({ top: 0, left: 0 });
  const [actualPlacement, setActualPlacement] = useState(placement);
  const triggerRef = useRef<HTMLElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const calculatePosition = () => {
    if (!triggerRef.current || !tooltipRef.current) return;

    const triggerRect = triggerRef.current.getBoundingClientRect();
    const tooltipRect = tooltipRef.current.getBoundingClientRect();
    const gap = 8;

    let newPlacement = placement;
    let top = 0;
    let left = 0;

    // Calculate initial position
    switch (placement) {
      case 'top':
        top = triggerRect.top - tooltipRect.height - gap;
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2;
        break;
      case 'bottom':
        top = triggerRect.bottom + gap;
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2;
        break;
      case 'left':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2;
        left = triggerRect.left - tooltipRect.width - gap;
        break;
      case 'right':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2;
        left = triggerRect.right + gap;
        break;
    }

    // Adjust if out of viewport
    if (top < 0 && placement === 'top') {
      newPlacement = 'bottom';
      top = triggerRect.bottom + gap;
    } else if (top + tooltipRect.height > window.innerHeight && placement === 'bottom') {
      newPlacement = 'top';
      top = triggerRect.top - tooltipRect.height - gap;
    }

    if (left < 0 && placement === 'left') {
      newPlacement = 'right';
      left = triggerRect.right + gap;
    } else if (left + tooltipRect.width > window.innerWidth && placement === 'right') {
      newPlacement = 'left';
      left = triggerRect.left - tooltipRect.width - gap;
    }

    // Ensure tooltip stays within viewport
    left = Math.max(8, Math.min(left, window.innerWidth - tooltipRect.width - 8));

    setPosition({ top, left });
    setActualPlacement(newPlacement);
  };

  const showTooltip = () => {
    if (disabled) return;
    timeoutRef.current = setTimeout(() => {
      setIsVisible(true);
    }, delay);
  };

  const hideTooltip = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
    setIsVisible(false);
  };

  useEffect(() => {
    if (isVisible) {
      calculatePosition();
    }
  }, [isVisible]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const arrowClasses: Record<TooltipPlacement, string> = {
    top: 'tooltip-arrow-bottom',
    bottom: 'tooltip-arrow-top',
    left: 'tooltip-arrow-right',
    right: 'tooltip-arrow-left',
  };

  const child = React.Children.only(children);
  const triggerProps = {
    ref: triggerRef,
    onMouseEnter: (e: React.MouseEvent) => {
      showTooltip();
      child.props.onMouseEnter?.(e);
    },
    onMouseLeave: (e: React.MouseEvent) => {
      hideTooltip();
      child.props.onMouseLeave?.(e);
    },
    onFocus: (e: React.FocusEvent) => {
      showTooltip();
      child.props.onFocus?.(e);
    },
    onBlur: (e: React.FocusEvent) => {
      hideTooltip();
      child.props.onBlur?.(e);
    },
  };

  return (
    <>
      {React.cloneElement(child, triggerProps)}
      {isVisible &&
        createPortal(
          <div
            ref={tooltipRef}
            className={cn('tooltip show animate-fade-in', arrowClasses[actualPlacement])}
            style={{
              position: 'fixed',
              top: position.top,
              left: position.left,
              zIndex: 9999,
            }}
            role="tooltip"
          >
            <div className="tooltip-inner">{content}</div>
          </div>,
          document.body
        )}
    </>
  );
};

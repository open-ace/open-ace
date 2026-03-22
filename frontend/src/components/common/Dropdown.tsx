/**
 * Dropdown Component - Dropdown menus with animations
 */

import React, { useState, useRef, useEffect } from 'react';
import { cn } from '@/utils';

interface DropdownItem {
  id: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  disabled?: boolean;
  danger?: boolean;
  divider?: boolean;
  onClick?: () => void;
}

interface DropdownProps {
  trigger: React.ReactNode;
  items: DropdownItem[];
  placement?: 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end';
  className?: string;
  menuClassName?: string;
}

export const Dropdown: React.FC<DropdownProps> = ({
  trigger,
  items,
  placement = 'bottom-start',
  className,
  menuClassName,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Close on escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
    }
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen]);

  const handleItemClick = (item: DropdownItem) => {
    if (item.disabled || item.divider) return;
    item.onClick?.();
    setIsOpen(false);
  };

  const placementClasses: Record<string, string> = {
    'bottom-start': 'dropdown-menu-start',
    'bottom-end': 'dropdown-menu-end',
    'top-start': 'dropup dropdown-menu-start',
    'top-end': 'dropup dropdown-menu-end',
  };

  return (
    <div ref={dropdownRef} className={cn('dropdown', placementClasses[placement], className)}>
      <div onClick={() => setIsOpen(!isOpen)}>{trigger}</div>
      {isOpen && (
        <div
          className={cn('dropdown-menu show animate-fade-in', menuClassName)}
          style={{ position: 'absolute' }}
        >
          {items.map((item) => {
            if (item.divider) {
              return <hr key={item.id} className="dropdown-divider" />;
            }

            return (
              <button
                key={item.id}
                type="button"
                className={cn(
                  'dropdown-item d-flex align-items-center',
                  item.disabled && 'disabled',
                  item.danger && 'text-danger'
                )}
                onClick={() => handleItemClick(item)}
                disabled={item.disabled}
              >
                {item.icon && <span className="me-2">{item.icon}</span>}
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

/**
 * SplitButton - Button with dropdown
 */
interface SplitButtonProps {
  variant?: 'primary' | 'secondary' | 'success' | 'danger' | 'warning' | 'info';
  size?: 'sm' | 'md' | 'lg';
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  items: DropdownItem[];
  disabled?: boolean;
  className?: string;
}

export const SplitButton: React.FC<SplitButtonProps> = ({
  variant = 'primary',
  size = 'md',
  label,
  icon,
  onClick,
  items,
  disabled,
  className,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className={cn('btn-group', className)}>
      <button
        type="button"
        className={cn(
          'btn',
          `btn-${variant}`,
          size === 'sm' && 'btn-sm',
          size === 'lg' && 'btn-lg'
        )}
        onClick={onClick}
        disabled={disabled}
      >
        {icon && <span className="me-2">{icon}</span>}
        {label}
      </button>
      <button
        type="button"
        className={cn(
          'btn dropdown-toggle dropdown-toggle-split',
          `btn-${variant}`,
          size === 'sm' && 'btn-sm',
          size === 'lg' && 'btn-lg'
        )}
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled}
        aria-expanded={isOpen}
      >
        <span className="visually-hidden">Toggle dropdown</span>
      </button>
      {isOpen && (
        <div className="dropdown-menu show animate-fade-in">
          {items.map((item) => {
            if (item.divider) {
              return <hr key={item.id} className="dropdown-divider" />;
            }

            return (
              <button
                key={item.id}
                type="button"
                className={cn(
                  'dropdown-item d-flex align-items-center',
                  item.disabled && 'disabled',
                  item.danger && 'text-danger'
                )}
                onClick={() => {
                  item.onClick?.();
                  setIsOpen(false);
                }}
                disabled={item.disabled}
              >
                {item.icon && <span className="me-2">{item.icon}</span>}
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

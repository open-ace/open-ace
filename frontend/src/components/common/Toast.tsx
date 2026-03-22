/**
 * Toast Component - Notification toast with animations
 */

import React, { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/utils';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastData {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}

interface ToastProps extends ToastData {
  onClose: (id: string) => void;
}

const toastIcons: Record<ToastType, string> = {
  success: 'bi-check-circle-fill',
  error: 'bi-x-circle-fill',
  warning: 'bi-exclamation-triangle-fill',
  info: 'bi-info-circle-fill',
};

const toastClasses: Record<ToastType, string> = {
  success: 'bg-success text-white',
  error: 'bg-danger text-white',
  warning: 'bg-warning',
  info: 'bg-info text-white',
};

const Toast: React.FC<ToastProps> = ({ id, type, title, message, duration = 5000, onClose }) => {
  const [isExiting, setIsExiting] = useState(false);

  const handleClose = useCallback(() => {
    setIsExiting(true);
    setTimeout(() => onClose(id), 300);
  }, [id, onClose]);

  useEffect(() => {
    if (duration > 0) {
      const timer = setTimeout(handleClose, duration);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [duration, handleClose]);

  return (
    <div
      className={cn(
        'toast show animate-slide-in-right',
        toastClasses[type],
        isExiting && 'animate-slide-out-right'
      )}
      role="alert"
      aria-live="assertive"
    >
      <div className="toast-header">
        <i className={cn('bi', toastIcons[type], 'me-2')} />
        <strong className="me-auto">{title}</strong>
        <button
          type="button"
          className="btn-close btn-close-white"
          onClick={handleClose}
          aria-label="Close"
        />
      </div>
      {message && <div className="toast-body">{message}</div>}
    </div>
  );
};

/**
 * Toast Container - Manages multiple toasts
 */
interface ToastContainerProps {
  toasts: ToastData[];
  onClose: (id: string) => void;
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
}

export const ToastContainer: React.FC<ToastContainerProps> = ({
  toasts,
  onClose,
  position = 'top-right',
}) => {
  const positionClasses: Record<string, string> = {
    'top-right': 'top-0 end-0',
    'top-left': 'top-0 start-0',
    'bottom-right': 'bottom-0 end-0',
    'bottom-left': 'bottom-0 start-0',
  };

  return createPortal(
    <div
      className={cn('toast-container position-fixed p-3', positionClasses[position])}
      style={{ zIndex: 9999 }}
    >
      {toasts.map((toast) => (
        <Toast key={toast.id} {...toast} onClose={onClose} />
      ))}
    </div>,
    document.body
  );
};

/**
 * useToast Hook - Easy toast management
 */
import { useState as useHookState, useCallback as useHookCallback } from 'react';

export const useToast = () => {
  const [toasts, setToasts] = useHookState<ToastData[]>([]);

  const addToast = useHookCallback((toast: Omit<ToastData, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setToasts((prev) => [...prev, { ...toast, id }]);
    return id;
  }, []);

  const removeToast = useHookCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const success = useHookCallback(
    (title: string, message?: string, duration?: number) =>
      addToast({ type: 'success', title, message, duration }),
    [addToast]
  );

  const error = useHookCallback(
    (title: string, message?: string, duration?: number) =>
      addToast({ type: 'error', title, message, duration }),
    [addToast]
  );

  const warning = useHookCallback(
    (title: string, message?: string, duration?: number) =>
      addToast({ type: 'warning', title, message, duration }),
    [addToast]
  );

  const info = useHookCallback(
    (title: string, message?: string, duration?: number) =>
      addToast({ type: 'info', title, message, duration }),
    [addToast]
  );

  return {
    toasts,
    addToast,
    removeToast,
    success,
    error,
    warning,
    info,
    ToastContainer: () => <ToastContainer toasts={toasts} onClose={removeToast} />,
  };
};

/**
 * Toast Component - Notification toast with animations
 *
 * Global toast system backed by a singleton Zustand store.
 *
 * Why a global store: the previous `useToast` hook held toasts in component-level
 * state, so each caller owned a private `toasts` array and only components that
 * rendered their own `<ToastContainer/>` ever displayed anything. By moving the
 * state to a single store and mounting one `<ToastHost/>` at the app root, every
 * `toast.success/error/...` call is visible globally regardless of which
 * component triggered it.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { create } from 'zustand';
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
 * Toast Container - Renders a list of toasts into a portal.
 * Presentational component; reads nothing from the store itself.
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
 * Global toast store - single source of truth for all toasts in the app.
 */
interface ToastStore {
  toasts: ToastData[];
  addToast: (toast: Omit<ToastData, 'id'>) => string;
  removeToast: (id: string) => void;
  clearToasts: () => void;
  success: (title: string, message?: string, duration?: number) => string;
  error: (title: string, message?: string, duration?: number) => string;
  warning: (title: string, message?: string, duration?: number) => string;
  info: (title: string, message?: string, duration?: number) => string;
}

const generateId = (): string => `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

export const useToastStore = create<ToastStore>((set, get) => ({
  toasts: [],
  addToast: (toast) => {
    const id = generateId();
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }));
    return id;
  },
  removeToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  clearToasts: () => set({ toasts: [] }),
  success: (title, message, duration) =>
    get().addToast({ type: 'success', title, message, duration }),
  error: (title, message, duration) => get().addToast({ type: 'error', title, message, duration }),
  warning: (title, message, duration) =>
    get().addToast({ type: 'warning', title, message, duration }),
  info: (title, message, duration) => get().addToast({ type: 'info', title, message, duration }),
}));

/**
 * Stable singleton API. Returning the same object reference every call keeps
 * `toast` stable in effect dependency arrays (callers only invoke actions, they
 * never read state from it), so existing `const toast = useToast()` call sites
 * keep working unchanged.
 */
const toastApi: Omit<ToastStore, 'toasts' | 'clearToasts'> = {
  addToast: (toast) => useToastStore.getState().addToast(toast),
  removeToast: (id) => useToastStore.getState().removeToast(id),
  success: (title, message, duration) => useToastStore.getState().success(title, message, duration),
  error: (title, message, duration) => useToastStore.getState().error(title, message, duration),
  warning: (title, message, duration) => useToastStore.getState().warning(title, message, duration),
  info: (title, message, duration) => useToastStore.getState().info(title, message, duration),
};

export const useToast = () => toastApi;

/**
 * ToastHost - Mount ONCE at the app root. Subscribes to the global store and
 * renders all active toasts. Replaces the per-component `toast.ToastContainer()`
 * pattern that only worked where explicitly rendered.
 */
export const ToastHost: React.FC = () => {
  const toasts = useToastStore((state) => state.toasts);
  const removeToast = useToastStore((state) => state.removeToast);
  return <ToastContainer toasts={toasts} onClose={removeToast} />;
};

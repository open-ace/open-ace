/**
 * Toast Store - Global toast notification state.
 *
 * Lives in the `store` chunk (not `components`) so that both `hooks` and
 * `components` can import it without creating a circular chunk dependency
 * (hooks -> components -> hooks). See TROUBLESHOOTING_LOG issue #1.
 */

import { create } from 'zustand';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastData {
  id: string;
  type: ToastType;
  title: string;
  message?: string;
  duration?: number;
}

export interface ToastStore {
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

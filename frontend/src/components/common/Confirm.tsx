/**
 * Global confirm dialog system.
 *
 * Mirrors the global Toast pattern: a singleton Zustand store backs a single
 * mounted `<ConfirmHost/>`, and `useConfirm()` returns a stable async function.
 * This replaces scattered `window.confirm()` calls (which are blocking, ugly,
 * untranslatable, and ignore the app's styling/a11y) with the existing
 * `ConfirmModal`, while keeping call sites one line:
 *
 *   const confirm = useConfirm();
 *   if (!(await confirm({ message: t('confirmDelete', language), variant: 'danger' }))) return;
 */

import React from 'react';
import { create } from 'zustand';
import { ConfirmModal } from './Modal';

export type ConfirmVariant = 'danger' | 'warning' | 'primary';

export interface ConfirmOptions {
  message: string;
  title?: string;
  confirmText?: string;
  cancelText?: string;
  variant?: ConfirmVariant;
}

interface ConfirmState {
  open: boolean;
  options: ConfirmOptions;
  resolve: ((value: boolean) => void) | null;
  /** Show the dialog; resolves true on confirm, false on cancel/dismiss. */
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  /** Internal: resolve the pending promise and close. */
  settle: (result: boolean) => void;
}

const INITIAL_OPTIONS: ConfirmOptions = { message: '' };

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  open: false,
  options: INITIAL_OPTIONS,
  resolve: null,
  confirm: (options) =>
    new Promise<boolean>((resolve) => {
      // If a dialog is somehow already open, resolve it as cancelled first so we
      // never leak a hanging promise.
      get().settle(false);
      set({ open: true, options, resolve });
    }),
  settle: (result) => {
    const resolve = get().resolve;
    if (resolve) {
      resolve(result);
    }
    set({ open: false, resolve: null });
  },
}));

/** Stable async confirm accessor. Returns the same function reference every call. */
export const useConfirm = () => useConfirmStore.getState().confirm;

/**
 * Mount ONCE at the app root. Renders the global ConfirmModal driven by the store.
 */
export const ConfirmHost: React.FC = () => {
  const open = useConfirmStore((state) => state.open);
  const options = useConfirmStore((state) => state.options);
  const settle = useConfirmStore((state) => state.settle);

  return (
    <ConfirmModal
      isOpen={open}
      onClose={() => settle(false)}
      onConfirm={() => settle(true)}
      title={options.title ?? ''}
      message={options.message}
      confirmText={options.confirmText}
      cancelText={options.cancelText}
      variant={options.variant}
    />
  );
};

/**
 * Tests for the global confirm dialog store and useConfirm accessor.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useConfirmStore, useConfirm, ConfirmHost } from './Confirm';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'zh',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string, lang: string) => {
    const translations: Record<string, Record<string, string>> = {
      en: { confirm: 'Confirm', cancel: 'Cancel' },
      zh: { confirm: '确认', cancel: '取消' },
    };
    return translations[lang]?.[key] ?? key;
  },
}));

describe('Confirm global store', () => {
  beforeEach(() => {
    // Reset to a closed, unresolved state before each test
    useConfirmStore.setState({ open: false, options: { message: '' }, resolve: null });
  });

  it('confirm() opens the dialog and returns a pending promise', () => {
    const promise = useConfirmStore.getState().confirm({ message: 'are you sure?' });
    expect(useConfirmStore.getState().open).toBe(true);
    expect(useConfirmStore.getState().options.message).toBe('are you sure?');
    // Still pending: settle has not been called
    expect(useConfirmStore.getState().resolve).not.toBeNull();
    // Clean up so the dangling promise doesn't leak into other tests
    useConfirmStore.getState().settle(false);
    return expect(promise).resolves.toBe(false);
  });

  it('settling true resolves the promise with true and closes', async () => {
    const promise = useConfirmStore.getState().confirm({ message: 'go?', variant: 'danger' });
    useConfirmStore.getState().settle(true);
    expect(await promise).toBe(true);
    expect(useConfirmStore.getState().open).toBe(false);
    expect(useConfirmStore.getState().resolve).toBeNull();
  });

  it('settling false resolves the promise with false and closes', async () => {
    const promise = useConfirmStore.getState().confirm({ message: 'cancel?' });
    useConfirmStore.getState().settle(false);
    expect(await promise).toBe(false);
    expect(useConfirmStore.getState().open).toBe(false);
  });

  it('opening a new dialog while one is pending resolves the old one as false', async () => {
    const first = useConfirmStore.getState().confirm({ message: 'first' });
    const second = useConfirmStore.getState().confirm({ message: 'second' });
    expect(await first).toBe(false); // displaced by the second dialog
    useConfirmStore.getState().settle(true);
    expect(await second).toBe(true);
  });
});

describe('useConfirm accessor', () => {
  it('returns the stable confirm function reference', () => {
    const a = useConfirm();
    const b = useConfirm();
    expect(a).toBe(b);
  });
});

describe('ConfirmHost i18n defaults', () => {
  beforeEach(() => {
    useConfirmStore.setState({ open: false, options: { message: '' }, resolve: null });
  });

  it('uses translated defaults when confirmText/cancelText are not provided', () => {
    // Open the dialog without explicit button text
    useConfirmStore.getState().confirm({ message: 'Are you sure?' });

    render(<ConfirmHost />);

    // Should show Chinese translations (mocked language is 'zh')
    expect(screen.getByRole('button', { name: '确认' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument();
  });

  it('uses explicit confirmText/cancelText when provided', () => {
    // Open the dialog with explicit button text
    useConfirmStore.getState().confirm({
      message: 'Delete item?',
      confirmText: 'Yes, delete',
      cancelText: 'No, keep',
    });

    render(<ConfirmHost />);

    // Should show the explicit text, not translations
    expect(screen.getByRole('button', { name: 'Yes, delete' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'No, keep' })).toBeInTheDocument();
    // Translated defaults should NOT be present
    expect(screen.queryByRole('button', { name: '确认' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
  });

  it('settles false when cancel button is clicked', () => {
    const promise = useConfirmStore.getState().confirm({ message: 'Confirm?' });
    render(<ConfirmHost />);

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(useConfirmStore.getState().open).toBe(false);
    return expect(promise).resolves.toBe(false);
  });

  it('settles true when confirm button is clicked', async () => {
    const promise = useConfirmStore.getState().confirm({ message: 'Confirm?' });
    render(<ConfirmHost />);

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(useConfirmStore.getState().open).toBe(false);
    expect(await promise).toBe(true);
  });
});

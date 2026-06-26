/**
 * Tests for the global confirm dialog store and useConfirm accessor.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useConfirmStore, useConfirm } from './Confirm';

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

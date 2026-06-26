/**
 * Tests for the global Toast store and useToast singleton API.
 *
 * These cover the refactor that moved toast state from per-component useState
 * into a single Zustand store: every action must mutate the shared store, and
 * `useToast()` must return a stable reference so effect dependency arrays don't
 * thrash.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useToastStore, useToast } from './Toast';

describe('Toast global store', () => {
  beforeEach(() => {
    // Reset to a clean slate before each test
    useToastStore.getState().clearToasts();
  });

  it('addToast appends a toast with a generated id', () => {
    const id = useToastStore.getState().addToast({ type: 'info', title: 'hello' });
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].id).toBe(id);
    expect(toasts[0].title).toBe('hello');
  });

  it('removeToast removes only the matching id', () => {
    const idA = useToastStore.getState().addToast({ type: 'info', title: 'a' });
    const idB = useToastStore.getState().addToast({ type: 'info', title: 'b' });
    useToastStore.getState().removeToast(idA);
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].id).toBe(idB);
  });

  it('convenience helpers create toasts of the correct type', () => {
    const store = useToastStore.getState();
    store.success('s');
    store.error('e');
    store.warning('w');
    store.info('i');
    const types = useToastStore.getState().toasts.map((t) => t.type);
    expect(types).toEqual(['success', 'error', 'warning', 'info']);
  });

  it('generated ids are unique', () => {
    const ids = new Set<string>();
    for (let i = 0; i < 50; i++) {
      ids.add(useToastStore.getState().addToast({ type: 'info', title: 'x' }));
    }
    expect(ids.size).toBe(50);
  });

  it('clearToasts empties the store', () => {
    useToastStore.getState().addToast({ type: 'info', title: 'a' });
    useToastStore.getState().addToast({ type: 'info', title: 'b' });
    useToastStore.getState().clearToasts();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});

describe('useToast singleton API', () => {
  it('returns the same stable object reference across calls', () => {
    const { result } = renderHook(() => useToast());
    const { result: result2 } = renderHook(() => useToast());
    expect(result.current).toBe(result2.current);
  });

  it('mutates the global store (visible to all callers)', () => {
    const { result } = renderHook(() => useToast());
    useToastStore.getState().clearToasts();
    result.current.error('boom', 'detail');
    expect(useToastStore.getState().toasts).toHaveLength(1);
    expect(useToastStore.getState().toasts[0].type).toBe('error');
  });

  it('keeps `toast` stable in a dependency-array scenario', () => {
    // Simulate the pattern: const toast = useToast(); useEffect(..., [toast])
    const a = renderHook(() => useToast()).result.current;
    const b = renderHook(() => useToast()).result.current;
    const deps = [a];
    // Re-render produces the same reference, so deps do not change
    expect(deps[0]).toBe(b);
  });
});

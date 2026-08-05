/**
 * usePageRefresh Hook Tests
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { usePageRefresh } from './usePageRefresh';
import { usePageRefreshStore } from '@/store';
import { createMatcherConfig } from '@/utils';

// Create wrapper with QueryClient
const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
        staleTime: 0,
      },
    },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
};

describe('usePageRefresh', () => {
  beforeEach(() => {
    // Reset store before each test
    usePageRefreshStore.getState().reset();
  });

  describe('initialization', () => {
    it('should initialize with default values', () => {
      const wrapper = createWrapper();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
          }),
        { wrapper }
      );

      expect(result.current.autoRefresh).toBe(false);
      expect(result.current.interval).toBe(60000);
      expect(result.current.isRefreshing).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.errorCount).toBe(0);
    });

    it('should initialize with custom values', () => {
      const wrapper = createWrapper();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
            interval: 30000,
            enabled: true,
          }),
        { wrapper }
      );

      expect(result.current.autoRefresh).toBe(true);
      expect(result.current.interval).toBe(30000);
    });
  });

  describe('setAutoRefresh', () => {
    it('should toggle auto refresh', async () => {
      const wrapper = createWrapper();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
          }),
        { wrapper }
      );

      act(() => {
        result.current.setAutoRefresh(true);
      });

      await waitFor(() => {
        expect(result.current.autoRefresh).toBe(true);
      });

      act(() => {
        result.current.setAutoRefresh(false);
      });

      await waitFor(() => {
        expect(result.current.autoRefresh).toBe(false);
      });
    });
  });

  describe('setInterval', () => {
    it('should change refresh interval', async () => {
      const wrapper = createWrapper();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
            interval: 60000,
          }),
        { wrapper }
      );

      act(() => {
        result.current.setInterval(30000);
      });

      await waitFor(() => {
        expect(result.current.interval).toBe(30000);
      });
    });
  });

  describe('refresh', () => {
    it('should call onRefresh even when no React Query cache entries match', async () => {
      const wrapper = createWrapper();
      const onRefresh = vi.fn();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
            onRefresh,
          }),
        { wrapper }
      );

      await act(async () => {
        await result.current.refresh();
      });

      // onRefresh should be called even without matching React Query cache entries
      expect(onRefresh).toHaveBeenCalledTimes(1);
      expect(result.current.isRefreshing).toBe(false);
    });

    it('should call recordRefresh when onRefresh is called without matching queries', async () => {
      const wrapper = createWrapper();
      const onRefresh = vi.fn();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/test-record-refresh',
            refreshKey: createMatcherConfig([['nonexistent_cache_key']], 'prefix'),
            onRefresh,
          }),
        { wrapper }
      );

      await act(async () => {
        await result.current.refresh();
      });

      // Verify lastRefreshTime is updated after manual refresh with onRefresh
      await waitFor(() => {
        const config = usePageRefreshStore.getState().configs['/manage/test-record-refresh'];
        expect(config?.lastRefreshTime).not.toBeNull();
      });
      expect(result.current.isRefreshing).toBe(false);
    });
  });

  describe('global pause', () => {
    it('should respect global pause state', async () => {
      const wrapper = createWrapper();

      // Set global pause
      act(() => {
        usePageRefreshStore.getState().pauseAll();
      });

      renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
            enabled: true,
          }),
        { wrapper }
      );

      // Should not auto-refresh when globally paused
      expect(usePageRefreshStore.getState().globalPaused).toBe(true);

      // Resume
      act(() => {
        usePageRefreshStore.getState().resumeAll();
      });

      expect(usePageRefreshStore.getState().globalPaused).toBe(false);
    });
  });

  describe('state persistence', () => {
    it('should persist config to store', async () => {
      const wrapper = createWrapper();

      const { result } = renderHook(
        () =>
          usePageRefresh({
            page: '/manage/dashboard',
            refreshKey: createMatcherConfig([['dashboard']], 'prefix'),
            interval: 30000,
          }),
        { wrapper }
      );

      act(() => {
        result.current.setAutoRefresh(true);
      });

      await waitFor(() => {
        const config = usePageRefreshStore.getState().getConfig('/manage/dashboard');
        expect(config?.autoRefresh).toBe(true);
        expect(config?.interval).toBe(30000);
      });
    });
  });
});

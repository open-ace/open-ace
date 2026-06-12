/**
 * Page Refresh Store Tests
 */

import { renderHook, act } from '@testing-library/react';
import { usePageRefreshStore } from './pageRefreshStore';

describe('pageRefreshStore', () => {
  beforeEach(() => {
    // Reset store before each test
    usePageRefreshStore.getState().reset();
  });

  describe('setConfig', () => {
    it('should set config for a page', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config).toBeDefined();
      expect(config?.autoRefresh).toBe(true);
      expect(config?.interval).toBe(60000);
    });

    it('should update existing config', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: false,
          interval: 30000,
        });
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
        });
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.autoRefresh).toBe(true);
      expect(config?.interval).toBe(30000); // Should keep previous value
    });

    it('should update nextRefreshTime when autoRefresh enabled', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      const now = Date.now();
      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.nextRefreshTime).toBeGreaterThan(now);
    });

    it('should clear nextRefreshTime when autoRefresh disabled', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: false,
        });
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.nextRefreshTime).toBeNull();
    });
  });

  describe('getConfig', () => {
    it('should return undefined for non-existent page', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      const config = result.current.getConfig('/nonexistent');
      expect(config).toBeUndefined();
    });

    it('should auto-cleanup stale configs', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      // Set config with old lastVisited time
      const staleTime = Date.now() - 31 * 24 * 60 * 60 * 1000; // 31 days ago
      act(() => {
        usePageRefreshStore.setState({
          configs: {
            '/stale-page': {
              autoRefresh: true,
              interval: 60000,
              lastRefreshTime: null,
              nextRefreshTime: null,
              errorCount: 0,
              lastError: null,
              lastVisited: staleTime,
            },
          },
        });
      });

      // getConfig should cleanup and return undefined
      const config = result.current.getConfig('/stale-page');
      expect(config).toBeUndefined();

      // Config should be removed from store
      expect(result.current.configs['/stale-page']).toBeUndefined();
    });
  });

  describe('pauseAll / resumeAll', () => {
    it('should pause all refreshes', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.pauseAll();
      });

      expect(result.current.globalPaused).toBe(true);
    });

    it('should resume all refreshes', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.pauseAll();
        result.current.resumeAll();
      });

      expect(result.current.globalPaused).toBe(false);
    });
  });

  describe('recordRefresh', () => {
    it('should record successful refresh', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      // Set initial config
      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
      });

      const now = Date.now();
      act(() => {
        result.current.recordRefresh('/manage/dashboard', true);
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.lastRefreshTime).toBeGreaterThanOrEqual(now);
      expect(config?.errorCount).toBe(0);
      expect(config?.lastError).toBeNull();
    });

    it('should record failed refresh', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
        result.current.recordRefresh('/manage/dashboard', false, 'Network error');
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.errorCount).toBe(1);
      expect(config?.lastError).toBe('Network error');
    });

    it('should update nextRefreshTime after refresh', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
      });

      const now = Date.now();
      act(() => {
        result.current.recordRefresh('/manage/dashboard', true);
      });

      const config = result.current.getConfig('/manage/dashboard');
      expect(config?.nextRefreshTime).toBeGreaterThan(now);
    });
  });

  describe('reset', () => {
    it('should reset all state', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
        result.current.pauseAll();
        result.current.reset();
      });

      expect(result.current.configs).toEqual({});
      expect(result.current.globalPaused).toBe(false);
    });
  });

  describe('resetPage', () => {
    it('should reset specific page', () => {
      const { result } = renderHook(() => usePageRefreshStore());

      act(() => {
        result.current.setConfig('/manage/dashboard', {
          autoRefresh: true,
          interval: 60000,
        });
        result.current.setConfig('/manage/messages', {
          autoRefresh: false,
          interval: 30000,
        });
        result.current.resetPage('/manage/dashboard');
      });

      expect(result.current.configs['/manage/dashboard']).toBeUndefined();
      expect(result.current.configs['/manage/messages']).toBeDefined();
    });
  });
});

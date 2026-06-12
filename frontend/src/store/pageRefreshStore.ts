/**
 * Page Refresh Store - Global page refresh state using Zustand
 *
 * Features:
 * - Per-page refresh configuration
 * - Persisted to localStorage with versioning
 * - Multi-tab synchronization via storage events
 * - Auto-cleanup of stale configurations
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

/**
 * PageRefreshConfig - Refresh configuration for each page
 */
export interface PageRefreshConfig {
  autoRefresh: boolean;
  interval: number; // milliseconds, 0 means disabled
  lastRefreshTime: number | null; // timestamp in milliseconds
  nextRefreshTime: number | null; // timestamp in milliseconds
  errorCount: number;
  lastError: string | null;
  lastVisited: number; // timestamp for cleanup
}

/**
 * PageRefreshState - Global state for page refresh management
 */
interface PageRefreshState {
  configs: Record<string, PageRefreshConfig>;
  globalPaused: boolean;

  // Actions
  setConfig: (page: string, config: Partial<PageRefreshConfig>) => void;
  getConfig: (page: string) => PageRefreshConfig | undefined;
  pauseAll: () => void;
  resumeAll: () => void;
  recordRefresh: (page: string, success: boolean, error?: string) => void;
  reset: () => void;
  resetPage: (page: string) => void;
}

/**
 * Default refresh configuration for a new page
 */
const DEFAULT_CONFIG: PageRefreshConfig = {
  autoRefresh: false,
  interval: 60000, // 1 minute default
  lastRefreshTime: null,
  nextRefreshTime: null,
  errorCount: 0,
  lastError: null,
  lastVisited: Date.now(),
};

/**
 * Storage version for future migrations
 * Format: page-refresh-config-v{version}
 */
const STORAGE_KEY = 'page-refresh-config-v1';

/**
 * Cleanup threshold in milliseconds (30 days)
 */
const CLEANUP_THRESHOLD = 30 * 24 * 60 * 60 * 1000;

/**
 * Custom storage with cleanup logic
 */
const customStorage = createJSONStorage(() => localStorage);

/**
 * Page Refresh Store
 */
export const usePageRefreshStore = create<PageRefreshState>()(
  persist(
    (set, get) => ({
      configs: {},
      globalPaused: false,

      setConfig: (page, config) =>
        set((state) => {
          const currentConfig = state.configs[page] || DEFAULT_CONFIG;
          const newConfig = {
            ...currentConfig,
            ...config,
            lastVisited: Date.now(),
          };

          // Update nextRefreshTime if interval or autoRefresh changed
          if (config.autoRefresh !== undefined || config.interval !== undefined) {
            const autoRefresh = config.autoRefresh ?? currentConfig.autoRefresh;
            const interval = config.interval ?? currentConfig.interval;

            if (autoRefresh && interval > 0) {
              newConfig.nextRefreshTime = Date.now() + interval;
            } else {
              newConfig.nextRefreshTime = null;
            }
          }

          return {
            configs: {
              ...state.configs,
              [page]: newConfig,
            },
          };
        }),

      getConfig: (page) => {
        const state = get();
        const config = state.configs[page];
        if (!config) {
          return undefined;
        }

        // Check if config is stale (not visited in 30 days)
        if (Date.now() - config.lastVisited > CLEANUP_THRESHOLD) {
          // Auto-cleanup stale config
          set((s) => ({
            configs: Object.fromEntries(Object.entries(s.configs).filter(([key]) => key !== page)),
          }));
          return undefined;
        }

        return config;
      },

      pauseAll: () => set({ globalPaused: true }),

      resumeAll: () => set({ globalPaused: false }),

      recordRefresh: (page, success, error) =>
        set((state) => {
          const currentConfig = state.configs[page] || DEFAULT_CONFIG;
          const now = Date.now();

          const newConfig: PageRefreshConfig = {
            ...currentConfig,
            lastRefreshTime: now,
            lastVisited: now,
            errorCount: success ? 0 : currentConfig.errorCount + 1,
            lastError: success ? null : (error ?? 'Refresh failed'),
          };

          // Update nextRefreshTime if auto refresh is enabled
          if (currentConfig.autoRefresh && currentConfig.interval > 0) {
            newConfig.nextRefreshTime = now + currentConfig.interval;
          }

          return {
            configs: {
              ...state.configs,
              [page]: newConfig,
            },
          };
        }),

      reset: () =>
        set({
          configs: {},
          globalPaused: false,
        }),

      resetPage: (page) =>
        set((state) => ({
          configs: Object.fromEntries(
            Object.entries(state.configs).filter(([key]) => key !== page)
          ),
        })),
    }),
    {
      name: STORAGE_KEY,
      storage: customStorage,
      // Partialize to only persist necessary fields
      partialize: (state) => ({
        configs: state.configs,
        globalPaused: state.globalPaused,
      }),
    }
  )
);

/**
 * Selectors for stable references
 */
export const usePageConfig = (page: string) => usePageRefreshStore((state) => state.configs[page]);

export const useGlobalPaused = () => usePageRefreshStore((state) => state.globalPaused);

export const useSetPageConfig = () => usePageRefreshStore((state) => state.setConfig);

export const useRecordRefresh = () => usePageRefreshStore((state) => state.recordRefresh);

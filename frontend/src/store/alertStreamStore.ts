/**
 * Alert Stream Store - Global state for SSE-based real-time alerts
 *
 * Issue #3332: Implements message deduplication with localStorage persistence
 */

import { create } from 'zustand';
import type { Alert } from '@/api';

export type ConnectionStatus =
  | 'disconnected'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'error';

interface AlertStreamState {
  // Connection state
  connectionStatus: ConnectionStatus;

  // Alert data (replaces local state in QuotaAlerts)
  alerts: Alert[];
  unreadCount: number;

  // Deduplication (memory + localStorage)
  processedAlertIds: Set<string>;

  // Actions
  setConnectionStatus: (status: ConnectionStatus) => void;
  addAlert: (alert: Alert) => void;
  setAlerts: (alerts: Alert[] | ((prev: Alert[]) => Alert[])) => void;
  setUnreadCount: (count: number | ((prev: number) => number)) => void;
  markAlertAsRead: (alertId: string) => void;
  markAllAsRead: () => void;
  removeAlert: (alertId: string) => void;
  incrementUnreadCount: () => void;
  decrementUnreadCount: () => void;
  reset: () => void;
  _loadFromLocalStorage: () => void;
  _saveToLocalStorage: () => void;
}

// Debounce helper for localStorage saves
let saveTimeout: ReturnType<typeof setTimeout> | null = null;
const debounceSave = (save: () => void) => {
  if (saveTimeout) {
    clearTimeout(saveTimeout);
  }
  saveTimeout = setTimeout(save, 1000);
};

// localStorage helpers
const LOCAL_STORAGE_KEY = 'processedAlertIds';
const MAX_PROCESSED_IDS = 100;

const loadProcessedIdsFromLocalStorage = (): Set<string> => {
  try {
    const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
    if (stored) {
      const ids = JSON.parse(stored) as string[];
      return new Set(ids);
    }
  } catch (error) {
    console.error('Failed to load processedAlertIds from localStorage:', error);
  }
  return new Set();
};

const saveProcessedIdsToLocalStorage = (ids: Set<string>) => {
  try {
    // Keep only the most recent IDs
    const idsArray = [...ids];
    const recent = idsArray.slice(-MAX_PROCESSED_IDS);
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(recent));
  } catch (error) {
    console.error('Failed to save processedAlertIds to localStorage:', error);
  }
};

export const useAlertStreamStore = create<AlertStreamState>((set, get) => ({
  // Initial state
  connectionStatus: 'disconnected',
  alerts: [],
  unreadCount: 0,
  processedAlertIds: new Set<string>(),

  // Actions
  setConnectionStatus: (status) => set({ connectionStatus: status }),

  addAlert: (alert) => {
    const { processedAlertIds } = get();

    // Deduplication check
    if (processedAlertIds.has(alert.alert_id)) {
      return; // Already processed
    }

    // Add to processed set
    processedAlertIds.add(alert.alert_id);

    // Update state
    set((state) => ({
      alerts: [alert, ...state.alerts],
      unreadCount: state.unreadCount + 1,
      processedAlertIds,
    }));

    // Debounced save to localStorage
    debounceSave(() => {
      get()._saveToLocalStorage();
    });
  },

  setAlerts: (alerts) => {
    if (typeof alerts === 'function') {
      set((state) => ({ alerts: alerts(state.alerts) }));
    } else {
      set({ alerts });
    }
  },

  setUnreadCount: (count) => {
    if (typeof count === 'function') {
      set((state) => ({ unreadCount: count(state.unreadCount) }));
    } else {
      set({ unreadCount: count });
    }
  },

  markAlertAsRead: (alertId) => {
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.alert_id === alertId ? { ...a, read: true } : a
      ),
      unreadCount: Math.max(0, state.unreadCount - 1),
    }));
  },

  markAllAsRead: () => {
    set((state) => ({
      alerts: state.alerts.map((a) => ({ ...a, read: true })),
      unreadCount: 0,
    }));
  },

  removeAlert: (alertId) => {
    set((state) => ({
      alerts: state.alerts.filter((a) => a.alert_id !== alertId),
    }));
  },

  incrementUnreadCount: () => {
    set((state) => ({ unreadCount: state.unreadCount + 1 }));
  },

  decrementUnreadCount: () => {
    set((state) => ({ unreadCount: Math.max(0, state.unreadCount - 1) }));
  },

  reset: () => {
    set({
      connectionStatus: 'disconnected',
      alerts: [],
      unreadCount: 0,
      processedAlertIds: new Set<string>(),
    });
    // Clear localStorage
    localStorage.removeItem(LOCAL_STORAGE_KEY);
  },

  _loadFromLocalStorage: () => {
    const ids = loadProcessedIdsFromLocalStorage();
    set({ processedAlertIds: ids });
  },

  _saveToLocalStorage: () => {
    const { processedAlertIds } = get();
    saveProcessedIdsToLocalStorage(processedAlertIds);
  },
}));

// Initialize from localStorage on first import
if (typeof window !== 'undefined') {
  useAlertStreamStore.getState()._loadFromLocalStorage();
}

// Selectors for stable references
export const useConnectionStatus = () =>
  useAlertStreamStore((state) => state.connectionStatus);

export const useAlerts = () =>
  useAlertStreamStore((state) => state.alerts);

export const useUnreadCount = () =>
  useAlertStreamStore((state) => state.unreadCount);

export const useProcessedAlertIds = () =>
  useAlertStreamStore((state) => state.processedAlertIds);
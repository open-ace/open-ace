/**
 * Admin Tenant Store
 *
 * Centralized state management for platform admin tenant selection.
 * Handles tenant list loading, selection persistence, and validation.
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

/* global performance */

import { create } from 'zustand';
import { useAppStore } from '@/store';
import { tenantApi, type Tenant } from '@/api';
import type { User } from '@/types';
import { canManageAllTenants } from '@/utils/permissions';
import {
  loadSelection,
  saveSelection,
  clearSelection,
  clearOtherUsersData,
  isTenantAccessible,
  findDefaultTenant,
} from '@/utils/adminTenantStorage';

/**
 * Performance thresholds in milliseconds
 */
const PERF_THRESHOLDS = {
  LOAD_TENANTS: 500,
  VALIDATE_SELECTION: 200,
} as const;

/**
 * Store state interface
 */
interface AdminTenantState {
  // State
  tenants: Tenant[];
  selectedTenantId: number | null;
  isLoading: boolean;
  error: string | null;
  abortController: AbortController | null;
  isInitialized: boolean;
}

/**
 * Store actions interface
 */
interface AdminTenantActions {
  // Actions
  initialize: (user: User | null) => Promise<void>;
  selectTenant: (tenantId: number) => void;
  clearSelection: () => void;
  retry: () => Promise<void>;
  cleanup: (userId: string) => void;
  reset: () => void;

  // Internal actions
  _setTenants: (tenants: Tenant[]) => void;
  _setSelectedTenantId: (tenantId: number | null) => void;
  _setLoading: (loading: boolean) => void;
  _setError: (error: string | null) => void;
  _setAbortController: (controller: AbortController | null) => void;
}

/**
 * Initial state
 */
const initialState: AdminTenantState = {
  tenants: [],
  selectedTenantId: null,
  isLoading: false,
  error: null,
  abortController: null,
  isInitialized: false,
};

/**
 * Admin Tenant Store
 *
 * Uses Zustand without persistence (we handle persistence manually via localStorage)
 */
export const useAdminTenantStore = create<AdminTenantState & AdminTenantActions>()((set, get) => ({
  ...initialState,

  /**
   * Initialize store with user context
   *
   * This should be called when a component that needs tenant selection mounts.
   * It will:
   * 1. Check if user is a platform admin
   * 2. Load tenant list
   * 3. Restore previous selection from localStorage
   * 4. Validate selection or select default tenant
   */
  initialize: async (user: User | null) => {
    const state = get();

    // Skip if already initialized
    if (state.isInitialized) {
      return;
    }

    // Skip if not a platform admin
    if (!user || !canManageAllTenants(user)) {
      return;
    }

    const userId = user.id;

    // Clean up other users' data on first login
    clearOtherUsersData(userId);

    // Cancel previous request if any
    if (state.abortController) {
      state.abortController.abort();
    }

    // Create new abort controller
    const abortController = new AbortController();
    set({ abortController, isLoading: true, error: null });

    const startTime = performance.now();

    try {
      // Load tenant list
      const result = await tenantApi.listTenants({ status: 'active', limit: 100 });
      const tenants = result.tenants;

      // Performance monitoring
      const loadDuration = performance.now() - startTime;
      if (loadDuration > PERF_THRESHOLDS.LOAD_TENANTS) {
        console.warn(
          `[AdminTenantStore] Performance warning: Tenant list load took ${loadDuration.toFixed(2)}ms`
        );
      }

      // Check if request was cancelled
      if (abortController.signal.aborted) {
        return;
      }

      // Update tenants list
      set({ tenants });

      // Determine initial selection
      let selectedTenantId: number | null = null;

      // Step 1: Try to restore from localStorage
      const savedTenantId = loadSelection(userId);
      if (savedTenantId !== null) {
        // Validate saved selection
        if (isTenantAccessible(savedTenantId, tenants)) {
          selectedTenantId = savedTenantId;
        }
      }

      // Step 2: If no valid saved selection, try default tenant
      if (selectedTenantId === null) {
        const defaultTenant = findDefaultTenant(tenants);
        if (defaultTenant) {
          selectedTenantId = defaultTenant.id;
        }
      }

      // Step 3: If still no selection, keep null (require manual selection)

      // Update state
      set({
        selectedTenantId,
        isLoading: false,
        error: null,
        isInitialized: true,
        abortController: null,
      });

      // Save selection to localStorage if we have one
      if (selectedTenantId !== null) {
        saveSelection(userId, selectedTenantId);
      }
    } catch (error) {
      // Check if request was cancelled
      if (abortController.signal.aborted) {
        return;
      }

      console.error('[AdminTenantStore] Failed to load tenants:', error);

      // Set error state
      set({
        isLoading: false,
        error: '加载租户列表失败，请检查网络连接后重试',
        isInitialized: true,
        abortController: null,
      });
    }
  },

  /**
   * Select a tenant
   *
   * @param tenantId - Tenant ID to select
   */
  selectTenant: (tenantId: number) => {
    const state = get();
    const user = useAppStore.getState().user;

    if (!user) {
      return;
    }

    // Validate tenant exists and is active
    const tenant = state.tenants.find((t) => t.id === tenantId);
    if (tenant?.status !== 'active') {
      console.warn(`[AdminTenantStore] Cannot select inactive or non-existent tenant ${tenantId}`);
      return;
    }

    // Update state
    set({ selectedTenantId: tenantId });

    // Save to localStorage
    saveSelection(user.id, tenantId);
  },

  /**
   * Clear the current selection
   */
  clearSelection: () => {
    const user = useAppStore.getState().user;

    if (!user) {
      return;
    }

    set({ selectedTenantId: null });
    clearSelection(user.id);
  },

  /**
   * Retry loading tenant list after a failure
   */
  retry: async () => {
    const user = useAppStore.getState().user;
    if (!user) {
      return;
    }

    // Reset initialization state to allow re-initialization
    set({ isInitialized: false });
    await get().initialize(user);
  },

  /**
   * Cleanup other users' data
   *
   * @param userId - Current user ID (string from User.id)
   */
  cleanup: (userId: string) => {
    clearOtherUsersData(userId);
  },

  /**
   * Reset store to initial state
   *
   * Should be called on logout
   */
  reset: () => {
    const state = get();

    // Cancel any pending request
    if (state.abortController) {
      state.abortController.abort();
    }

    set(initialState);
  },

  // Internal actions
  _setTenants: (tenants) => set({ tenants }),
  _setSelectedTenantId: (tenantId) => set({ selectedTenantId: tenantId }),
  _setLoading: (loading) => set({ isLoading: loading }),
  _setError: (error) => set({ error }),
  _setAbortController: (controller) => set({ abortController: controller }),
}));

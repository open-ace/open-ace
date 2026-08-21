/**
 * Admin Tenant Hook
 *
 * Provides simplified access to admin tenant selection functionality.
 * Handles tenant loading, selection, and cross-tab synchronization.
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

import { useEffect, useCallback, useRef } from 'react';
import { useAdminTenantStore } from '@/store/adminTenantStore';
import { useAppStore } from '@/store';
import { canManageAllTenants } from '@/utils/permissions';
import { getStorageKey } from '@/utils/adminTenantStorage';
import type { Tenant } from '@/api';

/**
 * Hook return type
 */
interface UseAdminTenantReturn {
  tenants: Tenant[];
  selectedTenantId: number | null;
  isLoading: boolean;
  error: string | null;
  selectTenant: (id: number) => void;
  clearSelection: () => void;
  retry: () => void;
  effectiveTenantId: number | null;
}

/**
 * Admin Tenant Selection Hook
 *
 * Usage:
 * ```tsx
 * const {
 *   tenants,
 *   selectedTenantId,
 *   selectTenant,
 *   clearSelection,
 *   effectiveTenantId,
 *   isLoading,
 *   error,
 *   retry,
 * } = useAdminTenant();
 * ```
 */
export function useAdminTenant(): UseAdminTenantReturn {
  const user = useAppStore((state) => state.user);
  const {
    tenants,
    selectedTenantId,
    isLoading,
    error,
    initialize,
    selectTenant,
    clearSelection,
    retry,
    _setSelectedTenantId,
  } = useAdminTenantStore();

  const isAdmin = canManageAllTenants(user);
  const isInitializedRef = useRef(false);

  // Initialize on mount
  useEffect(() => {
    if (!isAdmin || isInitializedRef.current) {
      return;
    }

    isInitializedRef.current = true;
    initialize(user);
  }, [isAdmin, user, initialize]);

  // Listen for cross-tab storage events
  useEffect(() => {
    if (!user || !isAdmin) {
      return;
    }

    const handleStorageChange = (e: StorageEvent) => {
      // Only handle our storage key
      const storageKey = getStorageKey(user.id);
      if (e.key !== storageKey || !e.newValue) {
        return;
      }

      try {
        const data = JSON.parse(e.newValue);
        if (typeof data.selectedTenantId === 'number' || data.selectedTenantId === null) {
          // Update store state without triggering another localStorage write
          _setSelectedTenantId(data.selectedTenantId);
        }
      } catch (error) {
        console.warn('[useAdminTenant] Failed to parse storage event:', error);
      }
    };

    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, [user, isAdmin, _setSelectedTenantId]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      // Mark as not initialized so it can re-initialize if remounted
      isInitializedRef.current = false;
    };
  }, []);

  // Wrap selectTenant to ensure it only works for admins
  const handleSelectTenant = useCallback(
    (id: number) => {
      if (!isAdmin) {
        console.warn('[useAdminTenant] Non-admin cannot select tenant');
        return;
      }
      selectTenant(id);
    },
    [isAdmin, selectTenant]
  );

  // Wrap clearSelection to ensure it only works for admins
  const handleClearSelection = useCallback(() => {
    if (!isAdmin) {
      console.warn('[useAdminTenant] Non-admin cannot clear selection');
      return;
    }
    clearSelection();
  }, [isAdmin, clearSelection]);

  // Wrap retry to ensure it only works for admins
  const handleRetry = useCallback(() => {
    if (!isAdmin) {
      return;
    }
    retry();
  }, [isAdmin, retry]);

  // Calculate effective tenant ID
  // For non-admins: use user's tenant_id
  // For admins: use selectedTenantId
  const effectiveTenantId = isAdmin ? selectedTenantId : (user?.tenant_id ?? null);

  return {
    tenants: isAdmin ? tenants : [],
    selectedTenantId: isAdmin ? selectedTenantId : (user?.tenant_id ?? null),
    isLoading: isAdmin && isLoading,
    error: isAdmin ? error : null,
    selectTenant: handleSelectTenant,
    clearSelection: handleClearSelection,
    retry: handleRetry,
    effectiveTenantId,
  };
}

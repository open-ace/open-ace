/**
 * Admin Tenant Storage Utilities
 *
 * Handles localStorage operations for admin tenant selection persistence
 * with deployment and user isolation.
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

/* global performance, DOMException */

import type { Tenant } from '@/api';

/**
 * Current version of the stored data format
 */
const STORAGE_VERSION = 1;

/**
 * Storage key prefix for admin tenant selection
 */
const STORAGE_KEY_PREFIX = 'open-ace-admin-tenant';

/**
 * Performance thresholds in milliseconds
 */
const PERF_THRESHOLDS = {
  LOCAL_STORAGE_WRITE: 10,
} as const;

/**
 * Stored data structure
 */
interface StoredData {
  version: number;
  selectedTenantId: number | null;
  lastUpdated: string;
}

/**
 * Get deployment identifier
 *
 * Priority order:
 * 1. Environment variable VITE_DEPLOYMENT_ID
 * 2. window.location.hostname + port
 *
 * @returns Deployment identifier
 */
export function getDeploymentId(): string {
  // Priority 1: Environment variable
  const envDeploymentId = import.meta.env.VITE_DEPLOYMENT_ID;
  if (envDeploymentId) {
    return envDeploymentId;
  }

  // Priority 2: hostname + port
  const { hostname, port } = window.location;
  return port ? `${hostname}:${port}` : hostname;
}

/**
 * Generate storage key for a specific user
 *
 * Format: open-ace-admin-tenant-{deploymentId}-{userId}
 *
 * @param userId - User ID (string from User.id)
 * @returns Storage key
 */
export function getStorageKey(userId: string): string {
  const deploymentId = getDeploymentId();
  return `${STORAGE_KEY_PREFIX}-${deploymentId}-${userId}`;
}

/**
 * Migrate stored data to current version
 *
 * @param data - Raw data from localStorage
 * @returns Migrated data in current format
 */
export function migrateStoredData(data: unknown): StoredData {
  // Handle null/undefined
  if (!data || typeof data !== 'object') {
    return {
      version: STORAGE_VERSION,
      selectedTenantId: null,
      lastUpdated: new Date().toISOString(),
    };
  }

  const storedData = data as Record<string, unknown>;

  // No version number - treat as invalid/old format
  if (typeof storedData.version !== 'number') {
    return {
      version: STORAGE_VERSION,
      selectedTenantId: null,
      lastUpdated: new Date().toISOString(),
    };
  }

  // Version 1 - current version
  if (storedData.version === STORAGE_VERSION) {
    return {
      version: STORAGE_VERSION,
      selectedTenantId:
        typeof storedData.selectedTenantId === 'number' ? storedData.selectedTenantId : null,
      lastUpdated:
        typeof storedData.lastUpdated === 'string'
          ? storedData.lastUpdated
          : new Date().toISOString(),
    };
  }

  // Future version - preserve unknown fields, downgrade selectedTenantId
  // This allows backward compatibility if a newer version writes to localStorage
  // and an older version reads it
  return {
    version: STORAGE_VERSION,
    selectedTenantId:
      typeof storedData.selectedTenantId === 'number' ? storedData.selectedTenantId : null,
    lastUpdated:
      typeof storedData.lastUpdated === 'string'
        ? storedData.lastUpdated
        : new Date().toISOString(),
  };
}

/**
 * Save tenant selection to localStorage
 *
 * @param userId - User ID (string from User.id)
 * @param tenantId - Selected tenant ID (null for no selection)
 * @returns true if save succeeded, false if failed (e.g., quota exceeded)
 */
export function saveSelection(userId: string, tenantId: number | null): boolean {
  const startTime = performance.now();

  try {
    const key = getStorageKey(userId);
    const data: StoredData = {
      version: STORAGE_VERSION,
      selectedTenantId: tenantId,
      lastUpdated: new Date().toISOString(),
    };

    localStorage.setItem(key, JSON.stringify(data));

    // Performance monitoring
    const duration = performance.now() - startTime;
    if (duration > PERF_THRESHOLDS.LOCAL_STORAGE_WRITE) {
      console.warn(
        `[AdminTenantStorage] Performance warning: localStorage write took ${duration.toFixed(2)}ms`
      );
    }

    return true;
  } catch (error) {
    // Quota exceeded or other storage error
    if (error instanceof DOMException && error.name === 'QuotaExceededError') {
      console.warn('[AdminTenantStorage] localStorage quota exceeded, selection not persisted');
    } else {
      console.warn('[AdminTenantStorage] Failed to save selection:', error);
    }
    return false;
  }
}

/**
 * Load tenant selection from localStorage
 *
 * @param userId - User ID (string from User.id)
 * @returns Selected tenant ID or null if not found/invalid
 */
export function loadSelection(userId: string): number | null {
  try {
    const key = getStorageKey(userId);
    const rawData = localStorage.getItem(key);

    if (!rawData) {
      return null;
    }

    let parsedData: unknown;
    try {
      parsedData = JSON.parse(rawData);
    } catch {
      // Invalid JSON - treat as corrupt data
      console.warn('[AdminTenantStorage] Corrupted data in localStorage, resetting');
      clearSelection(userId);
      return null;
    }

    const data = migrateStoredData(parsedData);
    return data.selectedTenantId;
  } catch (error) {
    console.warn('[AdminTenantStorage] Failed to load selection:', error);
    return null;
  }
}

/**
 * Clear tenant selection from localStorage
 *
 * @param userId - User ID (string from User.id)
 */
export function clearSelection(userId: string): void {
  try {
    const key = getStorageKey(userId);
    localStorage.removeItem(key);
  } catch (error) {
    console.warn('[AdminTenantStorage] Failed to clear selection:', error);
  }
}

/**
 * Clear all admin tenant selection data for other users
 *
 * Should be called during login to ensure data isolation between users.
 *
 * @param currentUserId - Current user ID (string from User.id, to preserve)
 */
export function clearOtherUsersData(currentUserId: string): void {
  try {
    const keysToRemove: string[] = [];
    const currentKeySuffix = `-${currentUserId}`;

    // Find all keys that match the pattern but are for other users
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(STORAGE_KEY_PREFIX) && !key.endsWith(currentKeySuffix)) {
        keysToRemove.push(key);
      }
    }

    // Remove the keys
    keysToRemove.forEach((key) => {
      try {
        localStorage.removeItem(key);
      } catch (error) {
        console.warn(`[AdminTenantStorage] Failed to remove key ${key}:`, error);
      }
    });

    if (keysToRemove.length > 0) {
      console.log(`[AdminTenantStorage] Cleared ${keysToRemove.length} other user(s) data`);
    }
  } catch (error) {
    console.warn('[AdminTenantStorage] Failed to clear other users data:', error);
  }
}

/**
 * Validate if a tenant ID is in the accessible tenant list
 *
 * @param tenantId - Tenant ID to validate
 * @param tenants - List of accessible tenants
 * @returns true if tenant is accessible and active
 */
export function isTenantAccessible(tenantId: number | null, tenants: Tenant[]): boolean {
  if (tenantId === null) {
    return false;
  }

  const tenant = tenants.find((t) => t.id === tenantId);
  return tenant?.status === 'active';
}

/**
 * Find default tenant from tenant list
 *
 * @param tenants - List of accessible tenants
 * @returns Default tenant or null if not found
 */
export function findDefaultTenant(tenants: Tenant[]): Tenant | null {
  return tenants.find((t) => t.slug === 'default' && t.status === 'active') ?? null;
}

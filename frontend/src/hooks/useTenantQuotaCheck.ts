/**
 * useTenantQuotaCheck Hook - Tenant quota checking logic
 *
 * Issue #3132: Tenant management page quota check functionality
 *
 * Features:
 * - Per-tenant independent checking state
 * - Duplicate request prevention using functional state updates
 * - Error handling with toast notifications
 * - Relies on apiClient's built-in timeout (30s) for timeout handling
 */

import { useState, useCallback } from 'react';
import { useToast } from '@/components/common';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { tenantApi, type Tenant } from '@/api';

/**
 * Check result returned from the API
 */
export interface CheckResult {
  tenant: Tenant;
  allowed: boolean;
  reason?: string;
  checkedAt: Date;
}

/**
 * Hook return type
 */
export interface UseTenantQuotaCheckReturn {
  /** Set of tenant IDs currently being checked */
  checkingTenants: Set<number>;
  /** Latest check result (null if no result yet) */
  checkResult: CheckResult | null;
  /** Initiate quota check for a tenant */
  checkQuota: (tenant: Tenant) => Promise<void>;
  /** Clear the check result */
  clearResult: () => void;
}

/**
 * Hook for checking tenant quota availability
 *
 * The API checks if the tenant's current usage exceeds their daily limits.
 * Parameters:
 * - tokens: 0 means "check if current usage exceeds limits" (not estimating additional usage)
 * - requests: defaults to 0 in backend when not provided
 *
 * @returns Hook return value with checking state and functions
 */
export function useTenantQuotaCheck(): UseTenantQuotaCheckReturn {
  const toast = useToast();
  const language = useLanguage();

  // Set of tenant IDs currently being checked (prevents duplicate requests)
  const [checkingTenants, setCheckingTenants] = useState<Set<number>>(new Set());

  // Latest check result
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);

  /**
   * Initiate quota check for a tenant
   *
   * Uses functional state updates to prevent race conditions with stale closure values.
   * The API client has built-in timeout handling (30 seconds), so we don't need
   * an additional AbortController here.
   *
   * @param tenant - Tenant to check
   */
  const checkQuota = useCallback(
    async (tenant: Tenant) => {
      // Use functional update to avoid stale closure issues with checkingTenants
      // This ensures we check against the latest state, not a snapshot from render time
      let shouldProceed = false;
      setCheckingTenants((prev) => {
        if (prev.has(tenant.id)) {
          // Already checking this tenant, don't proceed
          return prev;
        }
        shouldProceed = true;
        // Create new Set with the tenant added (immutable update)
        return new Set(prev).add(tenant.id);
      });

      if (!shouldProceed) {
        return;
      }

      try {
        // Call API with tokens: 0 to check if current usage exceeds limits
        // This queries whether the tenant has any remaining quota capacity
        // The apiClient has built-in 30s timeout and retry logic
        const result = await tenantApi.checkQuota(tenant.id, { tokens: 0 });

        // Store result with check timestamp
        setCheckResult({
          tenant,
          allowed: result.allowed,
          reason: result.reason,
          checkedAt: new Date(),
        });
      } catch (err: unknown) {
        // Handle different error types with user-friendly messages
        let errorMessage: string;
        let errorKey = 'failedToCheckQuota';

        if (err instanceof Error) {
          // Check for timeout/abort error (from apiClient's built-in timeout)
          if (err.name === 'AbortError') {
            errorMessage = t('requestTimeout', language);
            errorKey = 'requestTimeout';
          } else if (err.message.includes('fetch') || err.message.includes('network')) {
            errorMessage = t('networkError', language);
            errorKey = 'networkError';
          } else {
            errorMessage = err.message;
          }
        } else if (err && typeof err === 'object') {
          // API error response (has status code)
          const apiError = err as { message?: string; error?: string; status?: number };

          // Use status code for more reliable error type detection
          if (apiError.status === 404) {
            errorMessage = t('tenantNotFound', language);
            errorKey = 'tenantNotFound';
          } else if (apiError.status === 403) {
            errorMessage = t('noPermissionForTenantManagement', language);
            errorKey = 'noPermission';
          } else {
            const msg = apiError.message ?? apiError.error ?? 'Unknown error';
            // Fallback to string matching for error type
            if (msg.toLowerCase().includes('not found')) {
              errorMessage = t('tenantNotFound', language);
              errorKey = 'tenantNotFound';
            } else {
              errorMessage = msg;
            }
          }
        } else {
          errorMessage = t('failedToCheckQuota', language);
        }

        // Show error toast
        toast.error(t('failedToCheckQuota', language), errorMessage);

        // Log error for debugging with structured info
        console.error('Failed to check tenant quota:', {
          tenantId: tenant.id,
          tenantName: tenant.name,
          errorKey,
          error: err,
        });
      } finally {
        // Remove from checking set using functional update
        setCheckingTenants((prev) => {
          const next = new Set(prev);
          next.delete(tenant.id);
          return next;
        });
      }
    },
    [language, toast]
  );

  /**
   * Clear the check result
   */
  const clearResult = useCallback(() => {
    setCheckResult(null);
  }, []);

  return {
    checkingTenants,
    checkResult,
    checkQuota,
    clearResult,
  };
}

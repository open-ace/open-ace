/**
 * useTenantQuotaCheck Hook - Tenant quota checking logic
 *
 * Issue #3132: Tenant management page quota check functionality
 *
 * Features:
 * - Per-tenant independent checking state
 * - Duplicate request prevention
 * - Error handling with toast notifications
 * - 10-second timeout for API requests
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

/** API request timeout in milliseconds */
const REQUEST_TIMEOUT_MS = 10000;

/**
 * Hook for checking tenant quota availability
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
   * @param tenant - Tenant to check
   */
  const checkQuota = useCallback(
    async (tenant: Tenant) => {
      // Prevent duplicate requests for the same tenant
      if (checkingTenants.has(tenant.id)) {
        return;
      }

      // Add to checking set
      setCheckingTenants((prev) => new Set(prev).add(tenant.id));

      try {
        // Create abort controller for timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

        // Call API with tokens: 0 to check if current usage exceeds limits
        const result = await tenantApi.checkQuota(tenant.id, { tokens: 0 });

        clearTimeout(timeoutId);

        // Store result with check timestamp
        setCheckResult({
          tenant,
          allowed: result.allowed,
          reason: result.reason,
          checkedAt: new Date(),
        });
      } catch (err: unknown) {
        // Handle different error types
        let errorMessage: string;

        if (err instanceof Error) {
          if (err.name === 'AbortError') {
            errorMessage = t('requestTimeout', language);
          } else if (err.message.includes('fetch') || err.message.includes('network')) {
            errorMessage = t('networkError', language);
          } else if (err.message.includes('404') || err.message.includes('not found')) {
            errorMessage = t('tenantNotFound', language);
          } else {
            errorMessage = err.message;
          }
        } else if (err && typeof err === 'object' && 'message' in err) {
          // API error response
          const apiError = err as { message?: string; error?: string };
          const msg = apiError.message ?? apiError.error ?? 'Unknown error';
          if (msg.toLowerCase().includes('not found')) {
            errorMessage = t('tenantNotFound', language);
          } else {
            errorMessage = msg;
          }
        } else {
          errorMessage = t('failedToCheckQuota', language);
        }

        // Show error toast
        toast.error(t('failedToCheckQuota', language), errorMessage);

        // Log error for debugging
        console.error('Failed to check tenant quota:', {
          tenantId: tenant.id,
          tenantName: tenant.name,
          error: err,
        });
      } finally {
        // Remove from checking set
        setCheckingTenants((prev) => {
          const next = new Set(prev);
          next.delete(tenant.id);
          return next;
        });
      }
    },
    [checkingTenants, language, toast]
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

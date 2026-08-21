/**
 * Quota Hint Calculation Utilities
 *
 * Pure functions for calculating available quota values.
 * Separated from UI formatting for testability.
 */

import { QuotaType, MAX_TOKEN_QUOTA, MAX_REQUEST_QUOTA } from '@/constants/quota';
import type { QuotaStats, QuotaUsage } from '@/api/admin';

/**
 * Parameters for quota hint calculation
 */
export interface QuotaHintParams {
  quotaStats: QuotaStats | null | undefined;
  editingUser: QuotaUsage | null | undefined;
}

/**
 * Quota type configuration for calculation
 */
interface QuotaTypeConfig {
  remaining: number;
  current: number;
  max: number;
  isToken: boolean;
}

/**
 * Get the configuration for a specific quota type
 */
function getQuotaTypeConfig(
  quotaType: QuotaType,
  params: QuotaHintParams
): QuotaTypeConfig {
  const { quotaStats, editingUser } = params;

  const typeMap: Record<QuotaType, QuotaTypeConfig> = {
    [QuotaType.DAILY_TOKEN]: {
      remaining: quotaStats?.remaining.daily_token ?? 0,
      current: editingUser?.daily_token_quota ?? 0,
      max: MAX_TOKEN_QUOTA,
      isToken: true,
    },
    [QuotaType.MONTHLY_TOKEN]: {
      remaining: quotaStats?.remaining.monthly_token ?? 0,
      current: editingUser?.monthly_token_quota ?? 0,
      max: MAX_TOKEN_QUOTA,
      isToken: true,
    },
    [QuotaType.DAILY_REQUEST]: {
      remaining: quotaStats?.remaining.daily_request ?? 0,
      current: editingUser?.daily_request_quota ?? 0,
      max: MAX_REQUEST_QUOTA,
      isToken: false,
    },
    [QuotaType.MONTHLY_REQUEST]: {
      remaining: quotaStats?.remaining.monthly_request ?? 0,
      current: editingUser?.monthly_request_quota ?? 0,
      max: MAX_REQUEST_QUOTA,
      isToken: false,
    },
  };

  return typeMap[quotaType];
}

/**
 * Calculate the available quota value
 *
 * Formula: available = Math.max(0, Math.min(remaining + current, max))
 *
 * @param quotaType - The type of quota to calculate
 * @param params - Parameters including quotaStats and editingUser
 * @returns The calculated available quota value
 */
export function calculateAvailableQuota(
  quotaType: QuotaType,
  params: QuotaHintParams
): number {
  const config = getQuotaTypeConfig(quotaType, params);
  const available = Math.max(0, Math.min(config.remaining + config.current, config.max));
  return available;
}

/**
 * Get quota type configuration including max and isToken flag
 * Useful for formatQuotaHint
 *
 * @param quotaType - The type of quota
 * @returns Configuration with max value and isToken flag
 */
export function getQuotaTypeInfo(
  quotaType: QuotaType
): { max: number; isToken: boolean } {
  const isToken =
    quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN;
  return {
    max: isToken ? MAX_TOKEN_QUOTA : MAX_REQUEST_QUOTA,
    isToken,
  };
}
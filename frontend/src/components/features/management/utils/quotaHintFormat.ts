/**
 * Quota Hint Format Utilities
 *
 * UI text formatting for quota hints.
 * Separated from calculation logic for i18n flexibility.
 */

import { QuotaType } from '@/constants/quota';
import { formatNumberAsString } from '@/utils/quotaFormatter';

/**
 * Format the quota hint for display
 *
 * @param quotaType - The type of quota
 * @param available - The calculated available quota value
 * @param max - The maximum quota value
 * @param isStatsAvailable - Whether quota stats are available
 * @returns Formatted string for display
 */
export function formatQuotaHint(
  quotaType: QuotaType,
  available: number,
  max: number,
  isStatsAvailable: boolean
): string {
  const isToken = quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN;

  // Fallback when no stats available
  if (!isStatsAvailable) {
    return isToken ? `Max: ${max}M` : `Max: ${formatNumberAsString(max)}`;
  }

  // Format with available and max info
  if (isToken) {
    return `可用: ${available.toFixed(2)}M (上限: ${max}M)`;
  }
  return `可用: ${formatNumberAsString(available)} (上限: ${formatNumberAsString(max)})`;
}

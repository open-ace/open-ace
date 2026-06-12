/**
 * Quota Formatter Utilities
 *
 * Provides safe formatting and parsing for quota values to handle:
 * - Large numbers without precision loss
 * - Scientific notation input (e.g., 1e9)
 * - Locale-independent formatting
 */

import {
  MAX_TOKEN_QUOTA,
  MAX_REQUEST_QUOTA,
  TOKEN_QUOTA_MULTIPLIER,
  isUnlimited,
  exceedsSafeInteger,
  QuotaType,
  validateQuota,
} from '@/constants/quota';

/**
 * Format a quota value for display
 * Avoids toLocaleString() issues with large numbers
 *
 * @param value - Quota value (in M units for token quota)
 * @param isTokenQuota - Whether this is a token quota (display with M suffix)
 * @returns Formatted string or '∞' for unlimited
 */
export function formatQuotaForDisplay(
  value: number | null | undefined,
  isTokenQuota: boolean = true
): string {
  // Unlimited quota (null or undefined)
  if (value === null || value === undefined) {
    return '∞';
  }

  // Check for exceeding safe integer range
  if (exceedsSafeInteger(value)) {
    return `⚠️ ${value} (exceeds safe range)`;
  }

  // For token quotas, display with M suffix
  if (isTokenQuota) {
    // Use fixed decimal places to avoid locale issues
    const formatted = value.toFixed(2);
    return `${formatted}M`;
  }

  // For request quotas, format as regular number
  // Use string manipulation to avoid locale issues
  return formatNumberAsString(value);
}

/**
 * Format a number as string without locale dependency
 * Handles large numbers safely
 *
 * @param value - Number to format
 * @returns Formatted string
 */
export function formatNumberAsString(value: number): string {
  if (exceedsSafeInteger(value)) {
    // For very large numbers, use scientific notation explicitly
    return value.toString();
  }

  // Convert to string and add thousand separators manually
  const str = value.toFixed(0);
  const parts: string[] = [];

  // Split into groups of 3 digits from right
  for (let i = str.length - 1; i >= 0; i -= 3) {
    const start = Math.max(0, i - 2);
    parts.unshift(str.substring(start, i + 1));
  }

  return parts.join(',');
}

/**
 * Parse quota input from user
 * Handles:
 * - Regular numbers (e.g., "1000")
 * - Numbers with commas (e.g., "1,000")
 * - Scientific notation (e.g., "1e9", "2.5e6")
 * - Decimal numbers
 *
 * @param input - User input string
 * @returns Parsed number or null if invalid
 */
export function parseQuotaInput(input: string): number | null {
  if (!input || input.trim() === '') {
    return null;
  }

  // Clean the input: remove commas, spaces, and non-numeric characters (except e, ., -)
  const cleaned = input
    .trim()
    .replace(/,/g, '')
    .replace(/\s/g, '');

  // Try to parse as number (supports scientific notation)
  const parsed = Number(cleaned);

  // Check if parsing was successful
  if (isNaN(parsed)) {
    return null;
  }

  return parsed;
}

/**
 * Parse and validate quota input
 *
 * @param input - User input string
 * @param quotaType - Type of quota being parsed
 * @returns Object with parsed value and validation result
 */
export function parseAndValidateQuota(
  input: string,
  quotaType: QuotaType
): {
  value: number | null;
  validation: { isValid: boolean; error?: string; warning?: string };
} {
  const value = parseQuotaInput(input);
  const validation = validateQuota(value, quotaType);

  return { value, validation };
}

/**
 * Convert actual token count to M units for storage
 *
 * @param actualTokens - Actual token count
 * @returns Quota value in M units
 */
export function tokensToQuotaUnits(actualTokens: number): number {
  return Math.floor(actualTokens / TOKEN_QUOTA_MULTIPLIER);
}

/**
 * Convert quota value (M units) to actual token count for display/comparison
 *
 * @param quotaValue - Quota value in M units
 * @returns Actual token count
 */
export function quotaUnitsToTokens(quotaValue: number): number {
  return quotaValue * TOKEN_QUOTA_MULTIPLIER;
}

/**
 * Format actual token count for display (not in M units)
 *
 * @param tokens - Actual token count
 * @returns Formatted string with thousand separators
 */
export function formatTokensForDisplay(tokens: number): string {
  if (tokens === 0) {
    return '0';
  }

  return formatNumberAsString(tokens);
}

/**
 * Get quota input placeholder text
 *
 * @param quotaType - Type of quota
 * @returns Placeholder text
 */
export function getQuotaPlaceholder(quotaType: QuotaType): string {
  if (
    quotaType === QuotaType.DAILY_TOKEN ||
    quotaType === QuotaType.MONTHLY_TOKEN
  ) {
    return 'Unlimited (enter value in M)';
  }
  return 'Unlimited';
}

/**
 * Get quota input label text
 *
 * @param quotaType - Type of quota
 * @returns Label text
 */
export function getQuotaLabel(quotaType: QuotaType): string {
  switch (quotaType) {
    case QuotaType.DAILY_TOKEN:
      return 'Daily Token Quota (M)';
    case QuotaType.MONTHLY_TOKEN:
      return 'Monthly Token Quota (M)';
    case QuotaType.DAILY_REQUEST:
      return 'Daily Request Quota';
    case QuotaType.MONTHLY_REQUEST:
      return 'Monthly Request Quota';
    default:
      return 'Quota';
  }
}

/**
 * Check if a value should show as unlimited in UI
 * Returns true if value is null, undefined, or 0 (legacy)
 *
 * @param value - Quota value
 * @returns True if should display as unlimited
 */
export function shouldShowUnlimited(value: number | null | undefined): boolean {
  return isUnlimited(value);
}

/**
 * Get max quota display value for input validation hint
 *
 * @param quotaType - Type of quota
 * @returns Max value display string
 */
export function getMaxQuotaDisplay(quotaType: QuotaType): string {
  if (
    quotaType === QuotaType.DAILY_TOKEN ||
    quotaType === QuotaType.MONTHLY_TOKEN
  ) {
    return `Max: ${MAX_TOKEN_QUOTA}M`;
  }
  return `Max: ${formatNumberAsString(MAX_REQUEST_QUOTA)}`;
}
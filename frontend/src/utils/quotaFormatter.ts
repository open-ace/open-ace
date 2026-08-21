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
  const cleaned = input.trim().replace(/,/g, '').replace(/\s/g, '');

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
  if (quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN) {
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
  if (quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN) {
    return `Max: ${MAX_TOKEN_QUOTA}M`;
  }
  return `Max: ${formatNumberAsString(MAX_REQUEST_QUOTA)}`;
}

/**
 * Calculate available quota based on tenant remaining and current user allocation
 *
 * @param quotaType - Type of quota
 * @param remaining - Tenant remaining quota
 * @param current - Current user allocated quota (needs to be added back for editing)
 * @returns Available quota value
 */
export function calculateAvailableQuota(
  quotaType: QuotaType,
  remaining: number,
  current: number
): number {
  // Determine max value based on quota type
  const max =
    quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN
      ? MAX_TOKEN_QUOTA
      : MAX_REQUEST_QUOTA;

  // Calculate available: remaining + current (add back current allocation for editing)
  // Cap at max, floor at 0
  return Math.max(0, Math.min(remaining + current, max));
}

/**
 * Format available quota hint text
 *
 * @param available - Available quota value
 * @param quotaType - Type of quota
 * @param hasQuotaStats - Whether quota stats are available (determines fallback behavior)
 * @param language - Language for display (default: 'zh')
 * @returns Formatted hint string
 */
export function formatAvailableQuotaHint(
  available: number,
  quotaType: QuotaType,
  hasQuotaStats: boolean = true,
  language: 'zh' | 'en' = 'zh'
): string {
  // Determine max value and whether it's token quota
  const isToken = quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN;
  const max = isToken ? MAX_TOKEN_QUOTA : MAX_REQUEST_QUOTA;

  // Fallback: show only max when quota stats are not available
  if (!hasQuotaStats) {
    if (isToken) {
      return `Max: ${max}M`;
    }
    return `Max: ${formatNumberAsString(max)}`;
  }

  // Full hint with available and max
  if (isToken) {
    const availableStr = available.toFixed(2);
    return language === 'zh'
      ? `可用: ${availableStr}M (上限: ${max}M)`
      : `Available: ${availableStr}M (Max: ${max}M)`;
  }

  const availableStr = formatNumberAsString(available);
  const maxStr = formatNumberAsString(max);
  return language === 'zh'
    ? `可用: ${availableStr} (上限: ${maxStr})`
    : `Available: ${availableStr} (Max: ${maxStr})`;
}

/**
 * Get available quota hint for input field
 * Combines calculateAvailableQuota and formatAvailableQuotaHint
 *
 * @param quotaType - Type of quota
 * @param quotaStats - Quota statistics (can be null/undefined)
 * @param currentQuota - Current user quota (can be undefined)
 * @param language - Language for display (optional, default: 'zh')
 * @returns Formatted hint string
 */
export function getAvailableQuotaHint(
  quotaType: QuotaType,
  quotaStats:
    | {
        remaining: {
          daily_token?: number;
          monthly_token?: number;
          daily_request?: number;
          monthly_request?: number;
        };
      }
    | null
    | undefined,
  currentQuota: number | undefined,
  language?: 'zh' | 'en'
): string {
  // Extract remaining value based on quota type
  let remaining = 0;
  if (quotaStats?.remaining) {
    switch (quotaType) {
      case QuotaType.DAILY_TOKEN:
        remaining = quotaStats.remaining.daily_token ?? 0;
        break;
      case QuotaType.MONTHLY_TOKEN:
        remaining = quotaStats.remaining.monthly_token ?? 0;
        break;
      case QuotaType.DAILY_REQUEST:
        remaining = quotaStats.remaining.daily_request ?? 0;
        break;
      case QuotaType.MONTHLY_REQUEST:
        remaining = quotaStats.remaining.monthly_request ?? 0;
        break;
    }
  }

  // Current quota defaults to 0 if undefined
  const current = currentQuota ?? 0;

  // Calculate available quota
  const available = calculateAvailableQuota(quotaType, remaining, current);

  // Format and return hint
  const hasQuotaStats = quotaStats !== null && quotaStats !== undefined;
  return formatAvailableQuotaHint(available, quotaType, hasQuotaStats, language ?? 'zh');
}

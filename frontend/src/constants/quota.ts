/**
 * Quota Constants and Limits
 *
 * Token quotas are stored in M (millions) units in the database.
 * Database field type: BIGINT (Issue #3018: upgraded from INTEGER to BIGINT)
 *
 * New limits are set to be safe for JavaScript Number.MAX_SAFE_INTEGER (9,007,199,254,740,991).
 */

/**
 * Maximum token quota in M units
 * Issue #3018: Upgraded to BIGINT, new limit 100,000M (100 billion tokens)
 * This allows approximately 100 days of usage at 1 billion tokens/day.
 */
export const MAX_TOKEN_QUOTA = 100000;

/**
 * Maximum request quota (stored as actual count)
 * Issue #3018: Upgraded to BIGINT, new limit 10 billion requests
 * This allows approximately 11.5 days at 10,000 requests/second.
 */
export const MAX_REQUEST_QUOTA = 10000000000;

/**
 * Minimum quota value (0 means unlimited in display, but null is preferred)
 */
export const MIN_QUOTA = 0;

/**
 * Token quota multiplier (1M = 1,000,000 actual tokens)
 */
export const TOKEN_QUOTA_MULTIPLIER = 1_000_000;

/**
 * Quota limits configuration
 */
export const QUOTA_LIMITS = {
  TOKEN_QUOTA: {
    MIN: MIN_QUOTA,
    MAX: MAX_TOKEN_QUOTA,
    UNIT: 'M',
    DISPLAY_NAME: 'Token Quota',
  },
  REQUEST_QUOTA: {
    MIN: MIN_QUOTA,
    MAX: MAX_REQUEST_QUOTA,
    UNIT: '',
    DISPLAY_NAME: 'Request Quota',
  },
} as const;

/**
 * Quota type enum
 */
export enum QuotaType {
  DAILY_TOKEN = 'daily_token_quota',
  MONTHLY_TOKEN = 'monthly_token_quota',
  DAILY_REQUEST = 'daily_request_quota',
  MONTHLY_REQUEST = 'monthly_request_quota',
}

/**
 * Check if a quota value is unlimited (null or undefined)
 */
export function isUnlimited(value: number | null | undefined): boolean {
  return value === null || value === undefined;
}

/**
 * Get the maximum quota for a given quota type
 */
export function getMaxQuota(quotaType: QuotaType): number {
  if (quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN) {
    return MAX_TOKEN_QUOTA;
  }
  return MAX_REQUEST_QUOTA;
}

/**
 * Check if a quota value exceeds the safe integer range
 * JavaScript Number.MAX_SAFE_INTEGER = 9,007,199,254,740,991
 */
export function exceedsSafeInteger(value: number): boolean {
  return value > Number.MAX_SAFE_INTEGER || value < Number.MIN_SAFE_INTEGER;
}

/**
 * Quota validation result
 */
export interface QuotaValidationResult {
  isValid: boolean;
  error?: string;
  warning?: string;
}

/**
 * Validate a quota value
 */
export function validateQuota(
  value: number | null | undefined,
  quotaType: QuotaType
): QuotaValidationResult {
  // Unlimited is always valid
  if (isUnlimited(value)) {
    return { isValid: true };
  }

  // Check for NaN
  if (typeof value === 'number' && isNaN(value)) {
    return {
      isValid: false,
      error: 'Quota value cannot be NaN',
    };
  }

  // Check for negative values
  if (typeof value === 'number' && value < MIN_QUOTA) {
    return {
      isValid: false,
      error: 'Quota value cannot be negative',
    };
  }

  // Check for exceeding safe integer range (JavaScript precision issue)
  if (typeof value === 'number' && exceedsSafeInteger(value)) {
    return {
      isValid: false,
      error: `Quota value exceeds safe integer range. Maximum safe integer is ${Number.MAX_SAFE_INTEGER.toLocaleString()}`,
    };
  }

  // Check for exceeding database limit
  const maxQuota = getMaxQuota(quotaType);
  if (typeof value === 'number' && value > maxQuota) {
    return {
      isValid: false,
      error: `Quota value exceeds maximum limit of ${maxQuota}${
        quotaType === QuotaType.DAILY_TOKEN || quotaType === QuotaType.MONTHLY_TOKEN ? 'M' : ''
      }`,
    };
  }

  return { isValid: true };
}

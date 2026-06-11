/**
 * Tests for quota formatter utilities
 */

import {
  formatQuotaForDisplay,
  formatNumberAsString,
  parseQuotaInput,
  parseAndValidateQuota,
  tokensToQuotaUnits,
  quotaUnitsToTokens,
  getMaxQuotaDisplay,
  getQuotaPlaceholder,
  getQuotaLabel,
  shouldShowUnlimited,
} from './quotaFormatter';
import {
  QuotaType,
  MAX_TOKEN_QUOTA,
  MAX_REQUEST_QUOTA,
  TOKEN_QUOTA_MULTIPLIER,
} from '@/constants/quota';

describe('formatQuotaForDisplay', () => {
  it('should return ∞ for null/undefined values', () => {
    expect(formatQuotaForDisplay(null)).toBe('∞');
    expect(formatQuotaForDisplay(undefined)).toBe('∞');
  });

  it('should format token quotas with M suffix', () => {
    expect(formatQuotaForDisplay(100, true)).toBe('100.00M');
    expect(formatQuotaForDisplay(2147, true)).toBe('2147.00M');
  });

  it('should format request quotas without M suffix', () => {
    expect(formatQuotaForDisplay(1000, false)).toBe('1,000');
    expect(formatQuotaForDisplay(2147483647, false)).toBe('2,147,483,647');
  });

  it('should show warning for values exceeding safe integer', () => {
    const hugeValue = 1e21;
    expect(formatQuotaForDisplay(hugeValue, true)).toContain('⚠️');
    expect(formatQuotaForDisplay(hugeValue, true)).toContain('exceeds safe range');
  });
});

describe('formatNumberAsString', () => {
  it('should format numbers with thousand separators', () => {
    expect(formatNumberAsString(1000)).toBe('1,000');
    expect(formatNumberAsString(1000000)).toBe('1,000,000');
    expect(formatNumberAsString(2147483647)).toBe('2,147,483,647');
  });

  it('should handle zero', () => {
    expect(formatNumberAsString(0)).toBe('0');
  });

  it('should handle small numbers', () => {
    expect(formatNumberAsString(99)).toBe('99');
    expect(formatNumberAsString(100)).toBe('100');
  });

  it('should handle large numbers exceeding safe integer', () => {
    const hugeValue = 1e21;
    expect(formatNumberAsString(hugeValue)).toBe(hugeValue.toString());
  });
});

describe('parseQuotaInput', () => {
  it('should parse regular numbers', () => {
    expect(parseQuotaInput('100')).toBe(100);
    expect(parseQuotaInput('1000')).toBe(1000);
  });

  it('should parse numbers with commas', () => {
    expect(parseQuotaInput('1,000')).toBe(1000);
    expect(parseQuotaInput('2,147,483,647')).toBe(2147483647);
  });

  it('should parse scientific notation', () => {
    expect(parseQuotaInput('1e9')).toBe(1e9);
    expect(parseQuotaInput('2.5e6')).toBe(2.5e6);
  });

  it('should parse decimal numbers', () => {
    expect(parseQuotaInput('100.5')).toBe(100.5);
    expect(parseQuotaInput('0.5')).toBe(0.5);
  });

  it('should return null for empty or invalid input', () => {
    expect(parseQuotaInput('')).toBeNull();
    expect(parseQuotaInput('   ')).toBeNull();
    expect(parseQuotaInput('abc')).toBeNull();
    expect(parseQuotaInput('invalid')).toBeNull();
  });

  it('should handle whitespace and special characters', () => {
    expect(parseQuotaInput('  100  ')).toBe(100);
    expect(parseQuotaInput('100abc')).toBeNull();
  });
});

describe('parseAndValidateQuota', () => {
  it('should validate token quotas correctly', () => {
    const result = parseAndValidateQuota('100', QuotaType.DAILY_TOKEN);
    expect(result.value).toBe(100);
    expect(result.validation.isValid).toBe(true);
  });

  it('should reject token quotas exceeding max', () => {
    const result = parseAndValidateQuota('3000', QuotaType.MONTHLY_TOKEN);
    expect(result.value).toBe(3000);
    expect(result.validation.isValid).toBe(false);
    expect(result.validation.error).toContain('exceeds maximum limit');
  });

  it('should validate request quotas correctly', () => {
    const result = parseAndValidateQuota('1000', QuotaType.DAILY_REQUEST);
    expect(result.value).toBe(1000);
    expect(result.validation.isValid).toBe(true);
  });

  it('should reject request quotas exceeding max', () => {
    const result = parseAndValidateQuota('3000000000', QuotaType.MONTHLY_REQUEST);
    expect(result.value).toBe(3000000000);
    expect(result.validation.isValid).toBe(false);
    expect(result.validation.error).toContain('exceeds maximum limit');
  });

  it('should reject negative values', () => {
    const result = parseAndValidateQuota('-100', QuotaType.DAILY_TOKEN);
    expect(result.validation.isValid).toBe(false);
    expect(result.validation.error).toContain('cannot be negative');
  });

  it('should return null for empty input', () => {
    const result = parseAndValidateQuota('', QuotaType.DAILY_TOKEN);
    expect(result.value).toBeNull();
    expect(result.validation.isValid).toBe(true);
  });
});

describe('tokensToQuotaUnits', () => {
  it('should convert actual tokens to M units', () => {
    expect(tokensToQuotaUnits(1000000)).toBe(1);
    expect(tokensToQuotaUnits(2000000)).toBe(2);
    expect(tokensToQuotaUnits(1000000000)).toBe(1000);
  });

  it('should floor to integer', () => {
    expect(tokensToQuotaUnits(1500000)).toBe(1);
    expect(tokensToQuotaUnits(999999)).toBe(0);
  });
});

describe('quotaUnitsToTokens', () => {
  it('should convert M units to actual tokens', () => {
    expect(quotaUnitsToTokens(1)).toBe(1000000);
    expect(quotaUnitsToTokens(2)).toBe(2000000);
    expect(quotaUnitsToTokens(1000)).toBe(1000000000);
  });
});

describe('getMaxQuotaDisplay', () => {
  it('should return correct display for token quotas', () => {
    expect(getMaxQuotaDisplay(QuotaType.DAILY_TOKEN)).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
    expect(getMaxQuotaDisplay(QuotaType.MONTHLY_TOKEN)).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
  });

  it('should return correct display for request quotas', () => {
    expect(getMaxQuotaDisplay(QuotaType.DAILY_REQUEST)).toBe(`Max: 2,147,483,647`);
    expect(getMaxQuotaDisplay(QuotaType.MONTHLY_REQUEST)).toBe(`Max: 2,147,483,647`);
  });
});

describe('getQuotaPlaceholder', () => {
  it('should return correct placeholder for token quotas', () => {
    expect(getQuotaPlaceholder(QuotaType.DAILY_TOKEN)).toBe('Unlimited (enter value in M)');
    expect(getQuotaPlaceholder(QuotaType.MONTHLY_TOKEN)).toBe('Unlimited (enter value in M)');
  });

  it('should return correct placeholder for request quotas', () => {
    expect(getQuotaPlaceholder(QuotaType.DAILY_REQUEST)).toBe('Unlimited');
    expect(getQuotaPlaceholder(QuotaType.MONTHLY_REQUEST)).toBe('Unlimited');
  });
});

describe('getQuotaLabel', () => {
  it('should return correct labels', () => {
    expect(getQuotaLabel(QuotaType.DAILY_TOKEN)).toBe('Daily Token Quota (M)');
    expect(getQuotaLabel(QuotaType.MONTHLY_TOKEN)).toBe('Monthly Token Quota (M)');
    expect(getQuotaLabel(QuotaType.DAILY_REQUEST)).toBe('Daily Request Quota');
    expect(getQuotaLabel(QuotaType.MONTHLY_REQUEST)).toBe('Monthly Request Quota');
  });
});

describe('shouldShowUnlimited', () => {
  it('should return true for null/undefined', () => {
    expect(shouldShowUnlimited(null)).toBe(true);
    expect(shouldShowUnlimited(undefined)).toBe(true);
  });

  it('should return false for actual values', () => {
    expect(shouldShowUnlimited(0)).toBe(false);
    expect(shouldShowUnlimited(100)).toBe(false);
  });
});

describe('Edge Cases', () => {
  it('should handle scientific notation input like 1e9', () => {
    const result = parseAndValidateQuota('1e9', QuotaType.DAILY_TOKEN);
    expect(result.value).toBe(1e9);
    // Should be rejected as it exceeds MAX_TOKEN_QUOTA (2147)
    expect(result.validation.isValid).toBe(false);
  });

  it('should handle the 1e21 issue', () => {
    // This simulates the bug where JavaScript parseInt truncates scientific notation
    const result = parseAndValidateQuota('1e21', QuotaType.MONTHLY_TOKEN);
    expect(result.value).toBe(1e21);
    // Should be rejected as it exceeds safe integer range
    expect(result.validation.isValid).toBe(false);
  });

  it('should handle boundary values correctly', () => {
    // Just below max
    const belowMax = parseAndValidateQuota('2146', QuotaType.MONTHLY_TOKEN);
    expect(belowMax.validation.isValid).toBe(true);

    // Exactly at max
    const atMax = parseAndValidateQuota('2147', QuotaType.MONTHLY_TOKEN);
    expect(atMax.validation.isValid).toBe(true);

    // Just above max
    const aboveMax = parseAndValidateQuota('2148', QuotaType.MONTHLY_TOKEN);
    expect(aboveMax.validation.isValid).toBe(false);
  });
});
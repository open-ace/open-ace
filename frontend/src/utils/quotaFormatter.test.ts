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
  calculateAvailableQuota,
  formatAvailableQuotaHint,
  getAvailableQuotaHint,
} from './quotaFormatter';
import { QuotaType, MAX_TOKEN_QUOTA, MAX_REQUEST_QUOTA } from '@/constants/quota';

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
    // Issue #3018: New max is 100000M
    const result = parseAndValidateQuota('200000', QuotaType.MONTHLY_TOKEN);
    expect(result.value).toBe(200000);
    expect(result.validation.isValid).toBe(false);
    expect(result.validation.error).toContain('exceeds maximum limit');
  });

  it('should validate request quotas correctly', () => {
    const result = parseAndValidateQuota('1000', QuotaType.DAILY_REQUEST);
    expect(result.value).toBe(1000);
    expect(result.validation.isValid).toBe(true);
  });

  it('should reject request quotas exceeding max', () => {
    // Issue #3018: New max is 10 billion
    const result = parseAndValidateQuota('20000000000', QuotaType.MONTHLY_REQUEST);
    expect(result.value).toBe(20000000000);
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
    // Issue #3018: New max is 10 billion
    expect(getMaxQuotaDisplay(QuotaType.DAILY_REQUEST)).toBe(`Max: 10,000,000,000`);
    expect(getMaxQuotaDisplay(QuotaType.MONTHLY_REQUEST)).toBe(`Max: 10,000,000,000`);
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
    // Issue #3018: New max is 100000M, 1e9 exceeds it
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
    // Issue #3018: New max is 100000M
    // Just below max
    const belowMax = parseAndValidateQuota('99999', QuotaType.MONTHLY_TOKEN);
    expect(belowMax.validation.isValid).toBe(true);

    // Exactly at max
    const atMax = parseAndValidateQuota('100000', QuotaType.MONTHLY_TOKEN);
    expect(atMax.validation.isValid).toBe(true);

    // Just above max
    const aboveMax = parseAndValidateQuota('100001', QuotaType.MONTHLY_TOKEN);
    expect(aboveMax.validation.isValid).toBe(false);
  });
});

// ============================================================
// Tests for Available Quota Functions (Issue #2945)
// ============================================================

describe('quotaFormatter - available quota functions', () => {
  // Helper function to create mock QuotaStats
  function createMockQuotaStats(overrides?: {
    remaining?: {
      daily_token?: number;
      monthly_token?: number;
      daily_request?: number;
      monthly_request?: number;
    };
  }) {
    return {
      remaining: {
        daily_token: overrides?.remaining?.daily_token ?? 0,
        monthly_token: overrides?.remaining?.monthly_token ?? 0,
        daily_request: overrides?.remaining?.daily_request ?? 0,
        monthly_request: overrides?.remaining?.monthly_request ?? 0,
      },
    };
  }

  describe('calculateAvailableQuota', () => {
    describe('normal scenarios', () => {
      it('should calculate available quota for DAILY_TOKEN correctly', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, 500, 100)).toBe(600);
      });

      it('should calculate available quota for MONTHLY_TOKEN correctly', () => {
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, 800, 200)).toBe(1000);
      });

      it('should calculate available quota for DAILY_REQUEST correctly', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, 1000000, 500000)).toBe(1500000);
      });

      it('should calculate available quota for MONTHLY_REQUEST correctly', () => {
        expect(calculateAvailableQuota(QuotaType.MONTHLY_REQUEST, 2000000, 1000000)).toBe(3000000);
      });

      it('should return remaining when current is 0', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, 500, 0)).toBe(500);
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, 1000000, 0)).toBe(1000000);
      });

      it('should handle decimal values correctly for token quota', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, 100.5, 50.25)).toBe(150.75);
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, 99.9, 0.1)).toBe(100);
      });
    });

    describe('boundary scenarios', () => {
      it('should cap at max when remaining + current exceeds max for token types', () => {
        // Issue #3018: New max is 100000M
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, 200000, 0)).toBe(MAX_TOKEN_QUOTA);
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, 150000, 50000)).toBe(
          MAX_TOKEN_QUOTA
        );
      });

      it('should cap at max when remaining + current exceeds max for request types', () => {
        // Issue #3018: New max is 10 billion
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, 15000000000, 0)).toBe(
          MAX_REQUEST_QUOTA
        );
        expect(calculateAvailableQuota(QuotaType.MONTHLY_REQUEST, 8000000000, 5000000000)).toBe(
          MAX_REQUEST_QUOTA
        );
      });

      it('should return 0 when remaining + current is negative', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, -200, 50)).toBe(0);
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, -1000000, 500000)).toBe(0);
      });

      it('should handle negative remaining (over-allocated)', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, -100, 50)).toBe(0);
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, -500, 200)).toBe(0);
      });

      it('should handle both remaining and current negative', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, -100, -50)).toBe(0);
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, -1000000, -500000)).toBe(0);
      });

      it('should return 0 when remaining + current equals 0', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, -100, 100)).toBe(0);
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, 0, 0)).toBe(0);
      });

      it('should handle exactly max value', () => {
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, MAX_TOKEN_QUOTA, 0)).toBe(
          MAX_TOKEN_QUOTA
        );
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, MAX_REQUEST_QUOTA, 0)).toBe(
          MAX_REQUEST_QUOTA
        );
      });

      it('should handle large request quota values', () => {
        const result = calculateAvailableQuota(QuotaType.DAILY_REQUEST, 2000000000, 100000000);
        expect(result).toBe(2100000000);
      });
    });

    describe('max derivation', () => {
      it('should use MAX_TOKEN_QUOTA for DAILY_TOKEN', () => {
        // Issue #3018: New max is 100000M
        expect(calculateAvailableQuota(QuotaType.DAILY_TOKEN, 200000, 0)).toBe(MAX_TOKEN_QUOTA);
      });

      it('should use MAX_TOKEN_QUOTA for MONTHLY_TOKEN', () => {
        expect(calculateAvailableQuota(QuotaType.MONTHLY_TOKEN, 200000, 0)).toBe(MAX_TOKEN_QUOTA);
      });

      it('should use MAX_REQUEST_QUOTA for DAILY_REQUEST', () => {
        // Issue #3018: New max is 10 billion
        expect(calculateAvailableQuota(QuotaType.DAILY_REQUEST, 20000000000, 0)).toBe(
          MAX_REQUEST_QUOTA
        );
      });

      it('should use MAX_REQUEST_QUOTA for MONTHLY_REQUEST', () => {
        expect(calculateAvailableQuota(QuotaType.MONTHLY_REQUEST, 20000000000, 0)).toBe(
          MAX_REQUEST_QUOTA
        );
      });
    });
  });

  describe('formatAvailableQuotaHint', () => {
    describe('formatting', () => {
      it('should format token quota with M suffix and 2 decimal places', () => {
        const result = formatAvailableQuotaHint(150.75, QuotaType.DAILY_TOKEN, true, 'zh');
        expect(result).toBe(`可用: 150.75M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should format request quota with thousand separators', () => {
        const result = formatAvailableQuotaHint(1500000, QuotaType.DAILY_REQUEST, true, 'zh');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('可用: 1,500,000 (上限: 10,000,000,000)');
      });

      it('should not display M suffix for request quotas', () => {
        const result = formatAvailableQuotaHint(1000000, QuotaType.MONTHLY_REQUEST, true, 'zh');
        expect(result).not.toContain('M');
      });
    });

    describe('fallback', () => {
      it('should return max-only fallback when hasQuotaStats is false for token quota', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, false, 'zh');
        expect(result).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
        expect(result).not.toContain('可用');
      });

      it('should return max-only fallback when hasQuotaStats is false for request quota', () => {
        const result = formatAvailableQuotaHint(1000000, QuotaType.DAILY_REQUEST, false, 'zh');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('Max: 10,000,000,000');
        expect(result).not.toContain('可用');
      });

      it('should return full hint when hasQuotaStats is true', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, true, 'zh');
        expect(result).toContain('可用');
        expect(result).toContain('上限');
      });
    });

    describe('i18n', () => {
      it('should return Chinese hint by default', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, true);
        expect(result).toBe(`可用: 100.00M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should return Chinese hint when language is zh', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, true, 'zh');
        expect(result).toContain('可用');
        expect(result).toContain('上限');
      });

      it('should return English hint when language is en for token quota', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, true, 'en');
        expect(result).toBe(`Available: 100.00M (Max: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should return English hint when language is en for request quota', () => {
        const result = formatAvailableQuotaHint(1500000, QuotaType.DAILY_REQUEST, true, 'en');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('Available: 1,500,000 (Max: 10,000,000,000)');
      });

      it('should return English hint for ja language (fallback)', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, true, 'ja');
        expect(result).toBe(`Available: 100.00M (Max: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should return English hint for ko language (fallback)', () => {
        const result = formatAvailableQuotaHint(1000000, QuotaType.DAILY_REQUEST, true, 'ko');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('Available: 1,000,000 (Max: 10,000,000,000)');
      });

      it('should return English fallback when language is en and hasQuotaStats is false', () => {
        const result = formatAvailableQuotaHint(100, QuotaType.DAILY_TOKEN, false, 'en');
        expect(result).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
      });
    });
  });

  describe('getAvailableQuotaHint', () => {
    describe('integration', () => {
      it('should integrate calculate and format for DAILY_TOKEN', () => {
        const quotaStats = createMockQuotaStats({ remaining: { daily_token: 500 } });
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, quotaStats, 100, 'zh');
        expect(result).toBe(`可用: 600.00M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should integrate calculate and format for MONTHLY_TOKEN', () => {
        const quotaStats = createMockQuotaStats({ remaining: { monthly_token: 800 } });
        const result = getAvailableQuotaHint(QuotaType.MONTHLY_TOKEN, quotaStats, 200, 'zh');
        expect(result).toBe(`可用: 1000.00M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should integrate calculate and format for DAILY_REQUEST', () => {
        const quotaStats = createMockQuotaStats({ remaining: { daily_request: 1000000 } });
        const result = getAvailableQuotaHint(QuotaType.DAILY_REQUEST, quotaStats, 500000, 'zh');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('可用: 1,500,000 (上限: 10,000,000,000)');
      });

      it('should integrate calculate and format for MONTHLY_REQUEST', () => {
        const quotaStats = createMockQuotaStats({ remaining: { monthly_request: 2000000 } });
        const result = getAvailableQuotaHint(QuotaType.MONTHLY_REQUEST, quotaStats, 1000000, 'zh');
        // Issue #3018: New max is 10 billion
        expect(result).toBe('可用: 3,000,000 (上限: 10,000,000,000)');
      });

      it('should handle currentQuota as 0', () => {
        const quotaStats = createMockQuotaStats({ remaining: { daily_token: 500 } });
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, quotaStats, 0, 'zh');
        expect(result).toBe(`可用: 500.00M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should use default language zh when not specified', () => {
        const quotaStats = createMockQuotaStats({ remaining: { daily_token: 500 } });
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, quotaStats, 100);
        expect(result).toContain('可用');
      });
    });

    describe('null/undefined handling', () => {
      it('should return fallback when quotaStats is null', () => {
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, null, 100, 'zh');
        expect(result).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
        expect(result).not.toContain('可用');
      });

      it('should return fallback when quotaStats is undefined', () => {
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, undefined, 100, 'zh');
        expect(result).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
      });

      it('should handle quotaStats.remaining.daily_token as undefined', () => {
        const quotaStats = {
          remaining: {
            daily_token: undefined as any,
            monthly_token: 0,
            daily_request: 0,
            monthly_request: 0,
          },
        };
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, quotaStats, 100, 'zh');
        expect(result).toContain('100.00M'); // 0 (from undefined) + 100 = 100
      });

      it('should handle currentQuota as undefined', () => {
        const quotaStats = createMockQuotaStats({ remaining: { daily_token: 500 } });
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, quotaStats, undefined, 'zh');
        expect(result).toBe(`可用: 500.00M (上限: ${MAX_TOKEN_QUOTA}M)`);
      });

      it('should handle both quotaStats and currentQuota as undefined/null', () => {
        const result = getAvailableQuotaHint(QuotaType.DAILY_TOKEN, undefined, undefined, 'zh');
        expect(result).toBe(`Max: ${MAX_TOKEN_QUOTA}M`);
      });
    });
  });
});

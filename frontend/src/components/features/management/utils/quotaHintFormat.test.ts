/**
 * Tests for quotaHintFormat utility function
 *
 * Covers:
 * - Token type formatting with and without quotaStats
 * - Request type formatting with and without quotaStats
 * - Decimal formatting (toFixed(2) behavior)
 * - Number string formatting
 *
 * Issue #3018: Updated for new quota limits (100000M tokens, 10 billion requests)
 */

import { describe, it, expect } from 'vitest';
import { QuotaType, MAX_TOKEN_QUOTA, MAX_REQUEST_QUOTA } from '@/constants/quota';
import { formatQuotaHint } from './quotaHintFormat';

// ============================================================================
// Token Type Formatting Tests
// ============================================================================

describe('formatQuotaHint - token type', () => {
  it('should format token with quotaStats available', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150, MAX_TOKEN_QUOTA, true);
    // Issue #3018: New max is 100000M
    expect(result).toBe('可用: 150.00M (上限: 100000M)');
  });

  it('should format token without quotaStats (fallback)', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 0, MAX_TOKEN_QUOTA, false);
    expect(result).toBe('Max: 100000M');
  });

  it('should format token with decimal places', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150.1, MAX_TOKEN_QUOTA, true);
    expect(result).toBe('可用: 150.10M (上限: 100000M)');
  });

  it('should format token with zero available', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 0, MAX_TOKEN_QUOTA, true);
    expect(result).toBe('可用: 0.00M (上限: 100000M)');
  });

  it('should format MONTHLY_TOKEN correctly', () => {
    const result = formatQuotaHint(QuotaType.MONTHLY_TOKEN, 700, MAX_TOKEN_QUOTA, true);
    expect(result).toBe('可用: 700.00M (上限: 100000M)');
  });

  it('should format MONTHLY_TOKEN without quotaStats', () => {
    const result = formatQuotaHint(QuotaType.MONTHLY_TOKEN, 0, MAX_TOKEN_QUOTA, false);
    expect(result).toBe('Max: 100000M');
  });
});

// ============================================================================
// Request Type Formatting Tests
// ============================================================================

describe('formatQuotaHint - request type', () => {
  it('should format request with quotaStats available', () => {
    const result = formatQuotaHint(QuotaType.DAILY_REQUEST, 1500000, MAX_REQUEST_QUOTA, true);
    // Issue #3018: New max is 10 billion
    expect(result).toBe('可用: 1,500,000 (上限: 10,000,000,000)');
  });

  it('should format request without quotaStats (fallback)', () => {
    const result = formatQuotaHint(QuotaType.DAILY_REQUEST, 0, MAX_REQUEST_QUOTA, false);
    expect(result).toBe('Max: 10,000,000,000');
  });

  it('should format request with zero available', () => {
    const result = formatQuotaHint(QuotaType.DAILY_REQUEST, 0, MAX_REQUEST_QUOTA, true);
    expect(result).toBe('可用: 0 (上限: 10,000,000,000)');
  });

  it('should format MONTHLY_REQUEST correctly', () => {
    const result = formatQuotaHint(QuotaType.MONTHLY_REQUEST, 7000000, MAX_REQUEST_QUOTA, true);
    expect(result).toBe('可用: 7,000,000 (上限: 10,000,000,000)');
  });

  it('should format MONTHLY_REQUEST without quotaStats', () => {
    const result = formatQuotaHint(QuotaType.MONTHLY_REQUEST, 0, MAX_REQUEST_QUOTA, false);
    expect(result).toBe('Max: 10,000,000,000');
  });

  it('should format large request values correctly', () => {
    const result = formatQuotaHint(QuotaType.DAILY_REQUEST, 2000000000, MAX_REQUEST_QUOTA, true);
    expect(result).toBe('可用: 2,000,000,000 (上限: 10,000,000,000)');
  });
});

// ============================================================================
// Decimal Formatting Tests
// ============================================================================

describe('formatQuotaHint - decimal formatting', () => {
  it('should pad zeros for whole number (toFixed(2))', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150, MAX_TOKEN_QUOTA, true);
    expect(result).toContain('150.00M');
  });

  it('should handle 1 decimal place', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150.1, MAX_TOKEN_QUOTA, true);
    expect(result).toContain('150.10M');
  });

  it('should handle 2 decimal places', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150.12, MAX_TOKEN_QUOTA, true);
    expect(result).toContain('150.12M');
  });

  it('should round to 2 decimal places', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 150.125, MAX_TOKEN_QUOTA, true);
    expect(result).toContain('150.13M'); // JavaScript toFixed rounds
  });

  it('should handle very small decimal', () => {
    const result = formatQuotaHint(QuotaType.DAILY_TOKEN, 0.5, MAX_TOKEN_QUOTA, true);
    expect(result).toContain('0.50M');
  });
});

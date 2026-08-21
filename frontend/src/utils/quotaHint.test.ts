/**
 * Tests for quotaHint utility functions
 *
 * Covers:
 * - Null boundary conditions (quotaStats and editingUser)
 * - Field-level undefined handling
 * - All four quota types (DAILY_TOKEN, MONTHLY_TOKEN, DAILY_REQUEST, MONTHLY_REQUEST)
 * - Edge cases (negative, overflow, zero, large values)
 */

import { describe, it, expect } from 'vitest';
import { QuotaType, MAX_TOKEN_QUOTA, MAX_REQUEST_QUOTA } from '@/constants/quota';
import { calculateAvailableQuota, getQuotaTypeInfo } from './quotaHint';
import type { QuotaStats, QuotaUsage } from '@/api/admin';

// ============================================================================
// Test Fixtures
// ============================================================================

const createMockQuotaStats = (overrides?: Partial<QuotaStats>): QuotaStats => ({
  tenant_quota: {
    daily_token_limit: 10000,
    monthly_token_limit: 300000,
    daily_request_limit: 100000000,
    monthly_request_limit: 3000000000,
    max_users: 100,
  },
  allocated: {
    daily_token: 500,
    monthly_token: 15000,
    daily_request: 50000000,
    monthly_request: 1500000000,
  },
  remaining: {
    daily_token: 9500,
    monthly_token: 285000,
    daily_request: 50000000,
    monthly_request: 1500000000,
  },
  percentages: {
    daily_token: 5,
    monthly_token: 5,
    daily_request: 50,
    monthly_request: 50,
  },
  user_count: {
    total: 75,
    active: 50,
    max: 100,
  },
  ...overrides,
});

const createMockEditingUser = (overrides?: Partial<QuotaUsage>): QuotaUsage => ({
  id: 1,
  username: 'testuser',
  email: 'test@example.com',
  role: 'user',
  is_active: true,
  created_at: '2024-01-01T00:00:00Z',
  daily_token_quota: 100,
  monthly_token_quota: 3000,
  daily_request_quota: 1000000,
  monthly_request_quota: 30000000,
  ...overrides,
});

// ============================================================================
// Null Boundary Tests
// ============================================================================

describe('calculateAvailableQuota - null boundary', () => {
  it('should return current quota when quotaStats is null for token type', () => {
    const editingUser = createMockEditingUser({ daily_token_quota: 150 });
    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats: null,
      editingUser,
    });
    expect(result).toBe(150);
  });

  it('should return current quota when quotaStats is null for request type', () => {
    const editingUser = createMockEditingUser({ daily_request_quota: 2000000 });
    const result = calculateAvailableQuota(QuotaType.DAILY_REQUEST, {
      quotaStats: null,
      editingUser,
    });
    expect(result).toBe(2000000);
  });

  it('should return remaining quota capped at max when editingUser is null', () => {
    const quotaStats = createMockQuotaStats({
      remaining: { ...createMockQuotaStats().remaining, daily_token: 9500 },
    });
    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser: null,
    });
    // remaining is 9500, but max is 2147, so result is capped at 2147
    expect(result).toBe(MAX_TOKEN_QUOTA);
  });

  it('should return 0 when both quotaStats and editingUser are null', () => {
    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats: null,
      editingUser: null,
    });
    expect(result).toBe(0);
  });

  it('should return 0 when both quotaStats and editingUser are null for request type', () => {
    const result = calculateAvailableQuota(QuotaType.MONTHLY_REQUEST, {
      quotaStats: null,
      editingUser: null,
    });
    expect(result).toBe(0);
  });
});

// ============================================================================
// Field-level Undefined Tests
// ============================================================================

describe('calculateAvailableQuota - field-level undefined', () => {
  it('should use 0 for undefined remaining.daily_token', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: undefined as unknown as number,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_token_quota: 100 });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    // remaining is 0 (undefined ?? 0), current is 100, so available is 100
    expect(result).toBe(100);
  });

  it('should use 0 for undefined editingUser.daily_token_quota and cap at max', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 9500,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({
      daily_token_quota: undefined,
    });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    // remaining is 9500, current is 0 (undefined ?? 0), but max is 2147, so result is capped at 2147
    expect(result).toBe(MAX_TOKEN_QUOTA);
  });

  it('should return 0 when both remaining and current are undefined', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: undefined as unknown as number,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({
      daily_token_quota: undefined,
    });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    expect(result).toBe(0);
  });
});

// ============================================================================
// Quota Type Correctness Tests (Parameterized)
// ============================================================================

describe('calculateAvailableQuota - quota type correctness', () => {
  it.each([
    {
      type: QuotaType.DAILY_TOKEN,
      remaining: 100,
      current: 50,
      expected: 150,
      description: 'DAILY_TOKEN',
    },
    {
      type: QuotaType.MONTHLY_TOKEN,
      remaining: 500,
      current: 200,
      expected: 700,
      description: 'MONTHLY_TOKEN',
    },
    {
      type: QuotaType.DAILY_REQUEST,
      remaining: 1000000,
      current: 500000,
      expected: 1500000,
      description: 'DAILY_REQUEST',
    },
    {
      type: QuotaType.MONTHLY_REQUEST,
      remaining: 5000000,
      current: 2000000,
      expected: 7000000,
      description: 'MONTHLY_REQUEST',
    },
  ])('should calculate $description correctly', ({ type, remaining, current, expected }) => {
    const quotaStats = createMockQuotaStats();
    const editingUser = createMockEditingUser();

    // Override remaining values based on type
    if (type === QuotaType.DAILY_TOKEN) {
      quotaStats.remaining.daily_token = remaining;
      (editingUser as QuotaUsage).daily_token_quota = current;
    } else if (type === QuotaType.MONTHLY_TOKEN) {
      quotaStats.remaining.monthly_token = remaining;
      (editingUser as QuotaUsage).monthly_token_quota = current;
    } else if (type === QuotaType.DAILY_REQUEST) {
      quotaStats.remaining.daily_request = remaining;
      (editingUser as QuotaUsage).daily_request_quota = current;
    } else if (type === QuotaType.MONTHLY_REQUEST) {
      quotaStats.remaining.monthly_request = remaining;
      (editingUser as QuotaUsage).monthly_request_quota = current;
    }

    const result = calculateAvailableQuota(type, { quotaStats, editingUser });
    expect(result).toBe(expected);
  });
});

// ============================================================================
// Edge Cases Tests
// ============================================================================

describe('calculateAvailableQuota - edge cases', () => {
  it('should return 0 for negative result (Math.max(0, ...))', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: -50,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_token_quota: 30 });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    // -50 + 30 = -20, Math.max(0, -20) = 0
    expect(result).toBe(0);
  });

  it('should return max when sum exceeds max (Math.min(..., max))', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 2000,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_token_quota: 500 });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    // 2000 + 500 = 2500, but max is 2147, so result is 2147
    expect(result).toBe(MAX_TOKEN_QUOTA);
  });

  it('should return exact 0 for zero values', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 0,
        monthly_token: 0,
        daily_request: 0,
        monthly_request: 0,
      },
    });
    const editingUser = createMockEditingUser({
      daily_token_quota: 0,
      monthly_token_quota: 0,
      daily_request_quota: 0,
      monthly_request_quota: 0,
    });

    expect(
      calculateAvailableQuota(QuotaType.DAILY_TOKEN, { quotaStats, editingUser })
    ).toBe(0);
    expect(
      calculateAvailableQuota(QuotaType.DAILY_REQUEST, { quotaStats, editingUser })
    ).toBe(0);
  });

  it('should handle large request values without precision loss', () => {
    const largeValue = 2000000000;
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 9500,
        monthly_token: 285000,
        daily_request: largeValue,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_request_quota: 0 });

    const result = calculateAvailableQuota(QuotaType.DAILY_REQUEST, {
      quotaStats,
      editingUser,
    });
    expect(result).toBe(largeValue);
  });

  it('should return exact max when sum equals max', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 1647,
        monthly_token: 285000,
        daily_request: 50000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_token_quota: 500 });

    const result = calculateAvailableQuota(QuotaType.DAILY_TOKEN, {
      quotaStats,
      editingUser,
    });
    // 1647 + 500 = 2147 = MAX_TOKEN_QUOTA
    expect(result).toBe(MAX_TOKEN_QUOTA);
  });

  it('should cap at max request quota when sum exceeds', () => {
    const quotaStats = createMockQuotaStats({
      remaining: {
        daily_token: 9500,
        monthly_token: 285000,
        daily_request: 2000000000,
        monthly_request: 1500000000,
      },
    });
    const editingUser = createMockEditingUser({ daily_request_quota: 500000000 });

    const result = calculateAvailableQuota(QuotaType.DAILY_REQUEST, {
      quotaStats,
      editingUser,
    });
    expect(result).toBe(MAX_REQUEST_QUOTA);
  });
});

// ============================================================================
// getQuotaTypeInfo Tests
// ============================================================================

describe('getQuotaTypeInfo', () => {
  it('should return correct info for DAILY_TOKEN', () => {
    const info = getQuotaTypeInfo(QuotaType.DAILY_TOKEN);
    expect(info.max).toBe(MAX_TOKEN_QUOTA);
    expect(info.isToken).toBe(true);
  });

  it('should return correct info for MONTHLY_TOKEN', () => {
    const info = getQuotaTypeInfo(QuotaType.MONTHLY_TOKEN);
    expect(info.max).toBe(MAX_TOKEN_QUOTA);
    expect(info.isToken).toBe(true);
  });

  it('should return correct info for DAILY_REQUEST', () => {
    const info = getQuotaTypeInfo(QuotaType.DAILY_REQUEST);
    expect(info.max).toBe(MAX_REQUEST_QUOTA);
    expect(info.isToken).toBe(false);
  });

  it('should return correct info for MONTHLY_REQUEST', () => {
    const info = getQuotaTypeInfo(QuotaType.MONTHLY_REQUEST);
    expect(info.max).toBe(MAX_REQUEST_QUOTA);
    expect(info.isToken).toBe(false);
  });
});
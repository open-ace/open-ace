/**
 * Tests for dateRange utilities
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { getDefaultDateRange, toLocalDateString, DEFAULT_DATE_RANGE_DAYS } from './dateRange';

describe('DEFAULT_DATE_RANGE_DAYS', () => {
  it('is 30 (matches other analysis pages)', () => {
    expect(DEFAULT_DATE_RANGE_DAYS).toBe(30);
  });
});

describe('toLocalDateString', () => {
  it('formats a date as local YYYY-MM-DD with zero-padded month/day', () => {
    // new Date(year, monthIndex, day) is interpreted in LOCAL time, so the
    // local calendar date is unambiguous regardless of the host timezone.
    expect(toLocalDateString(new Date(2024, 0, 5))).toBe('2024-01-05');
    expect(toLocalDateString(new Date(2024, 10, 25))).toBe('2024-11-25');
    expect(toLocalDateString(new Date(2026, 5, 18))).toBe('2026-06-18');
  });

  it('does not shift the day across the UTC boundary (local midnight)', () => {
    // A local-midnight Date must still report its own calendar day. Using
    // toISOString() here would roll it back a day on UTC+ hosts.
    const localMidnight = new Date(2024, 2, 15); // local 2024-03-15 00:00
    expect(toLocalDateString(localMidnight)).toBe('2024-03-15');
  });
});

describe('getDefaultDateRange', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Pin "today" to local 2024-06-15 12:00 (local calendar day is stable).
    vi.setSystemTime(new Date(2024, 5, 15, 12, 0, 0));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('defaults to a 30-day window ending today (exactly 30 calendar days)', () => {
    const range = getDefaultDateRange();
    expect(range.end).toBe('2024-06-15');
    expect(range.start).toBe('2024-05-17'); // 30 days: 05-17 to 06-15
  });

  it('returns exactly N calendar days (inclusive range)', () => {
    const range = getDefaultDateRange(7);
    expect(range.end).toBe('2024-06-15');
    expect(range.start).toBe('2024-06-09'); // 7 days: 06-09,10,11,12,13,14,15
  });

  it('crosses month and year boundaries correctly', () => {
    vi.setSystemTime(new Date(2024, 0, 5, 12, 0, 0)); // local 2024-01-05
    const range = getDefaultDateRange(30);
    expect(range.end).toBe('2024-01-05');
    expect(range.start).toBe('2023-12-07'); // 30 days: 12-07 to 01-05
  });

  it('returns start == end for a 0-day lookback', () => {
    const range = getDefaultDateRange(0);
    expect(range.start).toBe(range.end);
    expect(range.end).toBe('2024-06-15');
  });

  it('handles leap year correctly', () => {
    vi.setSystemTime(new Date(2024, 2, 1, 12, 0, 0)); // local 2024-03-01 (leap year)
    const range = getDefaultDateRange(7);
    expect(range.end).toBe('2024-03-01');
    expect(range.start).toBe('2024-02-24'); // 7 days including Feb 29
  });

  it('always returns start <= end', () => {
    const range = getDefaultDateRange();
    expect(range.start <= range.end).toBe(true);
  });
});

/**
 * Tests for format utilities
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  formatTokens,
  formatNumber,
  formatPercentage,
  formatDate,
  formatDateTime,
  formatRelativeTime,
  formatBytes,
  formatDuration,
  formatChartDate,
  displaySessionId,
} from './format';

describe('formatTokens', () => {
  it('should format billions correctly', () => {
    expect(formatTokens(1_500_000_000)).toBe('1.50B');
    expect(formatTokens(2_345_678_901)).toBe('2.35B');
  });

  it('should format millions correctly', () => {
    expect(formatTokens(1_500_000)).toBe('1.50M');
    expect(formatTokens(1_000_000)).toBe('1.00M');
  });

  it('should format thousands correctly', () => {
    expect(formatTokens(1_500)).toBe('1.50K');
    expect(formatTokens(1_000)).toBe('1.00K');
  });

  it('should format small numbers without suffix', () => {
    expect(formatTokens(100)).toBe('100');
    expect(formatTokens(0)).toBe('0');
    expect(formatTokens(999)).toBe('999');
  });

  it('should handle edge cases', () => {
    expect(formatTokens(1000)).toBe('1.00K');
    expect(formatTokens(1_000_000)).toBe('1.00M');
    expect(formatTokens(1_000_000_000)).toBe('1.00B');
  });
});

describe('formatNumber', () => {
  it('should format numbers with locale formatting', () => {
    expect(formatNumber(1000)).toBe('1,000');
    expect(formatNumber(1234567)).toBe('1,234,567');
    expect(formatNumber(0)).toBe('0');
  });
});

describe('formatPercentage', () => {
  it('should format percentages with default decimals', () => {
    expect(formatPercentage(50)).toBe('50.0%');
    expect(formatPercentage(33.333)).toBe('33.3%');
  });

  it('should format percentages with custom decimals', () => {
    expect(formatPercentage(50, 2)).toBe('50.00%');
    expect(formatPercentage(33.333, 3)).toBe('33.333%');
  });
});

describe('formatDate', () => {
  it('should format date in short format by default', () => {
    const date = new Date('2024-03-15');
    const result = formatDate(date);
    expect(result).toMatch(/2024/);
  });

  it('should format date in ISO format', () => {
    const date = new Date('2024-03-15T12:00:00Z');
    expect(formatDate(date, 'iso')).toBe('2024-03-15');
  });

  it('should format date in long format', () => {
    const date = new Date('2024-03-15');
    const result = formatDate(date, 'long');
    // Check for year and day (month name depends on locale)
    expect(result).toMatch(/15/);
    expect(result).toMatch(/2024/);
  });

  it('should handle string dates', () => {
    expect(formatDate('2024-03-15', 'iso')).toBe('2024-03-15');
  });

  it('should localize per language when language is provided', () => {
    const langs = ['en', 'zh', 'ja', 'ko'] as const;
    for (const lang of langs) {
      const result = formatDate('2024-03-15', 'short', lang);
      // Day-of-month and year must always be present
      expect(result).toMatch(/15/);
      expect(result).toMatch(/2024/);
    }
  });

  it('should render long format with the UI language locale', () => {
    // zh-CN long format contains the CJK numeral for year, e.g. "2024年3月15日"
    const result = formatDate('2024-03-15', 'long', 'zh');
    expect(result).toMatch(/15/);
    expect(result).toMatch(/2024/);
    expect(result).toMatch(/年/);
  });

  it('should ignore language for ISO format (stable machine-readable output)', () => {
    expect(formatDate('2024-03-15', 'iso', 'zh')).toBe('2024-03-15');
  });

  it('should fall back to browser locale when language is omitted', () => {
    // No language argument — behaves exactly as before the i18n change.
    const result = formatDate('2024-03-15');
    expect(result).toMatch(/15/);
    expect(result).toMatch(/2024/);
  });
});

describe('formatDateTime', () => {
  it('should format datetime with locale formatting', () => {
    const date = new Date('2024-03-15T14:30:00');
    const result = formatDateTime(date);
    expect(result).toMatch(/2024/);
    expect(result).toMatch(/15/);
  });

  it('should return "-" for null/empty values', () => {
    expect(formatDateTime(null)).toBe('-');
    expect(formatDateTime('')).toBe('-');
  });

  it('should return "-" for invalid dates', () => {
    expect(formatDateTime('not-a-date')).toBe('-');
  });

  it('should localize per language when language is provided', () => {
    const langs = ['en', 'zh', 'ja', 'ko'] as const;
    for (const lang of langs) {
      const result = formatDateTime('2024-03-15T14:30:00', lang);
      expect(result).toMatch(/15/);
      expect(result).toMatch(/2024/);
    }
  });

  it('should fall back to browser locale when language is omitted', () => {
    const result = formatDateTime('2024-03-15T14:30:00');
    expect(result).toMatch(/15/);
    expect(result).toMatch(/2024/);
  });
});

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2024-03-15T12:00:00'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should return "just now" for recent times', () => {
    const date = new Date('2024-03-15T11:59:30');
    expect(formatRelativeTime(date)).toBe('just now');
  });

  it('should return minutes ago in short format', () => {
    const date = new Date('2024-03-15T11:55:00');
    expect(formatRelativeTime(date)).toBe('5 min ago');
  });

  it('should return hours ago in short format', () => {
    const date = new Date('2024-03-15T10:00:00');
    expect(formatRelativeTime(date)).toBe('2 hr ago');
  });

  it('should return days ago in short format', () => {
    const date = new Date('2024-03-13T12:00:00');
    expect(formatRelativeTime(date)).toBe('2 day ago');
  });

  it('should return formatted date for older dates', () => {
    const date = new Date('2024-03-01T12:00:00');
    const result = formatRelativeTime(date);
    expect(result).toMatch(/2024/);
  });

  it('should support custom translations', () => {
    const date = new Date('2024-03-15T11:55:00');
    expect(formatRelativeTime(date, { minAgo: '分钟前' })).toBe('5 分钟前');
  });

  it('should support Chinese translations', () => {
    const date = new Date('2024-03-15T11:55:00');
    const cnTranslations = { justNow: '刚刚', minAgo: '分钟前', hourAgo: '小时前', dayAgo: '天前' };
    expect(formatRelativeTime(date, cnTranslations)).toBe('5 分钟前');
  });
});

describe('formatBytes', () => {
  it('should format 0 bytes', () => {
    expect(formatBytes(0)).toBe('0 Bytes');
  });

  it('should format bytes', () => {
    expect(formatBytes(500)).toBe('500 Bytes');
  });

  it('should format kilobytes', () => {
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('should format megabytes', () => {
    expect(formatBytes(1048576)).toBe('1 MB');
  });

  it('should format gigabytes', () => {
    expect(formatBytes(1073741824)).toBe('1 GB');
  });

  it('should respect decimal parameter', () => {
    expect(formatBytes(1536, 0)).toBe('2 KB');
    // toFixed removes trailing zeros, so 1.500 becomes 1.5
    expect(formatBytes(1536, 3)).toBe('1.5 KB');
  });

  it('should use default 2 decimals', () => {
    expect(formatBytes(1536)).toBe('1.5 KB');
  });
});

describe('formatDuration', () => {
  it('should format seconds', () => {
    expect(formatDuration(30)).toBe('30s');
    expect(formatDuration(59)).toBe('59s');
  });

  it('should format minutes and seconds', () => {
    expect(formatDuration(90)).toBe('1m 30s');
    expect(formatDuration(120)).toBe('2m 0s');
  });

  it('should format hours and minutes', () => {
    expect(formatDuration(3600)).toBe('1h 0m');
    expect(formatDuration(3661)).toBe('1h 1m');
    expect(formatDuration(7325)).toBe('2h 2m');
  });
});

describe('formatChartDate', () => {
  // Structural assertions only — never lock exact locale glyphs (ICU data
  // varies across Node/CI), only the correctness properties that matter:
  // day-of-month is preserved (no UTC cross-day shift), no time/weekday/year
  // leaks onto the axis, and the single-month branch drops the month.

  it('formats a clean YYYY-MM-DD with short month + day (en)', () => {
    const result = formatChartDate('2026-06-15', 'en');
    expect(result).not.toBe('-');
    expect(result).toContain('15');
    expect(result).not.toMatch(/2026/); // year dropped on axis
    expect(result).not.toMatch(/:/); // no time component
    expect(result).not.toMatch(/GMT/); // no RFC822 leak
  });

  it('strips a trailing time component (YYYY-MM-DD HH:MM:SS)', () => {
    const result = formatChartDate('2026-06-15 00:00:00', 'en');
    expect(result).not.toBe('-');
    expect(result).toContain('15');
    expect(result).not.toMatch(/00:00:00/);
    expect(result).not.toMatch(/:/);
  });

  it('strips an ISO 8601 T separator', () => {
    const result = formatChartDate('2026-06-15T00:00:00', 'en');
    expect(result).not.toBe('-');
    expect(result).toContain('15');
    expect(result).not.toMatch(/T/);
  });

  it('returns "-" for RFC822 HTTP-date strings (defensive fallback)', () => {
    // Post-normalization this never reaches the frontend, but the guard must
    // not render garbage like "Mon," onto the axis.
    expect(formatChartDate('Mon, 15 Jun 2026 00:00:00 GMT', 'en')).toBe('-');
  });

  it('does not shift the day across the UTC boundary (local parse)', () => {
    // A pure-date string is parsed as LOCAL time, not UTC, so 2026-06-15 must
    // render as day 15 (never 14 or 16) regardless of the host timezone.
    const result = formatChartDate('2026-06-15', 'en');
    expect(result).toContain('15');
    expect(result).not.toMatch(/14|16/);
  });

  it('renders day-only when dayOnly option is set (single-month axis)', () => {
    // en-US { day: 'numeric' } deterministically yields the bare day number.
    expect(formatChartDate('2026-06-15', 'en', { dayOnly: true })).toBe('15');
  });

  it('localizes per language without leaking time/weekday/year', () => {
    const langs = ['en', 'zh', 'ja', 'ko'] as const;
    for (const lang of langs) {
      const result = formatChartDate('2026-06-15', lang);
      expect(result).not.toBe('-');
      expect(result).toContain('15');
      expect(result).not.toMatch(/GMT|Mon|Tue|Wed|Thu|Fri|Sat|Sun|:|2026/);
    }
  });

  it('returns "-" for null / empty / unparseable input', () => {
    expect(formatChartDate(null, 'en')).toBe('-');
    expect(formatChartDate('', 'en')).toBe('-');
    expect(formatChartDate(undefined, 'en')).toBe('-');
    expect(formatChartDate('not-a-date', 'en')).toBe('-');
  });
});

describe('displaySessionId', () => {
  it('strips the sess_ prefix before slicing', () => {
    // ZCode session id — must show the UUID prefix, not "sess"
    expect(displaySessionId('sess_2a277802-3956-44cd-bf3d-d540eac924ba', 4)).toBe('2a27');
    expect(displaySessionId('sess_2a277802-3956-44cd-bf3d-d540eac924ba', 8)).toBe('2a277802');
  });

  it('slices bare UUIDs unchanged (claude/codex/qwen)', () => {
    expect(displaySessionId('399519bd-2425-4377-bd2b-206cf6bbcc5e', 4)).toBe('3995');
    expect(displaySessionId('019edfd8-79fe-7423-8ce1-31117e13a10e', 4)).toBe('019e');
  });

  it('handles missing/empty input', () => {
    expect(displaySessionId(undefined)).toBe('');
    expect(displaySessionId(null)).toBe('');
    expect(displaySessionId('')).toBe('');
  });

  it('does not strip a bare id that merely starts with "sess_" elsewhere', () => {
    // Only strips a leading "sess_" prefix; an id without it is sliced as-is.
    expect(displaySessionId('session-abc', 4)).toBe('sess');
  });
});

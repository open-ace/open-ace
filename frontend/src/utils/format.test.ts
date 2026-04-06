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
});

describe('formatDateTime', () => {
  it('should format datetime with locale formatting', () => {
    const date = new Date('2024-03-15T14:30:00');
    const result = formatDateTime(date);
    expect(result).toMatch(/2024/);
    expect(result).toMatch(/15/);
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

/**
 * Format Utilities - Number and date formatting helpers
 *
 * Performance optimizations:
 * - formatTokens uses module-level Map cache (js-cache-function-results)
 */

import type { Language } from '@/types';

// Module-level cache for formatTokens (js-cache-function-results optimization)
const tokenFormatCache = new Map<number, string>();
const MAX_CACHE_SIZE = 1000; // Limit cache size to prevent memory issues

/**
 * Format a number of tokens with K/M/B suffixes
 * Cached for performance - same values return cached results
 */
export function formatTokens(tokens: number): string {
  // Check cache first
  const cached = tokenFormatCache.get(tokens);
  if (cached !== undefined) {
    return cached;
  }

  // Calculate result
  let result: string;
  if (tokens >= 1_000_000_000) {
    result = (tokens / 1_000_000_000).toFixed(2) + 'B';
  } else if (tokens >= 1_000_000) {
    result = (tokens / 1_000_000).toFixed(2) + 'M';
  } else if (tokens >= 1_000) {
    result = (tokens / 1_000).toFixed(2) + 'K';
  } else {
    result = tokens.toString();
  }

  // Cache result with size limit
  if (tokenFormatCache.size < MAX_CACHE_SIZE) {
    tokenFormatCache.set(tokens, result);
  }

  return result;
}

/**
 * Format a number with locale-specific formatting
 */
export function formatNumber(num: number): string {
  return num.toLocaleString();
}

/**
 * Format a percentage
 */
export function formatPercentage(value: number, decimals: number = 1): string {
  return `${value.toFixed(decimals)}%`;
}

/**
 * Format a date string
 */
export function formatDate(
  date: string | Date,
  format: 'short' | 'long' | 'iso' = 'short'
): string {
  const d = typeof date === 'string' ? new Date(date) : date;

  switch (format) {
    case 'iso':
      return d.toISOString().split('T')[0];
    case 'long':
      return d.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    case 'short':
    default:
      return d.toLocaleDateString();
  }
}

/**
 * Format a datetime string
 */
export function formatDateTime(date: string | Date | null): string {
  if (!date) return '-';
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '-';
  return d.toLocaleString();
}

/**
 * Format a timestamp with seconds precision
 * Format: [YYYY-MM-DD HH:MM:SS]
 * Used for workspace output display (Issue #354)
 */
export function formatTimestampWithSeconds(date: string | Date | null): string {
  if (!date) return '';
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '';

  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hours = String(d.getHours()).padStart(2, '0');
  const minutes = String(d.getMinutes()).padStart(2, '0');
  const seconds = String(d.getSeconds()).padStart(2, '0');

  return `[${year}-${month}-${day} ${hours}:${minutes}:${seconds}]`;
}

/**
 * Format a relative time (e.g., "2 hr ago")
 * @param date - Date to format
 * @param translations - Optional translations object with keys: justNow, minAgo, hourAgo, dayAgo
 */
export function formatRelativeTime(
  date: string | Date,
  translations?: { justNow?: string; minAgo?: string; hourAgo?: string; dayAgo?: string }
): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  const t = translations ?? {};

  if (diffSec < 60) {
    return t.justNow ?? 'just now';
  } else if (diffMin < 60) {
    return `${diffMin} ${t.minAgo ?? 'min ago'}`;
  } else if (diffHour < 24) {
    return `${diffHour} ${t.hourAgo ?? 'hr ago'}`;
  } else if (diffDay < 7) {
    return `${diffDay} ${t.dayAgo ?? 'day ago'}`;
  } else {
    return formatDate(d);
  }
}

/**
 * Format bytes to human-readable size
 */
export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return '0 Bytes';

  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + ' ' + sizes[i];
}

/**
 * Format duration in seconds to human-readable format
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${minutes}m ${secs}s`;
  } else {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }
}

// Locale tag per supported UI language, for chart date rendering.
const CHART_DATE_LOCALE: Record<Language, string> = {
  en: 'en-US',
  zh: 'zh-CN',
  ja: 'ja-JP',
  ko: 'ko-KR',
};

// Anchored YYYY-MM-DD. Deliberately matched only at the start so trailing
// time components (e.g. "2026-06-01 00:00:00") are ignored, and non-matching
// formats (e.g. RFC822 "Mon, 01 Jun 2026 ...") fall through to the '-' guard.
const ISO_DATE_PREFIX_RE = /^(\d{4})-(\d{2})-(\d{2})/;

/**
 * Parse a YYYY-MM-DD prefix into local-time Date parts.
 *
 * The parts are extracted manually and fed to `new Date(y, m-1, d)` (local
 * time) rather than `new Date("2026-06-01")`, because the latter is treated
 * as UTC per the spec and would render as the previous day in UTC+ zones.
 */
function parseLocalDateParts(raw: string): { year: number; month: number; day: number } | null {
  const m = ISO_DATE_PREFIX_RE.exec(raw);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { year, month, day };
}

/**
 * Format a chart axis date label.
 *
 * Input is expected to be a (backend-normalized) YYYY-MM-DD string. Renders a
 * compact, locale-aware label:
 *  - default: short month + day (e.g. "Jun 1" / "6月1日")
 *  - `{ dayOnly: true }`: day only (e.g. "1") — use when every label on the
 *    axis shares the same month/year, so the month prefix is not repeated on
 *    each tick.
 *
 * Unparseable / null input returns '-'. Does NOT reuse `formatDate`, whose
 * 'short' branch builds the Date from the raw string and hits the UTC offset
 * pitfall described above.
 */
export function formatChartDate(
  raw: string | null | undefined,
  language: Language,
  options: { dayOnly?: boolean } = {}
): string {
  if (!raw) return '-';
  const parts = parseLocalDateParts(raw);
  if (!parts) return '-';
  const d = new Date(parts.year, parts.month - 1, parts.day);
  if (isNaN(d.getTime())) return '-';
  const locale = CHART_DATE_LOCALE[language] ?? 'en-US';
  return d.toLocaleDateString(locale, {
    day: 'numeric',
    ...(options.dayOnly ? {} : { month: 'short' }),
  });
}

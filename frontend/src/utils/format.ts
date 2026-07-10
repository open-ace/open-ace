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
  // Ensure numeric type - API may return strings in some cases
  const numTokens = Number(tokens) || 0;

  // Check cache first
  const cached = tokenFormatCache.get(numTokens);
  if (cached !== undefined) {
    return cached;
  }

  // Calculate result
  let result: string;
  if (numTokens >= 1_000_000_000) {
    result = (numTokens / 1_000_000_000).toFixed(2) + 'B';
  } else if (numTokens >= 1_000_000) {
    result = (numTokens / 1_000_000).toFixed(2) + 'M';
  } else if (numTokens >= 1_000) {
    result = (numTokens / 1_000).toFixed(2) + 'K';
  } else {
    result = numTokens.toString();
  }

  // Cache result with size limit
  if (tokenFormatCache.size < MAX_CACHE_SIZE) {
    tokenFormatCache.set(numTokens, result);
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
 * Format a date string.
 *
 * When `language` is provided, the date is rendered with the locale that
 * matches the active UI language (e.g. zh → zh-CN). Otherwise the host
 * browser locale is used, preserving backward compatibility.
 */
export function formatDate(
  date: string | Date,
  format: 'short' | 'long' | 'iso' = 'short',
  language?: Language
): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  const locale = language ? DATE_LOCALE[language] : undefined;

  switch (format) {
    case 'iso':
      return d.toISOString().split('T')[0];
    case 'long':
      return d.toLocaleDateString(locale, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      });
    case 'short':
    default:
      return d.toLocaleDateString(locale);
  }
}

/**
 * Format a datetime string.
 *
 * When `language` is provided, the value is rendered with the locale that
 * matches the active UI language. Otherwise the host browser locale is used,
 * preserving backward compatibility.
 */
export function formatDateTime(date: string | Date | null, language?: Language): string {
  if (!date) return '-';
  const d = typeof date === 'string' ? new Date(date) : date;
  if (isNaN(d.getTime())) return '-';
  const locale = language ? DATE_LOCALE[language] : undefined;
  return d.toLocaleString(locale);
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
 * Strip a known CLI tool prefix from a session id and return a short slice
 * for display in the session list.
 *
 * Claude/Codex/Qwen store bare UUIDs (e.g. "399519bd-..."), but ZCode stores
 * "sess_<uuid>" — a naive slice(0, 4) would show "sess" for every ZCode
 * session. We strip the "sess_" prefix first so all tools display the same
 * 4-char UUID prefix. The full id is never mutated, only the display value.
 *
 * @param sessionId - Full session id (may be undefined)
 * @param length - Number of characters to show (default 4)
 */
export function displaySessionId(sessionId: string | undefined | null, length: number = 4): string {
  if (!sessionId) return '';
  const stripped = sessionId.startsWith('sess_') ? sessionId.slice(5) : sessionId;
  return stripped.slice(0, length);
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

// Locale tag per supported UI language, shared across date rendering helpers.
const DATE_LOCALE: Record<Language, string> = {
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
  const locale = DATE_LOCALE[language] ?? 'en-US';
  return d.toLocaleDateString(locale, {
    day: 'numeric',
    ...(options.dayOnly ? {} : { month: 'short' }),
  });
}

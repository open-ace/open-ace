/**
 * Date Range Utilities - default date range helpers for filter UIs
 *
 * Provides a shared, locale-correct way to compute the "last N days to today"
 * range used as the default filter on analysis pages (e.g. conversation
 * history, token trend).
 *
 * Why local dates: the underlying `<input type="date">` controls and the DB
 * `date` column both operate on calendar-day strings (YYYY-MM-DD). Building the
 * string from local date parts (rather than `toISOString()`) avoids the
 * UTC-offset pitfall where an early-morning access in a UTC+ timezone would
 * shift "today" to the previous day. This mirrors the local-date handling in
 * `formatTimestampWithSeconds` and `parseLocalDateParts` in `./format`.
 */

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** The default look-back window (in days) shared across analysis pages. */
export const DEFAULT_DATE_RANGE_DAYS = 30;

export interface DateRange {
  start: string;
  end: string;
}

/**
 * Format a Date as a local YYYY-MM-DD string (no UTC conversion).
 */
export function toLocalDateString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Build a `{ start, end }` range covering exactly `days` calendar days through today,
 * expressed as local YYYY-MM-DD strings.
 *
 * IMPORTANT: Backend APIs use inclusive date range (date >= start AND date <= end).
 * To return exactly `days` days, we set start = today - (days - 1).
 *
 * Uses toLocalDateString to avoid UTC timezone offset issues.
 *
 * @param days - Number of calendar days to include (today is the inclusive end).
 *               Defaults to `DEFAULT_DATE_RANGE_DAYS` (30) to match the default
 *               on other analysis pages.
 *               Example: days=7 returns start=2024-06-09, end=2024-06-15 (7 days total)
 */
export function getDefaultDateRange(days: number = DEFAULT_DATE_RANGE_DAYS): DateRange {
  if (days <= 0) {
    const today = toLocalDateString(new Date());
    return { start: today, end: today };
  }
  const end = new Date();
  const start = new Date(end.getTime() - (days - 1) * MS_PER_DAY);
  return {
    start: toLocalDateString(start),
    end: toLocalDateString(end),
  };
}

/**
 * Quick range options type for date range selection.
 */
export type QuickRangeOption = '7' | '30' | '90' | 'all';

/**
 * Get date range for quick range selection.
 *
 * @param quickRange - Quick range option ('7', '30', '90', 'all')
 * @param dataRange - Optional data range for 'all' option (min_date, max_date)
 * @returns Date range as local date strings
 */
export function getQuickRangeDateRange(
  quickRange: QuickRangeOption,
  dataRange?: { min_date: string; max_date: string }
): DateRange {
  switch (quickRange) {
    case '7':
      return getDefaultDateRange(7);
    case '30':
      return getDefaultDateRange(30);
    case '90':
      return getDefaultDateRange(90);
    case 'all':
    default:
      if (dataRange?.min_date && dataRange?.max_date) {
        return { start: dataRange.min_date, end: dataRange.max_date };
      }
      return getDefaultDateRange(365);
  }
}

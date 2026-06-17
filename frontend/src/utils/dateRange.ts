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
 * Build a `{ start, end }` range covering the last `days` days through today,
 * expressed as local YYYY-MM-DD strings.
 *
 * @param days - Number of days to look back (today is the inclusive end).
 *               Defaults to `DEFAULT_DATE_RANGE_DAYS` (30) to match the default
 *               on other analysis pages.
 */
export function getDefaultDateRange(days: number = DEFAULT_DATE_RANGE_DAYS): DateRange {
  const end = new Date();
  const start = new Date(end.getTime() - days * MS_PER_DAY);
  return {
    start: toLocalDateString(start),
    end: toLocalDateString(end),
  };
}

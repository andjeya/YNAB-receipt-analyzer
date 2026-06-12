/**
 * dates.ts
 *
 * Shared helpers for parsing timestamps that arrive from the API.
 *
 * The backend serialises timestamps in two ways:
 *   - Naive UTC (no offset): "2026-06-12T19:32:17.708617"
 *   - Aware UTC (+00:00 / Z): "2026-06-12T19:32:17+00:00"
 *
 * JS `new Date("2026-06-12T19:32:17.708617")` treats a string with a 'T' but
 * no timezone as *local* time per the ECMAScript spec, which causes elapsed
 * times to go negative for any user behind UTC.
 *
 * Additionally, date-only strings (YYYY-MM-DD, used for display_receipt_date /
 * transaction_date) must *not* be parsed with `new Date("YYYY-MM-DD")` because
 * that is UTC-midnight and can shift the displayed calendar day for users
 * behind UTC.  Use parseApiDate() for those too; it detects the pattern and
 * returns a local-calendar Date.
 *
 * Rule:
 *   - Contains 'T', no trailing 'Z' or '±hh:mm' offset → naive UTC → append Z.
 *   - Contains 'T', already has a timezone suffix → parse as-is.
 *   - No 'T' and matches YYYY-MM-DD → local calendar date (year, month-1, day).
 *   - Everything else → fall back to new Date(value).
 */

/** Matches an ISO-8601 UTC offset suffix: Z, +HH:MM, or -HH:MM */
const TZ_SUFFIX_RE = /(?:Z|[+-]\d{2}:\d{2})$/;

/** Matches a calendar date-only string: YYYY-MM-DD */
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Parse an API-supplied date/timestamp string into a JS Date.
 *
 * - Naive datetime (T but no tz): treated as UTC.
 * - Aware datetime (T + tz suffix): parsed as-is.
 * - Date-only (YYYY-MM-DD): parsed as local calendar date (no timezone shift).
 */
export function parseApiDate(value: string): Date {
  if (value.includes("T")) {
    // datetime string
    if (TZ_SUFFIX_RE.test(value)) {
      // already has timezone — parse as-is
      return new Date(value);
    }
    // naive UTC — append Z so JS interprets it as UTC
    return new Date(`${value}Z`);
  }
  if (DATE_ONLY_RE.test(value)) {
    // calendar date — keep on the right local day
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
  }
  // fallback (unknown format)
  return new Date(value);
}

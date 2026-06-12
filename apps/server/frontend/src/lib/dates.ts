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

const WEEK_DAY_FORMAT: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };

function formatDayInZone(date: Date, timeZone: string): string {
  try {
    return date.toLocaleDateString("en-US", { ...WEEK_DAY_FORMAT, timeZone });
  } catch {
    // invalid/unsupported IANA name — fall back to UTC (the game default)
    return date.toLocaleDateString("en-US", { ...WEEK_DAY_FORMAT, timeZone: "UTC" });
  }
}

/**
 * Format a game week range ("Jun 7–Jun 13") for the trail.
 *
 * Week slots are bounded in the GAME timezone (rules.timezone), so the days
 * must be rendered in that zone — a browser-local render shifts the range by
 * a day for anyone whose tz differs from the game's. `endExclusiveIso` is the
 * next week's start; the displayed end is the inclusive last day (24h back —
 * still the same calendar day across DST transitions, since week bounds are
 * midnight-aligned in the game zone).
 */
export function formatWeekRange(startIso: string, endExclusiveIso: string, timeZone: string): string {
  const start = parseApiDate(startIso);
  const endInclusive = new Date(parseApiDate(endExclusiveIso).getTime() - 24 * 60 * 60 * 1000);
  return `${formatDayInZone(start, timeZone)}–${formatDayInZone(endInclusive, timeZone)}`;
}

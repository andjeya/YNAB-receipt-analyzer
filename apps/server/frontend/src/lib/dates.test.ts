/**
 * dates.test.ts
 *
 * node:test suite for parseApiDate().
 * Run via: npm run test:unit (tsx --test src/lib/*.test.ts)
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { parseApiDate } from "./dates";

// ---------------------------------------------------------------------------
// Naive UTC vs aware — must parse to the same instant
// ---------------------------------------------------------------------------

describe("parseApiDate — naive vs aware", () => {
  const naiveStr = "2026-06-12T19:32:17.708617";
  const awareZ = "2026-06-12T19:32:17.708617Z";
  const awarePlusTZ = "2026-06-12T19:32:17+00:00";

  it("naive string (no tz suffix) is treated as UTC", () => {
    const d = parseApiDate(naiveStr);
    // Should equal the UTC-aware version
    assert.strictEqual(d.toISOString(), new Date(awareZ).toISOString());
  });

  it("naive string and Z-aware string parse to the same instant", () => {
    const naive = parseApiDate(naiveStr);
    const aware = parseApiDate(awareZ);
    assert.strictEqual(naive.getTime(), aware.getTime());
  });

  it("naive string and +00:00-aware string parse to the same instant (second-level precision)", () => {
    // awarePlusTZ has no sub-second component — compare to second-level precision.
    const naive = parseApiDate("2026-06-12T19:32:17");  // no sub-seconds
    const aware = parseApiDate(awarePlusTZ);
    assert.strictEqual(naive.getTime(), aware.getTime());
  });

  it("aware Z string is parsed correctly", () => {
    const d = parseApiDate(awareZ);
    assert.strictEqual(d.getUTCFullYear(), 2026);
    assert.strictEqual(d.getUTCMonth(), 5); // 0-indexed June
    assert.strictEqual(d.getUTCDate(), 12);
    assert.strictEqual(d.getUTCHours(), 19);
  });

  it("aware +00:00 string is parsed correctly", () => {
    const d = parseApiDate(awarePlusTZ);
    assert.strictEqual(d.getUTCHours(), 19);
  });

  it("aware +05:30 offset string is parsed correctly", () => {
    const d = parseApiDate("2026-06-12T01:00:00+05:30");
    // 01:00 IST = 19:30 UTC previous day
    assert.strictEqual(d.getUTCHours(), 19);
    assert.strictEqual(d.getUTCMinutes(), 30);
    assert.strictEqual(d.getUTCDate(), 11);
  });
});

// ---------------------------------------------------------------------------
// Date-only strings — must stay on the right calendar day regardless of host TZ
// ---------------------------------------------------------------------------

describe("parseApiDate — date-only (YYYY-MM-DD)", () => {
  it("parses to local calendar year", () => {
    const d = parseApiDate("2026-03-15");
    assert.strictEqual(d.getFullYear(), 2026);
  });

  it("parses to local calendar month (0-indexed March = 2)", () => {
    const d = parseApiDate("2026-03-15");
    assert.strictEqual(d.getMonth(), 2);
  });

  it("parses to local calendar day", () => {
    const d = parseApiDate("2026-03-15");
    assert.strictEqual(d.getDate(), 15);
  });

  it("does NOT shift: getDate() matches the string day even in UTC-12", () => {
    // Simulate what would happen if we used new Date("2026-01-01") (UTC midnight):
    // In UTC-12 that would be Dec 31, 2025.  Our function must return Jan 1.
    // We can't change the test runner's TZ here, but we can prove the mechanism:
    // local components of the returned date must equal the string components.
    const d = parseApiDate("2026-01-01");
    assert.strictEqual(d.getFullYear(), 2026);
    assert.strictEqual(d.getMonth(), 0); // January
    assert.strictEqual(d.getDate(), 1);
  });
});

// ---------------------------------------------------------------------------
// formatWallWait-style elapsed is positive and grows for a naive timestamp
// ---------------------------------------------------------------------------

describe("parseApiDate — elapsed time stays positive and grows", () => {
  /**
   * Simulate formatWallWait: elapsed = nowMs - parseApiDate(ts).getTime()
   * Even if the host TZ is UTC-8 (8h behind UTC), a naive UTC timestamp
   * from "a few hours ago" must yield a *positive* elapsed value.
   */
  function elapsedMinutes(ts: string, nowMs: number): number {
    return Math.round((nowMs - parseApiDate(ts).getTime()) / 60_000);
  }

  it("elapsed is positive for a naive timestamp from 3h ago (clock via explicit now)", () => {
    // Construct a naive UTC timestamp 3 hours in the past
    const nowMs = Date.UTC(2026, 5, 12, 20, 0, 0); // 2026-06-12T20:00:00Z in ms
    const threeHoursAgoUtc = "2026-06-12T17:00:00.000000"; // naive UTC string
    const minutes = elapsedMinutes(threeHoursAgoUtc, nowMs);
    assert.ok(minutes > 0, `Expected positive elapsed, got ${minutes}`);
    assert.strictEqual(minutes, 180);
  });

  it("elapsed grows: 3h ago > 1h ago", () => {
    const nowMs = Date.UTC(2026, 5, 12, 20, 0, 0);
    const threeHoursAgo = elapsedMinutes("2026-06-12T17:00:00.000000", nowMs);
    const oneHourAgo = elapsedMinutes("2026-06-12T19:00:00.000000", nowMs);
    assert.ok(threeHoursAgo > oneHourAgo, `${threeHoursAgo} should be > ${oneHourAgo}`);
  });

  it("elapsed for aware string matches naive string for same instant", () => {
    const nowMs = Date.UTC(2026, 5, 12, 20, 0, 0);
    const naive = elapsedMinutes("2026-06-12T17:00:00.000000", nowMs);
    const aware = elapsedMinutes("2026-06-12T17:00:00Z", nowMs);
    assert.strictEqual(naive, aware);
  });
});

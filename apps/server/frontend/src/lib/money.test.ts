import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  signedDollars,
  formatSignedDollars,
  formatSignedDollarsFromMilliunits,
  formatSignedDollarsWithDirection,
  formatDollarsMagnitude,
  dollarsToMilliunits,
} from "./money.js";

// ---------------------------------------------------------------------------
// signedDollars
// ---------------------------------------------------------------------------
describe("signedDollars", () => {
  it("purchase returns negative magnitude", () => {
    assert.strictEqual(signedDollars(25.62, "purchase"), -25.62);
  });

  it("refund returns positive magnitude", () => {
    assert.strictEqual(signedDollars(12.40, "refund"), 12.40);
  });

  it("handles zero", () => {
    assert.strictEqual(signedDollars(0, "purchase"), -0);
    assert.strictEqual(signedDollars(0, "refund"), 0);
  });

  it("takes absolute value before signing", () => {
    // Even if caller passes negative, result matches magnitude
    assert.strictEqual(signedDollars(-10, "purchase"), -10);
    assert.strictEqual(signedDollars(-10, "refund"), 10);
  });
});

// ---------------------------------------------------------------------------
// formatSignedDollars
// ---------------------------------------------------------------------------
describe("formatSignedDollars", () => {
  it("zero → no sign", () => {
    assert.strictEqual(formatSignedDollars(0), "$0.00");
  });

  it("negative zero → no sign", () => {
    assert.strictEqual(formatSignedDollars(-0), "$0.00");
  });

  it("negative → U+2212 minus", () => {
    assert.strictEqual(formatSignedDollars(-25.62), "−$25.62");
  });

  it("positive → plus sign", () => {
    assert.strictEqual(formatSignedDollars(12.40), "+$12.40");
  });

  it("rounds to 2 decimals", () => {
    assert.strictEqual(formatSignedDollars(-0.001), "−$0.00");
    assert.strictEqual(formatSignedDollars(1.999), "+$2.00");
  });

  it("large value with thousands separator", () => {
    assert.strictEqual(formatSignedDollars(-1234.56), "−$1,234.56");
  });
});

// ---------------------------------------------------------------------------
// formatSignedDollarsFromMilliunits
// ---------------------------------------------------------------------------
describe("formatSignedDollarsFromMilliunits", () => {
  it("null → double dash", () => {
    assert.strictEqual(formatSignedDollarsFromMilliunits(null), "--");
  });

  it("positive milliunits → plus format (raw magnitude)", () => {
    assert.strictEqual(formatSignedDollarsFromMilliunits(25620), "+$25.62");
  });

  it("negative milliunits → minus format", () => {
    assert.strictEqual(formatSignedDollarsFromMilliunits(-25620), "−$25.62");
  });

  it("zero", () => {
    assert.strictEqual(formatSignedDollarsFromMilliunits(0), "$0.00");
  });
});

// ---------------------------------------------------------------------------
// formatSignedDollarsWithDirection
// ---------------------------------------------------------------------------
describe("formatSignedDollarsWithDirection", () => {
  it("purchase (negative) → outflow label", () => {
    assert.strictEqual(formatSignedDollarsWithDirection(-25.62, "purchase"), "−$25.62 (outflow)");
  });

  it("refund (positive) → inflow label", () => {
    assert.strictEqual(formatSignedDollarsWithDirection(12.40, "refund"), "+$12.40 (inflow)");
  });

  it("zero → no label appended", () => {
    assert.strictEqual(formatSignedDollarsWithDirection(0, "purchase"), "$0.00");
    assert.strictEqual(formatSignedDollarsWithDirection(0, "refund"), "$0.00");
  });
});

  it("large negative value with thousands separator", () => {
    assert.strictEqual(formatSignedDollarsWithDirection(-1234.56, "purchase"), "−$1,234.56 (outflow)");
  });

// ---------------------------------------------------------------------------
// formatDollarsMagnitude — plain magnitude (no sign prefix)
// ---------------------------------------------------------------------------
describe("formatDollarsMagnitude", () => {
  it("null → double dash", () => {
    assert.strictEqual(formatDollarsMagnitude(null), "--");
  });

  it("positive milliunits → $N.NN (no sign)", () => {
    assert.strictEqual(formatDollarsMagnitude(25620), "$25.62");
  });

  it("negative milliunits → absolute value", () => {
    assert.strictEqual(formatDollarsMagnitude(-25620), "$25.62");
  });

  it("zero → $0.00", () => {
    assert.strictEqual(formatDollarsMagnitude(0), "$0.00");
  });

  it("fractional cents round correctly", () => {
    assert.strictEqual(formatDollarsMagnitude(119190), "$119.19");
  });

  it("large value with thousands separator", () => {
    assert.strictEqual(formatDollarsMagnitude(1234560), "$1,234.56");
  });
});

// ---------------------------------------------------------------------------
// dollarsToMilliunits — ROUND_HALF_UP parity
// ---------------------------------------------------------------------------
describe("dollarsToMilliunits", () => {
  it("basic outflow", () => {
    assert.strictEqual(dollarsToMilliunits(25.62, true), -25620);
  });

  it("basic inflow", () => {
    assert.strictEqual(dollarsToMilliunits(12.40, false), 12400);
  });

  it("half-up: 25.625 → 25625", () => {
    assert.strictEqual(dollarsToMilliunits(25.625, false), 25625);
  });

  it("half-up: 0.0005 → 1 milliunit", () => {
    assert.strictEqual(dollarsToMilliunits(0.0005, false), 1);
  });

  it("half-up: 119.19 → 119190", () => {
    assert.strictEqual(dollarsToMilliunits(119.19, false), 119190);
  });

  it("zero → zero (both signs)", () => {
    assert.strictEqual(dollarsToMilliunits(0, true), -0);
    assert.strictEqual(dollarsToMilliunits(0, false), 0);
  });

  it("throws on negative input", () => {
    assert.throws(() => dollarsToMilliunits(-1, true), /negative input not allowed/);
    assert.throws(() => dollarsToMilliunits(-0.01, false), /negative input not allowed/);
  });

  it("rounds small fractional millis correctly", () => {
    // 0.001 dollars = exactly 1 milliunit
    assert.strictEqual(dollarsToMilliunits(0.001, false), 1);
  });

  // Python parity: Decimal("0.5005").quantize(0.001, ROUND_HALF_UP) → 0.501 → 501
  it("0.5005 → 501 milliunits (mirrors Python money.py ROUND_HALF_UP)", () => {
    assert.strictEqual(dollarsToMilliunits(0.5005, false), 501);
  });

  // Python parity: Decimal("0.4995").quantize(0.001, ROUND_HALF_UP) → 0.500 → 500
  // (4th digit 5 rounds up 3rd digit: 9 → 10 → carry; 0.4995 rounds to 0.500)
  it("0.4995 → 500 milliunits (4th digit=5 rounds up, carries: 0.4995 → 0.500)", () => {
    assert.strictEqual(dollarsToMilliunits(0.4995, false), 500);
  });
});

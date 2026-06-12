import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { shouldSkipPreview } from "./sync-skip.js";

// Shorthand: a single ambiguity flag for tests that need one
const FLAG = { line_item: "Widget", confidence: 0.9 };

describe("shouldSkipPreview", () => {
  // ----------------------------------------------------------------
  // skipEnabled = false → never skip
  // ----------------------------------------------------------------

  it("returns false when skipEnabled is false, even with no warnings", () => {
    assert.equal(
      shouldSkipPreview({ stripReasons: [], lockWarnings: [], ambiguityFlags: [], skipEnabled: false }),
      false,
    );
  });

  it("returns false when skipEnabled is false even if all other conditions are clear", () => {
    assert.equal(
      shouldSkipPreview({ stripReasons: [], lockWarnings: [], ambiguityFlags: [], skipEnabled: false }),
      false,
    );
  });

  // ----------------------------------------------------------------
  // All clear → skip
  // ----------------------------------------------------------------

  it("returns true when skipEnabled=true and no reasons/warnings/flags", () => {
    assert.equal(
      shouldSkipPreview({ stripReasons: [], lockWarnings: [], ambiguityFlags: [], skipEnabled: true }),
      true,
    );
  });

  // ----------------------------------------------------------------
  // stripReasons present → always show dialog
  // ----------------------------------------------------------------

  it("returns false when stripReasons is non-empty", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: ["Confirm Date + Time in Receipt Twin before syncing"],
        lockWarnings: [],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  it("returns false when multiple stripReasons are present", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: ["Twin unconfirmed", "Category required"],
        lockWarnings: [],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  // ----------------------------------------------------------------
  // lockWarnings present → always show dialog (FIX 1 core assertion)
  // ----------------------------------------------------------------

  it("returns false when lockWarnings is non-empty even with no other reasons", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: [],
        lockWarnings: ["Transaction date falls in a locked period"],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  it("returns false when lockWarnings has multiple entries", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: [],
        lockWarnings: ["Locked period warning 1", "Reconciled period warning 2"],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  // ----------------------------------------------------------------
  // ambiguityFlags present → always show dialog (FIX 1 ambiguity gate)
  // ----------------------------------------------------------------

  it("returns false when ambiguityFlags is non-empty", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: [],
        lockWarnings: [],
        ambiguityFlags: [FLAG],
        skipEnabled: true,
      }),
      false,
    );
  });

  it("returns false when multiple ambiguity flags are present", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: [],
        lockWarnings: [],
        ambiguityFlags: [FLAG, { line_item: "Gadget", confidence: 0.85 }],
        skipEnabled: true,
      }),
      false,
    );
  });

  // ----------------------------------------------------------------
  // Combinations: any single warning source overrides skip
  // ----------------------------------------------------------------

  it("returns false when only lockWarnings are present (stripReasons empty, no ambiguity)", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: [],
        lockWarnings: ["Reconciled period"],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  it("returns false when stripReasons AND lockWarnings both present", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: ["Twin unconfirmed"],
        lockWarnings: ["Locked period"],
        ambiguityFlags: [],
        skipEnabled: true,
      }),
      false,
    );
  });

  it("returns false when all three warning types are present", () => {
    assert.equal(
      shouldSkipPreview({
        stripReasons: ["Strip reason"],
        lockWarnings: ["Lock warning"],
        ambiguityFlags: [FLAG],
        skipEnabled: true,
      }),
      false,
    );
  });
});

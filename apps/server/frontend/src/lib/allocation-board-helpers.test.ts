import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { isSelectionFullyInLane } from "./allocation-board-helpers.js";

const assignments = [
  { item_id: "a", lane_id: "main" },
  { item_id: "b", lane_id: "main" },
  { item_id: "c", lane_id: "split-0" },
  { item_id: "d", lane_id: "unassigned" },
];

describe("isSelectionFullyInLane", () => {
  it("returns false when selection is empty", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(), "main", assignments), false);
  });

  it("returns true when all selected items are already in the target lane", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["a", "b"]), "main", assignments), true);
  });

  it("returns false when some selected items are in a different lane", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["a", "c"]), "main", assignments), false);
  });

  it("returns false when no selected item is in the target lane", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["c"]), "main", assignments), false);
  });

  it("returns true for single item already in target lane", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["c"]), "split-0", assignments), true);
  });

  it("returns false when item has no assignment", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["z"]), "main", assignments), false);
  });

  it("returns false when selection spans multiple lanes even if one matches", () => {
    assert.strictEqual(isSelectionFullyInLane(new Set(["a", "d"]), "main", assignments), false);
  });
});

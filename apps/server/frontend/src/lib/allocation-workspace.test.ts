import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  buildFallbackWorkspace,
  reconcileWorkspaceToDraft,
  workspaceFromApi,
  moveWorkspaceItems,
  setWorkspaceLanePinnedAmount,
  clearWorkspacePins,
  MAIN_LANE_ID,
  UNASSIGNED_LANE_ID,
} from "./allocation-workspace.js";
import type { ReceiptTwin, ValidationPayloadInput } from "./types.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeDraft(overrides: Partial<ValidationPayloadInput> = {}): ValidationPayloadInput {
  return {
    account_id: "acc-1",
    category_id: "cat-1",
    memo: "",
    transaction_date: "2026-06-12",
    transaction_time: null,
    total_amount: 10.0,
    transaction_kind: "purchase",
    payee_name: "Test Store",
    splits: [],
    ...overrides,
  };
}

function makeTwin(lineItems: ReceiptTwin["payload"]["line_items"] = []): ReceiptTwin {
  return {
    id: 1,
    receipt_id: "r-1",
    version: 1,
    source: "test",
    payload: {
      store_name: "Test Store",
      store_address: "",
      transaction_date: "2026-06-12",
      transaction_time: null,
      currency: "USD",
      line_items: lineItems,
      subtotal: null,
      tax_total: null,
      total_amount: 10.0,
      payment_method: "card",
      receipt_language: "en",
    },
    confirmed_sections: { date_time: true, total: true },
    created_at: "2026-06-12T00:00:00Z",
  };
}

// ---------------------------------------------------------------------------
// inferAllocatableItems — label priority: translated_text first, raw_text fallback
// ---------------------------------------------------------------------------

describe("inferAllocatableItems via buildFallbackWorkspace", () => {
  it("uses translated_text as label when both texts are present", () => {
    const twin = makeTwin([
      {
        index: 0,
        raw_text: "BIO MILCH 1L",
        translated_text: "Organic Milk 1L",
        quantity: 1,
        unit_price: 2.5,
        line_total: 2.5,
        tax_code: null,
        item_type: "product",
      },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const item = workspace.items[0];
    assert.ok(item, "item should exist");
    assert.strictEqual(item.label, "Organic Milk 1L");
    assert.strictEqual(item.translated_text, "Organic Milk 1L");
    assert.strictEqual(item.raw_text, "BIO MILCH 1L");
  });

  it("falls back to raw_text when translated_text is absent", () => {
    const twin = makeTwin([
      {
        index: 0,
        raw_text: "BIO MILCH 1L",
        translated_text: "",
        quantity: 1,
        unit_price: 2.5,
        line_total: 2.5,
        tax_code: null,
        item_type: "product",
      },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const item = workspace.items[0];
    assert.ok(item, "item should exist");
    assert.strictEqual(item.label, "BIO MILCH 1L");
    assert.strictEqual(item.translated_text, null);
    assert.strictEqual(item.raw_text, "BIO MILCH 1L");
  });

  it("uses placeholder when both texts are empty", () => {
    const twin = makeTwin([
      {
        index: 0,
        raw_text: "",
        translated_text: "",
        quantity: null,
        unit_price: null,
        line_total: 5.0,
        tax_code: null,
        item_type: "product",
      },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const item = workspace.items[0];
    assert.ok(item, "item should exist");
    assert.strictEqual(item.label, "Line 1");
    assert.strictEqual(item.translated_text, null);
    assert.strictEqual(item.raw_text, null);
  });

  it("stores raw_text even when translated is the same (no deduplication)", () => {
    const twin = makeTwin([
      {
        index: 0,
        raw_text: "MILK",
        translated_text: "MILK",
        quantity: null,
        unit_price: null,
        line_total: 2.0,
        tax_code: null,
        item_type: "product",
      },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const item = workspace.items[0];
    assert.ok(item, "item should exist");
    assert.strictEqual(item.label, "MILK");
    assert.strictEqual(item.translated_text, "MILK");
    assert.strictEqual(item.raw_text, "MILK");
  });

  it("excludes subtotal and total rows", () => {
    const twin = makeTwin([
      { index: 0, raw_text: "Item A", translated_text: "Item A", quantity: 1, unit_price: 5, line_total: 5, tax_code: null, item_type: "product" },
      { index: 1, raw_text: "SUBTOTAL", translated_text: "Subtotal", quantity: null, unit_price: null, line_total: 5, tax_code: null, item_type: "subtotal" },
      { index: 2, raw_text: "TOTAL", translated_text: "Total", quantity: null, unit_price: null, line_total: 5, tax_code: null, item_type: "total" },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    assert.strictEqual(workspace.items.length, 1);
    assert.strictEqual(workspace.items[0]?.label, "Item A");
  });
});

// ---------------------------------------------------------------------------
// buildFallbackWorkspace — lane/assignment structure
// ---------------------------------------------------------------------------

describe("buildFallbackWorkspace", () => {
  it("creates a main lane and unassigned lane for single-split draft", () => {
    const workspace = buildFallbackWorkspace(makeDraft(), null);
    const laneIds = workspace.lanes.map((l) => l.lane_id);
    assert.ok(laneIds.includes(MAIN_LANE_ID));
    assert.ok(laneIds.includes(UNASSIGNED_LANE_ID));
    assert.strictEqual(laneIds.length, 2);
  });

  it("creates split lanes for draft with splits", () => {
    const draft = makeDraft({
      splits: [
        { category_id: "cat-1", amount: 5, memo: "" },
        { category_id: "cat-2", amount: 5, memo: "" },
      ],
    });
    const workspace = buildFallbackWorkspace(draft, null);
    const laneIds = workspace.lanes.map((l) => l.lane_id);
    assert.ok(laneIds.includes("split-0"));
    assert.ok(laneIds.includes("split-1"));
    assert.ok(laneIds.includes(UNASSIGNED_LANE_ID));
    assert.strictEqual(laneIds.length, 3);
  });

  it("assigns items with amounts to first lane, unknown-amount items to unassigned", () => {
    const twin = makeTwin([
      { index: 0, raw_text: "A", translated_text: "A", quantity: 1, unit_price: 2, line_total: 2.0, tax_code: null, item_type: "product" },
      { index: 1, raw_text: "B", translated_text: "B", quantity: null, unit_price: null, line_total: null, tax_code: null, item_type: "product" },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const byItem = new Map(workspace.assignments.map((a) => [a.item_id, a.lane_id]));
    const itemA = workspace.items.find((i) => i.label === "A");
    const itemB = workspace.items.find((i) => i.label === "B");
    assert.ok(itemA);
    assert.ok(itemB);
    assert.strictEqual(byItem.get(itemA.item_id), MAIN_LANE_ID);
    assert.strictEqual(byItem.get(itemB.item_id), UNASSIGNED_LANE_ID);
  });
});

// ---------------------------------------------------------------------------
// moveWorkspaceItems
// ---------------------------------------------------------------------------

describe("moveWorkspaceItems", () => {
  it("moves specified items to the target lane", () => {
    const twin = makeTwin([
      { index: 0, raw_text: "A", translated_text: "A", quantity: 1, unit_price: 2, line_total: 2.0, tax_code: null, item_type: "product" },
      { index: 1, raw_text: "B", translated_text: "B", quantity: 1, unit_price: 3, line_total: 3.0, tax_code: null, item_type: "product" },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const itemA = workspace.items[0];
    assert.ok(itemA);
    const moved = moveWorkspaceItems(workspace, [itemA.item_id], UNASSIGNED_LANE_ID);
    const assignment = moved.assignments.find((a) => a.item_id === itemA.item_id);
    assert.strictEqual(assignment?.lane_id, UNASSIGNED_LANE_ID);
  });

  it("does not mutate the original workspace", () => {
    const twin = makeTwin([
      { index: 0, raw_text: "A", translated_text: "A", quantity: 1, unit_price: 2, line_total: 2.0, tax_code: null, item_type: "product" },
    ]);
    const workspace = buildFallbackWorkspace(makeDraft(), twin);
    const itemA = workspace.items[0];
    assert.ok(itemA);
    const original = workspace.assignments[0]?.lane_id;
    moveWorkspaceItems(workspace, [itemA.item_id], UNASSIGNED_LANE_ID);
    assert.strictEqual(workspace.assignments[0]?.lane_id, original);
  });
});

// ---------------------------------------------------------------------------
// setWorkspaceLanePinnedAmount / clearWorkspacePins
// ---------------------------------------------------------------------------

describe("setWorkspaceLanePinnedAmount", () => {
  it("sets pinned amount on a lane", () => {
    const workspace = buildFallbackWorkspace(makeDraft(), null);
    const next = setWorkspaceLanePinnedAmount(workspace, MAIN_LANE_ID, 12.5);
    const lane = next.lanes.find((l) => l.lane_id === MAIN_LANE_ID);
    assert.strictEqual(lane?.pinned_amount, 12.5);
  });

  it("rounds to two decimal places", () => {
    const workspace = buildFallbackWorkspace(makeDraft(), null);
    const next = setWorkspaceLanePinnedAmount(workspace, MAIN_LANE_ID, 10.006);
    const lane = next.lanes.find((l) => l.lane_id === MAIN_LANE_ID);
    assert.strictEqual(lane?.pinned_amount, 10.01);
  });

  it("clears pinned amount when null is passed", () => {
    const workspace = buildFallbackWorkspace(makeDraft(), null);
    const pinned = setWorkspaceLanePinnedAmount(workspace, MAIN_LANE_ID, 5);
    const cleared = setWorkspaceLanePinnedAmount(pinned, MAIN_LANE_ID, null);
    const lane = cleared.lanes.find((l) => l.lane_id === MAIN_LANE_ID);
    assert.strictEqual(lane?.pinned_amount, null);
  });
});

describe("clearWorkspacePins", () => {
  it("clears all pinned amounts", () => {
    const workspace = buildFallbackWorkspace(makeDraft(), null);
    const pinned = setWorkspaceLanePinnedAmount(workspace, MAIN_LANE_ID, 5);
    const cleared = clearWorkspacePins(pinned);
    assert.ok(cleared.lanes.every((l) => l.pinned_amount == null));
  });
});

// ---------------------------------------------------------------------------
// workspaceFromApi
// ---------------------------------------------------------------------------

describe("workspaceFromApi", () => {
  it("returns fallback workspace when value is not workspace-like", () => {
    const workspace = workspaceFromApi(null, makeDraft(), null);
    assert.ok(Array.isArray(workspace.items));
    assert.ok(Array.isArray(workspace.lanes));
    assert.ok(Array.isArray(workspace.assignments));
  });

  it("returns fallback workspace for invalid shape", () => {
    const workspace = workspaceFromApi({ foo: "bar" }, makeDraft(), null);
    assert.ok(Array.isArray(workspace.items));
  });

  it("reconciles a valid existing workspace", () => {
    const original = buildFallbackWorkspace(makeDraft(), null);
    const reconciled = workspaceFromApi(original, makeDraft(), null);
    assert.strictEqual(reconciled.items.length, original.items.length);
  });
});

// ---------------------------------------------------------------------------
// reconcileWorkspaceToDraft — twin version warning
// ---------------------------------------------------------------------------

describe("reconcileWorkspaceToDraft", () => {
  it("adds stale twin warning when twin version differs from stored version", () => {
    const original = buildFallbackWorkspace(makeDraft(), makeTwin());
    // Simulate twin version advancing
    const newerTwin = { ...makeTwin(), version: 99 };
    const reconciled = reconcileWorkspaceToDraft(original, makeDraft(), newerTwin);
    assert.ok(reconciled.warnings.some((w) => w.includes("Line items changed")));
  });

  it("no stale warning when versions match", () => {
    const twin = makeTwin();
    const original = buildFallbackWorkspace(makeDraft(), twin);
    const reconciled = reconcileWorkspaceToDraft(original, makeDraft(), twin);
    assert.ok(!reconciled.warnings.some((w) => w.includes("Line items changed")));
  });
});

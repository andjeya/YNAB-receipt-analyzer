import assert from "node:assert/strict";
import test from "node:test";

import { cloneTwinPayload, computeTwinEditWarnings, isRealLineItem, normalizeTwinTimeForInput } from "./receipt-twin";
import { ReceiptLineItem, ReceiptTwinPayload } from "./types";

function samplePayload(): ReceiptTwinPayload {
  return {
    store_name: "Store",
    store_address: "Address",
    transaction_date: "2026-02-15",
    transaction_time: "10:30:00",
    currency: "USD",
    line_items: [
      {
        index: 0,
        raw_text: "ITEM",
        translated_text: "ITEM",
        quantity: 2,
        unit_price: 5,
        line_total: 10,
        tax_code: null,
        item_type: "product",
      },
      {
        index: 1,
        raw_text: "TAX",
        translated_text: "TAX",
        quantity: null,
        unit_price: null,
        line_total: 1,
        tax_code: null,
        item_type: "tax",
      },
    ],
    subtotal: 10,
    tax_total: 1,
    total_amount: 11,
    payment_method: "card",
    receipt_language: "en",
  };
}

test("normalizeTwinTimeForInput trims seconds", () => {
  assert.equal(normalizeTwinTimeForInput("10:30:00"), "10:30");
  assert.equal(normalizeTwinTimeForInput(null), "");
});

test("cloneTwinPayload deep-copies line items", () => {
  const original = samplePayload();
  const cloned = cloneTwinPayload(original);

  cloned.line_items[0].raw_text = "CHANGED";
  assert.equal(original.line_items[0].raw_text, "ITEM");
});

test("computeTwinEditWarnings reports math mismatches", () => {
  const payload = samplePayload();
  payload.line_items[0].line_total = 9;
  payload.total_amount = 20;

  const warnings = computeTwinEditWarnings(payload);
  assert.equal(warnings.length, 2);
});

test("computeTwinEditWarnings returns empty for aligned totals", () => {
  const warnings = computeTwinEditWarnings(samplePayload());
  assert.deepEqual(warnings, []);
});

test("cloneTwinPayload preserves null quantity/unit_price/line_total (no 0-coercion)", () => {
  // Regression: Number(null) === 0, which used to turn missing quantities into
  // quantity 0 and trip false "quantity × unit price" warnings on discount/tax rows.
  const payload = samplePayload();
  payload.line_items = [
    {
      index: 1,
      raw_text: "SODSTRM CRBNTR RT",
      translated_text: "SodaStream Carbonator Return Credit",
      quantity: null,
      unit_price: 15,
      line_total: 15,
      tax_code: "B",
      item_type: "discount",
    },
  ];
  const cloned = cloneTwinPayload(payload);
  assert.equal(cloned.line_items[0].quantity, null);
  assert.equal(cloned.line_items[0].unit_price, 15);
  assert.equal(cloned.line_items[0].line_total, 15);
});

test("computeTwinEditWarnings: Kroger discount receipt produces no false warnings", () => {
  // Real-world shape: cylinder exchange with a positive-magnitude discount line.
  // 31.99 − 15.00 + 1.02 = 18.01 — internally consistent, must not warn.
  const payload = samplePayload();
  payload.line_items = cloneTwinPayload({
    ...payload,
    line_items: [
      { index: 1, raw_text: "SODSTRM CRBNTR RT", translated_text: "SodaStream Carbonator Return Credit", quantity: null, unit_price: 15, line_total: 15, tax_code: "B", item_type: "discount" },
      { index: 2, raw_text: "SODA CYLINDER", translated_text: "SodaStream Cylinder", quantity: 1, unit_price: 31.99, line_total: 31.99, tax_code: "T", item_type: "product" },
      { index: 3, raw_text: "TAX", translated_text: "Sales Tax", quantity: null, unit_price: 1.02, line_total: 1.02, tax_code: null, item_type: "tax" },
    ],
  }).line_items;
  payload.subtotal = 16.99;
  payload.tax_total = 1.02;
  payload.total_amount = 18.01;

  assert.deepEqual(computeTwinEditWarnings(payload), []);
});

test("computeTwinEditWarnings names the item instead of a line number", () => {
  const payload = samplePayload();
  payload.line_items[0].line_total = 9;
  payload.total_amount = 20;

  const warnings = computeTwinEditWarnings(payload);
  assert.equal(warnings.length, 2);
  assert.match(warnings[0], /"ITEM"/);
  assert.match(warnings[0], /2 × \$5\.00 is \$10\.00/);
  assert.match(warnings[1], /add up to \$10\.00/);
});

// --- isRealLineItem predicate tests ---

function makeItem(overrides: Partial<ReceiptLineItem>): ReceiptLineItem {
  return {
    index: 0,
    raw_text: "Item",
    translated_text: "Item",
    quantity: 1,
    unit_price: 5,
    line_total: 5,
    tax_code: null,
    item_type: "product",
    ...overrides,
  };
}

test("isRealLineItem: normal product rows are real", () => {
  assert.equal(isRealLineItem(makeItem({})), true);
});

test("isRealLineItem: subtotal rows are NOT real", () => {
  assert.equal(isRealLineItem(makeItem({ item_type: "subtotal", raw_text: "Subtotal" })), false);
});

test("isRealLineItem: total rows are NOT real", () => {
  assert.equal(isRealLineItem(makeItem({ item_type: "total", raw_text: "Total" })), false);
});

test("isRealLineItem: artifact row with no description, zero qty, zero amount is NOT real", () => {
  assert.equal(
    isRealLineItem(makeItem({ raw_text: "", translated_text: "", quantity: 0, unit_price: 0, line_total: 0 })),
    false,
  );
});

test("isRealLineItem: artifact row with no description and null qty and null amount is NOT real", () => {
  assert.equal(
    isRealLineItem(makeItem({ raw_text: "", translated_text: "", quantity: null, unit_price: null, line_total: null })),
    false,
  );
});

test("isRealLineItem: row with only a description is real (even if amount is null)", () => {
  assert.equal(
    isRealLineItem(makeItem({ raw_text: "MYSTERY ITEM", quantity: null, line_total: null })),
    true,
  );
});

test("isRealLineItem: discount row with description is real", () => {
  assert.equal(isRealLineItem(makeItem({ item_type: "discount", raw_text: "10% discount", line_total: -1 })), true);
});

test("isRealLineItem: tax row with description is real", () => {
  assert.equal(isRealLineItem(makeItem({ item_type: "tax", raw_text: "Sales Tax", line_total: 1.5 })), true);
});

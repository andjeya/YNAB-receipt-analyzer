import assert from "node:assert/strict";
import test from "node:test";

import { cloneTwinPayload, computeTwinEditWarnings, normalizeTwinTimeForInput } from "./receipt-twin";
import { ReceiptTwinPayload } from "./types";

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

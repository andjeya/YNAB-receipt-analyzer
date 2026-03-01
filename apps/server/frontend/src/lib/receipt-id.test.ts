import assert from "node:assert/strict";
import test from "node:test";

import { extractReceiptIdFromText } from "./receipt-id";

test("extractReceiptIdFromText returns UUID from raw ID", () => {
  const raw = "11111111-2222-4333-8444-555555555555";
  assert.equal(extractReceiptIdFromText(raw), raw);
});

test("extractReceiptIdFromText parses memo marker", () => {
  const raw = "Lunch [receipt_id:11111111-2222-4333-8444-555555555555]";
  assert.equal(extractReceiptIdFromText(raw), "11111111-2222-4333-8444-555555555555");
});

test("extractReceiptIdFromText returns null for invalid input", () => {
  assert.equal(extractReceiptIdFromText("not-a-receipt-id"), null);
});

/**
 * receipt-buckets.test.ts
 *
 * node:test suite for the pure bucketing + sorting logic.
 * Run via: npm run test:unit (tsx --test src/lib/*.test.ts)
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { bucketForStatus, isProcessingStatus, partitionReceipts } from "./receipt-buckets.js";
import type { BucketableReceipt } from "./receipt-buckets.js";

// ---------------------------------------------------------------------------
// bucketForStatus — maps each of the 8 known statuses
// ---------------------------------------------------------------------------

describe("bucketForStatus", () => {
  it("needs_review → review", () => {
    assert.strictEqual(bucketForStatus("needs_review"), "review");
  });

  it("duplicate_review → review", () => {
    assert.strictEqual(bucketForStatus("duplicate_review"), "review");
  });

  it("error_extract → review", () => {
    assert.strictEqual(bucketForStatus("error_extract"), "review");
  });

  it("error_sync → review", () => {
    assert.strictEqual(bucketForStatus("error_sync"), "review");
  });

  it("ingested → review (processing lives inside To Review)", () => {
    assert.strictEqual(bucketForStatus("ingested"), "review");
  });

  it("extracting → review (processing lives inside To Review)", () => {
    assert.strictEqual(bucketForStatus("extracting"), "review");
  });

  it("syncing → review (processing lives inside To Review)", () => {
    assert.strictEqual(bucketForStatus("syncing"), "review");
  });

  it("synced → done", () => {
    assert.strictEqual(bucketForStatus("synced"), "done");
  });

  it("unknown status → null (does not crash)", () => {
    assert.strictEqual(bucketForStatus("totally_unknown_status"), null);
    assert.strictEqual(bucketForStatus(""), null);
  });
});

// ---------------------------------------------------------------------------
// isProcessingStatus
// ---------------------------------------------------------------------------

describe("isProcessingStatus", () => {
  it("true for ingested / extracting / syncing", () => {
    assert.strictEqual(isProcessingStatus("ingested"), true);
    assert.strictEqual(isProcessingStatus("extracting"), true);
    assert.strictEqual(isProcessingStatus("syncing"), true);
  });

  it("false for actionable + done statuses", () => {
    assert.strictEqual(isProcessingStatus("needs_review"), false);
    assert.strictEqual(isProcessingStatus("duplicate_review"), false);
    assert.strictEqual(isProcessingStatus("error_extract"), false);
    assert.strictEqual(isProcessingStatus("error_sync"), false);
    assert.strictEqual(isProcessingStatus("synced"), false);
    assert.strictEqual(isProcessingStatus("ghost"), false);
  });
});

// ---------------------------------------------------------------------------
// partitionReceipts — correct bucketing
// ---------------------------------------------------------------------------

function makeReceipt(status: string, ingested_at: string): BucketableReceipt {
  return { status: status as BucketableReceipt["status"], ingested_at };
}

describe("partitionReceipts — bucketing", () => {
  it("routes each status to the right bucket", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review",    "2026-01-01T10:00:00Z"),
      makeReceipt("duplicate_review","2026-01-01T11:00:00Z"),
      makeReceipt("error_extract",   "2026-01-01T12:00:00Z"),
      makeReceipt("error_sync",      "2026-01-01T13:00:00Z"),
      makeReceipt("ingested",        "2026-01-02T10:00:00Z"),
      makeReceipt("extracting",      "2026-01-02T11:00:00Z"),
      makeReceipt("syncing",         "2026-01-02T12:00:00Z"),
      makeReceipt("synced",          "2026-01-03T10:00:00Z"),
    ];

    const { review, done } = partitionReceipts(receipts);

    assert.strictEqual(review.length, 7); // 4 actionable + 3 processing
    assert.strictEqual(done.length, 1);
  });

  it("excludes unknown statuses without crashing", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review", "2026-01-01T10:00:00Z"),
      makeReceipt("ghost_status", "2026-01-01T11:00:00Z"),
      makeReceipt("synced",       "2026-01-01T12:00:00Z"),
    ];

    const { review, done } = partitionReceipts(receipts);

    assert.strictEqual(review.length, 1);
    assert.strictEqual(done.length, 1);
  });

  it("handles empty array", () => {
    const { review, done } = partitionReceipts([]);
    assert.strictEqual(review.length, 0);
    assert.strictEqual(done.length, 0);
  });
});

// ---------------------------------------------------------------------------
// partitionReceipts — sort order
// ---------------------------------------------------------------------------

describe("partitionReceipts — sort order", () => {
  it("actionable receipts are sorted oldest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review", "2026-01-03T10:00:00Z"), // newest
      makeReceipt("needs_review", "2026-01-01T10:00:00Z"), // oldest
      makeReceipt("needs_review", "2026-01-02T10:00:00Z"), // middle
    ];

    const { review } = partitionReceipts(receipts);

    assert.strictEqual(review[0].ingested_at, "2026-01-01T10:00:00Z");
    assert.strictEqual(review[1].ingested_at, "2026-01-02T10:00:00Z");
    assert.strictEqual(review[2].ingested_at, "2026-01-03T10:00:00Z");
  });

  it("processing receipts always sink BELOW actionable receipts", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("extracting",   "2026-01-01T00:00:00Z"), // older than every actionable
      makeReceipt("needs_review", "2026-01-05T00:00:00Z"),
      makeReceipt("ingested",     "2026-01-02T00:00:00Z"),
      makeReceipt("error_sync",   "2026-01-06T00:00:00Z"),
    ];

    const { review } = partitionReceipts(receipts);

    assert.deepStrictEqual(
      review.map((r) => r.status),
      ["needs_review", "error_sync", "extracting", "ingested"],
    );
  });

  it("processing receipts are themselves sorted oldest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("extracting", "2026-01-05T00:00:00Z"),
      makeReceipt("ingested",   "2026-01-01T00:00:00Z"),
      makeReceipt("syncing",    "2026-01-03T00:00:00Z"),
    ];

    const { review } = partitionReceipts(receipts);

    assert.strictEqual(review[0].ingested_at, "2026-01-01T00:00:00Z");
    assert.strictEqual(review[1].ingested_at, "2026-01-03T00:00:00Z");
    assert.strictEqual(review[2].ingested_at, "2026-01-05T00:00:00Z");
  });

  it("done bucket is sorted newest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("synced", "2026-01-01T00:00:00Z"), // oldest
      makeReceipt("synced", "2026-01-10T00:00:00Z"), // newest
      makeReceipt("synced", "2026-01-05T00:00:00Z"), // middle
    ];

    const { done } = partitionReceipts(receipts);

    assert.strictEqual(done[0].ingested_at, "2026-01-10T00:00:00Z");
    assert.strictEqual(done[1].ingested_at, "2026-01-05T00:00:00Z");
    assert.strictEqual(done[2].ingested_at, "2026-01-01T00:00:00Z");
  });

  it("review sort is stable: error statuses also sorted oldest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("error_sync",    "2026-06-10T12:00:00Z"),
      makeReceipt("error_extract", "2026-06-10T09:00:00Z"),
      makeReceipt("needs_review",  "2026-06-10T11:00:00Z"),
    ];

    const { review } = partitionReceipts(receipts);

    assert.strictEqual(review[0].status, "error_extract"); // 09:00
    assert.strictEqual(review[1].status, "needs_review");  // 11:00
    assert.strictEqual(review[2].status, "error_sync");    // 12:00
  });
});

// ---------------------------------------------------------------------------
// partitionReceipts — count correctness
// ---------------------------------------------------------------------------

describe("partitionReceipts — counts", () => {
  it("total count preserved: review + done === inputs minus unknowns", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review",    "2026-01-01T00:00:00Z"),
      makeReceipt("synced",          "2026-01-02T00:00:00Z"),
      makeReceipt("extracting",      "2026-01-03T00:00:00Z"),
      makeReceipt("unknown_thing",   "2026-01-04T00:00:00Z"), // excluded
      makeReceipt("duplicate_review","2026-01-05T00:00:00Z"),
    ];

    const { review, done } = partitionReceipts(receipts);

    assert.strictEqual(review.length + done.length, 4); // 5 minus 1 unknown
  });
});

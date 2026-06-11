/**
 * receipt-buckets.test.ts
 *
 * node:test suite for the pure bucketing + sorting logic.
 * Run via: npm run test:unit (tsx --test src/lib/*.test.ts)
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { bucketForStatus, partitionReceipts } from "./receipt-buckets.js";
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

  it("ingested → processing", () => {
    assert.strictEqual(bucketForStatus("ingested"), "processing");
  });

  it("extracting → processing", () => {
    assert.strictEqual(bucketForStatus("extracting"), "processing");
  });

  it("syncing → processing", () => {
    assert.strictEqual(bucketForStatus("syncing"), "processing");
  });

  it("synced → history", () => {
    assert.strictEqual(bucketForStatus("synced"), "history");
  });

  it("unknown status → null (does not crash)", () => {
    assert.strictEqual(bucketForStatus("totally_unknown_status"), null);
    assert.strictEqual(bucketForStatus(""), null);
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

    const { review, processing, history } = partitionReceipts(receipts);

    assert.strictEqual(review.length, 4);
    assert.strictEqual(processing.length, 3);
    assert.strictEqual(history.length, 1);
  });

  it("excludes unknown statuses without crashing", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review", "2026-01-01T10:00:00Z"),
      makeReceipt("ghost_status", "2026-01-01T11:00:00Z"),
      makeReceipt("synced",       "2026-01-01T12:00:00Z"),
    ];

    const { review, processing, history } = partitionReceipts(receipts);

    assert.strictEqual(review.length, 1);
    assert.strictEqual(processing.length, 0);
    assert.strictEqual(history.length, 1);
  });

  it("handles empty array", () => {
    const { review, processing, history } = partitionReceipts([]);
    assert.strictEqual(review.length, 0);
    assert.strictEqual(processing.length, 0);
    assert.strictEqual(history.length, 0);
  });
});

// ---------------------------------------------------------------------------
// partitionReceipts — sort order
// ---------------------------------------------------------------------------

describe("partitionReceipts — sort order", () => {
  it("review bucket is sorted oldest-first", () => {
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

  it("processing bucket is sorted oldest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("extracting", "2026-01-05T00:00:00Z"),
      makeReceipt("ingested",   "2026-01-01T00:00:00Z"),
      makeReceipt("syncing",    "2026-01-03T00:00:00Z"),
    ];

    const { processing } = partitionReceipts(receipts);

    assert.strictEqual(processing[0].ingested_at, "2026-01-01T00:00:00Z");
    assert.strictEqual(processing[1].ingested_at, "2026-01-03T00:00:00Z");
    assert.strictEqual(processing[2].ingested_at, "2026-01-05T00:00:00Z");
  });

  it("history bucket is sorted newest-first", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("synced", "2026-01-01T00:00:00Z"), // oldest
      makeReceipt("synced", "2026-01-10T00:00:00Z"), // newest
      makeReceipt("synced", "2026-01-05T00:00:00Z"), // middle
    ];

    const { history } = partitionReceipts(receipts);

    assert.strictEqual(history[0].ingested_at, "2026-01-10T00:00:00Z");
    assert.strictEqual(history[1].ingested_at, "2026-01-05T00:00:00Z");
    assert.strictEqual(history[2].ingested_at, "2026-01-01T00:00:00Z");
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
  it("total count preserved: review + processing + history === inputs minus unknowns", () => {
    const receipts: BucketableReceipt[] = [
      makeReceipt("needs_review",    "2026-01-01T00:00:00Z"),
      makeReceipt("synced",          "2026-01-02T00:00:00Z"),
      makeReceipt("extracting",      "2026-01-03T00:00:00Z"),
      makeReceipt("unknown_thing",   "2026-01-04T00:00:00Z"), // excluded
      makeReceipt("duplicate_review","2026-01-05T00:00:00Z"),
    ];

    const { review, processing, history } = partitionReceipts(receipts);

    assert.strictEqual(review.length + processing.length + history.length, 4); // 5 minus 1 unknown
  });
});

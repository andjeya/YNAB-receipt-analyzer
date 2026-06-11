/**
 * receipt-buckets.ts
 *
 * Pure client-side logic for bucketing and sorting receipts into the three
 * primary queue tabs: "review", "processing", and "history".
 *
 * No React / API imports — keeps this fully testable with node:test.
 *
 * NOTE: The "history" bucket (synced receipts) is rendered newest-first and
 * could be paginated at scale (e.g. cursor-based pagination on ingested_at).
 * For now counts are small so we load all receipts in a single fetch.
 */

import type { ReceiptStatus } from "./types.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReceiptBucket = "review" | "processing" | "history";

/** Minimal receipt shape required for bucketing + sorting. */
export interface BucketableReceipt {
  status: ReceiptStatus;
  ingested_at: string; // ISO-8601
}

export interface PartitionResult<T extends BucketableReceipt> {
  review: T[];
  processing: T[];
  history: T[];
}

// ---------------------------------------------------------------------------
// Status → bucket mapping
// ---------------------------------------------------------------------------

/**
 * Returns the bucket for a given ReceiptStatus, or null for unknown values.
 *
 * Bucket membership:
 *   review     — needs_review, duplicate_review, error_extract, error_sync
 *   processing — ingested, extracting, syncing
 *   history    — synced
 */
export function bucketForStatus(status: string): ReceiptBucket | null {
  switch (status) {
    case "needs_review":
    case "duplicate_review":
    case "error_extract":
    case "error_sync":
      return "review";

    case "ingested":
    case "extracting":
    case "syncing":
      return "processing";

    case "synced":
      return "history";

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Partition + sort
// ---------------------------------------------------------------------------

/**
 * Partitions a flat array of receipts into the three buckets and sorts each
 * bucket by the fixed sort rule for that bucket:
 *
 *   review     → oldest-first by ingested_at (longest-waiting on top)
 *   processing → oldest-first by ingested_at
 *   history    → newest-first by ingested_at
 *
 * Receipts with an unknown status are silently excluded (bucket === null).
 */
export function partitionReceipts<T extends BucketableReceipt>(
  receipts: T[],
): PartitionResult<T> {
  const review: T[] = [];
  const processing: T[] = [];
  const history: T[] = [];

  for (const receipt of receipts) {
    const bucket = bucketForStatus(receipt.status);
    if (bucket === "review") review.push(receipt);
    else if (bucket === "processing") processing.push(receipt);
    else if (bucket === "history") history.push(receipt);
    // null → excluded
  }

  const oldestFirst = (a: T, b: T): number =>
    new Date(a.ingested_at).getTime() - new Date(b.ingested_at).getTime();

  const newestFirst = (a: T, b: T): number =>
    new Date(b.ingested_at).getTime() - new Date(a.ingested_at).getTime();

  review.sort(oldestFirst);
  processing.sort(oldestFirst);
  history.sort(newestFirst);

  return { review, processing, history };
}

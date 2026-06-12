/**
 * receipt-buckets.ts
 *
 * Pure client-side logic for bucketing and sorting receipts into the two
 * primary queue tabs: "review" (To Review) and "done" (synced receipts).
 *
 * Processing receipts (ingested / extracting / syncing) live INSIDE the
 * To Review tab: they are sorted below the actionable receipts and rendered
 * with a "Processing" label, so there is no separate tab to check.
 *
 * No React / API imports — keeps this fully testable with node:test.
 *
 * NOTE: The "done" bucket (synced receipts) is rendered newest-first and
 * could be paginated at scale (e.g. cursor-based pagination on ingested_at).
 * For now counts are small so we load all receipts in a single fetch.
 */

import type { ReceiptStatus } from "./types.js";
import { parseApiDate } from "./dates";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReceiptBucket = "review" | "done";

/** Minimal receipt shape required for bucketing + sorting. */
export interface BucketableReceipt {
  status: ReceiptStatus;
  ingested_at: string; // ISO-8601
}

export interface PartitionResult<T extends BucketableReceipt> {
  review: T[];
  done: T[];
}

// ---------------------------------------------------------------------------
// Status classification
// ---------------------------------------------------------------------------

/**
 * Statuses that mean the app (not the user) is currently working on the
 * receipt. These render inside To Review with a "Processing" label, below
 * everything the user can act on.
 */
export function isProcessingStatus(status: string): boolean {
  return status === "ingested" || status === "extracting" || status === "syncing";
}

/**
 * Returns the bucket for a given ReceiptStatus, or null for unknown values.
 *
 * Bucket membership:
 *   review — needs_review, duplicate_review, error_extract, error_sync,
 *            plus the processing statuses (ingested, extracting, syncing)
 *   done   — synced
 */
export function bucketForStatus(status: string): ReceiptBucket | null {
  switch (status) {
    case "needs_review":
    case "duplicate_review":
    case "error_extract":
    case "error_sync":
    case "ingested":
    case "extracting":
    case "syncing":
      return "review";

    case "synced":
      return "done";

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Partition + sort
// ---------------------------------------------------------------------------

/**
 * Partitions a flat array of receipts into the two buckets and sorts each
 * bucket by the fixed sort rule for that bucket:
 *
 *   review → actionable receipts oldest-first (longest-waiting on top),
 *            then processing receipts oldest-first at the bottom
 *   done   → newest-first by ingested_at
 *
 * Receipts with an unknown status are silently excluded (bucket === null).
 */
export function partitionReceipts<T extends BucketableReceipt>(
  receipts: T[],
): PartitionResult<T> {
  const actionable: T[] = [];
  const processing: T[] = [];
  const done: T[] = [];

  for (const receipt of receipts) {
    const bucket = bucketForStatus(receipt.status);
    if (bucket === "review") {
      if (isProcessingStatus(receipt.status)) processing.push(receipt);
      else actionable.push(receipt);
    } else if (bucket === "done") {
      done.push(receipt);
    }
    // null → excluded
  }

  const oldestFirst = (a: T, b: T): number =>
    parseApiDate(a.ingested_at).getTime() - parseApiDate(b.ingested_at).getTime();

  const newestFirst = (a: T, b: T): number =>
    parseApiDate(b.ingested_at).getTime() - parseApiDate(a.ingested_at).getTime();

  actionable.sort(oldestFirst);
  processing.sort(oldestFirst);
  done.sort(newestFirst);

  return { review: [...actionable, ...processing], done };
}

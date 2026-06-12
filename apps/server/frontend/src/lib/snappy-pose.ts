import type { SnappyPose } from "@/components/snappy/snappy";

export interface SnappyPoseResult {
  pose: SnappyPose;
  line: string;
}

/**
 * Derive the Snappy mascot pose and greeting from the current receipt queue state.
 *
 * Rules (in priority order):
 * - totalCount === 0  → asleep, "All caught up!"
 * - needsReviewCount > 0 → concerned, "<n> receipt(s) need your eyes"
 * - else → idle, "Welcome back!"
 */
export function deriveSnappyPose({
  needsReviewCount,
  totalCount,
}: {
  needsReviewCount: number;
  totalCount: number;
}): SnappyPoseResult {
  if (totalCount === 0) {
    return { pose: "asleep", line: "All caught up!" };
  }
  if (needsReviewCount > 0) {
    const noun = needsReviewCount === 1 ? "receipt" : "receipts";
    return {
      pose: "concerned",
      line: `${needsReviewCount} ${noun} need${needsReviewCount === 1 ? "s" : ""} your attention`,
    };
  }
  return { pose: "idle", line: "Welcome back!" };
}

/**
 * Returns true when the streak is a positive multiple of threshold.
 * Used to fire the streak-milestone celebration.
 */
export function isStreakMilestone(streak: number, threshold: number): boolean {
  return streak > 0 && threshold > 0 && streak % threshold === 0;
}

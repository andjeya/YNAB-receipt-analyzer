import type { SnappyPose } from "@/components/snappy/snappy";
import { SNAPPY_QUOTES } from "./snappy-quotes";

export interface SnappyPoseResult {
  pose: SnappyPose;
  line: string;
  /** Set when `line` is a historical quote — e.g. "Benjamin Franklin". */
  attribution?: string;
  /** Source work for the quote, for a tooltip (e.g. "The Way to Wealth, 1758"). */
  attributionSource?: string;
}

export interface SnappyPoseInput {
  needsReviewCount: number;
  totalCount: number;
  /** Display name for greetings; set in the debug panel. */
  userName?: string;
  /** Injectable clock for tests (defaults to the user's local time). */
  now?: Date;
  /** Injectable RNG for tests (defaults to Math.random). */
  random?: () => number;
}

/**
 * Time-of-day greeting variants. Uses the browser's local clock, so "late
 * night" is the user's late night regardless of timezone.
 */
export function greetingsForHour(hour: number, name: string): string[] {
  if (hour >= 5 && hour < 12) {
    return [`Good morning, ${name}!`, `Morning, ${name}!`, `Rise and shine, ${name}!`];
  }
  if (hour >= 12 && hour < 17) {
    return [`Good afternoon, ${name}!`, `Welcome back, ${name}!`, `${name} returns!`];
  }
  if (hour >= 17 && hour < 22) {
    return [`Good evening, ${name}!`, `Welcome back, ${name}!`, `${name} returns!`];
  }
  return [`It's a late night, ${name}!`, `Up late, ${name}?`, `Burning the midnight oil, ${name}?`];
}

function pick<T>(items: T[], random: () => number): T {
  const index = Math.min(Math.floor(random() * items.length), items.length - 1);
  return items[index];
}

/** Chance that a calm Snappy offers a verified historical quote instead of a greeting. */
const QUOTE_CHANCE = 0.4;

/**
 * Derive the Snappy mascot pose and speech-bubble line from the current
 * receipt queue state.
 *
 * Rules (in priority order):
 * - totalCount === 0  → asleep; "All caught up!" or a quote
 * - needsReviewCount > 0 → concerned; "<n> receipt(s) need your attention"
 *   (always informative — no quotes when there's work to do)
 * - else → idle; a time-of-day greeting by name, or a quote
 *
 * Callers should memoize the result (the line is randomized, so recomputing
 * on every poll would make the bubble flicker).
 */
export function deriveSnappyPose({
  needsReviewCount,
  totalCount,
  userName = "Anna",
  now = new Date(),
  random = Math.random,
}: SnappyPoseInput): SnappyPoseResult {
  const name = userName.trim() || "Anna";

  if (totalCount === 0) {
    if (random() < QUOTE_CHANCE) {
      const quote = pick(SNAPPY_QUOTES, random);
      return { pose: "asleep", line: quote.text, attribution: quote.author, attributionSource: quote.source };
    }
    return { pose: "asleep", line: "All caught up!" };
  }

  if (needsReviewCount > 0) {
    const noun = needsReviewCount === 1 ? "receipt" : "receipts";
    return {
      pose: "concerned",
      line: `${needsReviewCount} ${noun} need${needsReviewCount === 1 ? "s" : ""} your attention`,
    };
  }

  if (random() < QUOTE_CHANCE) {
    const quote = pick(SNAPPY_QUOTES, random);
    return { pose: "idle", line: quote.text, attribution: quote.author, attributionSource: quote.source };
  }
  return { pose: "idle", line: pick(greetingsForHour(now.getHours(), name), random) };
}

/**
 * Returns true when the streak is a positive multiple of threshold.
 * Used to fire the streak-milestone celebration.
 */
export function isStreakMilestone(streak: number, threshold: number): boolean {
  return streak > 0 && threshold > 0 && streak % threshold === 0;
}

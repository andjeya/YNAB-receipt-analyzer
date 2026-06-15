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
  /**
   * Number of active flames across all weeks. When > 0 (and not celebrating),
   * Snappy switches to "concerned" pose with a fire-extinguish nudge.
   */
  activeFlames?: number;
  /** Whether the user holds any water to extinguish a flame. */
  hasWater?: boolean;
  /** Label of the single flaming week (e.g. "Jun 7"); null when 0 or >1 weeks. */
  flameWeekLabel?: string | null;
  /** How many distinct weeks currently carry flames. */
  flameWeekCount?: number;
}

/**
 * Compose the fire-nudge speech line. Names the flaming week when there's
 * exactly one, falls back to a week count for several, and switches the
 * call-to-action on whether the user has water to spend.
 */
export function fireNudgeLine({
  activeFlames,
  hasWater,
  flameWeekLabel,
  flameWeekCount,
}: {
  activeFlames: number;
  hasWater: boolean;
  flameWeekLabel: string | null;
  flameWeekCount: number;
}): string {
  const subject = activeFlames === 1 ? "A fire started" : "Fires started";
  const where =
    flameWeekCount === 1 && flameWeekLabel
      ? ` in the week of ${flameWeekLabel}`
      : flameWeekCount > 1
        ? ` across ${flameWeekCount} weeks`
        : "";
  const cause =
    activeFlames === 1
      ? " after a receipt was corrected in YNAB."
      : " after receipts were corrected in YNAB.";
  const action = hasWater
    ? ` Tap the flame on your trail to extinguish ${activeFlames === 1 ? "it" : "them"}.`
    : " Collect water by catching Snappy's mistakes during review, then extinguish.";
  return `${subject}${where}${cause}${action}`;
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
  activeFlames = 0,
  hasWater = false,
  flameWeekLabel = null,
  flameWeekCount = 0,
}: SnappyPoseInput): SnappyPoseResult {
  const name = userName.trim() || "Anna";
  const fireLine = () => fireNudgeLine({ activeFlames, hasWater, flameWeekLabel, flameWeekCount });

  if (totalCount === 0) {
    // activeFlames nudge still applies even when queue is empty
    if (activeFlames > 0) {
      return { pose: "concerned", line: fireLine() };
    }
    if (random() < QUOTE_CHANCE) {
      const quote = pick(SNAPPY_QUOTES, random);
      return { pose: "asleep", line: quote.text, attribution: quote.author, attributionSource: quote.source };
    }
    return { pose: "asleep", line: "All caught up!" };
  }

  if (needsReviewCount > 0) {
    // Active flames take priority over generic "needs attention" when both apply
    if (activeFlames > 0) {
      return { pose: "concerned", line: fireLine() };
    }
    const noun = needsReviewCount === 1 ? "receipt" : "receipts";
    return {
      pose: "concerned",
      line: `${needsReviewCount} ${noun} need${needsReviewCount === 1 ? "s" : ""} your attention`,
    };
  }

  // Queue all reviewed — fire nudge takes priority over greetings/quotes
  if (activeFlames > 0) {
    return { pose: "concerned", line: fireLine() };
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

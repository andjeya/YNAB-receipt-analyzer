import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { deriveSnappyPose, fireNudgeLine, greetingsForHour, isStreakMilestone } from "./snappy-pose.js";
import { SNAPPY_QUOTES } from "./snappy-quotes";

// Deterministic RNG helpers
const never = () => 0.99; // above QUOTE_CHANCE → greeting branch, picks last variant
const always = () => 0.0; // below QUOTE_CHANCE → quote branch, picks first quote

const NOON = new Date("2026-06-12T12:00:00");

describe("deriveSnappyPose", () => {
  it("returns asleep when totalCount is 0", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 0, random: never });
    assert.equal(result.pose, "asleep");
    assert.equal(result.line, "All caught up!");
    assert.equal(result.attribution, undefined);
  });

  it("returns asleep even if needsReviewCount is non-zero but totalCount is 0", () => {
    // totalCount===0 takes highest priority
    const result = deriveSnappyPose({ needsReviewCount: 5, totalCount: 0, random: never });
    assert.equal(result.pose, "asleep");
  });

  it("asleep can offer a quote with attribution", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 0, random: always });
    assert.equal(result.pose, "asleep");
    assert.equal(result.line, SNAPPY_QUOTES[0].text);
    assert.equal(result.attribution, SNAPPY_QUOTES[0].author);
    assert.equal(result.attributionSource, SNAPPY_QUOTES[0].source);
  });

  it("returns concerned with singular when needsReviewCount is 1", () => {
    const result = deriveSnappyPose({ needsReviewCount: 1, totalCount: 3, random: always });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, "1 receipt needs your attention");
    assert.equal(result.attribution, undefined); // never a quote when there's work
  });

  it("returns concerned with plural when needsReviewCount > 1", () => {
    const result = deriveSnappyPose({ needsReviewCount: 4, totalCount: 7 });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, "4 receipts need your attention");
  });

  it("idle greets by name with a time-of-day line", () => {
    const result = deriveSnappyPose({
      needsReviewCount: 0, totalCount: 5, userName: "Anna", now: NOON, random: never,
    });
    assert.equal(result.pose, "idle");
    assert.equal(result.line, "Anna returns!"); // last of the afternoon variants
  });

  it("idle can offer a quote with attribution", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 5, random: always });
    assert.equal(result.pose, "idle");
    assert.equal(result.line, SNAPPY_QUOTES[0].text);
    assert.equal(result.attribution, SNAPPY_QUOTES[0].author);
  });

  it("defaults the name to Anna when blank", () => {
    const result = deriveSnappyPose({
      needsReviewCount: 0, totalCount: 5, userName: "   ", now: NOON, random: never,
    });
    assert.ok(result.line.includes("Anna"));
  });

  it("uses the provided name", () => {
    const result = deriveSnappyPose({
      needsReviewCount: 0, totalCount: 5, userName: "Andjey", now: NOON, random: never,
    });
    assert.ok(result.line.includes("Andjey"));
  });
});

describe("greetingsForHour", () => {
  const buckets: Array<[number, string]> = [
    [5, "morning"], [11, "morning"],
    [12, "afternoon"], [16, "afternoon"],
    [17, "evening"], [21, "evening"],
    [22, "late"], [2, "late"], [4, "late"],
  ];

  for (const [hour, label] of buckets) {
    it(`hour ${hour} → ${label} bucket`, () => {
      const lines = greetingsForHour(hour, "Anna");
      assert.ok(lines.length >= 2);
      if (label === "morning") assert.ok(lines[0].includes("morning"));
      if (label === "afternoon") assert.ok(lines[0].includes("afternoon"));
      if (label === "evening") assert.ok(lines[0].includes("evening"));
      if (label === "late") assert.ok(lines[0].includes("late night"));
      for (const line of lines) assert.ok(line.includes("Anna"));
    });
  }
});

describe("SNAPPY_QUOTES integrity", () => {
  it("every quote has text, author, and source", () => {
    assert.ok(SNAPPY_QUOTES.length >= 20, "wants plenty of variety");
    for (const q of SNAPPY_QUOTES) {
      assert.ok(q.text.trim().length > 0);
      assert.ok(q.author.trim().length > 0);
      assert.ok(q.source.trim().length > 0);
    }
  });

  it("quotes are short enough for the speech bubble", () => {
    for (const q of SNAPPY_QUOTES) {
      assert.ok(q.text.length <= 110, `too long for the bubble: ${q.text}`);
    }
  });
});

describe("fireNudgeLine", () => {
  it("names the single flaming week, singular cause, water → tap to extinguish it", () => {
    const line = fireNudgeLine({ activeFlames: 1, hasWater: true, flameWeekLabel: "Jun 7", flameWeekCount: 1 });
    assert.equal(
      line,
      "A fire started in the week of Jun 7 after a receipt was corrected in YNAB. Tap the flame on your trail to extinguish it.",
    );
  });

  it("pluralizes across multiple weeks, plural cause, water → extinguish them", () => {
    const line = fireNudgeLine({ activeFlames: 3, hasWater: true, flameWeekLabel: null, flameWeekCount: 2 });
    assert.equal(
      line,
      "Fires started across 2 weeks after receipts were corrected in YNAB. Tap the flame on your trail to extinguish them.",
    );
  });

  it("switches the call to action to collect-water when out of water", () => {
    const line = fireNudgeLine({ activeFlames: 1, hasWater: false, flameWeekLabel: "Jun 7", flameWeekCount: 1 });
    assert.equal(
      line,
      "A fire started in the week of Jun 7 after a receipt was corrected in YNAB. Collect water by catching Snappy's mistakes during review, then extinguish.",
    );
  });

  it("omits the location when the flaming week is unknown", () => {
    const line = fireNudgeLine({ activeFlames: 1, hasWater: true, flameWeekLabel: null, flameWeekCount: 0 });
    assert.equal(
      line,
      "A fire started after a receipt was corrected in YNAB. Tap the flame on your trail to extinguish it.",
    );
  });
});

describe("deriveSnappyPose — activeFlames", () => {
  // The computed fire line (no-water default, no known week).
  const fireFor = (activeFlames: number) =>
    fireNudgeLine({ activeFlames, hasWater: false, flameWeekLabel: null, flameWeekCount: 0 });

  it("concerned + fire line when activeFlames>0 and queue empty", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 0, activeFlames: 1, random: never });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, fireFor(1));
    assert.equal(result.attribution, undefined);
  });

  it("threads week + water context into the fire line", () => {
    const result = deriveSnappyPose({
      needsReviewCount: 0, totalCount: 0, activeFlames: 1,
      hasWater: true, flameWeekLabel: "Jun 7", flameWeekCount: 1, random: never,
    });
    assert.equal(result.pose, "concerned");
    assert.equal(
      result.line,
      "A fire started in the week of Jun 7 after a receipt was corrected in YNAB. Tap the flame on your trail to extinguish it.",
    );
  });

  it("concerned + fire line when activeFlames>0 and needsReviewCount>0", () => {
    const result = deriveSnappyPose({ needsReviewCount: 2, totalCount: 3, activeFlames: 2, random: never });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, fireFor(2));
  });

  it("concerned + fire line when activeFlames>0 and queue all reviewed", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 5, activeFlames: 3, random: never });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, fireFor(3));
  });

  it("does NOT show fire line when activeFlames is 0", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 5, activeFlames: 0, random: never, now: NOON, userName: "Anna" });
    assert.notEqual(result.line, fireFor(0));
    assert.equal(result.pose, "idle");
  });

  it("does NOT show fire line when activeFlames is undefined (default)", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 5, random: never, now: NOON, userName: "Anna" });
    assert.notEqual(result.line, fireFor(0));
  });
});

describe("isStreakMilestone", () => {
  it("returns false when streak is 0", () => {
    assert.equal(isStreakMilestone(0, 5), false);
  });

  it("returns true when streak is a positive multiple of threshold", () => {
    assert.equal(isStreakMilestone(5, 5), true);
    assert.equal(isStreakMilestone(10, 5), true);
    assert.equal(isStreakMilestone(3, 3), true);
  });

  it("returns false when streak is not a multiple of threshold", () => {
    assert.equal(isStreakMilestone(4, 5), false);
    assert.equal(isStreakMilestone(7, 5), false);
  });

  it("returns false when threshold is 0 (guard)", () => {
    assert.equal(isStreakMilestone(5, 0), false);
  });

  it("returns false for negative streak", () => {
    assert.equal(isStreakMilestone(-5, 5), false);
  });
});

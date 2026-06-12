import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { deriveSnappyPose, isStreakMilestone } from "./snappy-pose.js";

describe("deriveSnappyPose", () => {
  it("returns asleep when totalCount is 0", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 0 });
    assert.equal(result.pose, "asleep");
    assert.equal(result.line, "All caught up!");
  });

  it("returns asleep even if needsReviewCount is non-zero but totalCount is 0", () => {
    // totalCount===0 takes highest priority
    const result = deriveSnappyPose({ needsReviewCount: 5, totalCount: 0 });
    assert.equal(result.pose, "asleep");
  });

  it("returns concerned with singular when needsReviewCount is 1", () => {
    const result = deriveSnappyPose({ needsReviewCount: 1, totalCount: 3 });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, "1 receipt needs your attention");
  });

  it("returns concerned with plural when needsReviewCount > 1", () => {
    const result = deriveSnappyPose({ needsReviewCount: 4, totalCount: 7 });
    assert.equal(result.pose, "concerned");
    assert.equal(result.line, "4 receipts need your attention");
  });

  it("returns idle when totalCount > 0 and needsReviewCount is 0", () => {
    const result = deriveSnappyPose({ needsReviewCount: 0, totalCount: 5 });
    assert.equal(result.pose, "idle");
    assert.equal(result.line, "Welcome back!");
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

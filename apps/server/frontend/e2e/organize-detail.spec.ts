/**
 * Type-to-organize (detail page) test.
 *
 * Typing an instruction returns transient proposals; "Apply" writes them into the
 * draft and clears the proposal list. The Gemini call is mocked at the API layer.
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  CATEGORY_ID,
  RECEIPT_SYNC_READY,
  CONFIG_DRY_RUN,
  buildStandardRouter,
  mountApiMocks,
  jsonOk,
  type MockRouter,
} from "./fixtures";

test("type-to-organize returns proposals and Apply writes them into the draft", async ({ page }) => {
  const organizePosts: string[] = [];
  const standard = buildStandardRouter({
    receiptId: RECEIPT_ID,
    receiptFixture: RECEIPT_SYNC_READY,
    config: CONFIG_DRY_RUN,
    syncPosts: [],
  });

  const router: MockRouter = (pathname, method, route) => {
    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/allocation/organize`) {
      organizePosts.push(pathname);
      void jsonOk(route, {
        proposals: [
          { label: "Gifts", rationale: "party supplies are gifts", confidence: 0.7, category_id: CATEGORY_ID, splits: [], provenance: "user_instruction" },
        ],
      });
      return true;
    }
    return standard(pathname, method, route);
  };

  await mountApiMocks(page, router);
  await page.goto(`/receipts/${RECEIPT_ID}`);

  await expect(page.getByText("Reorganize with a sentence")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("organize-input").fill("party supplies to gifts");
  await page.getByTestId("organize-submit").click();

  await expect(page.getByTestId("organize-proposals")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("organize-apply-0")).toBeVisible();

  await page.getByTestId("organize-apply-0").click();

  // Proposals clear after applying; exactly one organize call was made.
  await expect(page.getByTestId("organize-proposals")).not.toBeVisible();
  expect(organizePosts).toHaveLength(1);
});

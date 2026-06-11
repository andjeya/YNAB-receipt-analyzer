/**
 * A11y smoke tests (M6 Increment 2)
 *
 * Verifies that:
 * 1. The sync-preview-dialog (already migrated to Dialog primitive) has the
 *    correct role="dialog" and aria-modal="true" attributes.
 * 2. The Button component carries the expected focus-visible ring classes in
 *    its class attribute (static class check via DOM inspection).
 *
 * Note: WaterSpend/Debug/Incident modals require specific game-state data that
 * the mock API does not surface, so those modals are not exercised here.
 * Arrow-key combobox navigation is out of scope (tracked as a follow-up).
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  RECEIPT_SYNC_READY,
  CONFIG_DRY_RUN,
  mountApiMocks,
  buildStandardRouter,
} from "./fixtures";

const DETAIL_URL = `/receipts/${RECEIPT_ID}`;

async function gotoDetail(page: import("@playwright/test").Page) {
  await page.goto(DETAIL_URL);
  await expect(page.getByText("Trader Joe's").first()).toBeVisible({ timeout: 15_000 });
}

// ---------------------------------------------------------------------------
// Test 1 — sync-preview-dialog has role="dialog" and aria-modal="true"
// ---------------------------------------------------------------------------

test("sync-preview-dialog has role=dialog and aria-modal=true", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNC_READY,
      config: CONFIG_DRY_RUN,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeEnabled({ timeout: 10_000 });
  await syncButton.click();

  const dialog = page.getByTestId("sync-preview-dialog");
  await expect(dialog).toBeVisible();

  // Dialog primitive must set role="dialog"
  await expect(dialog).toHaveAttribute("role", "dialog");
  // Dialog primitive must set aria-modal="true"
  await expect(dialog).toHaveAttribute("aria-modal", "true");
  // Dialog must be labelled by the heading id
  await expect(dialog).toHaveAttribute("aria-labelledby", "sync-preview-heading");
});

// ---------------------------------------------------------------------------
// Test 2 — Button component carries focus-visible ring in base class
// ---------------------------------------------------------------------------

test("Button component includes focus-visible ring classes", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNC_READY,
      config: CONFIG_DRY_RUN,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  // The sync-button is a Button component — it must carry the focus-visible classes
  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeVisible();
  const className = await syncButton.getAttribute("class");
  expect(className).toContain("focus-visible:ring-2");
  expect(className).toContain("focus-visible:ring-mint");
});

/**
 * Refund sync-preview suite (plan T1-01, T1-02; report UI-02)
 *
 * The reports flagged refund preview as the top missing frontend fixture. These
 * prove the sync preview renders a refund as an INFLOW (positive amount with an
 * "(inflow)" label) and applies the "Return: " memo prefix exactly the way the
 * backend (_ensure_refund_memo_prefix) would — without double-prefixing an
 * already-prefixed memo. All /api/* traffic is mocked; no real backend/YNAB.
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  RECEIPT_REFUND_READY,
  RECEIPT_REFUND_SPLIT_READY,
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
// T1-01 — single-category refund preview: inflow total + Return: memo + 1 POST
// ---------------------------------------------------------------------------

test("refund preview shows inflow total and Return: memo; confirm fires one POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_REFUND_READY,
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

  // Inflow direction (not outflow), positive signed total (+$25.62).
  await expect(dialog).toContainText("(inflow)");
  await expect(dialog).not.toContainText("(outflow)");
  await expect(dialog.locator("td").filter({ hasText: /\+\$25\.62/ })).toBeVisible();

  // Refund memo prefix applied exactly as the backend would.
  await expect(dialog).toContainText("Return: Coffee maker");

  // Confirm → exactly one sync POST.
  const confirmBtn = page.getByTestId("sync-preview-confirm");
  await expect(confirmBtn).toBeEnabled();
  await confirmBtn.click();
  await expect(dialog).not.toBeVisible({ timeout: 8_000 });

  expect(syncPosts).toHaveLength(1);
  expect(syncPosts[0].url).toBe(`/api/receipts/${RECEIPT_ID}/sync`);
});

// ---------------------------------------------------------------------------
// T1-02 — refund split preview: inflow total, split magnitudes, idempotent memo
// ---------------------------------------------------------------------------

test("refund split preview shows inflow total, split amounts, and idempotent Return: memo", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_REFUND_SPLIT_READY,
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

  // Inflow total and split magnitudes (which sum to the total).
  // (+$25.62 (inflow) appears in both the top Total row and the split footer.)
  await expect(dialog).toContainText("(inflow)");
  await expect(dialog).toContainText("+$25.62");
  await expect(dialog).toContainText("$20.62");
  await expect(dialog).toContainText("$5.00");

  // Memo already begins with "Return: " → not double-prefixed.
  await expect(dialog).toContainText("Return: assorted items");
  await expect(dialog).not.toContainText("Return: Return:");

  // Opening the preview must not have fired a sync.
  expect(syncPosts).toHaveLength(0);
});

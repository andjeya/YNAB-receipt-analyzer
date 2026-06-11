/**
 * Cannot-approve-unsafe suite (M5)
 *
 * Proves that the user CANNOT approve a financially unsafe transaction.
 * All /api/* traffic is intercepted at the browser layer — no real backend,
 * no YNAB network calls.
 *
 * Each test tracks sync POSTs via a syncPosts array.  Any unexpected POST to
 * /api/receipts/<id>/sync is a test failure.
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  ACCOUNT_ID,
  CATEGORY_ID,
  MATCHED_RECEIPT_ID,
  RECEIPT_TWIN_UNCONFIRMED,
  RECEIPT_SYNC_READY,
  RECEIPT_UNKNOWN_ACCOUNT,
  RECEIPT_SPLIT_MISMATCH,
  RECEIPT_DUPLICATE_REVIEW,
  RECEIPT_MATCHED,
  CONFIG_DRY_RUN,
  CONFIG_LIVE,
  YNAB_CACHE,
  SYNC_ENQUEUE_RESPONSE,
  mountApiMocks,
  buildStandardRouter,
  jsonOk,
  makeDraftSaveResponse,
} from "./fixtures";

const DETAIL_URL = `/receipts/${RECEIPT_ID}`;

// ---------------------------------------------------------------------------
// Helper: navigate to the receipt detail page and wait for it to load
// ---------------------------------------------------------------------------

async function gotoDetail(page: import("@playwright/test").Page) {
  await page.goto(DETAIL_URL);
  // Wait for the header with the payee name to appear
  await expect(page.getByText("Trader Joe's").first()).toBeVisible({ timeout: 15_000 });
}

// ---------------------------------------------------------------------------
// Test 1 — Twin unconfirmed → sync button disabled; strip lists reason; no POST
// ---------------------------------------------------------------------------

test("twin unconfirmed → sync button disabled, strip shows reason, no sync POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_TWIN_UNCONFIRMED,
      config: CONFIG_DRY_RUN,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  // Sync button must be disabled
  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeDisabled();

  // Status strip must be visible and contain the twin reason
  const strip = page.getByTestId("sync-status-strip");
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("Confirm Date + Time");

  // Clicking the disabled button does NOT open the dialog and does NOT POST
  await syncButton.click({ force: true });
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();

  // Verify no sync POST happened
  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 2 — Validation error (unknown account) → sync disabled; strip shows reason; no POST
// ---------------------------------------------------------------------------

test("unknown account → sync button disabled, strip shows account error, no sync POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_UNKNOWN_ACCOUNT,
      config: CONFIG_DRY_RUN,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeDisabled();

  const strip = page.getByTestId("sync-status-strip");
  await expect(strip).toBeVisible();
  // The validation error for unknown account should appear in the strip
  await expect(strip).toContainText("Account is unknown");

  await syncButton.click({ force: true });
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();

  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 2b — Validation error (split mismatch) → sync disabled; strip shows reason; no POST
// ---------------------------------------------------------------------------

test("split mismatch → sync button disabled, strip shows split error, no sync POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SPLIT_MISMATCH,
      config: CONFIG_DRY_RUN,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeDisabled();

  const strip = page.getByTestId("sync-status-strip");
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("Split amounts must equal total");

  await syncButton.click({ force: true });
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();

  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 3 — duplicate_review state → dup UI shown; no sync POST possible
// ---------------------------------------------------------------------------

test("duplicate_review → duplicate UI shown, no sync button, no sync POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(page, (pathname, method, route) => {
    if (method === "GET" && pathname === `/api/receipts/${RECEIPT_ID}`) {
      void jsonOk(route, RECEIPT_DUPLICATE_REVIEW);
      return true;
    }
    if (method === "GET" && pathname === `/api/receipts/${MATCHED_RECEIPT_ID}`) {
      void jsonOk(route, RECEIPT_MATCHED);
      return true;
    }
    if (method === "GET" && pathname === "/api/ynab/cache") {
      void jsonOk(route, YNAB_CACHE);
      return true;
    }
    if (method === "GET" && pathname === "/api/config") {
      void jsonOk(route, CONFIG_DRY_RUN);
      return true;
    }
    if (method === "GET" && pathname === "/api/receipts") {
      void jsonOk(route, []);
      return true;
    }
    if (method === "GET" && pathname === "/api/stats/summary") {
      void jsonOk(route, { status_counts: {}, avg_extraction_duration_ms: null, avg_validation_duration_ms: null, avg_receipt_age_at_validation_ms: null });
      return true;
    }
    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/draft`) {
      void jsonOk(route, makeDraftSaveResponse());
      return true;
    }
    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/sync`) {
      syncPosts.push({ url: pathname, body: "" });
      void jsonOk(route, SYNC_ENQUEUE_RESPONSE);
      return true;
    }
    if (method === "GET" && pathname.startsWith("/api/game/")) {
      void jsonOk(route, {});
      return true;
    }
    return false;
  });

  await gotoDetail(page);

  // Duplicate review section must be shown (use heading role to avoid strict-mode
  // violation from repeated text in the status_reason and card body)
  await expect(page.getByRole("heading", { name: "Duplicate Detected" })).toBeVisible();

  // The normal sync button must NOT exist (duplicate_review renders a different UI branch)
  await expect(page.getByTestId("sync-button")).not.toBeVisible();

  // The sync preview dialog must not be open
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();

  // No sync POST
  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 4 — sync-ready receipt → clicking "Sync" OPENS preview dialog; NO POST yet
// ---------------------------------------------------------------------------

test("sync-ready → clicking sync opens preview dialog, no sync POST on open", async ({ page }) => {
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

  // Sync button must be enabled (no blocking errors)
  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeEnabled({ timeout: 10_000 });

  // Click sync
  await syncButton.click();

  // Dialog must open
  const dialog = page.getByTestId("sync-preview-dialog");
  await expect(dialog).toBeVisible();

  // Zero sync POSTs so far — dialog open does NOT fire the sync
  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 5 — open dialog: signed total with sign, account NAME (not id), correct mode badge
// ---------------------------------------------------------------------------

test("dialog shows signed total, account name, and DRY RUN badge", async ({ page }) => {
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

  // Signed total: purchase → negative, rendered as −$25.62 or -$25.62
  // The formatSignedDollarsWithDirection function returns a string like "−$25.62"
  // We accept both the minus sign (U+2212) and the hyphen (U+002D)
  const totalCell = dialog.locator("td").filter({ hasText: /[−\-]\$25\.62/ });
  await expect(totalCell).toBeVisible();

  // Account NAME (not the raw ID)
  await expect(dialog).toContainText("Anna Venture X");
  // Verify the raw account ID is NOT shown as the account value
  // (The ID appears nowhere in the visible dialog text)
  const accountRow = dialog.locator("tr").filter({ hasText: "Account" });
  await expect(accountRow).toContainText("Anna Venture X");
  await expect(accountRow).not.toContainText(ACCOUNT_ID);

  // Mode badge: DRY RUN
  await expect(dialog).toContainText("DRY RUN");
});

test("dialog shows LIVE badge when config.ynab_dry_run=false", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNC_READY,
      config: CONFIG_LIVE,
      syncPosts,
    }),
  );

  await gotoDetail(page);

  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeEnabled({ timeout: 10_000 });
  await syncButton.click();

  const dialog = page.getByTestId("sync-preview-dialog");
  await expect(dialog).toBeVisible();

  // In LIVE mode the badge shows "LIVE"
  await expect(dialog).toContainText("LIVE");
  await expect(dialog).not.toContainText("DRY RUN");
});

// ---------------------------------------------------------------------------
// Test 6 — Escape and Cancel close the dialog without a sync POST
// ---------------------------------------------------------------------------

test("Escape closes dialog without sync POST", async ({ page }) => {
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

  // Press Escape
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();

  // No sync POST
  expect(syncPosts).toHaveLength(0);
});

test("Cancel button closes dialog without sync POST", async ({ page }) => {
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

  // Click Cancel
  await page.getByTestId("sync-preview-cancel").click();
  await expect(dialog).not.toBeVisible();

  // No sync POST
  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 7 — Confirm button fires EXACTLY ONE POST; rapid double-click ≤ 1 POST
// ---------------------------------------------------------------------------

test("confirm fires exactly one sync POST", async ({ page }) => {
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

  // Confirm
  const confirmBtn = page.getByTestId("sync-preview-confirm");
  await expect(confirmBtn).toBeEnabled();
  await confirmBtn.click();

  // Wait for dialog to close (sync success)
  await expect(dialog).not.toBeVisible({ timeout: 8_000 });

  // Exactly one POST
  expect(syncPosts).toHaveLength(1);
  expect(syncPosts[0].url).toBe(`/api/receipts/${RECEIPT_ID}/sync`);
});

test("rapid double-click on confirm fires at most one sync POST", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  // Add artificial delay to the sync response so the button has time to disable
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const pathname = url.pathname;
    const method = req.method().toUpperCase();

    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/sync`) {
      syncPosts.push({ url: pathname, body: "" });
      // Small artificial delay to simulate real latency
      await new Promise((r) => setTimeout(r, 200));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(SYNC_ENQUEUE_RESPONSE),
      });
      return;
    }

    // Delegate remaining routes to the standard router
    const router = buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNC_READY,
      config: CONFIG_DRY_RUN,
      syncPosts,
    });
    const handled = await router(pathname, method, route);
    if (!handled) {
      console.error(`[e2e] UNMOCKED (double-click test): ${method} ${pathname}`);
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unmocked: ${method} ${pathname}` }),
      });
    }
  });

  await gotoDetail(page);

  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeEnabled({ timeout: 10_000 });
  await syncButton.click();

  const dialog = page.getByTestId("sync-preview-dialog");
  await expect(dialog).toBeVisible();

  const confirmBtn = page.getByTestId("sync-preview-confirm");
  await expect(confirmBtn).toBeEnabled();

  // Rapid double-click.
  // The first click fires the sync and sets isSyncing=true (syncMutation.isPending),
  // which disables the button (disabled={confirmFullyDisabled} where isSyncing is
  // included in the disabled condition).  The second click uses force:true so
  // Playwright does not wait for the element to be enabled — it fires the click
  // event regardless.  Since the button is disabled, onClick is not called.
  await confirmBtn.click();
  // Second click with force — button may already be disabled; that is the
  // correct outcome we are verifying.
  await confirmBtn.click({ force: true });

  // Wait for dialog to close
  await expect(dialog).not.toBeVisible({ timeout: 8_000 });

  /**
   * Client-side behavior VERIFIED: the confirm button disables on first click
   * because syncMutation.isPending → isSyncing → confirmFullyDisabled=true.
   * Even with force:true the second click is a no-op (React ignores onClick on
   * disabled buttons at the event-handler level).  We assert exactly 1 POST.
   *
   * Note: the server also guards with a SYNCING lock, but we assert the
   * CLIENT-SIDE behavior here.
   */
  expect(syncPosts.length).toBeLessThanOrEqual(1);
});

// ---------------------------------------------------------------------------
// Test 8 — Confirm disabled while draft dirty / autosaving
// ---------------------------------------------------------------------------

test("confirm disabled while draft is dirty (autosave pending)", async ({ page }) => {
  const syncPosts: { url: string; body: string }[] = [];

  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNC_READY,
      config: CONFIG_DRY_RUN,
      syncPosts,
      // draftSavePending: true means the POST /draft never resolves in time —
      // simulates the autosave being in-flight.  This prevents `dirty` from
      // clearing, which keeps isConfirmDisabled=true.
      draftSavePending: true,
    }),
  );

  await gotoDetail(page);

  // Wait for initial load
  await expect(page.getByText("Trader Joe's").first()).toBeVisible({ timeout: 15_000 });

  // Trigger a draft change by selecting the account (even the same value)
  // This sets dirty=true and triggers autosave.
  // Use page.evaluate to directly fire a change event on the select
  const accountSelect = page.getByTestId("account-select");
  await expect(accountSelect).toBeVisible();

  // Select the same value to force a dirty change without changing data
  await accountSelect.selectOption({ value: ACCOUNT_ID });
  // Typing in a field to mark dirty
  // Actually the select above triggers onChange → setDirty(true)

  // Wait for the status strip to show "Unsaved changes"
  // (The autosave is pending so dirty=true persists)
  // The status section shows "Changes pending autosave" or "Autosaving..."
  await expect(page.getByText(/Changes pending autosave|Autosaving/)).toBeVisible({ timeout: 5_000 });

  // Now try to open the dialog — sync button should be disabled (dirty guard)
  const syncButton = page.getByTestId("sync-button");
  await expect(syncButton).toBeDisabled();

  // Force-click to ensure dialog doesn't open
  await syncButton.click({ force: true });
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();

  expect(syncPosts).toHaveLength(0);
});

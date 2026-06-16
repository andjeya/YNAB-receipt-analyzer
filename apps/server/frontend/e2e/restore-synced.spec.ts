/**
 * "Restore synced" gating regression suite
 *
 * Guards the fix for the bug found reviewing commit 56acf84: the detail UI must
 * gate the "Restore synced" button (and its POST /reset-to-synced call) on
 * `synced_validation`, NOT on the broader `has_successful_sync` flag.
 *
 * The two diverge for a STRUCTURE-IGNORED matched update — YNAB kept the
 * top-level amount but ignored the local split/category structure, so the
 * backend leaves `synced_validation` null (YNAB never held that payload) while
 * `has_successful_sync` stays true. If the button were gated on
 * `has_successful_sync`, clicking it would call POST /reset-to-synced and the
 * endpoint would return 400 no_successful_sync — a guaranteed-to-fail button.
 *
 * All /api/* traffic is intercepted at the browser layer — no real backend.
 */

import { test, expect, type Route } from "@playwright/test";
import {
  RECEIPT_ID,
  RECEIPT_SYNCED_STRUCTURE_IGNORED,
  RECEIPT_SYNCED_RESTORABLE,
  CONFIG_DRY_RUN,
  mountApiMocks,
  buildStandardRouter,
  jsonOk,
  jsonError,
  type MockRouter,
} from "./fixtures";

const DETAIL_URL = `/receipts/${RECEIPT_ID}`;
const RESET_TO_SYNCED_PATH = `/api/receipts/${RECEIPT_ID}/reset-to-synced`;

async function gotoDetail(page: import("@playwright/test").Page) {
  await page.goto(DETAIL_URL);
  await expect(page.getByText("Trader Joe's").first()).toBeVisible({ timeout: 15_000 });
}

/**
 * Standard detail router that also records every POST /reset-to-synced into
 * `restorePosts`. `resetResponder` decides how to answer the call (200 happy
 * path vs. the real backend's 400 no_successful_sync).
 */
function routerRecordingRestore(
  fixture: Record<string, unknown>,
  restorePosts: string[],
  resetResponder: (route: Route) => void,
): MockRouter {
  const base = buildStandardRouter({
    receiptId: RECEIPT_ID,
    receiptFixture: fixture,
    config: CONFIG_DRY_RUN,
    syncPosts: [],
  });
  return (pathname: string, method: string, route: Route): boolean => {
    if (method === "POST" && pathname === RESET_TO_SYNCED_PATH) {
      restorePosts.push(pathname);
      resetResponder(route);
      return true;
    }
    return base(pathname, method, route);
  };
}

// ---------------------------------------------------------------------------
// Test 1 — structure-ignored-only sync → button reads "Reset", NOT "Restore synced"
// ---------------------------------------------------------------------------

test("structure-ignored-only synced receipt offers Reset, not Restore synced", async ({ page }) => {
  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNCED_STRUCTURE_IGNORED, // has_successful_sync=true, synced_validation=null
      config: CONFIG_DRY_RUN,
      syncPosts: [],
    }),
  );

  await gotoDetail(page);

  // The reset button must fall back to the AI-baseline "Reset", because there is
  // no restorable synced_validation. has_successful_sync alone must NOT promote
  // it to "Restore synced".
  const resetButton = page.getByTestId("reset-button");
  await expect(resetButton).toHaveText("Reset");

  // No "Restore synced" control exists anywhere on the page.
  await expect(page.getByRole("button", { name: "Restore synced" })).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Test 2 — restorable sync (synced_validation present) → button reads "Restore synced"
// ---------------------------------------------------------------------------

test("restorable synced receipt offers Restore synced", async ({ page }) => {
  await mountApiMocks(
    page,
    buildStandardRouter({
      receiptId: RECEIPT_ID,
      receiptFixture: RECEIPT_SYNCED_RESTORABLE, // synced_validation present
      config: CONFIG_DRY_RUN,
      syncPosts: [],
    }),
  );

  await gotoDetail(page);

  const resetButton = page.getByTestId("reset-button");
  await expect(resetButton).toHaveText("Restore synced");
});

// ---------------------------------------------------------------------------
// Test 3 — restorable: clicking "Restore synced" fires exactly one POST /reset-to-synced
// ---------------------------------------------------------------------------

test("Restore synced click calls POST /reset-to-synced exactly once", async ({ page }) => {
  const restorePosts: string[] = [];

  await mountApiMocks(
    page,
    routerRecordingRestore(RECEIPT_SYNCED_RESTORABLE, restorePosts, (route) =>
      void jsonOk(route, RECEIPT_SYNCED_RESTORABLE),
    ),
  );

  await gotoDetail(page);

  const resetButton = page.getByTestId("reset-button");
  await expect(resetButton).toHaveText("Restore synced");

  // Edit the memo so the draft diverges from the synced baseline — this is what
  // enables the reset button (canResetToBaseline = draft !== baseline).
  await page.locator("#memo-input").fill("Accidental edit while browsing history");
  await expect(resetButton).toBeEnabled({ timeout: 10_000 });

  await resetButton.click();

  await expect.poll(() => restorePosts.length, { timeout: 8_000 }).toBe(1);
});

// ---------------------------------------------------------------------------
// Test 4 — structure-ignored-only: clicking "Reset" must NEVER hit /reset-to-synced
// ---------------------------------------------------------------------------

test("structure-ignored-only Reset click never calls /reset-to-synced", async ({ page }) => {
  const restorePosts: string[] = [];

  // If the button were mis-wired to restoreSynced, this records the call AND
  // returns the real backend's 400 no_successful_sync.
  await mountApiMocks(
    page,
    routerRecordingRestore(RECEIPT_SYNCED_STRUCTURE_IGNORED, restorePosts, (route) =>
      void jsonError(route, 400, "no_successful_sync"),
    ),
  );

  await gotoDetail(page);

  const resetButton = page.getByTestId("reset-button");
  await expect(resetButton).toHaveText("Reset");

  await page.locator("#memo-input").fill("Accidental edit while browsing history");
  await expect(resetButton).toBeEnabled({ timeout: 10_000 });

  await resetButton.click();

  // After a local "Reset" the draft equals the baseline again, so the button
  // disables — a deterministic settle point for the assertion below.
  await expect(resetButton).toBeDisabled({ timeout: 10_000 });

  // The dead endpoint must never have been called.
  expect(restorePosts).toHaveLength(0);
});

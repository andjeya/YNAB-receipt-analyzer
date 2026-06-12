/**
 * Quick-sync list feature tests.
 *
 * Covers:
 * 1. A list card with sync_ready=true shows the "Looks right — Sync" button.
 * 2. Clicking the button opens the sync PREVIEW dialog (categories visible)
 *    and nothing is sent until Confirm; Confirm fires exactly one sync.
 * 3. Cancelling the preview never fires a sync.
 * 4. sync_ready=false cards do NOT show the button.
 *
 * All API traffic is intercepted at the browser layer — no real backend.
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  RECEIPT_SYNC_READY,
  SYNC_ENQUEUE_RESPONSE,
  YNAB_CACHE,
  mountApiMocks,
  jsonOk,
} from "./fixtures";

const LIST_URL = "/";

// ---------------------------------------------------------------------------
// Minimal game dashboard fixture (avoids momentum.current_streak crash)
// ---------------------------------------------------------------------------

const GAME_DASHBOARD_MINIMAL = {
  generated_at: "2026-06-12T10:00:00Z",
  window: "week",
  debug_tools_enabled: false,
  rules: {
    green_hours_threshold: 24,
    brown_hours_threshold: 72,
    shred_daily_spend_cap: 1,
    water_capacity: 5,
    fire_burn_threshold: 3,
    pass_every_green_weeks: 4,
    timezone: "UTC",
  },
  momentum: {
    current_streak: 0,
    max_streak: 0,
    token_balance: 0,
    token_earned_count: 0,
    token_spent_count: 0,
    pass_every_green_weeks: 4,
    next_pass_in_weeks: 4,
    spendable_now: false,
  },
  forest: {
    latest_receipt_id: null,
    counts: { green: 0, yellow: 0, brown: 0, shredded: 0 },
    receipts: [],
    weekly_slots: [],
  },
  correctness: {
    water_units: 0,
    water_capacity: 5,
    last_reconciled_at: null,
    total_active_flames: 0,
    burnt_week_count: 0,
  },
  summary: {
    window: "week",
    total_validated: 0,
    green_count: 0,
    yellow_count: 0,
    brown_count: 0,
    shredded_count: 0,
    green_percent: 0,
    avg_validation_age_hours: null,
  },
};

// ---------------------------------------------------------------------------
// Shared minimal list fixtures
// ---------------------------------------------------------------------------

function makeSummary(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    id: RECEIPT_ID,
    status: "needs_review",
    original_filename: "test-receipt.jpg",
    display_payee_name: "Quick Mart",
    display_total_milliunits: 12340,
    display_receipt_date: "2026-06-12",
    transaction_kind: "purchase",
    ingested_at: "2026-06-12T10:00:00Z",
    updated_at: "2026-06-12T10:00:00Z",
    correction_detected_at: null,
    correction_expires_at: null,
    correction_shade_opacity: null,
    correction_message: null,
    duplicate_of_receipt_id: null,
    sync_ready: false,
    ...overrides,
  };
}

const SUMMARY_SYNC_READY = makeSummary({ sync_ready: true });
const SUMMARY_NOT_READY = makeSummary({ sync_ready: false });

function buildListRouter(
  summaries: Record<string, unknown>[],
  syncPosts: { url: string }[],
) {
  return (pathname: string, method: string, route: import("@playwright/test").Route): boolean => {
    if (method === "GET" && pathname === "/api/receipts") {
      void jsonOk(route, summaries);
      return true;
    }
    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/sync`) {
      syncPosts.push({ url: pathname });
      void jsonOk(route, SYNC_ENQUEUE_RESPONSE);
      return true;
    }
    if (method === "GET" && pathname === "/api/stats/summary") {
      void jsonOk(route, { status_counts: {}, avg_extraction_duration_ms: null, avg_validation_duration_ms: null, avg_receipt_age_at_validation_ms: null });
      return true;
    }
    if (method === "GET" && pathname === "/api/game/dashboard") {
      void jsonOk(route, GAME_DASHBOARD_MINIMAL);
      return true;
    }
    if (method === "GET" && pathname.startsWith("/api/game/incidents")) {
      void jsonOk(route, []);
      return true;
    }
    if (method === "GET" && pathname.startsWith("/api/game/")) {
      void jsonOk(route, {});
      return true;
    }
    if (method === "GET" && pathname === "/api/config") {
      void jsonOk(route, { ynab_sync_enabled: true, ynab_dry_run: true, ynab_budget_id: null, ynab_budget_name: null, new_transaction_flag_color: "blue", updated_transaction_flag_color: "blue", debug_tools_enabled: false });
      return true;
    }
    // Quick-sync preview fetches the receipt detail + ynab cache on demand
    if (method === "GET" && pathname === `/api/receipts/${RECEIPT_ID}`) {
      void jsonOk(route, RECEIPT_SYNC_READY);
      return true;
    }
    if (method === "GET" && pathname === "/api/ynab/cache") {
      void jsonOk(route, YNAB_CACHE);
      return true;
    }
    // Automatic refresh on page open
    if (method === "POST" && pathname === "/api/ingest/scan") {
      void jsonOk(route, { ingested_count: 0, duplicate_count: 0, skipped_count: 0, error_count: 0 });
      return true;
    }
    if (method === "POST" && pathname === "/api/ynab/updates/fetch") {
      void jsonOk(route, { fetched_count: 0 });
      return true;
    }
    return false;
  };
}

// ---------------------------------------------------------------------------
// Test 1 — card with sync_ready=true shows the quick-sync button
// ---------------------------------------------------------------------------

test("sync_ready card shows 'Looks right — Sync' button", async ({ page }) => {
  const syncPosts: { url: string }[] = [];

  await mountApiMocks(page, buildListRouter([SUMMARY_SYNC_READY], syncPosts));

  await page.goto(LIST_URL);
  // Wait for list to render
  await expect(page.getByText("Quick Mart").first()).toBeVisible({ timeout: 15_000 });

  const quickSyncBtn = page.getByTestId("quick-sync-button");
  await expect(quickSyncBtn).toBeVisible();
  await expect(quickSyncBtn).toContainText("Looks right — Sync");
});

// ---------------------------------------------------------------------------
// Test 2 — clicking the button does NOT navigate away
// ---------------------------------------------------------------------------

test("clicking quick-sync opens the preview dialog and does NOT sync until confirmed", async ({ page }) => {
  const syncPosts: { url: string }[] = [];

  await mountApiMocks(page, buildListRouter([SUMMARY_SYNC_READY], syncPosts));

  await page.goto(LIST_URL);
  await expect(page.getByText("Quick Mart").first()).toBeVisible({ timeout: 15_000 });

  const quickSyncBtn = page.getByTestId("quick-sync-button");
  await expect(quickSyncBtn).toBeVisible();

  await quickSyncBtn.click();

  // Still on the list page (URL unchanged), preview dialog open, NO sync yet
  expect(page.url()).not.toContain(`/receipts/${RECEIPT_ID}`);
  await expect(page.getByTestId("sync-preview-dialog")).toBeVisible({ timeout: 15_000 });
  expect(syncPosts).toHaveLength(0);

  // The preview must show the category breakdown before approval
  await expect(page.getByTestId("sync-preview-dialog")).toContainText("Categor");

  // Confirm → the sync fires
  await page.getByTestId("sync-preview-confirm").click();
  await expect.poll(() => syncPosts.length, { timeout: 10_000 }).toBe(1);
});

test("cancelling the quick-sync preview never fires a sync", async ({ page }) => {
  const syncPosts: { url: string }[] = [];

  await mountApiMocks(page, buildListRouter([SUMMARY_SYNC_READY], syncPosts));

  await page.goto(LIST_URL);
  await expect(page.getByText("Quick Mart").first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("quick-sync-button").click();
  await expect(page.getByTestId("sync-preview-dialog")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("sync-preview-cancel").click();
  await expect(page.getByTestId("sync-preview-dialog")).not.toBeVisible();
  expect(syncPosts).toHaveLength(0);
});

// ---------------------------------------------------------------------------
// Test 3 — card with sync_ready=false does NOT show the button
// ---------------------------------------------------------------------------

test("sync_ready=false card does not show quick-sync button", async ({ page }) => {
  const syncPosts: { url: string }[] = [];

  await mountApiMocks(page, buildListRouter([SUMMARY_NOT_READY], syncPosts));

  await page.goto(LIST_URL);
  await expect(page.getByText("Quick Mart").first()).toBeVisible({ timeout: 15_000 });

  const quickSyncBtn = page.getByTestId("quick-sync-button");
  await expect(quickSyncBtn).not.toBeVisible();

  expect(syncPosts).toHaveLength(0);
});

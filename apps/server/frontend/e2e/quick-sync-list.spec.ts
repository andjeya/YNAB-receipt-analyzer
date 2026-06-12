/**
 * Quick-sync list feature tests.
 *
 * Covers:
 * 1. A list card with sync_ready=true shows the "Looks right — Sync" button.
 * 2. Clicking the button does NOT navigate (card Link is not triggered).
 * 3. sync_ready=false cards do NOT show the button.
 *
 * All API traffic is intercepted at the browser layer — no real backend.
 */

import { test, expect } from "@playwright/test";
import {
  RECEIPT_ID,
  SYNC_ENQUEUE_RESPONSE,
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
    token_earn_every_greens: 5,
    shred_daily_spend_cap: 1,
    water_capacity: 10,
    bucket_capacity: 10,
    fire_burn_threshold: 3,
  },
  momentum: {
    current_streak: 0,
    max_streak: 0,
    last_green_at: null,
    break_reason: null,
    token_balance: 0,
    token_earned_count: 0,
    token_spent_count: 0,
    token_threshold: 5,
    token_progress_current: 0,
    next_token_in: 5,
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
    fire_units: 0,
    fires_to_burn: 0,
  },
  summary: {
    run_id: 1,
    scanned_receipts: 0,
    detected_mistakes: 0,
    applied_penalties: 0,
    fires_added: 0,
    waters_spent: 0,
    burns_triggered: 0,
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
      void jsonOk(route, { ynab_sync_enabled: true, ynab_dry_run: true, ynab_budget_id: null, ynab_budget_name: null, new_transaction_flag_color: "blue", updated_transaction_flag_color: "blue" });
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

test("clicking quick-sync button does not navigate to receipt detail", async ({ page }) => {
  const syncPosts: { url: string }[] = [];

  await mountApiMocks(page, buildListRouter([SUMMARY_SYNC_READY], syncPosts));

  await page.goto(LIST_URL);
  await expect(page.getByText("Quick Mart").first()).toBeVisible({ timeout: 15_000 });

  const quickSyncBtn = page.getByTestId("quick-sync-button");
  await expect(quickSyncBtn).toBeVisible();

  await quickSyncBtn.click();

  // Should still be on the list page (URL unchanged)
  expect(page.url()).not.toContain(`/receipts/${RECEIPT_ID}`);
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

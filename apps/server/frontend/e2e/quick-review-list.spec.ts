/**
 * Quick-review (3-card category chooser) list feature tests.
 *
 * Covers:
 * 1. A card with has_candidates=true shows the "Quick review" button (not Quick sync).
 * 2. Clicking it opens the SAME sync preview dialog with candidate chips; #1 is
 *    pre-selected and switching chips updates the previewed category.
 * 3. "Quick sync this one" promotes the selected candidate (choose) then enqueues
 *    the sync — exactly one of each, with the chosen index.
 *
 * All API traffic is intercepted at the browser layer — no real backend.
 */

import { test, expect } from "@playwright/test";
import { RECEIPT_ID, ACCOUNT_ID, CATEGORY_ID, mountApiMocks, jsonOk } from "./fixtures";

const LIST_URL = "/";
const CATEGORY_ID_2 = "cat00000-0000-0000-0000-000000000002";

const CACHE = [
  { entity_type: "account", entity_id: ACCOUNT_ID, name: "Anna Venture X", group_name: null, raw_json: {}, fetched_at: "2026-06-07T20:00:00Z" },
  { entity_type: "category", entity_id: CATEGORY_ID, name: "Groceries", group_name: "Everyday", raw_json: {}, fetched_at: "2026-06-07T20:00:00Z" },
  { entity_type: "category", entity_id: CATEGORY_ID_2, name: "Household goods", group_name: "Everyday", raw_json: {}, fetched_at: "2026-06-07T20:00:00Z" },
];

const VALIDATION_PAYLOAD = {
  payee_name: "Costco", account_id: ACCOUNT_ID, transaction_date: "2026-06-07", transaction_time: "19:43",
  memo: "Groceries", total_amount: 25.62, transaction_kind: "purchase", category_id: CATEGORY_ID, splits: [],
};

const SUMMARY = {
  id: RECEIPT_ID, status: "needs_review", original_filename: "costco.jpg", display_payee_name: "Costco",
  display_total_milliunits: 25620, display_receipt_date: "2026-06-07", transaction_kind: "purchase",
  ingested_at: "2026-06-07T20:00:00Z", updated_at: "2026-06-07T20:00:00Z",
  correction_detected_at: null, correction_expires_at: null, correction_shade_opacity: null, correction_message: null,
  duplicate_of_receipt_id: null, sync_ready: false, review_hint: "category_issue", has_candidates: true,
};

const DETAIL = {
  ...SUMMARY, status_reason: null, storage_key: "uploads/costco.jpg", mime_type: "image/jpeg",
  latest_extraction: null, extraction_primary: null,
  latest_validation: { id: 10, version: 1, source: "model", payload: VALIDATION_PAYLOAD, allocation_workspace: null, is_valid: true, errors: null, created_at: "2026-06-07T20:00:00Z" },
  model_validation: null, synced_validation: null,
  latest_twin: { id: 1, receipt_id: RECEIPT_ID, version: 1, source: "model", payload: { store_name: "Costco", transaction_date: "2026-06-07", transaction_time: "19:43", total_amount: 25.62, line_items: [], currency: "USD", subtotal: null, tax_total: null, payment_method: "", receipt_language: "en", store_address: "" }, confirmed_sections: { date_time: true, total: true }, created_at: "2026-06-07T20:00:00Z" },
  candidate_set: {
    id: 1, version: 1, source: "model_topk", chosen_index: null, created_at: "2026-06-07T20:00:00Z",
    candidates: [
      { label: "Groceries", rationale: "You've used Groceries here before", confidence: 0.7, category_id: CATEGORY_ID, splits: [], provenance: "model_primary" },
      { label: "Household goods", rationale: "Could be household", confidence: 0.4, category_id: CATEGORY_ID_2, splits: [], provenance: "model_topk" },
    ],
  },
  locked_fields: { transaction_date: false, transaction_time: false, total_amount: false },
  ingested_at: "2026-06-07T20:00:00Z", extraction_started_at: null, extraction_completed_at: null,
  sync_started_at: null, sync_completed_at: null, has_successful_sync: false, latest_sync: null,
  correction_history: [], created_at: "2026-06-07T20:00:00Z", updated_at: "2026-06-07T20:00:00Z",
};

function buildRouter(choosePosts: { version: string; index: number }[], syncPosts: string[]) {
  return (pathname: string, method: string, route: import("@playwright/test").Route): boolean => {
    if (method === "GET" && pathname === "/api/receipts") { void jsonOk(route, [SUMMARY]); return true; }
    if (method === "GET" && pathname === `/api/receipts/${RECEIPT_ID}`) { void jsonOk(route, DETAIL); return true; }
    if (method === "GET" && pathname === "/api/ynab/cache") { void jsonOk(route, CACHE); return true; }
    if (method === "GET" && pathname === "/api/config") {
      void jsonOk(route, { ynab_sync_enabled: true, ynab_dry_run: true, ynab_budget_id: null, ynab_budget_name: null, new_transaction_flag_color: "blue", updated_transaction_flag_color: "blue", debug_tools_enabled: false });
      return true;
    }
    const chooseMatch = pathname.match(new RegExp(`^/api/receipts/${RECEIPT_ID}/candidates/(\\d+)/choose$`));
    if (method === "POST" && chooseMatch) {
      const body = JSON.parse(route.request().postData() || "{}");
      choosePosts.push({ version: chooseMatch[1], index: body.index });
      void jsonOk(route, { validation: DETAIL.latest_validation, can_sync: true, lock_warnings: [] });
      return true;
    }
    if (method === "POST" && pathname === `/api/receipts/${RECEIPT_ID}/sync`) {
      syncPosts.push(pathname);
      void jsonOk(route, { receipt_id: RECEIPT_ID, queue_name: "sync", job_id: "job-1", status: "syncing" });
      return true;
    }
    if (method === "GET" && pathname === "/api/stats/summary") { void jsonOk(route, { status_counts: {}, avg_extraction_duration_ms: null, avg_validation_duration_ms: null, avg_receipt_age_at_validation_ms: null }); return true; }
    if (method === "GET" && pathname === "/api/game/dashboard") { void jsonOk(route, { generated_at: "2026-06-12T10:00:00Z", window: "week", debug_tools_enabled: false, rules: { green_hours_threshold: 24, brown_hours_threshold: 72, shred_daily_spend_cap: 1, water_capacity: 5, fire_burn_threshold: 3, pass_every_green_weeks: 4, timezone: "UTC" }, momentum: { current_streak: 0, max_streak: 0, token_balance: 0, token_earned_count: 0, token_spent_count: 0, pass_every_green_weeks: 4, next_pass_in_weeks: 4, spendable_now: false }, forest: { latest_receipt_id: null, counts: { green: 0, yellow: 0, brown: 0, shredded: 0 }, receipts: [], weekly_slots: [] }, correctness: { water_units: 0, water_capacity: 5, last_reconciled_at: null, total_active_flames: 0, burnt_week_count: 0 }, summary: { window: "week", total_validated: 0, green_count: 0, yellow_count: 0, brown_count: 0, shredded_count: 0, green_percent: 0, avg_validation_age_hours: null } }); return true; }
    if (method === "GET" && pathname.startsWith("/api/game/")) { void jsonOk(route, []); return true; }
    if (method === "POST" && pathname === "/api/ingest/scan") { void jsonOk(route, { ingested_count: 0, duplicate_count: 0, skipped_count: 0, error_count: 0 }); return true; }
    if (method === "POST" && pathname === "/api/ynab/updates/fetch") { void jsonOk(route, { fetched_count: 0 }); return true; }
    return false;
  };
}

test("has_candidates card shows 'Quick review' (not Quick sync)", async ({ page }) => {
  await mountApiMocks(page, buildRouter([], []));
  await page.goto(LIST_URL);
  await expect(page.getByText("Costco").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("quick-review-button")).toBeVisible();
  await expect(page.getByTestId("quick-sync-button")).not.toBeVisible();
});

test("Quick review opens the preview with chips; switching updates the category", async ({ page }) => {
  await mountApiMocks(page, buildRouter([], []));
  await page.goto(LIST_URL);
  await expect(page.getByText("Costco").first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("quick-review-button").click();
  await expect(page.getByTestId("sync-preview-dialog")).toBeVisible({ timeout: 15_000 });
  // #1 pre-selected → Groceries shown in the preview.
  await expect(page.getByTestId("sync-preview-dialog")).toContainText("Groceries");

  // Switch to option 2 → preview shows Household goods.
  await page.getByTestId("quick-review-option-1").click();
  await expect(page.getByTestId("sync-preview-dialog")).toContainText("Household goods");
});

test("'Quick sync this one' chooses the selected candidate then syncs", async ({ page }) => {
  const choosePosts: { version: string; index: number }[] = [];
  const syncPosts: string[] = [];
  await mountApiMocks(page, buildRouter(choosePosts, syncPosts));
  await page.goto(LIST_URL);
  await expect(page.getByText("Costco").first()).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("quick-review-button").click();
  await expect(page.getByTestId("sync-preview-dialog")).toBeVisible({ timeout: 15_000 });

  // Pick option 2 then confirm.
  await page.getByTestId("quick-review-option-1").click();
  await page.getByTestId("sync-preview-confirm").click();

  await expect.poll(() => choosePosts.length, { timeout: 10_000 }).toBe(1);
  expect(choosePosts[0]).toEqual({ version: "1", index: 1 });
  await expect.poll(() => syncPosts.length, { timeout: 10_000 }).toBe(1);
});

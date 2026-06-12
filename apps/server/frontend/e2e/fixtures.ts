/**
 * E2E fixtures — mock API responses matching real backend shapes.
 *
 * All shapes are derived from apps/server/frontend/src/lib/types.ts.
 * Each helper mounts routes via page.route() so the Next.js
 * rewrite never reaches a real backend.
 *
 * Design: interceptor matches on URL path; method-specific handlers are
 * implemented inside each test via mountApiMocks, which accepts a router
 * function that overrides specific routes while falling back to a 500 for
 * anything unexpected (so unmocked calls fail loudly).
 */
import type { Page, Route, Request as PlaywrightRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// Canonical entity IDs (stable across all fixtures)
// ---------------------------------------------------------------------------

export const ACCOUNT_ID = "aac00000-0000-0000-0000-000000000001";
export const CATEGORY_ID = "cat00000-0000-0000-0000-000000000001";
export const RECEIPT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
export const MATCHED_RECEIPT_ID = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb";

// ---------------------------------------------------------------------------
// Reusable sub-fixtures
// ---------------------------------------------------------------------------

const TWIN_PAYLOAD = {
  store_name: "Trader Joe's",
  store_address: "123 Main St",
  transaction_date: "2026-06-07",
  transaction_time: "19:43",
  currency: "USD",
  line_items: [
    { index: 0, raw_text: "Organic Bananas", translated_text: "Organic Bananas", quantity: 1, unit_price: 0.79, line_total: 0.79, tax_code: null, item_type: "product" },
    { index: 1, raw_text: "Almond Milk", translated_text: "Almond Milk", quantity: 1, unit_price: 3.49, line_total: 3.49, tax_code: null, item_type: "product" },
  ],
  subtotal: 4.28,
  tax_total: 0.00,
  total_amount: 25.62,
  payment_method: "Visa",
  receipt_language: "en",
};

const TWIN_CONFIRMED: Record<string, unknown> = {
  id: 1,
  receipt_id: RECEIPT_ID,
  version: 2,
  source: "model",
  payload: TWIN_PAYLOAD,
  confirmed_sections: { date_time: true, total: true },
  created_at: "2026-06-07T20:00:00Z",
};

const TWIN_UNCONFIRMED: Record<string, unknown> = {
  ...TWIN_CONFIRMED,
  confirmed_sections: { date_time: false, total: false },
};

const TWIN_DATE_ONLY_CONFIRMED: Record<string, unknown> = {
  ...TWIN_CONFIRMED,
  confirmed_sections: { date_time: true, total: false },
};

const VALIDATION_PAYLOAD_READY = {
  payee_name: "Trader Joe's",
  account_id: ACCOUNT_ID,
  transaction_date: "2026-06-07",
  transaction_time: "19:43",
  memo: "Groceries",
  total_amount: 25.62,
  transaction_kind: "purchase",
  category_id: CATEGORY_ID,
  splits: [],
};

const VALIDATION_PAYLOAD_UNKNOWN_ACCOUNT = {
  ...VALIDATION_PAYLOAD_READY,
  account_id: "__unknown__",
};

const VALIDATION_PAYLOAD_SPLIT_MISMATCH = {
  ...VALIDATION_PAYLOAD_READY,
  category_id: "",
  splits: [
    { category_id: CATEGORY_ID, amount: 10.00, memo: "Split 1" },
    { category_id: CATEGORY_ID, amount: 5.00, memo: "Split 2" },
    // sum = 15.00, but total_amount = 25.62 → mismatch
  ],
};

function makeValidation(payload: Record<string, unknown>, id = 10): Record<string, unknown> {
  return {
    id,
    version: 1,
    source: "user",
    payload,
    allocation_workspace: null,
    is_valid: true,
    errors: null,
    created_at: "2026-06-07T20:00:00Z",
  };
}

// ---------------------------------------------------------------------------
// Complete receipt fixtures
// ---------------------------------------------------------------------------

/** needs_review — twin UNCONFIRMED (date+total both false) */
export const RECEIPT_TWIN_UNCONFIRMED: Record<string, unknown> = {
  id: RECEIPT_ID,
  status: "needs_review",
  status_reason: null,
  original_filename: "receipt.pdf",
  storage_key: "uploads/receipt.pdf",
  mime_type: "application/pdf",
  display_payee_name: "Trader Joe's",
  display_total_milliunits: 25620,
  display_receipt_date: "2026-06-07",
  latest_extraction: null,
  extraction_primary: null,
  latest_validation: makeValidation(VALIDATION_PAYLOAD_READY as Record<string, unknown>),
  model_validation: null,
  latest_twin: TWIN_UNCONFIRMED,
  locked_fields: { transaction_date: false, transaction_time: false, total_amount: false },
  ingested_at: "2026-06-07T20:00:00Z",
  extraction_started_at: null,
  extraction_completed_at: null,
  sync_started_at: null,
  sync_completed_at: null,
  has_successful_sync: false,
  latest_sync: null,
  correction_detected_at: null,
  correction_expires_at: null,
  correction_shade_opacity: null,
  correction_message: null,
  duplicate_of_receipt_id: null,
  correction_history: [],
  created_at: "2026-06-07T20:00:00Z",
  updated_at: "2026-06-07T20:00:00Z",
};

/** needs_review — twin CONFIRMED + valid draft (sync-ready) */
export const RECEIPT_SYNC_READY: Record<string, unknown> = {
  ...RECEIPT_TWIN_UNCONFIRMED,
  latest_twin: TWIN_CONFIRMED,
};

/** needs_review — unknown account (validation error) */
export const RECEIPT_UNKNOWN_ACCOUNT: Record<string, unknown> = {
  ...RECEIPT_TWIN_UNCONFIRMED,
  latest_twin: TWIN_CONFIRMED,
  latest_validation: makeValidation(VALIDATION_PAYLOAD_UNKNOWN_ACCOUNT as Record<string, unknown>, 11),
};

/** needs_review — split amounts don't sum to total */
export const RECEIPT_SPLIT_MISMATCH: Record<string, unknown> = {
  ...RECEIPT_TWIN_UNCONFIRMED,
  latest_twin: TWIN_CONFIRMED,
  latest_validation: makeValidation(VALIDATION_PAYLOAD_SPLIT_MISMATCH as Record<string, unknown>, 12),
};

/** duplicate_review state */
export const RECEIPT_DUPLICATE_REVIEW: Record<string, unknown> = {
  ...RECEIPT_TWIN_UNCONFIRMED,
  status: "duplicate_review",
  status_reason: "Duplicate detected: same payee, date, and total as an existing receipt.",
  duplicate_of_receipt_id: MATCHED_RECEIPT_ID,
};

/** Matched receipt for duplicate review */
export const RECEIPT_MATCHED: Record<string, unknown> = {
  ...RECEIPT_TWIN_UNCONFIRMED,
  id: MATCHED_RECEIPT_ID,
  status: "needs_review",
};

// ---------------------------------------------------------------------------
// Config fixtures
// ---------------------------------------------------------------------------

export const CONFIG_DRY_RUN: Record<string, unknown> = {
  ynab_sync_enabled: true,
  ynab_dry_run: true,
  ynab_budget_id: "budget-test-001",
  ynab_budget_name: "testplandevelopmentonly",
  new_transaction_flag_color: "blue",
  updated_transaction_flag_color: "blue",
};

export const CONFIG_LIVE: Record<string, unknown> = {
  ...CONFIG_DRY_RUN,
  ynab_dry_run: false,
};

export const CONFIG_SYNC_DISABLED: Record<string, unknown> = {
  ...CONFIG_DRY_RUN,
  ynab_sync_enabled: false,
};

// ---------------------------------------------------------------------------
// YNAB cache fixture (one account + one category + one payee)
// ---------------------------------------------------------------------------

export const YNAB_CACHE: Record<string, unknown>[] = [
  {
    entity_type: "account",
    entity_id: ACCOUNT_ID,
    name: "Anna Venture X",
    group_name: null,
    raw_json: {},
    fetched_at: "2026-06-07T20:00:00Z",
  },
  {
    entity_type: "category",
    entity_id: CATEGORY_ID,
    name: "Groceries",
    group_name: "Everyday Expenses",
    raw_json: {},
    fetched_at: "2026-06-07T20:00:00Z",
  },
  {
    entity_type: "payee",
    entity_id: "pay00000-0000-0000-0000-000000000001",
    name: "Trader Joe's",
    group_name: null,
    raw_json: {},
    fetched_at: "2026-06-07T20:00:00Z",
  },
];

// ---------------------------------------------------------------------------
// Draft save response (used for autosave mock)
// ---------------------------------------------------------------------------

export function makeDraftSaveResponse(
  payload: Record<string, unknown> = VALIDATION_PAYLOAD_READY as Record<string, unknown>,
): Record<string, unknown> {
  return {
    validation: makeValidation(payload),
    can_sync: true,
    lock_warnings: [],
  };
}

// ---------------------------------------------------------------------------
// Sync enqueue response
// ---------------------------------------------------------------------------

export const SYNC_ENQUEUE_RESPONSE: Record<string, unknown> = {
  receipt_id: RECEIPT_ID,
  queue_name: "sync",
  job_id: "job-001",
  status: "syncing",
};

// ---------------------------------------------------------------------------
// Game dashboard fixture (new v3 backend contract)
// ---------------------------------------------------------------------------

function makeWeekSlot(index: number, opts: {
  display_state?: "green" | "yellow" | "brown" | null;
  receipt_count?: number;
  flames?: number;
  burnt?: boolean;
  is_empty?: boolean;
} = {}): Record<string, unknown> {
  const base = new Date("2026-06-01T00:00:00Z");
  const start = new Date(base.getTime() + (index - 8) * 7 * 24 * 3600 * 1000);
  const end = new Date(start.getTime() + 7 * 24 * 3600 * 1000);
  return {
    index,
    start_at: start.toISOString(),
    end_at: end.toISOString(),
    is_empty: opts.is_empty ?? (opts.receipt_count === 0),
    display_state: opts.display_state ?? null,
    receipt_count: opts.receipt_count ?? 0,
    flames: opts.flames ?? 0,
    burnt: opts.burnt ?? false,
  };
}

/** Full game dashboard in v3 shape — includes a slot with flames for coverage. */
export const GAME_DASHBOARD_V3: Record<string, unknown> = {
  generated_at: "2026-06-12T20:00:00Z",
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
    current_streak: 3,
    max_streak: 5,
    token_balance: 0,
    token_earned_count: 1,
    token_spent_count: 1,
    pass_every_green_weeks: 4,
    next_pass_in_weeks: 1,
    spendable_now: true,
  },
  correctness: {
    water_units: 2,
    water_capacity: 5,
    last_reconciled_at: "2026-06-12T10:00:00Z",
    total_active_flames: 1,
    burnt_week_count: 0,
  },
  forest: {
    latest_receipt_id: null,
    counts: { green: 3, yellow: 1, brown: 0, shredded: 0, burnt: 0 },
    receipts: [],
    weekly_slots: [
      makeWeekSlot(0, { is_empty: true }),
      makeWeekSlot(1, { display_state: "green", receipt_count: 2 }),
      makeWeekSlot(2, { display_state: "green", receipt_count: 1 }),
      makeWeekSlot(3, { is_empty: true }),
      makeWeekSlot(4, { display_state: "yellow", receipt_count: 3 }),
      // Week with active flames — key coverage item
      makeWeekSlot(5, { display_state: "green", receipt_count: 2, flames: 1 }),
      makeWeekSlot(6, { display_state: "green", receipt_count: 1 }),
      makeWeekSlot(7, { display_state: "green", receipt_count: 2 }),
      // Current week (hero tile) — in progress
      makeWeekSlot(8, { display_state: null, receipt_count: 0, is_empty: true }),
    ],
  },
  summary: {
    window: "week",
    window_start: "2026-06-08T00:00:00Z",
    window_end: "2026-06-15T00:00:00Z",
    total_validated: 9,
    green_count: 3,
    yellow_count: 1,
    brown_count: 0,
    shredded_count: 0,
    green_percent: 75.0,
    avg_validation_age_hours: 8.5,
  },
};

// ---------------------------------------------------------------------------
// Route-mounting helpers
// ---------------------------------------------------------------------------

export type MockRouter = (url: string, method: string, route: Route) => boolean;

/**
 * Mount API mocks on a Playwright page.  Calls `router` for each /api/**
 * request; if router returns false, responds with 500 so unmocked calls fail
 * loudly (never silently reach :8000 or :9).
 */
export async function mountApiMocks(page: Page, router: MockRouter): Promise<void> {
  await page.route("**/api/**", async (route: Route) => {
    const req: PlaywrightRequest = route.request();
    const url = new URL(req.url());
    const pathname = url.pathname;
    const method = req.method().toUpperCase();

    const handled = await router(pathname, method, route);
    if (!handled) {
      console.error(`[e2e] UNMOCKED REQUEST: ${method} ${pathname}`);
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unmocked e2e request: ${method} ${pathname}` }),
      });
    }
  });
}

/** Convenience: respond with JSON 200 */
export async function jsonOk(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Convenience: respond with JSON error */
export async function jsonError(route: Route, status: number, detail: string): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify({ detail }),
  });
}

/**
 * Build a standard mock router for the receipt detail page.
 *
 * Covers all the endpoints the receipt detail component fetches:
 *   GET /api/receipts/<id>
 *   GET /api/ynab/cache
 *   GET /api/config
 *   POST /api/receipts/<id>/draft   (autosave — returns immediately)
 *   POST /api/receipts/<id>/sync    (tracked via syncPosts array)
 *   GET /api/stats/summary          (app layout / header — return minimal shape)
 *   GET /api/receipts               (list — return empty for simplicity)
 *
 * Extra handlers can be prepended by callers.
 */
export function buildStandardRouter(options: {
  receiptId: string;
  receiptFixture: Record<string, unknown>;
  config: Record<string, unknown>;
  syncPosts: { url: string; body: string }[];
  /** Return pending (never resolves) from the draft save route — simulates autosaving */
  draftSavePending?: boolean;
  /** Override receipt returned AFTER a sync POST (simulate status flip) */
  receiptAfterSync?: Record<string, unknown>;
}): MockRouter {
  const {
    receiptId,
    receiptFixture,
    config,
    syncPosts,
    draftSavePending = false,
    receiptAfterSync,
  } = options;

  let syncCount = 0;

  return (pathname: string, method: string, route: Route): boolean => {
    // --- Receipt detail ---
    if (method === "GET" && pathname === `/api/receipts/${receiptId}`) {
      const fixture =
        receiptAfterSync && syncCount > 0 ? receiptAfterSync : receiptFixture;
      void jsonOk(route, fixture);
      return true;
    }

    // --- YNAB cache ---
    if (method === "GET" && pathname === "/api/ynab/cache") {
      void jsonOk(route, YNAB_CACHE);
      return true;
    }

    // --- Config ---
    if (method === "GET" && pathname === "/api/config") {
      void jsonOk(route, config);
      return true;
    }

    // --- Draft save (autosave) ---
    if (method === "POST" && pathname === `/api/receipts/${receiptId}/draft`) {
      if (draftSavePending) {
        // Never resolve — simulates a pending autosave
        // We must still call route.fulfill eventually but we want the UI
        // to see it as pending. We use a very long artificial delay to
        // simulate it — the test must not wait longer than its expect timeout.
        setTimeout(() => {
          void jsonOk(route, makeDraftSaveResponse());
        }, 60_000);
      } else {
        void jsonOk(route, makeDraftSaveResponse());
      }
      return true;
    }

    // --- Sync POST ---
    if (method === "POST" && pathname === `/api/receipts/${receiptId}/sync`) {
      syncCount += 1;
      syncPosts.push({ url: pathname, body: "" });
      void jsonOk(route, SYNC_ENQUEUE_RESPONSE);
      return true;
    }

    // --- Stats summary (header/layout) ---
    if (method === "GET" && pathname === "/api/stats/summary") {
      void jsonOk(route, {
        status_counts: {},
        avg_extraction_duration_ms: null,
        avg_validation_duration_ms: null,
        avg_receipt_age_at_validation_ms: null,
      });
      return true;
    }

    // --- Receipt list (may be fetched by layout/header invalidation) ---
    if (method === "GET" && pathname === "/api/receipts") {
      void jsonOk(route, []);
      return true;
    }

    // --- Game dashboard (may be fetched by layout/header) ---
    if (method === "GET" && pathname === "/api/game/dashboard") {
      void jsonOk(route, GAME_DASHBOARD_V3);
      return true;
    }
    // --- Game incidents (layout polls for incidents) ---
    if (method === "GET" && pathname === "/api/game/incidents") {
      void jsonOk(route, []);
      return true;
    }

    return false;
  };
}

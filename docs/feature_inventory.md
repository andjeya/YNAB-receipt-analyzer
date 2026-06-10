# Feature Inventory

Seven categories from the audit (2026-06-10), updated with the settled
decisions. Use this to classify work and avoid building things that are out of
scope. `file:line` anchors are audit-time — re-verify.

## 1. Working
Ingestion (folder scan, file-stability, file-hash dedupe); schema-bound Gemini
extraction + AI gateway/limiter + TUI; Receipt Twin (versioned, confirm locks,
optimistic concurrency); draft autosave + validation; semantic duplicate
detection + polished duplicate-review UI; game economy
(streak/water/fire/shred/incidents); YNAB cache + category/payee search;
reconciliation job (category corrections → fires); Pi outbox shipper; memo
traceability via `[receipt_id:]` markers.

## 2. Risky / fragile
Entire YNAB write path (kill-switch, idempotency, double-click, delete-recreate,
amount-blind reconciliation, milliunit drift); allocation pin edge cases
(all-pinned shortfall silent; discounts inflate lane weights
`allocation_workspace.py:298`); extraction quality gate effectively advisory
(`twin_strict_mode=False` default); silent UI mutation failures (no toasts, no
`onError`); raw-JSON error messages (`api.ts:43-46`); secrets exported to every
shell + yolo aliases.

## 3. In progress
Allocation V2 follow-ups (memo re-sync endpoint — designed, gemini-2.5-flash-lite
pinned, **not built**; twin staleness refresh — warning-only; pin
visibility/lane totals); receipt-list memo subtitle (partial); NAS deploy
(placeholder).

## 4. Missing for MVP
Sync kill-switch + dry-run + test-budget mode; payload preview + confirm gate;
`import_id` + sync state-machine guard; exact milliunit-sum invariant;
toast/error layer; pytest config + network isolation + money tests; ingest-scan
feedback; mismatch warnings in read mode.
**Refunds/inflow support** — moved here from "needs decision": **decided
supported** (decision 2026-06-10), build during M1/M2.

## 5. Optional / future
Partial-item % allocations (deferred); twin shadow values; challenges UI; batch
review mode; in-app upload UI; Postgres parity tests.

## 6. Not needed now
Webpage camera scanner (user-stated; current frontend has no scanner route);
multi-user/auth (Phase 6); NAS prod hardening; Web-Worker scanner detection.

## 7. Decided / resolved (was "needs user decision")
- **Refunds/inflow:** supported end-to-end (M1/M2). See `accounting_invariants.md` sign table.
- **Delete-recreate:** prohibited — flag receipt for manual fix instead; **M2 decided work**. See `ynab_safety_model.md`.
- **Gemini model/SDK:** **fixed (done 2026-06-10)** — google-genai 1.30.0→1.75.0, thinking-config feature-detection + broadened fallback in `apps/server/shared/receipt_shared/ai/providers/gemini.py`; 112/112.
- **Yolo aliases / secrets-in-shell:** keep + commit aliases (test-budget-only token; user-accepted risk).
- Still open (see `agent_loop_state.md` → Blocked on Human): memo re-sync endpoint wanted?; orphaned `gamification-dashboard.tsx` wire-or-delete; reconciliation amount-drift (provisional pull/flag); time-less near-match warning (provisional yes).

---

## Detail blocks — key remaining features

### Refund / inflow support (M1/M2)
- **Goal/why:** returns and credits are real money events; the budget is wrong without them.
- **Workflow:** receipt with negative/refund total → inflow transaction, memo "Returning …".
- **Areas:** `services/ynab.py`, validation (replace `total <= 0` reject), `money.py` (`outflow=False`), UI Confirm screen sign display.
- **Acceptance:** a refund receipt produces a positive-milliunit YNAB transaction with a "Returning …" memo and correct splits; sum invariant holds.
- **Tests:** payload sign tests; refund round-trip; Confirm-screen shows positive/inflow.
- **Classification:** Missing for MVP — decided.

### Sync kill-switch + dry-run (M0)
- **Goal/why:** survive a production token; never write without intent.
- **Workflow:** `ynab_sync_enabled=false` default; `dry_run` builds/persists/previews payload, no POST.
- **Areas:** `config.py:57-58`, sync path in `services/ynab.py`, `api/receipts.py`.
- **Acceptance:** with flag off / dry-run, zero YNAB client POSTs; payload still viewable.
- **Tests:** zero-client-calls-when-disabled guardrail.
- **Classification:** Missing for MVP — M0.

### Sync idempotency + guard (M2)
- **Goal/why:** one receipt = at most one transaction, even on retry/double-click.
- **Workflow:** `import_id` on create; SYNCING row-lock; retry preserves `created_transaction_id`; post-POST bookkeeping failure ≠ FAILED.
- **Areas:** `api/receipts.py:944-997`, `services/ynab.py:746-757, 846-849`.
- **Acceptance:** concurrent sync → one transaction; retry never double-posts.
- **Tests:** double-click, retry-preserves-evidence, post-POST-failure guardrails.
- **Classification:** Missing for MVP — M2.

### Approval / Confirm gate (M3)
- **Goal/why:** a human always sees the signed payload before it leaves the machine.
- **Workflow:** Confirm screen with signed amount, account, splits, duplicate status, mode badge → explicit confirm.
- **Areas:** frontend Review/Confirm screens, `formatAmount` (remove `Math.abs`), toast layer.
- **Acceptance:** cannot approve an unsafe payload; sign visible; failures surface as toasts.
- **Tests:** Playwright "cannot approve unsafe" suite.
- **Classification:** Missing for MVP — M3.

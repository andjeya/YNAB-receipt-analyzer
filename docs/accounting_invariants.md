# Accounting Invariants (non-negotiable)

These rules govern all money handling. Violating any one is a correctness bug
that can corrupt the real budget. Anchors are `file:line` at audit time
(2026-06-10) — re-verify before relying on them.

## 1. Decimal-only authoritative math
- All authoritative money math uses `Decimal`, never `float` arithmetic.
- **Forbidden:** `int(float * 1000)` truncation. Known offenders to remove in M1:
  `apps/server/backend/app/api/receipts.py:236`, `apps/server/backend/app/services/reconciliation.py:219`.
- floats may appear only at I/O edges (JSON in, display out); never inside a sum that feeds a POST.

## 2. Single conversion path
- The **only** dollars→milliunits conversion is
  `apps/server/shared/receipt_shared/money.py::dollars_to_milliunits`.
- It quantizes to `0.001` with `ROUND_HALF_UP`, then scales ×1000 with
  `ROUND_HALF_UP` (`money.py:6-11`). Document and preserve `ROUND_HALF_UP`.
- The inverse is `milliunits_to_dollars` (`money.py:14-16`). No ad-hoc conversions anywhere.

## 3. Exact milliunit sum before POST
- `sum(split.amount_milliunits) == total_milliunits` **exactly**, compared in
  integer milliunits, immediately before any YNAB POST.
- The current `$0.01` tolerance in the split-sum check (`services/ynab.py:513`)
  is **not acceptable** — replace with exact integer equality in M1.
- Convert total and each split through the single path, then assert integer equality (no re-rounding the sum).

## 4. Sign convention

| Case | Direction | milliunits sign | Notes |
|---|---|---|---|
| Purchase | outflow | **negative** | `dollars_to_milliunits(x, outflow=True)` → negative |
| Refund / return | inflow | **positive** | `outflow=False`; memo begins "Returning …" (wording flexible). Supported end-to-end (decision 2026-06-10) — must replace the old `total <= 0` rejection. |
| Discount within a receipt | line item | reduces line/lane weight | Never a negative split unless a negative-split path is explicitly designed |
| Credit | inflow | **positive** | Same handling as refund |

- `Math.abs`/`abs()` must **not** strip signs on adopt-from-YNAB paths
  (`services/ynab.py:680`, `reconciliation.py:122`) — the sign is data, not noise.
- The whole receipt's splits share one direction; do not mix outflow and inflow splits in one transaction unless a designed mixed path exists.

## 5. Duplicate-signature rules
- Duplicate detection keys on a semantic signature (payee/date/total/items).
- **Missing-field policy:** a receipt with no time must **not** silently bypass
  detection (current bypass: `services/duplicates.py:127-132`). Time-less
  receipts get a **near-match warning** on date+total (provisional decision: yes).
- A near-match warning informs the human; it never auto-blocks or auto-merges.

## 6. Allocation invariants
- Largest-remainder distribution must make the splits sum **exactly** to the total milliunits (the algorithm is correct in isolation — keep it that way).
- **Pins may never resurrect a stale total:** a pinned main-lane amount must not
  revert a corrected total via `keep_manual_amounts` recompute
  (`services/allocation_workspace.py:213, 355-356`) — silent corruption vector.
- **All-pinned shortfall must warn:** if every lane is pinned and the pins don't
  reach the total, surface a warning (do not silently absorb the remainder).
- **Discounts subtract from lane weights** — they must not be `abs`-added into
  weights (current bug `allocation_workspace.py:298`).

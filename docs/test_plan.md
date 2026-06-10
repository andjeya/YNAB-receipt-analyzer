# Test Plan (living checklist)

Prioritized from the audit. `[x]` = exists today, `[ ]` = missing. Run the gate
(`docs/agent_loop_state.md` → Canonical Commands) before checking anything off.
Coverage map reflects audit state 2026-06-10.

## Config / harness
- [ ] `pytest.ini` with an `integration` marker.
- [ ] **Network-off by default** — live-network tests run only with `-m integration`. Currently a real `GEMINI_API_KEY` in `.env` makes the live test run by default; fix in M0.
- [ ] `app.db`-isolated test DB (no real budget/DB touched by tests).

## Money math
- [ ] Direct tests for `receipt_shared/money.py`: `dollars_to_milliunits` (rounding `ROUND_HALF_UP`, outflow sign, str/int/float inputs), `milliunits_to_dollars` round-trip. **Zero direct tests today.**

## Payload construction
- [ ] Milliunit-sum invariant: `sum(splits) == total` exactly (integer), no $0.01 tolerance. (`validate_payload` split-sum rule untested today.)
- [ ] Sign correctness: purchase → negative outflow; **refund → positive inflow** with "Returning …" memo.
- [ ] No `int(float*1000)` reachable in any payload path.
- [~] `test_validation_payload.py` exists — extend for sums + signs + refunds.

## Reconciliation
- [~] `test_reconciliation_helpers.py` exists.
- [ ] Amount-drift → flag (pull), never push/overwrite a YNAB-side edit.

## Splits / allocation (property tests)
- [~] `test_allocation_workspace.py` exists.
- [ ] Property test: largest-remainder splits always sum exactly to total.
- [ ] Pins never resurrect a stale total; all-pinned shortfall warns; discounts subtract from lane weights.

## Duplicates
- [x] `test_duplicate_detection.py` exists.
- [ ] Time-less near-match (date+total) produces a warning, not a silent bypass.

## Guardrails
- [x] `test_ai_gateway_no_bypass.py` exists.
- [ ] Zero YNAB client calls when `ynab_sync_enabled=false` / `dry_run` (assert no POST).
- [ ] Double-click / concurrent sync → exactly one create (SYNCING guard + lock).
- [ ] Retry preserves `created_transaction_id`; post-POST bookkeeping failure does not mark FAILED.

## API (TestClient)
- [ ] FastAPI `TestClient` tests for sync/draft/validation endpoints. **None today.**

## E2E (Playwright)
- [ ] Playwright + mocked backend harness.
- [ ] "Cannot approve unsafe" suite: confirm gate blocks approval when payload is unsafe (sign/sum/duplicate/mode mismatch).

## Existing suites (keep green)
- AI usage limiter, correctness economy, debug tools, game scoring, gemini schema sanitizer, gemini structured integration (live, opt-in), prompt/traceability, receipt draft save, receipt twin v2, ynab split sync.
- Baseline: **112/112** passing after the 2026-06-10 Gemini fix (was 111 pass + 1 fail).

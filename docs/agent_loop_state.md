# Agent Loop State — Single Source of Truth

**Read this file FIRST, before any implementation work.** It defines the goal,
the operating model, the canonical commands, the milestone checklists, what is
blocked on a human, and the append-only decisions log.

## Top-Level Goal

Make the receipt → YNAB pipeline safe to run autonomously against the real
budget, then complete it to MVP. The app extracts receipts (Gemini), lets a
human review/allocate line items into category splits, and writes a single
correct YNAB transaction per receipt — purchases as outflows and refunds as
inflows — with exact milliunit math, idempotent writes, and a confirm gate so a
human always sees the signed payload before it leaves the machine. Until the
safety foundations (kill-switch, dry-run, import_id, sync guards, exact money
invariants) exist and are tested, no further live YNAB writes are made. Every
sync-affecting change is validated against the real test budget
(`testplandevelopmentonly`) via the YNAB API and the web UI; production writes
are never autonomous.

## Operating Model

### Read-first order (every session)
1. This file (`docs/agent_loop_state.md`) — current milestone, checklists, decisions, blockers.
2. `AGENTS.md` — coordination + commit policy.
3. Current week's latest `plans/YYYY/MM/week-NN/report-*.md` and most recent `session-*.md`.
4. Supporting docs as needed: `accounting_invariants.md`, `ynab_safety_model.md`, `test_plan.md`, `ui_product_direction.md`, `feature_inventory.md`.
5. Re-verify any cited `file:line` still matches before relying on it (code drifts).

### Task selection rule
Pick the **smallest unchecked task in the lowest-numbered open milestone**. Do
not jump ahead to a more interesting milestone. A task should be **≤ ~300 lines
of change including its tests**; if larger, split it and check off the pieces.

### Maker / checker split
- **Maker** implements the task + its tests, runs the full gate (see commands), updates this file and plans notes.
- **Checker** (mandatory for M0–M2; recommended thereafter) reviews in fresh
  context, skeptical of money math, signs, rounding, idempotency, and sync
  safety. Checker must confirm tests actually exercise the invariant, not just pass.
- Checker is **required** before any task in M0, M1, or M2 is marked done.

### Model delegation policy
- **Fable** directs at the highest level (goals, priorities, approving milestones).
- **Opus** plans (decomposes milestones into tasks, designs invariants/state machines).
- **Sonnet** implements (writes code + tests under the plan), and may run setup/verification subagents.

## Canonical Commands

Backend tests, from repo root (network-off by default once `integration` marker lands in M0):
```bash
PYTHONPATH=apps/server/backend:apps/server/shared python3 -m pytest apps/server/backend/tests/ -q \
  --deselect apps/server/backend/tests/test_gemini_structured_integration.py
# integration (live network + billable Gemini call, deliberate runs only):
PYTHONPATH=apps/server/backend:apps/server/shared python3 -m pytest apps/server/backend/tests/test_gemini_structured_integration.py -q
```

Frontend:
```bash
cd apps/server/frontend && npm run build
cd apps/server/frontend && npm run test:unit
```

Dev stack (safe browsing = **API + frontend ONLY**; never start worker/scanner
during doc/UI work so no Gemini calls or YNAB writes can fire):
```bash
scripts/dev-env.sh        # source for aliases/env
scripts/dev-up.sh         # Redis + API + frontend (start worker/scanner only when intended)
scripts/dev-down.sh
```

Live YNAB test-budget verification (read-only check that a write landed; token from `.env`):
```bash
curl -s -H "Authorization: Bearer $YNAB_ACCESS_TOKEN" \
  "https://api.ynab.com/v1/budgets/$YNAB_BUDGET_ID/transactions" \
  | grep -o '\[receipt_id:[^]]*\]'   # confirm the receipt memo marker is present
```
Playwright YNAB UI check uses `YNAB_TEST_USER_EMAIL` / `YNAB_TEST_USER_PASSWORD`
from untracked `.env` (env var names only — never write the values anywhere).
Local webapp must also be loaded and **visually inspected** via Playwright
(Chromium installed), not only unit-tested.

## Milestones

### M0 — Safety foundations ✅ COMPLETE 2026-06-11 (checker: APPROVE)
- [x] `ynab_sync_enabled: bool = False` + `ynab_dry_run: bool = True` — gated at API (409 `ynab_sync_disabled`) AND authoritatively in `sync_receipt_to_ynab` before any client construction; dry-run persists payload on `YNABSync` (status `dry_run`), exposed via `latest_sync` on the detail API (M3 preview hook); receipt → `needs_review`. Dev `.env` overrides to enabled+live (test budget only).
- [x] `apps/server/backend/pytest.ini` — `integration` marker excluded by default; pytest-socket blocks external network (localhost allowed; live Gemini test carries `enable_socket` for `-m integration` opt-in runs). Canonical command no longer needs `--deselect`.
- [x] Direct `money.py` tests (33) — pins ROUND_HALF_UP, float-artifact handling, and the negative-input/outflow quirk (deferred to M1 by design).
- [x] Fix Gemini `thinking_level`/`ThinkingConfig` extraction bug — **DONE 2026-06-10** (google-genai 1.30.0→1.75.0; thinking-config feature-detection + broadened fallback in `apps/server/shared/receipt_shared/ai/providers/gemini.py`; live integration test passed, 112/112).
- [x] Commit `.devcontainer/devcontainer-lock.json` — **DONE 2026-06-10** (commit `61bca39`, with yolo aliases + bubblewrap persistence).

### M1 — Money invariants
- [ ] Single conversion path everywhere → `dollars_to_milliunits` (remove `int(float*1000)` at `api/receipts.py:236`, `reconciliation.py:219`).
- [ ] Exact milliunit-sum invariant before any POST (`sum(splits) == total`, no $0.01 tolerance).
- [ ] `ValidationSplit.amount` bounds + **refund path** (replace `total <= 0` rejection with designed inflow support; memo "Returning …").
- [ ] Allocation pin fixes: stale-total revert, all-pinned shortfall warning, discount-weight handling.

### M2 — Sync idempotency
- [ ] Set YNAB `import_id` on every create.
- [ ] SYNCING-status guard + row lock on `POST /receipts/{id}/sync` (no double-post).
- [ ] Retry preserves `created_transaction_id` evidence; post-POST bookkeeping (gamification) failure must **not** mark sync FAILED.
- [ ] **Delete-recreate PROHIBITED** — when YNAB ignores a split-structure update, leave the transaction untouched and flag the receipt for manual fix (default: never delete-recreate).
- [ ] Reconciliation amount-drift → **pull/flag, never push** (provisional).

### M3 — Approval UX
- [ ] Sync preview modal: full signed payload (sign, account, splits, duplicate status, mode badge) + explicit confirm. Checker notes from M0: (a) the persisted dry-run payload is the *create-intent* payload — label it as such, since a matched-update live path may send a minimal memo-marker update instead; (b) branch the previewed flag color on update-vs-create (`prior_success_sync and not force_create` → updated color) instead of always-blue.
- [ ] Signed amount display (remove `Math.abs` in formatAmount helpers).
- [ ] Toast/error layer + `onError` on sync/autosave; mismatch warnings visible in read mode.

### M4 — Workflow completeness
- [ ] Fix "Original Scan" pane rendering empty on receipt detail (observed on every receipt in 2026-06-10 live review — reviewer cannot compare against source).
- [ ] Hide extraction artifact rows (e.g. bare `0000370179/1695152 · 0×` lines) from the twin line-item display instead of rendering them in red.
- [ ] Allocation board polish (pin badges, lane totals, keyboard DnD sensor, undo).
- [ ] Twin staleness refresh action.
- [ ] Ingest-scan result feedback.
- [ ] Near-duplicate (date+total, no time) warning (provisional: yes).

### M5 — E2E harness
- [ ] Add `data-testid` attributes to interactive controls (twin confirm buttons, sync bar, allocation lanes) — 2026-06-10 live E2E had to fall back to the API for twin confirms because text-based selectors matched the wrong button.
- [ ] Playwright + mocked backend.
- [ ] "Cannot approve unsafe" approval-gate suite.

### Live E2E baseline (2026-06-10, passed)
Full pipeline validated against the real test budget: receipt `2026_02_23_13_09_21.pdf` → ingest (GUI scan button) → Gemini extraction (all 6 line items + total exact vs human-read ground truth) → GUI sync click → YNAB transaction `16b484ec` (−119190 milliunits, Groceries, Anna Venture X, approved=false, blue flag, memo marker). Confirms: money path exact ×1000 with correct sign; `import_id` absent (M2 gap live-confirmed). Two fresh June scans (`receipt_examples/2026-06-10 19.43.*.pdf`) are reserved as the next live-validation inputs.

### M6 — Delight pass
- [ ] Completion celebrations, batch review flow.
- [ ] `gamification-dashboard.tsx` decision (wire or delete).
- [ ] a11y: dialog semantics, focus traps, contrast.

### M7 — Test-budget validation → production (human-gated)
- [ ] Full live validation against `testplandevelopmentonly` (API + Playwright UI).
- [ ] Production enablement — **human-only checklist, never autonomous**.

## Blocked on Human (open decisions)
- Memo re-sync endpoint (gemini-2.5-flash-lite) — still wanted?
- Orphaned `gamification-dashboard.tsx` (399 lines, unmounted) — wire or delete?
- Reconciliation amount-drift handling — recommendation **pull/flag, never push** adopted *provisionally* pending objection.
- Time-less duplicate near-match warning — recommendation **yes** adopted *provisionally*.

## Decisions Log (append-only)
- **2026-06-11** — M0 complete (maker: Opus plan + Sonnet implementers; checker: Opus, verdict APPROVE with 2 MINOR deferred-to-M3 notes). Suite: 152 passed, 1 deselected. Dev `.env` sets `YNAB_SYNC_ENABLED=true` / `YNAB_DRY_RUN=false` (token reaches only the test budget); code defaults remain safe-off. A standing checker agent exists at `.claude/agents/checker.md` — note: new agent definitions register at session start, so spawn it via subagent_type "checker" from the NEXT session on (this session used general-purpose + charter file, equivalent).
- **2026-06-10** — Refunds are supported end-to-end: inflow transactions must be representable; memo language like "Returning X" (wording flexible). Replaces `total <= 0` rejection with a designed refund path in M1/M2.
- **2026-06-10** — Delete-recreate is prohibited: when YNAB ignores split-structure updates, leave the YNAB transaction untouched and flag the receipt for manual fixing (at minimum for bank-linked transactions; default never delete-recreate).
- **2026-06-10** — Current YNAB token sees only the dev test budget `testplandevelopmentonly` (verified live). Live dev writes hit only this budget. Production uses a different, human-managed token. `ynab_sync_enabled`/`dry_run` still required (M0) so the safety model survives a production token.
- **2026-06-10** — Live validation loop is required practice after sync-affecting changes: verify in the real test budget via YNAB API (memo `[receipt_id:]` marker) and/or Playwright YNAB UI login (`YNAB_TEST_USER_EMAIL`/`YNAB_TEST_USER_PASSWORD`); also load + visually inspect the local webapp via Playwright.
- **2026-06-10** — Gemini extraction fixed (google-genai 1.30.0→1.75.0; thinking-config feature-detection + broadened fallback); live integration test passed, 112/112.
- **2026-06-10** — Yolo aliases in `scripts/dev-env.sh` are kept and will be committed (user-accepted risk; token is test-budget-only).
- **2026-06-10** — Model delegation: Fable directs, Opus plans, Sonnet implements.

## Hard Rules (NEVER)
- Never make production YNAB writes autonomously.
- Never enable sync (`ynab_sync_enabled`) against a non-test token.
- Never read, print, commit, or paste secret values (tokens, passwords); refer to env vars by name only.
- Never commit anything under `plans/`.
- Never disable, skip, or weaken tests to make a gate pass.
- Never force-push, delete YNAB data, or run destructive migrations without a backup.

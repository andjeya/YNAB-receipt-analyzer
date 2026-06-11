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

### M1 — Money invariants ✅ COMPLETE 2026-06-11 (checker: FINDINGS → all resolved)
- [x] Single conversion path — float-truncation sites replaced; AST-based guard test (catches nested-paren/round()/1000.0 variants).
- [x] Exact milliunit-sum invariant — enforced in `_build_sync_transaction_payload` (raises) AND mirrored in `validate_payload` (UI gate now agrees with builder; no $0.01 blessing).
- [x] Refund/inflow support — `transaction_kind` purchase|refund on ValidationPayload + extraction contracts; amounts positive internally, sign only at YNAB boundary; deterministic "Return: " memo prefix; adopt-from-YNAB maps sign→kind, mixed-sign flags NEEDS_REVIEW (no abs() corruption); money.py rejects negatives; `ValidationSplit.amount ge=0`; minimal frontend kind selector. Purchase byte-identity verified by checker.
- [x] Allocation pin fixes — recompute can NEVER mutate total_amount; stale main-lane pins cleared; all-pinned shortfall warns; discounts subtract from weights (floor 0).
- Checker finding deferred to M4: same-timestamp purchase/refund collides on duplicate signature → show as near-match note (kind-differing), not duplicate-candidate.

### M2 — Sync idempotency ✅ COMPLETE 2026-06-11 (checker: FINDINGS incl. 1 BLOCKER → all resolved)
- [x] `import_id` = `RA:1:` + 31 uuid hex (≤36 chars, deterministic per receipt, amount/date-independent) on every create; stripped from PUT bodies. Duplicate-import_id 409 (YNAB's real single-create dedupe contract, per OpenAPI spec) resolves idempotently via `_create_transaction_idempotent` (list + match import_id/memo marker) — checker BLOCKER fixed; force_create documented as never-double-creating.
- [x] Endpoint atomic claim (conditional UPDATE → 409 `sync_in_progress`; rollback on enqueue failure) + worker claim on unique `idempotency_key` row (fresh RUNNING → skip; stale → reclaim). Gate ordering verified: no path strands SYNCING.
- [x] Evidence preserved on retry (only error_text cleared); verify-before-create via `get_transaction`; post-POST bookkeeping isolated (write result commits first; gamification failure → incident + `bookkeeping_ok=False`, never FAILED). No transient SYNCED window (final status in commit-1).
- [x] Delete-recreate REMOVED — structure-ignored updates leave YNAB untouched, receipt → NEEDS_REVIEW with manual-fix reason; `delete_transaction` never called anywhere (mock-level invariant test).
- [x] Reconciliation amount-drift → pull + NEEDS_REVIEW flag + correction/fire, never push; `_split_signature` deliberately stays amount-blind (regression-tested).
- [x] Stuck-job reset also FAILs stale RUNNING sync rows (receipt/row coherence).

### M4 — Workflow completeness ✅ COMPLETE 2026-06-11 (checker: FINDINGS incl. 1 BLOCKER → all resolved)
- [x] Original Scan pane: root cause = headless-Chromium can't render PDF iframes (screenshot artifact, not a real break); hardened with `<object>` + loading/error states + Open/Download links.
- [x] Artifact line-item rows hidden in twin read mode (`isRealLineItem`); de-emphasized (not red) in edit mode; discounts recolored emerald. (Note: this file was clobbered by concurrent agents and re-applied by hand — process lesson: never run two implementers on overlapping frontend files on a shared tree; isolate or partition.)
- [x] Allocation board: KeyboardSensor + a11y announcements, lane dollar totals, pin badges with unpin, undo (pre-action snapshot), "Refresh from twin" staleness action (client rebuild, pins preserved by lane). Workspace structurally cannot mutate total_amount (M1 invariant holds).
- [x] Kind-aware near-duplicate (M1 deferral): different-kind signature collision downgrades to non-blocking near-match — BUT blocks whenever ANY same-kind match exists in the pool (checker BLOCKER fix: heterogeneous-pool slip-through closed, mixed-pool regression test added).
- Near-dup (date+total, no time) warning: NOT implemented this round — separate signature, deferred to a future pass.

### M3 — Approval UX ✅ COMPLETE 2026-06-11 (checker: FINDINGS 1 MAJOR + 5 MINOR → all resolved)
- [x] Sync preview/confirm dialog (bank register, hand-rolled a11y Dialog primitive w/ focus trap) — signed payload, account names, mode badge (DRY RUN / LIVE→budget / SYNC DISABLED via new GET /api/config), create-vs-update intent labeling + flag-color branch (M0 notes addressed), last-dry-run reference payload, confirm gated on ALL readiness errors + dirty/autosave; syncMutation reachable ONLY via dialog confirm. Twin confirmation now ALSO enforced server-side (400 twin_unconfirmed; twin-less receipts exempt, mirroring client).
- [x] Signed amounts everywhere (Math.abs removed from money displays; refunds +$ emerald, purchases −$ ink; thousands separators; magnitude formatter for duplicate cards); transaction_kind on ReceiptSummary (batched query, no N+1); JS milliunit mirror now string-safe ROUND_HALF_UP (0.5005→501 parity with money.py).
- [x] Toast layer (aria-live, reduced-motion) + parsed ApiError messages + onError on all 13 mutations; ingest-scan success counts toast; twin warnings visible in read mode; aggregated "Resolve before syncing" strip adjacent to action bar.

### M4 — Workflow completeness
- [ ] Fix "Original Scan" pane rendering empty on receipt detail (observed on every receipt in 2026-06-10 live review — reviewer cannot compare against source). [parallel agent owns this]
- [x] Hide extraction artifact rows (e.g. bare `0000370179/1695152 · 0×` lines) from the twin line-item display instead of rendering them in red. — `isRealLineItem` predicate in receipt-twin.ts; subtotal/total hidden; zero/no-description artifacts hidden; de-emphasized in edit mode; 8 unit tests.
- [x] Allocation board polish (pin badges, lane totals, keyboard DnD sensor, undo). — KeyboardSensor+sortableKeyboardCoordinates; LaneColumn shows dollar total and pin badge; undo affordance 6s; screen-reader announcements; aria-roledescription on drag items.
- [x] Twin staleness refresh action. — "Refresh allocation from twin" button in warnings section; client-side buildFallbackWorkspace + pin preservation; wired in receipt-detail.tsx.
- [ ] Ingest-scan result feedback.
- [x] Near-duplicate (date+total, no time) warning — implemented as kind-aware near-match: refund matching purchase signature → NEEDS_REVIEW + near_match_reason note (non-blocking); 3 new backend tests.

### M5 — E2E harness ✅ COMPLETE 2026-06-11
- [x] Add `data-testid` attributes to interactive controls (twin confirm buttons, sync bar, allocation lanes) — `confirm-date-time`, `confirm-total`, `sync-button`, `account-select`, `lane-${laneId}`, `alloc-item-${source_index}`, `recompute-keep`, `recompute-discard`.
- [x] Playwright 1.60.0 + mocked backend — `playwright.config.ts` (testDir e2e, webServer `next dev --port 3001`, INTERNAL_API_ORIGIN=127.0.0.1:9); `e2e/fixtures.ts` (buildStandardRouter, all receipt states, config variants).
- [x] "Cannot approve unsafe" approval-gate suite — `e2e/sync-safety.spec.ts`, 12/12 tests passing 15.2s. No safety gaps found.

### Live validation #2 (2026-06-11, passed — post-M6, full polished flow)
Kroger receipt (`2026-06-10 19.43.20.pdf`) — the discount/tax edge case. Ground truth: SodaStream carbonator $31.99 list − $15.00 Kroger coupon + $1.02 tax = $18.01, 2026-06-08. **Coupon handled correctly**: extraction typed the $15 line as `discount`, subtotal $16.99, total $18.01 (NOT $31.99); dry-run persisted amount -18010 + import_id `RA:1:62da5ad1…` with ZERO YNAB writes (API-verified); live sync created YNAB `f1d87e2e` = −18010 milliunits, Kroger/Groceries/approved=false/blue, matching ground truth exactly. Twin-confirm data-testids resolved the selector ambiguity (no API fallback). Preview dialog showed −$18.01 (outflow) + twin checks; Snappy visible in header. Receipt id `62da5ad1-645b-4ed6-9e98-f38cb1707828`.

FOLLOW-UPS surfaced (not blockers):
- **Mode-badge vs worker-env decoupling:** the preview badge reflects the SERVER `ynab_dry_run` config flag; a per-worker `YNAB_DRY_RUN=true` process override makes the run dry while the badge still says LIVE. Normal operation (no override) they agree, but the badge should ideally reflect the actual execution mode. Consider surfacing worker dry-run state or removing the per-process override path.
- Mode badge shows budget UUID, not friendly name (budget name not cacheable without a network call — known M3 limitation).
- Minor GUI robustness: hamburger "Check ingestion queue" didn't auto-fire under Playwright (used API); account-set occasionally lost the category (re-set via /draft). Worth a GUI hardening pass.
- Test budget now holds 3 app-created transactions (Costco $119.19, TJ $25.62, Kroger $18.01) — all unapproved/flagged, for human review/cleanup.

### Live validation #1 (2026-06-11, passed — post-M2)
Trader Joe's receipt (`2026-06-10 19.43.06.pdf`, ground truth $25.62 / 2026-06-07 / 7 lines) through the full pipeline at commit cdc32e4: dry-run phase persisted the exact create-intent payload (amount -25620, import_id `RA:1:0644b3e2…` 36 chars, approved=false, blue flag) with ZERO YNAB writes (API-verified); same sync row then transitioned dry_run→live and created YNAB transaction `13b59672` matching ground truth on every field. No worker/api log anomalies. GUI twin-confirm still needs data-testid (M5). Receipt id `0644b3e2-c24e-4d56-8aa9-3c8a6ab2769d`.

### Live E2E baseline (2026-06-10, passed)
Full pipeline validated against the real test budget: receipt `2026_02_23_13_09_21.pdf` → ingest (GUI scan button) → Gemini extraction (all 6 line items + total exact vs human-read ground truth) → GUI sync click → YNAB transaction `16b484ec` (−119190 milliunits, Groceries, Anna Venture X, approved=false, blue flag, memo marker). Confirms: money path exact ×1000 with correct sign; `import_id` absent (M2 gap live-confirmed). Two fresh June scans (`receipt_examples/2026-06-10 19.43.*.pdf`) are reserved as the next live-validation inputs.

### M6 — Delight pass ✅ COMPLETE 2026-06-11 (checker: FINDINGS, all MINOR → resolved)
- [x] Snappy mascot — 5-pose inline SVG (idle/happy/concerned/celebrating/asleep), cute receipt character w/ catchlit eyes + cheeks + feet (Fable-reviewed + refined); `deriveSnappyPose` pure logic; header + empty-state placement.
- [x] Celebrations tied 1:1 to incentives, firing on VERIFIED state edges (ref-guarded against 6s-poll re-fire — checker confirmed no spurious fire on opening an already-synced receipt): accuracy = sync→synced edge (Snappy celebrate + "Clean sync" toast); timeliness = green-tile sprout; consistency = streak-milestone pose swap. Empty queue = asleep Snappy "All caught up!". No celebration on the bank-register confirm dialog.
- [x] Deleted orphan gamification-dashboard.tsx + dead GameChallenge types (kept GameWindow; backend `challenges` JSON harmlessly ignored).
- [x] Migrated WaterSpendModal/DebugPanel/GameIncidentModal to the accessible Dialog primitive (incident stays blocking via no-op onClose; Acknowledge reachable in focus trap).
- [x] A11y sweep: focus-visible on all buttons, contrast bumps, tooltip→aria-label, label htmlFor/id, combobox ARIA (useId-unique listbox ids, aria-controls gated on open). Arrow-key combobox nav noted as follow-up.

### M6 — Delight pass (original checklist)
- [ ] Completion celebrations, batch review flow.
- [ ] `gamification-dashboard.tsx` decision (wire or delete).
- [ ] a11y: dialog semantics, focus traps, contrast.

### F1 — Learned card→account mapping (feature, 2026-06-11) — ✅ COMPLETE
User feature request before the next milestone. A persistent `card_last_four → ynab_account` map (per budget) that auto-suggests the account for known cards (incl. Apple Pay virtual cards, which have a stable distinct last-4). Settled decisions: LEARN ON SYNC (non-blocking upsert in post-sync bookkeeping); learned mapping ALWAYS WINS at extraction time (overrides AI guess, still user-editable, ignored if mapped account deleted); key = normalized trailing 4 digits (null for cash); many-cards→one-account allowed; debug-mode-only admin panel (Card last-4 → Account name, view/edit/delete).
- [x] Inc1 backend ✅ 2026-06-11 (checker: FINDINGS incl. 1 MAJOR → resolved). `card_account_mappings` table + migration 0008 (idempotent); `card_last_four` on 3 extraction contracts + prompts (normalization strips trailing decimal + ASCII-only → no "5830.0"→"8300" mis-key); services/card_mapping.py (lookup w/ account-in-cache guard = override can't push a sync-invalid account; upsert last-write-wins, no full-rollback so caller's bookkeeping survives a race); override wired at the single `_validate_ynab_payload` chokepoint (all 3 extraction paths; NOT on draft-save so user edits never clobbered); non-fatal upsert on sync; debug API (404 when off). 342 tests pass (+74).
- [x] Inc2 frontend ✅ 2026-06-11 (commit bdb8ca2). Debug-only CardMappingPanel Dialog: two columns Card(last-4)→Account, inline account select per row, add-row footer, delete, stale/deleted-account amber handling, onError toasts, a11y labels. Reachable from the hamburger menu only when debug tools enabled. Build/71 unit/14 e2e green.
- [x] Live proof ✅ 2026-06-11: deterministic override verified (lookup_account_for_card returns mapped account for known card `7777`→Andjey Venture X 1198, `None` for unknown `0000`); debug API PUT/GET/DELETE + GUI panel edit/add/delete all persist (screenshots /tmp/f1-live/).
- [x] Extraction confirmed live ✅ 2026-06-11: a FRESH unified extraction of `2026_02_23_13_10_28.pdf` (TJ Apple Pay, masked PAN `************7992`, Type: MOBILE) returns `card_last_four='7992'` correctly with the current prompt. The earlier "learn-on-sync wrote null" was NOT an extraction failure and NOT a code bug: that receipt's stored extraction is from 2026-03-03 (pre-feature, no card field), and content file-hash dedupe blocked re-ingestion, so the live-proof re-synced the stale March extraction. Gemini + the pipeline work on a fresh run. NOTE the receipt shows the Apple Pay device last-4 (7992), not the physical card 1198 (which the account is named for) — the canonical case the feature targets.
- OPERATIONAL FOLLOW-UP (not a bug): pre-feature receipts have `card_last_four=null` until re-extracted; file-hash dedupe blocks naive re-ingest of the same file. A "re-extract existing receipt" path would let the backlog learn mappings. Defer unless wanted.
- Follow-ups (deferred, NOT blockers): (1) per-receipt "Remembered from last time" account hint — needs a backend flag on the receipt detail response; (2) Snappy SVG renders unbounded if CSS fails to load (a broken mid-run dev build showed a giant empty-state mascot — purely a missing-CSS artifact, correct once built; optional 1-line hardening = explicit svg width/height fallback).

### F2 — Status-bucketed review queue (UX, 2026-06-11) — ✅ COMPLETE
User concern: receipts ordered newest-first by ingestion could bury an older UNREVIEWED receipt below a new batch → missed. Decision (AskUserQuestion): split by STATUS (actionable vs done), not date; segmented tabs on one page; To Review default; oldest/most-overdue first.
- [x] Three tabs replace the flat status chips: **To Review** (needs_review/duplicate_review/error_*) default, **Processing** (ingested/extracting/syncing), **History** (synced). Each with a count badge; game header unchanged above. To Review + Processing sorted OLDEST-FIRST (new scans queue at the bottom — the anti-miss fix); History newest-first. Per-card "Xd waiting" in To Review. Empty states: To Review → asleep Snappy "All caught up!"; Processing → "Nothing processing."; History → "No synced receipts yet." Pure bucketing logic in src/lib/receipt-buckets.ts (17 unit tests: mapping + sort + count invariant). Single fetch + client partition (no /stats dependency for counts). 88 unit, 14 e2e, build clean.
- [x] Live-validated ✅ 2026-06-11 on the fresh DB: ingested 3 receipts (TJ 18:27 → Kroger 18:28 → REI 18:29); To Review showed them OLDEST-FIRST (TJ top) with "1m waiting", count (3). FRESH extraction captured card_last_four = 7992 (TJ Apple Pay), 7992 (Kroger), 1198 (REI) — confirms the earlier null was stale pre-feature data, and 1198 is real (on the REI receipt / physical card, "different receipts" as the user said). Synced the TJ receipt → it moved To Review (3→2) into History (0→1); counts: To Review 2 · Processing 0 · History 1; Snappy greeting "2 receipts need your eyes" tracks the To-Review count. Screenshots /tmp/tabs-live/. (Live-proof agent died mid-run when the parent process exited; completed the after-sync capture manually.)
F2 ✅ COMPLETE.
- Note: dev DB wiped 2026-06-11 (user-authorized — old test data lacked card_last_four; reset re-applied migration 0008 live). Clean slate.

### M7 — Test-budget validation → production (human-gated)
- [x] Full live validation against `testplandevelopmentonly` (API + Playwright UI) — THREE passes: baseline (Costco, pre-M2), #1 (TJ, post-M2 dry-run+import_id), #2 (Kroger, post-M6 full polished flow incl. coupon/discount edge case). All matched human-read ground truth exactly.
- [ ] **Production enablement — BLOCKED ON HUMAN. Never autonomous.** Requires: a separate production YNAB token (current token reaches only the test budget), human review of the 3 test-budget transactions, and explicit sign-off. The loop STOPS here.

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

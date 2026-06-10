# YNAB Write-Path Safety Model

How writes to YNAB are kept safe. Anchors are `file:line` at audit time
(2026-06-10) — re-verify before relying on them.

## Current state (honest)
- **No kill-switch yet.** The only gate is the presence of `ynab_access_token`
  (`config.py:57-58`). M0 adds `ynab_sync_enabled: bool = False` (default off) +
  `dry_run` (payload built/persisted/previewable, no POST).
- **No idempotency yet.** No YNAB `import_id` is set; local idempotency key
  dedupes DB rows only. M2 adds `import_id` on every create.
- **Token scope (verified live 2026-06-10):** the current `.env` token sees only
  the dev test budget `testplandevelopmentonly`. Production uses a different,
  human-managed token. The flags above are still required so the safety model
  survives a production token.

## Target sync state machine (M2)
- **Status guard + row lock:** `POST /receipts/{id}/sync` (`api/receipts.py:944-997`)
  must reject/serialize when already SYNCING (DB row lock), so a double-click can
  never enqueue two creating jobs.
- **Exactly-one-create:** one receipt → at most one YNAB transaction. Guaranteed
  by the SYNCING guard plus `import_id`.
- **Retry preserves evidence:** a retry must not wipe `created_transaction_id`
  (current bug `services/ynab.py:846-849`); the recorded transaction id is the
  proof a create succeeded and is how retries avoid double-posting.
- **Post-POST bookkeeping failures must not mark FAILED:** if the POST succeeded
  but later bookkeeping (e.g. gamification) throws (`ynab.py:746-757`), the sync
  is **successful** — record the transaction id; never mark FAILED (FAILED →
  retry → double-post).

## Delete-recreate policy — PROHIBITED
- The app must **not** delete-then-recreate a YNAB transaction
  (current window: `_update_or_replace_transaction`, `ynab.py:454-482`).
- When YNAB ignores a split-structure update (it can refuse structural edits on
  bank-imported transactions), **leave the YNAB transaction untouched** and
  **flag the receipt for manual fixing**.
- Default: never delete-recreate. At minimum, never for bank-linked transactions.
- Rationale (decision 2026-06-10): deleting can destroy bank-import/cleared/
  reconciled state and a crash mid-window loses the transaction entirely.

## Reconciliation policy — pull/flag, never push (provisional)
- Reconciliation is currently amount-blind (`reconciliation.py:42-51` ignores
  top-level amount) → YNAB-side amount edits get silently overwritten on re-sync.
- Target: when the YNAB amount differs from the receipt, **pull/flag** for human
  review; **never push** the receipt amount over a human's YNAB edit.
- Provisional pending objection (decision 2026-06-10).

## Test-budget vs production
- **Dev (current):** token = test budget only. Live writes during development
  hit only `testplandevelopmentonly`. Autonomous loop may write here once M0–M2
  land and pass their gates.
- **Production:** different, human-managed token. Enablement is a **human-only
  checklist** (M7): set the production token, set `ynab_sync_enabled=true`
  deliberately, confirm budget id. Never performed autonomously.

## Live validation loop (required after sync-affecting changes)
- **API check:** `GET /budgets/{id}/transactions` with the `.env` token; confirm
  the `[receipt_id:]` memo marker (and signed amount) on the expected transaction.
- **UI check:** Playwright login to the YNAB web UI using `YNAB_TEST_USER_EMAIL`
  / `YNAB_TEST_USER_PASSWORD` from untracked `.env` (env var **names** only —
  never write the values into any file).
- **Local webapp check:** load + visually inspect the app via Playwright
  (Chromium installed) — not just unit tests.

## Never autonomous
- Production YNAB writes.
- Enabling `ynab_sync_enabled` against a non-test token.
- Deleting YNAB data / delete-recreate.
- Touching secrets (reading, printing, committing token/password values).
- Destructive migrations without a backup; force pushes.

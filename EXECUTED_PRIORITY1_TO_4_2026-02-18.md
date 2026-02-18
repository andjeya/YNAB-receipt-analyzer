# Executed Priority 1-4 (2026-02-18)

## Checkpoint A: Correctness economy + reconciliation backend
- Added schema migration `0003_correctness_economy` with:
  - `game_correctness_state`
  - `receipt_corrections`
  - `ynab_reconciliation_runs`
- Added correctness economy service:
  - water earn/spend
  - fire add/extinguish
  - board burn reset threshold logic
  - recompute from event history
- Added YNAB reconciliation service:
  - scans last N days (default 90)
  - compares latest synced payload vs live YNAB category/splits
  - records correction metadata per receipt
  - adds fire units for detected mismatch
  - applies 24h overdue resync penalties for brown receipts
- Added queue/worker/scanner plumbing for twice-daily reconciliation cadence.
- Added manual endpoints:
  - `POST /api/game/reconcile`
  - `POST /api/game/correctness/recompute`

## Checkpoint B: UI and game loop redesign
- Reworked game dashboard around sketch style:
  - header with streak/token/menu
  - 27-slot bi-week board (oldest -> newest)
  - fire row + water row
  - scrollable transaction list
- Added celebratory marker on 30-validation intervals.
- Added receipt state and fire iconography.
- Added queue/detail correction shading and correction copy display.
- Added reject flow button in detail UI to require resync.

## Checkpoint C: Tests and hardening
- Added backend correctness economy tests.
- Added reconciliation helper tests.
- Added frontend receipt ID helper + unit tests.
- Added category guidance example and private guidance gitignore handling.

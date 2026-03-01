#!/usr/bin/env bash

set -euo pipefail

CONFIRM_PHRASE="DELETE-ALL-DATABASE"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

echo "DANGER: destructive reset requested."
echo
echo "This script will:"
echo "1) Stop local app processes (api, worker, scanner, frontend)."
echo "2) Delete all receipt pipeline data from SQLite:"
echo "   - receipts"
echo "   - extraction_runs"
echo "   - validations"
echo "   - ynab_sync"
echo "   - timing_metrics"
echo "   - game_receipt_states"
echo "   - game_events"
echo "   - game_streaks"
echo "   - game_tokens"
echo "   - game_correctness_state"
echo "   - game_debug_seed"
echo "   - game_incidents"
echo "   - receipt_corrections"
echo "   - ynab_reconciliation_runs"
echo "3) Delete stored receipt files under data/receipts."
echo "4) Delete files in data/ingest."
echo "5) Flush Redis DB at REDIS_URL (or redis://host.docker.internal:6379/0)."
echo
echo "Type ${CONFIRM_PHRASE} to continue."
read -r -p "> " typed

if [[ "${typed}" != "${CONFIRM_PHRASE}" ]]; then
  echo "Confirmation phrase mismatch. Aborting."
  exit 1
fi

echo "[1/5] Stopping local app processes..."
pkill -f "uvicorn app.main:app|apps/server/worker/worker.py|apps/server/worker/scanner.py|next start" || true

if [[ -f "data/app.db" ]]; then
  echo "[2/5] Clearing SQLite receipt data..."
  sqlite3 data/app.db "
  DELETE FROM extraction_runs;
  DELETE FROM validations;
  DELETE FROM ynab_sync;
  DELETE FROM timing_metrics;
  DELETE FROM game_receipt_states;
  DELETE FROM game_events;
  DELETE FROM game_streaks;
  DELETE FROM game_tokens;
  DELETE FROM game_correctness_state;
  DELETE FROM game_debug_seed;
  DELETE FROM game_incidents;
  DELETE FROM receipt_corrections;
  DELETE FROM ynab_reconciliation_runs;
  DELETE FROM receipts;
  "
else
  echo "[2/5] data/app.db not found; skipping SQLite cleanup."
fi

echo "[3/5] Deleting stored receipt files..."
find data/receipts -type f -delete 2>/dev/null || true

echo "[4/5] Deleting ingest files..."
find data/ingest -type f -delete 2>/dev/null || true

echo "[5/5] Flushing Redis DB..."
redis_url="${REDIS_URL:-redis://host.docker.internal:6379/0}"
if command -v redis-cli >/dev/null 2>&1; then
  redis-cli -u "${redis_url}" FLUSHDB >/dev/null
  echo "Redis flushed at ${redis_url}"
else
  echo "redis-cli not found; skipping Redis flush."
fi

echo
echo "Reset complete."
echo "Run 'bash scripts/dev-up.sh' to start services again."

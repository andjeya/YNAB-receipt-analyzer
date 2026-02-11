#!/usr/bin/env bash
# run set -a; source .env; set +a
# run ./backup_ynab_budget.sh <budget_id>
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <budget_id> [output_dir]" >&2
  exit 1
fi

if [[ -z "${YNAB_ACCESS_TOKEN:-}" ]]; then
  echo "YNAB_ACCESS_TOKEN is not set. Load it from your environment (for example via .env) and retry." >&2
  exit 1
fi

BUDGET_ID="$1"
OUTPUT_DIR="${2:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$OUTPUT_DIR"
OUT_FILE="${OUTPUT_DIR}/ynab_budget_${BUDGET_ID}_${TIMESTAMP}.json"

curl --fail --show-error --silent \
  -H "Authorization: Bearer ${YNAB_ACCESS_TOKEN}" \
  -H "Accept: application/json" \
  "https://api.ynab.com/v1/budgets/${BUDGET_ID}" \
  > "$OUT_FILE"

echo "Saved raw YNAB budget snapshot to: ${OUT_FILE}"

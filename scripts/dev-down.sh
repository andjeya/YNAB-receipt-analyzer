#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

echo "Stopping local dev services..."

# Match the processes started by scripts/dev-up.sh and direct manual starts.
pkill -f "uvicorn app.main:app|apps/server/worker/worker.py|apps/server/worker/scanner.py|apps/server/frontend/.next/standalone/server.js|next-server|next start" || true

sleep 0.3

echo
echo "Port check:"
if ss -ltnp | grep -E ":3000|:8000" >/dev/null 2>&1; then
  ss -ltnp | grep -E ":3000|:8000"
else
  echo "  ports 3000 and 8000 are free"
fi

echo
echo "Done."

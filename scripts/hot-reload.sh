#!/usr/bin/env bash
# Toggle hot-reload for the api and frontend services.
# Worker and scanner are left untouched.
#
# Usage:
#   ./scripts/hot-reload.sh --on      # next dev + uvicorn --reload
#   ./scripts/hot-reload.sh --off     # next build + standalone server + plain uvicorn
#   ./scripts/hot-reload.sh --status  # show current mode

# with hot reload on
# ----------------------------------------------------------
# Change type	            Action needed
# ----------------------------------------------------------
# Frontend .tsx/.css	    None — browser updates live
# Backend .py	            None — uvicorn auto-reloads
# New pip/npm dependency	dev-down + dev-up (unavoidable)
# .env change	            dev-down + dev-up
# DB migration	            Run alembic upgrade head once

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "${repo_root}"

if [[ ! -f "${repo_root}/scripts/dev-env.sh" ]]; then
  echo "Missing scripts/dev-env.sh" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${repo_root}/scripts/dev-env.sh"

run_dir="${repo_root}/.run"
log_dir="${run_dir}/logs"
pid_dir="${run_dir}/pids"
flag_file="${run_dir}/hot-reload.flag"
mkdir -p "${log_dir}" "${pid_dir}"

usage() {
  echo "Usage: $0 [--on|--off|--status]" >&2
  exit 1
}

start_service() {
  local name="$1"
  local command="$2"
  local logfile="${log_dir}/${name}.log"
  local pidfile="${pid_dir}/${name}.pid"

  echo "[start] ${name}"
  nohup bash -lc "cd '${repo_root}' && source scripts/dev-env.sh >/dev/null && ${command}" >"${logfile}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${pidfile}"
  sleep 0.5

  if kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[ok] ${name} pid=${pid}  log=${logfile}"
  else
    echo "[fail] ${name} failed to start; check ${logfile}" >&2
    exit 1
  fi
}

stop_frontend() {
  pkill -f "apps/server/frontend/.next/standalone/server.js" 2>/dev/null || true
  pkill -f "next-server"                                    2>/dev/null || true
  pkill -f "next dev"                                       2>/dev/null || true
  pkill -f "next start"                                     2>/dev/null || true
}

stop_api() {
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
}

show_status() {
  if [[ -f "${flag_file}" ]]; then
    echo "hot-reload: ON  (next dev + uvicorn --reload)"
  else
    echo "hot-reload: OFF (next build + standalone server + plain uvicorn)"
  fi
}

case "${1:-}" in
  --status)
    show_status
    exit 0
    ;;

  --on)
    echo "==> Enabling hot reload"
    echo "[stop] api"
    stop_api
    echo "[stop] frontend"
    stop_frontend
    sleep 0.5

    start_service "api" \
      "PYTHONPATH=apps/server/backend:apps/server/shared uvicorn app.main:app \
         --host 0.0.0.0 --port 8000 \
         --reload --reload-dir apps/server/backend --reload-dir apps/server/shared"

    start_service "frontend" \
      "cd apps/server/frontend && npm run dev -- --hostname 0.0.0.0 --port 3000"

    touch "${flag_file}"
    echo
    echo "Hot reload ON"
    echo "  Frontend (HMR): http://localhost:3000"
    echo "  API (auto-reload on .py save): http://localhost:8000"
    echo "  Logs: ${log_dir}/api.log"
    echo "        ${log_dir}/frontend.log"
    ;;

  --off)
    echo "==> Disabling hot reload (production mode: standalone frontend)"
    echo "[stop] api"
    stop_api
    echo "[stop] frontend"
    stop_frontend
    sleep 0.5

    start_service "api" \
      "PYTHONPATH=apps/server/backend:apps/server/shared uvicorn app.main:app --host 0.0.0.0 --port 8000"

    echo "[build] frontend"
    (cd apps/server/frontend && npm run build)

    start_service "frontend" \
      "cd apps/server/frontend && HOSTNAME=0.0.0.0 PORT=3000 node .next/standalone/server.js"

    rm -f "${flag_file}"
    echo
    echo "Hot reload OFF"
    echo "  Frontend: http://localhost:3000"
    echo "  API:      http://localhost:8000"
    echo "  Logs: ${log_dir}/api.log"
    echo "        ${log_dir}/frontend.log"
    ;;

  *)
    usage
    ;;
esac

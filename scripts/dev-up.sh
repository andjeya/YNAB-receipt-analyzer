#!/usr/bin/env bash

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
mkdir -p "${log_dir}" "${pid_dir}"

start_redis_if_needed() {
  if [[ "${REDIS_URL}" != "redis://host.docker.internal:6379/0" ]]; then
    return
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; cannot ensure Redis container is running for REDIS_URL=${REDIS_URL}" >&2
    return
  fi

  if docker inspect ynab-receipt-redis >/dev/null 2>&1; then
    if [[ "$(docker inspect -f '{{.State.Running}}' ynab-receipt-redis 2>/dev/null)" == "true" ]]; then
      return
    fi
    docker start ynab-receipt-redis >/dev/null
    return
  fi

  echo "Starting Redis container: ynab-receipt-redis"
  docker run -d --name ynab-receipt-redis -p 6379:6379 redis:7-alpine >/dev/null
}

is_running_pattern() {
  local pattern="$1"
  pgrep -f "${pattern}" >/dev/null 2>&1
}

start_service() {
  local name="$1"
  local pattern="$2"
  local command="$3"
  local logfile="${log_dir}/${name}.log"
  local pidfile="${pid_dir}/${name}.pid"

  if is_running_pattern "${pattern}"; then
    echo "[skip] ${name} already running"
    return
  fi

  echo "[start] ${name}"
  nohup bash -lc "cd '${repo_root}' && source scripts/dev-env.sh >/dev/null && ${command}" >"${logfile}" 2>&1 &
  local pid=$!
  echo "${pid}" >"${pidfile}"
  sleep 0.5

  if kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[ok] ${name} pid=${pid} log=${logfile}"
  else
    echo "[fail] ${name} failed to start; check ${logfile}" >&2
  fi
}

start_redis_if_needed

start_service \
  "api" \
  "uvicorn app.main:app --host 0.0.0.0 --port 8000" \
  "PYTHONPATH=apps/server/backend:apps/server/shared uvicorn app.main:app --host 0.0.0.0 --port 8000"

start_service \
  "worker" \
  "python apps/server/worker/worker.py" \
  "PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/worker.py"

start_service \
  "scanner" \
  "python apps/server/worker/scanner.py" \
  "PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/scanner.py"

if ! is_running_pattern "next-server"; then
  echo "[build] frontend (production mode, no hot reload)"
  (cd apps/server/frontend && npm run build)
fi

start_service \
  "frontend" \
  "next-server" \
  "cd apps/server/frontend && npm run start -- --hostname 0.0.0.0 --port 3000"

echo
echo "Services:"
echo "  Frontend: http://localhost:3000"
echo "  API:      http://localhost:8000"
echo "  Health:   http://localhost:8000/healthz"
echo
echo "Log files:"
echo "  ${log_dir}/api.log"
echo "  ${log_dir}/worker.log"
echo "  ${log_dir}/scanner.log"
echo "  ${log_dir}/frontend.log"

#!/usr/bin/env bash

# Keep strict mode local to this script when sourced from interactive shells.
__ynab_load_dev_env() {
  local -
  set -euo pipefail

  # This script must be sourced so exported vars are available to subsequent commands.
  if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "Run this as: source scripts/dev-env.sh" >&2
    exit 1
  fi

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "${script_dir}/.." && pwd)"
  env_files=("${repo_root}/.env" "${repo_root}/.env.local")

  load_env_file() {
    local file_path="$1"
    if [[ ! -f "${file_path}" ]]; then
      return
    fi

    # Parse dotenv safely (supports spaces and comments) and export into current shell.
    eval "$(
      ENV_FILE="${file_path}" python - <<'PY'
import os
import shlex
from dotenv import dotenv_values

for key, value in dotenv_values(os.environ["ENV_FILE"]).items():
    if value is None:
        continue
    print(f"export {key}={shlex.quote(value)}")
PY
    )"
  }

  if [[ ! -f "${env_files[0]}" ]]; then
    echo "Warning: ${env_files[0]} not found; continuing with current environment." >&2
  fi

  for file_path in "${env_files[@]}"; do
    load_env_file "${file_path}"
  done

  # If INGEST_DIR points to a host-only absolute path, remap to the devcontainer
  # bind target prepared by .devcontainer/scripts/prepare-host-mounts.sh.
  if [[ -f "/.dockerenv" && -n "${INGEST_DIR:-}" && ! -e "${INGEST_DIR}" && -e "/mnt/ingest-host" ]]; then
    export INGEST_DIR="/mnt/ingest-host"
  fi

  # Devcontainer default for reaching a Redis container started via Docker-outside-of-Docker.
  if [[ -z "${REDIS_URL:-}" ]]; then
    export REDIS_URL="redis://host.docker.internal:6379/0"
  elif [[ -f "/.dockerenv" && "${REDIS_URL}" == "redis://localhost:6379/0" ]]; then
    export REDIS_URL="redis://host.docker.internal:6379/0"
  fi

  if [[ -z "${NEXT_PUBLIC_API_BASE_URL:-}" || "${NEXT_PUBLIC_API_BASE_URL}" == "http://localhost:8000/api" ]]; then
    export NEXT_PUBLIC_API_BASE_URL="/api"
  fi

  # Pydantic parses list fields from env as JSON; normalize comma-separated CORS values.
  if [[ -n "${CORS_ORIGINS:-}" && "${CORS_ORIGINS}" != \[* ]]; then
    export CORS_ORIGINS="$(CORS_ORIGINS_RAW="${CORS_ORIGINS}" python - <<'PY'
import json
import os
origins = [item.strip() for item in os.environ["CORS_ORIGINS_RAW"].split(",") if item.strip()]
print(json.dumps(origins))
PY
)"
  fi

  echo "Loaded environment for YNAB Receipt Analyzer:"
  echo "  REDIS_URL=${REDIS_URL}"
  echo "  DATABASE_URL=${DATABASE_URL:-sqlite:///./data/app.db}"
  echo "  INGEST_DIR=${INGEST_DIR:-./data/ingest}"
  echo "  NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}"
  echo "  GEMINI_API_KEY=$([[ -n "${GEMINI_API_KEY:-}" ]] && echo set || echo missing)"
  echo "  YNAB_ACCESS_TOKEN=$([[ -n "${YNAB_ACCESS_TOKEN:-}" ]] && echo set || echo missing)"
  echo "  YNAB_BUDGET_ID=$([[ -n "${YNAB_BUDGET_ID:-}" ]] && echo set || echo missing)"
  echo "  YNAB_DEFAULT_ACCOUNT_ID=$([[ -n "${YNAB_DEFAULT_ACCOUNT_ID:-}" ]] && echo set || echo missing)"
}

__ynab_load_dev_env "$@"
unset -f __ynab_load_dev_env

# Convenience launchers for agent CLIs inside this devcontainer.
alias codex-yolo='codex --dangerously-bypass-approvals-and-sandbox'
alias claude-yolo='claude --dangerously-skip-permissions'

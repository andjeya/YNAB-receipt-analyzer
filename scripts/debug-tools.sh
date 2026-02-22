#!/usr/bin/env bash

set -euo pipefail

FLAG_PATH="${DEBUG_TOOLS_FLAG_PATH:-data/debug_tools_enabled.flag}"

usage() {
  cat <<'EOF'
Usage:
  scripts/debug-tools.sh on
  scripts/debug-tools.sh off
  scripts/debug-tools.sh status

Behavior:
  - `on` creates the debug flag file and enables in-app debug panel + debug APIs.
  - `off` removes the debug flag file and hard-disables debug panel + debug APIs.
  - `status` prints whether debug tools are enabled.
EOF
}

ensure_parent_dir() {
  mkdir -p "$(dirname "${FLAG_PATH}")"
}

case "${1:-}" in
  on)
    ensure_parent_dir
    touch "${FLAG_PATH}"
    echo "Debug tools: ON (${FLAG_PATH})"
    ;;
  off)
    rm -f "${FLAG_PATH}"
    echo "Debug tools: OFF (${FLAG_PATH})"
    ;;
  status)
    if [[ -f "${FLAG_PATH}" ]]; then
      echo "Debug tools: ON (${FLAG_PATH})"
    else
      echo "Debug tools: OFF (${FLAG_PATH})"
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac

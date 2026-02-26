#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
link_path="${repo_root}/.devcontainer/.devtools-host"
env_local="${repo_root}/.env.local"

read_env_value() {
  local env_key="$1"
  ENV_LOCAL="${env_local}" ENV_KEY="${env_key}" python3 - <<'PY'
import os
from pathlib import Path

env_local = Path(os.environ["ENV_LOCAL"])
env_key = os.environ["ENV_KEY"]
if not env_local.exists():
    raise SystemExit(0)

for raw in env_local.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != env_key:
        continue
    val = value.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    print(val.strip())
    break
PY
}

devtools_path="$(read_env_value "DEVTOOLS_DIR")"

if [[ -z "${devtools_path}" ]]; then
  echo "Warning: DEVTOOLS_DIR is not set in ${env_local}. Skipping link setup."
  exit 0
fi

if [[ -d "${devtools_path}" ]]; then
  rm -rf "${link_path}"
  ln -s "${devtools_path}" "${link_path}"
  echo "Linked ${link_path} -> ${devtools_path}"
else
  echo "Warning: Devtools not found at ${devtools_path}. Skipping link setup."
  if [[ -f "/.dockerenv" ]]; then
    echo "Hint: this shell is inside a container. If DEVTOOLS_DIR is a host path, run this script from the host and reopen the devcontainer."
  fi
fi

exit 0

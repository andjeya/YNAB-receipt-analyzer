#!/usr/bin/env bash

set -euo pipefail

mkdir -p "${HOME}/.codex" "${HOME}/.claude"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
env_local="${repo_root}/.env.local"
mount_link="${repo_root}/.devcontainer/.ingest-host"

ingest_dir="$(
  ENV_LOCAL="${env_local}" python3 - <<'PY'
import os
from pathlib import Path

env_local = Path(os.environ["ENV_LOCAL"])
if not env_local.exists():
    raise SystemExit(0)

for raw in env_local.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != "INGEST_DIR":
        continue
    val = value.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1]
    print(val.strip())
    break
PY
)"

rm -rf "${mount_link}"

if [[ -n "${ingest_dir}" && "${ingest_dir}" = /* ]]; then
  mkdir -p "${ingest_dir}"
  ln -s "${ingest_dir}" "${mount_link}"
else
  mkdir -p "${mount_link}"
fi

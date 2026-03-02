#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
frontend_dir="${repo_root}/apps/server/frontend"
build_dir="${frontend_dir}/.next"
standalone_dir="${build_dir}/standalone"
source_static_dir="${build_dir}/static"
target_next_dir="${standalone_dir}/.next"
target_static_dir="${target_next_dir}/static"
source_public_dir="${frontend_dir}/public"
target_public_dir="${standalone_dir}/public"

if [[ ! -d "${standalone_dir}" ]]; then
  echo "Missing ${standalone_dir}; run frontend build first." >&2
  exit 1
fi

if [[ ! -d "${source_static_dir}" ]]; then
  echo "Missing ${source_static_dir}; run frontend build first." >&2
  exit 1
fi

mkdir -p "${target_next_dir}"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "${source_static_dir}/" "${target_static_dir}/"
else
  mkdir -p "${target_static_dir}"
  cp -a "${source_static_dir}/." "${target_static_dir}/"
fi

if [[ -d "${source_public_dir}" ]]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${source_public_dir}/" "${target_public_dir}/"
  else
    mkdir -p "${target_public_dir}"
    cp -a "${source_public_dir}/." "${target_public_dir}/"
  fi
fi

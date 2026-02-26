#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

git -C "${repo_root}" config core.hooksPath githooks
echo "Configured repository hooks path: githooks"

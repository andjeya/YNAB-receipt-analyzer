#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[post-create] Python: $(python --version)"
echo "[post-create] Node: $(node --version)"
echo "[post-create] npm: $(npm --version)"

if [[ -f requirements.txt ]]; then
  echo "[post-create] Installing Python dependencies"
  pip install --upgrade pip
  pip install -r requirements.txt
fi

if [[ -f apps/server/frontend/package.json ]]; then
  echo "[post-create] Installing frontend dependencies"
  (cd apps/server/frontend && npm install)
fi

# Optional: CLI tools for agent workflows. Failures are non-fatal to avoid blocking setup.
if command -v npm >/dev/null 2>&1; then
  echo "[post-create] Attempting optional CLI installs (codex, claude)"
  npm install -g @openai/codex @anthropic-ai/claude-code >/tmp/agent-cli-install.log 2>&1 || {
    echo "[post-create] Optional CLI install failed; continuing. See /tmp/agent-cli-install.log"
  }
fi

bashrc_hook='source /workspaces/YNAB-receipt-analyzer/scripts/dev-env.sh >/dev/null 2>&1 || true'
if ! grep -Fq "${bashrc_hook}" "${HOME}/.bashrc"; then
  echo "[post-create] Configuring ~/.bashrc to auto-load scripts/dev-env.sh"
  {
    echo
    echo "# YNAB Receipt Analyzer dev environment"
    echo "${bashrc_hook}"
  } >> "${HOME}/.bashrc"
fi

echo "[post-create] Complete"

# Dev Container Setup

## What this includes

- Python 3.12 + Node.js 22 toolchains
- Docker CLI in-container (via docker-outside-of-docker feature)
- Recommended VS Code extensions for Python, Next.js, Docker, Tailwind, and agent workflows
- Optional mounts for local Codex/Claude configuration directories

## Credential options

Use one of these approaches:

1. Workspace `.env` file

- Keep project secrets in `${workspaceFolder}/.env`
- App code loads via `python-dotenv`.

2. Host environment variables (optional)

- Export credentials in the container terminal session when needed.
- This repo defaults to `.env` loading to avoid putting secrets in devcontainer config.

## Open in VS Code

1. Install Docker Desktop and VS Code Dev Containers extension.
2. In VS Code: `Cmd+Shift+P` -> `Dev Containers: Reopen in Container`.
3. First boot runs `.devcontainer/scripts/post-create.sh`:
   - `pip install -r requirements.txt`
   - `cd apps/server/frontend && npm install`
   - optional global install of `@openai/codex` and `@anthropic-ai/claude-code`

## Verify inside container

Run:

```bash
python --version
node --version
npm --version
docker version
pip show fastapi
cd apps/server/frontend && npm run lint
```

## Notes

- `initializeCommand` runs `.devcontainer/scripts/prepare-host-mounts.sh` on the host.
- That script:
  - creates `~/.codex` and `~/.claude` if needed
  - reads `INGEST_DIR` from repo `.env.local` (if present)
  - reads optional `DEVTOOLS_DIR` from repo `.env.local` (if present)
  - prepares `.devcontainer/.ingest-host` as a symlink to that host path
  - prepares `.devcontainer/.devtools-host` as a symlink to that host path
- The devcontainer mounts `.devcontainer/.ingest-host` to `/mnt/ingest-host`.
- The devcontainer mounts `.devcontainer/.devtools-host` to `/opt/devtools`.
- Inside the container, `scripts/dev-env.sh` remaps `INGEST_DIR` to `/mnt/ingest-host` when the host-only absolute path is not directly available.
- If you do not use Codex/Claude host config, remove the related mounts in `devcontainer.json`.

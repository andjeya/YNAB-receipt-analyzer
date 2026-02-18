# Receipt -> YNAB Local Web App (MVP)

This project re-architects the original receipt CLI prototype into a local, single-user web app with a long-running backend service, background workers, persistent storage, and mobile-first UI.

The original CLI (`main.py`) is preserved for reference; reusable logic was extracted into `shared/receipt_shared`.

## Project Structure

```text
backend/
  app/
    api/                # FastAPI routes
    jobs/               # RQ queue helpers + background tasks
    services/           # ingestion, validation, ynab sync logic
    config.py           # env-based settings
    db.py               # SQLAlchemy engine/session
    models.py           # DB models
    schemas.py          # API schemas
    main.py             # FastAPI app entrypoint
  alembic/
    versions/0001_mvp_init.py
frontend/
  src/app/              # Next.js app routes
  src/components/       # mobile-first UI components
  src/lib/              # API client + shared types
worker/
  scanner.py            # ingest folder polling loop
  worker.py             # RQ worker
shared/
  receipt_shared/
    gemini.py           # prompt + strict JSON extraction
    ynab_client.py      # YNAB API client
    money.py            # milliunit conversion
    contracts.py        # pydantic contracts
```

## MVP Lifecycle

`ingested -> extracting -> needs_review -> syncing -> synced`

Error states:

- `error_extract`
- `error_sync`

State transitions are persisted on `receipts.status` and visible in the API/UI.

## Core Design

- Backend is a long-running FastAPI service.
- Gemini calls happen only in RQ background jobs.
- SQLite is source of truth.
- Receipt files are immutable blobs after ingest.
- DB stores `storage_key`; original filename is display-only.
- Ingestion deduplicates by SHA-256 hash and waits for stable file size.
- YNAB sync is idempotent via `ynab_sync.idempotency_key`.

## Database

Tables:

- `receipts`
- `extraction_runs`
- `validations`
- `ynab_cache`
- `ynab_sync`
- `timing_metrics`

Create schema with Alembic:

```bash
alembic -c backend/alembic.ini upgrade head
```

## Local Run

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Configure environment:

```bash
cp .env.example .env
```

3. Run backend API:

```bash
PYTHONPATH=backend:shared uvicorn app.main:app --reload --port 8000
```

4. Run RQ worker:

```bash
PYTHONPATH=backend:shared python worker/worker.py
```

5. Run scanner:

```bash
PYTHONPATH=backend:shared python worker/scanner.py
```

6. Run frontend:

```bash
cd frontend
npm install
npm run dev
```

## Dev Container (Recommended For Consistent Dev Environments)

This repo includes a VS Code devcontainer in `.devcontainer/`.

Quick start:

```bash
mkdir -p ~/.codex ~/.claude ~/.config/claude
```

Then in VS Code run `Dev Containers: Reopen in Container`.

The first container start installs:

- Python dependencies from `requirements.txt`
- Frontend dependencies from `frontend/package.json`

Detailed instructions: `.devcontainer/README.md`.

Future runtime containerization plan: `plans/compose-plan.md`.

## API (MVP)

- `GET /healthz`
- `POST /api/ingest/scan`
- `GET /api/receipts`
- `GET /api/receipts/{id}`
- `GET /api/receipts/{id}/file`
- `POST /api/receipts/{id}/draft`
- `POST /api/receipts/{id}/reject`
- `POST /api/receipts/{id}/sync`
- `GET /api/ynab/cache`
- `POST /api/ynab/cache/refresh`
- `GET /api/stats/summary`
- `GET /api/game/dashboard`
- `POST /api/game/receipts/{id}/shred`
- `POST /api/game/rebuild`
- `POST /api/game/reconcile`
- `POST /api/game/correctness/recompute`

## Included V1 Features

- Folder ingestion (polling scanner)
- Gemini vision extraction on receipt file
- Strict JSON parsing + schema validation tracking
- Versioned validation drafts
- YNAB cache for categories/accounts/payees
- YNAB match-or-create sync with idempotency
- Timing metrics: extraction, validation, age at validation
- Gamification core loop (green/yellow/brown classification, streak tracking, shred token earn/spend, forest dashboard + weekly/monthly summaries + challenges)
- Correctness economy (water/fire counters, mismatch penalties, board burn threshold)
- YNAB reconciliation run tracking with 3-month lookback (default) and 12-hour cadence (default)
- Receipt correction metadata + fade shading for queue/detail visibility

## Gamification Strategy

Current strategy (implemented):

- First successful sync classifies each receipt as `green`, `yellow`, or `brown` based on hours from transaction date to sync time.
- Consecutive green receipts build streaks.
- Every configured green threshold mints one shred token.
- Tokens can shred yellow/brown receipts in the forest view.
- Resync does not add duplicate forest entries for the same receipt.
- Manual category/split corrections can award water (up to capacity).
- Reconciliation mismatches add fire units; stored water auto-extinguishes fires.
- Fire debt can burn the board when threshold is reached and no water remains.
- Correction-linked penalties apply when brown receipts remain un-resynced for >24h.

Planned correctness strategy (next phase):

- Add a second loop that rewards correction quality, not just speed.
- Track user-corrected category/split improvements as "water".
- Reconcile YNAB changes twice daily across the last 3 months and convert missed corrections into "fire" debt.
- Tie mistakes back to specific transactions and surface correction history in UI.
- Burn/reset behavior triggers when fire debt reaches threshold and no water remains.

## Category Guidance Template

- Keep your local/private guidance in: `shared/receipt_shared/resources/category_guidance.json`
- This file is intentionally gitignored.
- Start from the committed template: `shared/receipt_shared/resources/example_category_guidance.json`

## Explicitly Not Implemented (V1)

- User accounts/auth flows
- Dropbox API ingestion
- Timezone normalization
- Advanced receipt highlight overlays

## V2 TODOs

- Dropbox API ingestion pipeline
- Account identifier to YNAB account mapping strategy
- Expanded gamification economy and rewards
- Multi-user tenancy and auth
- Object storage backend abstraction for S3/MinIO
- Postgres migration hardening and production deployment profile

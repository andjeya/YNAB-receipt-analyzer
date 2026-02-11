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

## API (MVP)

- `GET /healthz`
- `POST /api/ingest/scan`
- `GET /api/receipts`
- `GET /api/receipts/{id}`
- `GET /api/receipts/{id}/file`
- `POST /api/receipts/{id}/draft`
- `POST /api/receipts/{id}/sync`
- `GET /api/ynab/cache`
- `POST /api/ynab/cache/refresh`
- `GET /api/stats/summary`

## Included V1 Features

- Folder ingestion (polling scanner)
- Gemini vision extraction on receipt file
- Strict JSON parsing + schema validation tracking
- Versioned validation drafts
- YNAB cache for categories/accounts/payees
- YNAB match-or-create sync with idempotency
- Timing metrics: extraction, validation, age at validation

## Explicitly Not Implemented (V1)

- User accounts/auth flows
- Dropbox API ingestion
- Gamification system
- Timezone normalization
- Advanced receipt highlight overlays

## V2 TODOs

- Dropbox API ingestion pipeline
- Account identifier to YNAB account mapping strategy
- Full gamification features
- Multi-user tenancy and auth
- Object storage backend abstraction for S3/MinIO
- Postgres migration hardening and production deployment profile

# Backend Service (FastAPI + RQ)

## Responsibilities

- Persist receipt lifecycle and immutable extraction runs in SQLite via SQLAlchemy.
- Scan ingest directory and move stable files into object storage (`storage_key`-based).
- Queue Gemini extraction in background jobs.
- Store versioned validation drafts and sync receipts to YNAB with idempotency.
- Expose API endpoints for the Next.js frontend.

## Run

```bash
pip install -r requirements.txt
alembic -c backend/alembic.ini upgrade head
PYTHONPATH=backend:shared uvicorn app.main:app --reload --port 8000
```

## API Surface (MVP)

- `GET /healthz`
- `POST /api/ingest/scan`
- `GET /api/receipts`
- `GET /api/receipts/{receipt_id}`
- `GET /api/receipts/{receipt_id}/file`
- `POST /api/receipts/{receipt_id}/draft`
- `POST /api/receipts/{receipt_id}/sync`
- `GET /api/ynab/cache`
- `POST /api/ynab/cache/refresh`
- `GET /api/stats/summary`
- `GET /api/game/dashboard`
- `POST /api/game/receipts/{receipt_id}/shred`
- `POST /api/game/rebuild`

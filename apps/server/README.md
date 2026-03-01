# Server Runtime (NAS)

This directory contains the core receipt application runtime that will run on the NAS:

- `backend/`: FastAPI API, DB models/migrations, jobs/services
- `worker/`: queue worker and ingestion scanner loop
- `shared/`: shared Python libraries/contracts/AI integrations
- `frontend/`: Next.js UI

## Local Development (unchanged workflow)

From repository root:

```bash
pip install -r requirements.txt
alembic -c apps/server/backend/alembic.ini upgrade head
PYTHONPATH=apps/server/backend:apps/server/shared uvicorn app.main:app --reload --port 8000
PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/worker.py
PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/scanner.py
cd apps/server/frontend && npm run dev
```

## Component Docs

- Backend details: `apps/server/backend/README.md`
- Worker notes: `apps/server/worker/README.md`
- Frontend notes: `apps/server/frontend/README.md`

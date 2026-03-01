.PHONY: migrate api worker scanner frontend

migrate:
	alembic -c apps/server/backend/alembic.ini upgrade head

api:
	PYTHONPATH=apps/server/backend:apps/server/shared uvicorn app.main:app --reload --port 8000

worker:
	PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/worker.py

scanner:
	PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/scanner.py

frontend:
	cd apps/server/frontend && npm run dev

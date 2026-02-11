.PHONY: migrate api worker scanner frontend

migrate:
	alembic -c backend/alembic.ini upgrade head

api:
	PYTHONPATH=backend:shared uvicorn app.main:app --reload --port 8000

worker:
	PYTHONPATH=backend:shared python worker/worker.py

scanner:
	PYTHONPATH=backend:shared python worker/scanner.py

frontend:
	cd frontend && npm run dev

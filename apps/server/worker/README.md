# Worker Processes

Start Redis first, then run two long-lived processes.

## RQ Worker

```bash
PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/worker.py
```

## Ingestion Scanner Loop

```bash
PYTHONPATH=apps/server/backend:apps/server/shared python apps/server/worker/scanner.py
```

The scanner watches `INGEST_DIR`, waits for file size stability, deduplicates by SHA-256 hash,
and moves accepted files into object storage before enqueueing extraction jobs.

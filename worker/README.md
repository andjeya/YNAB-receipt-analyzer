# Worker Processes

Start Redis first, then run two long-lived processes.

## RQ Worker

```bash
PYTHONPATH=backend:shared python worker/worker.py
```

## Ingestion Scanner Loop

```bash
PYTHONPATH=backend:shared python worker/scanner.py
```

The scanner watches `INGEST_DIR`, waits for file size stability, deduplicates by SHA-256 hash,
and moves accepted files into object storage before enqueueing extraction jobs.

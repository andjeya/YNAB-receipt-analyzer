from __future__ import annotations

from redis import Redis
from rq import Queue

from app.config import get_settings

EXTRACTION_QUEUE_NAME = "extraction"
SYNC_QUEUE_NAME = "sync"
RECONCILIATION_QUEUE_NAME = "reconciliation"


def get_redis_connection() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url)


def get_queue(queue_name: str) -> Queue:
    return Queue(name=queue_name, connection=get_redis_connection())


def _run_extraction_job(receipt_id: str) -> None:
    # Resolve lazily in worker processes to avoid import-order issues during startup.
    from app.jobs.tasks import run_extraction_job

    run_extraction_job(receipt_id)


def _run_sync_job(receipt_id: str, force_create: bool = False, allow_update_match: bool = True) -> None:
    from app.jobs.tasks import run_sync_job

    run_sync_job(receipt_id, force_create=force_create, allow_update_match=allow_update_match)


def _run_reconciliation_job() -> None:
    from app.jobs.tasks import run_reconciliation_job

    run_reconciliation_job()


def enqueue_extraction_job(receipt_id: str) -> str:
    job = get_queue(EXTRACTION_QUEUE_NAME).enqueue(
        "app.jobs.queue._run_extraction_job",
        receipt_id,
        job_timeout=900,
        result_ttl=24 * 3600,
    )
    return job.id


def enqueue_sync_job(receipt_id: str, force_create: bool = False, allow_update_match: bool = True) -> str:
    job = get_queue(SYNC_QUEUE_NAME).enqueue(
        "app.jobs.queue._run_sync_job",
        receipt_id,
        force_create,
        allow_update_match,
        job_timeout=900,
        result_ttl=24 * 3600,
    )
    return job.id


def enqueue_reconciliation_job() -> str:
    job = get_queue(RECONCILIATION_QUEUE_NAME).enqueue(
        "app.jobs.queue._run_reconciliation_job",
        job_timeout=1800,
        result_ttl=24 * 3600,
    )
    return job.id

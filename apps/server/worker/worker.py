from __future__ import annotations

import logging
from types import TracebackType

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings
from app.jobs.queue import EXTRACTION_QUEUE_NAME, RECONCILIATION_QUEUE_NAME, SYNC_QUEUE_NAME
from app.log_setup import configure_logging
from app.migrations import ensure_schema_current

logger = logging.getLogger("app.worker.worker")


def _log_job_exception(job, exc_type: type[BaseException], exc_value: BaseException, exc_tb: TracebackType | None) -> bool:
    logger.error(
        "RQ job failed queue=%s job_id=%s func=%s",
        getattr(job, "origin", "<unknown>"),
        getattr(job, "id", "<unknown>"),
        getattr(job, "func_name", "<unknown>"),
        exc_info=(exc_type, exc_value, exc_tb),
    )
    return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_file_path)
    ensure_schema_current()
    # Preload tasks so RQ workhorse imports are stable even when jobs run in forked children.
    import app.jobs.tasks  # noqa: F401

    connection = Redis.from_url(settings.redis_url)

    queues = [
        Queue(EXTRACTION_QUEUE_NAME, connection=connection),
        Queue(SYNC_QUEUE_NAME, connection=connection),
        Queue(RECONCILIATION_QUEUE_NAME, connection=connection),
    ]
    worker = Worker(queues, connection=connection)
    worker.push_exc_handler(_log_job_exception)
    worker.work(with_scheduler=True, logging_level="INFO")


if __name__ == "__main__":
    main()

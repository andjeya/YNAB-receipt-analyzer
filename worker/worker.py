from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings
from app.jobs.queue import EXTRACTION_QUEUE_NAME, RECONCILIATION_QUEUE_NAME, SYNC_QUEUE_NAME
from app.log_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_file_path)
    connection = Redis.from_url(settings.redis_url)

    queues = [
        Queue(EXTRACTION_QUEUE_NAME, connection=connection),
        Queue(SYNC_QUEUE_NAME, connection=connection),
        Queue(RECONCILIATION_QUEUE_NAME, connection=connection),
    ]
    worker = Worker(queues, connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()

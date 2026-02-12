from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from app.config import get_settings
from app.jobs.queue import EXTRACTION_QUEUE_NAME, SYNC_QUEUE_NAME


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)

    queues = [
        Queue(EXTRACTION_QUEUE_NAME, connection=connection),
        Queue(SYNC_QUEUE_NAME, connection=connection),
    ]
    worker = Worker(queues, connection=connection)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()

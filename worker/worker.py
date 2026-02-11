from __future__ import annotations

from redis import Redis
from rq import Connection, Queue, Worker

from app.config import get_settings
from app.jobs.queue import EXTRACTION_QUEUE_NAME, SYNC_QUEUE_NAME


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)

    with Connection(connection):
        queues = [Queue(EXTRACTION_QUEUE_NAME), Queue(SYNC_QUEUE_NAME)]
        worker = Worker(queues)
        worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()

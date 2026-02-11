from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.db import SessionLocal
from app.services.ingestion import IngestionScanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    scanner = IngestionScanner(settings)

    logger.info("Starting scanner loop: ingest_dir=%s interval=%ss", settings.ingest_dir, settings.scan_interval_seconds)

    while True:
        with SessionLocal() as db:
            result = scanner.scan_once(db)
            if result.ingested_count or result.duplicate_count or result.error_count:
                logger.info(
                    "scan result ingested=%s duplicate=%s skipped=%s errors=%s",
                    result.ingested_count,
                    result.duplicate_count,
                    result.skipped_count,
                    result.error_count,
                )
                for error in result.errors:
                    logger.error(error)

        time.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()

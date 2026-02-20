from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import SessionLocal
from app.jobs.queue import enqueue_reconciliation_job
from app.log_setup import configure_logging
from app.migrations import ensure_schema_current
from app.models import GameCorrectnessState
from app.services.ingestion import IngestionScanner

logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_file_path)
    ensure_schema_current()
    scanner = IngestionScanner(settings)
    last_reconcile_enqueue_at: datetime | None = None

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

            # Reconciliation cadence: at most every configured interval (default 12h).
            now = datetime.now(timezone.utc)
            state = db.get(GameCorrectnessState, 1)
            last_reconciled_at = _as_utc(state.last_reconciled_at) if state else None
            interval = timedelta(hours=settings.ynab_reconciliation_interval_hours)
            should_enqueue = last_reconciled_at is None or now - last_reconciled_at >= interval
            if should_enqueue:
                if last_reconcile_enqueue_at is None or now - last_reconcile_enqueue_at >= interval:
                    try:
                        enqueue_reconciliation_job()
                        last_reconcile_enqueue_at = now
                        logger.info(
                            "Enqueued reconciliation run (interval=%sh last_reconciled_at=%s)",
                            settings.ynab_reconciliation_interval_hours,
                            last_reconciled_at,
                        )
                    except Exception as exc:  # pragma: no cover - scanner safety
                        logger.exception("Failed to enqueue reconciliation job: %s", exc)

        time.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()

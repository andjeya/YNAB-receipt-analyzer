from __future__ import annotations

import logging
from datetime import timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt
from app.utils import utcnow

logger = logging.getLogger(__name__)


def recover_orphaned_ingested(
    db: Session,
    settings: Settings,
    enqueue: Callable[[str], str] | None = None,
) -> list[str]:
    """Re-enqueue receipts stranded at INGESTED so they don't sit forever.

    A receipt lands here in two ways, neither of which anything else recovers:

    1. Its extraction job was enqueued at ingest but then lost — Redis was
       restarted before the worker drained the queue (``run-app`` spins up a
       fresh Redis each launch), or the enqueue raised.
    2. ``_reset_stuck_jobs`` flipped a stuck EXTRACTING receipt back to INGESTED
       but never re-enqueued it.

    The scanner only enqueues *newly discovered* files, so without this sweep an
    INGESTED receipt never advances. We only touch rows older than
    ``ingested_reenqueue_after_seconds`` so we never race the normal at-ingest
    enqueue (which moves the receipt to EXTRACTING within seconds).

    On success the receipt is flipped to EXTRACTING with a fresh
    ``extraction_started_at``, so if *this* enqueue is also lost the existing
    stuck-EXTRACTING recovery picks it up on the next restart — a self-healing
    loop rather than another dead end.
    """
    if enqueue is None:
        from app.jobs.queue import enqueue_extraction_job

        enqueue = enqueue_extraction_job

    cutoff = utcnow() - timedelta(seconds=settings.ingested_reenqueue_after_seconds)
    orphans = list(
        db.scalars(
            select(Receipt).where(
                Receipt.status == ReceiptStatus.INGESTED.value,
                Receipt.deleted_at.is_(None),
                Receipt.ingested_at < cutoff,
            )
        )
    )

    recovered: list[str] = []
    for receipt in orphans:
        try:
            enqueue(receipt.id)
        except Exception:
            logger.exception("Failed to re-enqueue orphaned INGESTED receipt %s", receipt.id)
            continue
        receipt.status = ReceiptStatus.EXTRACTING.value
        receipt.status_reason = "Re-enqueued after a lost extraction job"
        receipt.extraction_started_at = utcnow()
        recovered.append(receipt.id)

    if recovered:
        db.commit()
        logger.info(
            "Re-enqueued %d orphaned INGESTED receipt(s): %s",
            len(recovered),
            ", ".join(recovered),
        )
    return recovered

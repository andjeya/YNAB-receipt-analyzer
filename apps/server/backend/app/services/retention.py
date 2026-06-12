"""Hard-purge of soft-deleted receipts after the Undo window.

A soft-deleted receipt (``deleted_at`` set) stays recoverable until this sweep
removes it: the stored file is unlinked and the row is deleted (cascading to
extraction runs, twins, validations, sync rows, etc.).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Receipt
from app.services.storage import storage_path
from app.utils import utcnow

logger = logging.getLogger(__name__)


def purge_soft_deleted_receipts(db: Session, settings, *, now=None) -> int:
    """Hard-delete receipts soft-deleted longer than the purge window.

    Returns the number of receipts purged.
    """
    reference = now or utcnow()
    cutoff = reference - timedelta(hours=settings.soft_delete_purge_hours)

    stale = list(
        db.scalars(
            select(Receipt).where(
                Receipt.deleted_at.is_not(None),
                Receipt.deleted_at < cutoff,
            )
        )
    )
    if not stale:
        return 0

    store_root = Path(settings.object_store_root).resolve()
    for receipt in stale:
        # Only unlink files that resolve safely under the object store root.
        absolute_path = storage_path(store_root, receipt.storage_key).resolve()
        if str(absolute_path).startswith(str(store_root) + "/"):
            absolute_path.unlink(missing_ok=True)
        db.delete(receipt)

    db.commit()
    logger.info("Purged %d soft-deleted receipt(s)", len(stale))
    return len(stale)

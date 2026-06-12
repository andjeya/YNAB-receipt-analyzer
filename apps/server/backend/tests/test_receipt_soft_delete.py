"""Soft-delete + Undo: discard a non-synced receipt, restore it, purge it later."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.receipts import (
    delete_receipt,
    get_receipt_detail,
    list_receipts,
    restore_receipt,
)
from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Base, Receipt
from app.services.retention import purge_soft_deleted_receipts


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _receipt(rid: str, status: str = ReceiptStatus.NEEDS_REVIEW.value, **kw) -> Receipt:
    return Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="receipt.jpg",
        file_hash=f"hash-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1234,
        status=status,
        **kw,
    )


def test_delete_soft_deletes_and_hides_from_list():
    settings = Settings(_env_file=None)
    with _memory_session() as db:
        db.add(_receipt("r1"))
        db.commit()

        resp = delete_receipt(receipt_id="r1", db=db)
        assert resp.deleted is True

        r1 = db.get(Receipt, "r1")
        assert r1.deleted_at is not None  # row still exists (recoverable)

        listed = list_receipts(status=None, sort="newest", limit=200, db=db, settings=settings)
        assert all(item.id != "r1" for item in listed)


def test_delete_is_idempotent():
    with _memory_session() as db:
        db.add(_receipt("r1"))
        db.commit()
        delete_receipt(receipt_id="r1", db=db)
        # second call must not error
        resp = delete_receipt(receipt_id="r1", db=db)
        assert resp.deleted is True


def test_synced_receipt_cannot_be_deleted():
    with _memory_session() as db:
        db.add(_receipt("r1", status=ReceiptStatus.SYNCED.value))
        db.commit()
        with pytest.raises(HTTPException) as exc:
            delete_receipt(receipt_id="r1", db=db)
        assert exc.value.status_code == 409


def test_syncing_receipt_cannot_be_deleted():
    with _memory_session() as db:
        db.add(_receipt("r1", status=ReceiptStatus.SYNCING.value))
        db.commit()
        with pytest.raises(HTTPException) as exc:
            delete_receipt(receipt_id="r1", db=db)
        assert exc.value.status_code == 409


def test_restore_brings_receipt_back():
    settings = Settings(_env_file=None)
    with _memory_session() as db:
        db.add(_receipt("r1"))
        db.commit()
        delete_receipt(receipt_id="r1", db=db)

        restored = restore_receipt(receipt_id="r1", db=db)
        assert restored.receipt_id == "r1"
        assert db.get(Receipt, "r1").deleted_at is None

        listed = list_receipts(status=None, sort="newest", limit=200, db=db, settings=settings)
        assert any(item.id == "r1" for item in listed)


def test_detail_404_when_deleted():
    with _memory_session() as db:
        db.add(_receipt("r1"))
        db.commit()
        delete_receipt(receipt_id="r1", db=db)
        with pytest.raises(HTTPException) as exc:
            get_receipt_detail(receipt_id="r1", db=db)
        assert exc.value.status_code == 404


def test_purge_removes_old_soft_deletes_but_keeps_recent_and_active():
    settings = Settings(_env_file=None)  # soft_delete_purge_hours default 24
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        # Old soft-delete (beyond window) → purged.
        db.add(_receipt("old", deleted_at=now - timedelta(hours=48)))
        # Recent soft-delete (within window) → kept for Undo.
        db.add(_receipt("recent", deleted_at=now - timedelta(hours=1)))
        # Active receipt → never touched.
        db.add(_receipt("active"))
        db.commit()

        purged = purge_soft_deleted_receipts(db, settings, now=now)
        assert purged == 1
        assert db.get(Receipt, "old") is None
        assert db.get(Receipt, "recent") is not None
        assert db.get(Receipt, "active") is not None

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
    settings = Settings(_env_file=None)  # soft_delete_purge_minutes default 5
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        # Old soft-delete (beyond the short Undo window) → purged.
        db.add(_receipt("old", deleted_at=now - timedelta(minutes=10)))
        # Recent soft-delete (still within the Undo window) → kept.
        db.add(_receipt("recent", deleted_at=now - timedelta(minutes=1)))
        # Active receipt → never touched.
        db.add(_receipt("active"))
        db.commit()

        purged = purge_soft_deleted_receipts(db, settings, now=now)
        assert purged == 1
        assert db.get(Receipt, "old") is None
        assert db.get(Receipt, "recent") is not None
        assert db.get(Receipt, "active") is not None


# ---------------------------------------------------------------------------
# Re-scanning a deleted receipt must NOT resurrect the old (zombie) row.
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402

from app.services.ingestion import ingest_file  # noqa: E402
from app.utils import utcnow  # noqa: E402


def _ingest_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        object_store_root=str(tmp_path / "store"),
        ingest_dir=str(tmp_path / "ingest"),
        ynab_access_token="t",
        ynab_budget_id="b",
    )


def _source_file(tmp_path, content: bytes) -> "object":
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    src = ingest_dir / "scan.jpg"
    src.write_bytes(content)
    return src


def test_reingesting_soft_deleted_file_replaces_zombie_with_fresh_receipt(tmp_path, monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.jobs.queue.enqueue_extraction_job", lambda rid: enqueued.append(rid))

    settings = _ingest_settings(tmp_path)
    content = b"miners-den-receipt-bytes"
    file_hash = hashlib.sha256(content).hexdigest()

    with _memory_session() as db:
        # A receipt with this content was deleted by the user (soft-delete).
        db.add(Receipt(
            id="old", storage_key="receipts/old.jpg", original_filename="old.jpg",
            file_hash=file_hash, file_ext=".jpg", mime_type="image/jpeg",
            file_size_bytes=len(content), status=ReceiptStatus.NEEDS_REVIEW.value,
            status_reason="Reset from stuck SYNCING state on startup",
            deleted_at=utcnow(),
        ))
        db.commit()

        src = _source_file(tmp_path, content)
        receipt, was_ingested = ingest_file(src, db, settings)

        assert was_ingested is True               # treated as a NEW scan, not a duplicate
        assert receipt.id != "old"                # a fresh row
        assert receipt.deleted_at is None
        assert receipt.status == ReceiptStatus.INGESTED.value
        assert receipt.status_reason is None      # no stale zombie note
        assert receipt.file_hash == file_hash
        assert db.get(Receipt, "old") is None      # old zombie hard-deleted
        assert enqueued == [receipt.id]            # re-extracted fresh


def test_reingesting_live_file_is_still_a_duplicate(tmp_path, monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.jobs.queue.enqueue_extraction_job", lambda rid: enqueued.append(rid))

    settings = _ingest_settings(tmp_path)
    content = b"live-receipt-bytes"
    file_hash = hashlib.sha256(content).hexdigest()

    with _memory_session() as db:
        db.add(Receipt(
            id="live", storage_key="receipts/live.jpg", original_filename="live.jpg",
            file_hash=file_hash, file_ext=".jpg", mime_type="image/jpeg",
            file_size_bytes=len(content), status=ReceiptStatus.NEEDS_REVIEW.value,
        ))
        db.commit()

        src = _source_file(tmp_path, content)
        receipt, was_ingested = ingest_file(src, db, settings)

        assert was_ingested is False              # genuine duplicate of a live receipt
        assert receipt.id == "live"
        assert not src.exists()                   # duplicate source unlinked
        assert enqueued == []                     # no re-extraction

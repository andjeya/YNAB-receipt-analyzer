"""Recovery of receipts stranded at INGESTED (lost extraction jobs).

Regression coverage for the orphan diagnosed 2026-06-15: a receipt whose
extraction job was lost on a Redis restart sat at INGESTED forever because
nothing re-enqueued it (`_reset_stuck_jobs` only handles EXTRACTING/SYNCING).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus
from app.models import Receipt
from app.services.recovery import recover_orphaned_ingested
from app.utils import utcnow


def _receipt(rid: str, *, status: str, age_seconds: float, **kw) -> Receipt:
    ts = utcnow() - timedelta(seconds=age_seconds)
    return Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.pdf",
        original_filename=f"{rid}.pdf",
        file_hash=f"hash-{rid}",
        file_ext=".pdf",
        mime_type="application/pdf",
        file_size_bytes=1234,
        status=status,
        ingested_at=ts,
        created_at=ts,
        updated_at=ts,
        **kw,
    )


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, ingested_reenqueue_after_seconds=180)


def test_old_orphan_is_reenqueued(db_session: Session, settings: Settings):
    db_session.add(_receipt("old", status=ReceiptStatus.INGESTED.value, age_seconds=3600))
    db_session.commit()

    calls: list[str] = []
    recovered = recover_orphaned_ingested(db_session, settings, enqueue=lambda rid: calls.append(rid) or "job-1")

    assert recovered == ["old"]
    assert calls == ["old"]
    receipt = db_session.get(Receipt, "old")
    assert receipt.status == ReceiptStatus.EXTRACTING.value
    assert receipt.extraction_started_at is not None  # arms the stuck-EXTRACTING net


def test_fresh_ingested_is_left_alone(db_session: Session, settings: Settings):
    # Younger than the threshold: the normal at-ingest enqueue is still in flight.
    db_session.add(_receipt("fresh", status=ReceiptStatus.INGESTED.value, age_seconds=5))
    db_session.commit()

    calls: list[str] = []
    recovered = recover_orphaned_ingested(db_session, settings, enqueue=lambda rid: calls.append(rid) or "job")

    assert recovered == []
    assert calls == []
    assert db_session.get(Receipt, "fresh").status == ReceiptStatus.INGESTED.value


def test_reset_from_stuck_extracting_is_recovered(db_session: Session, settings: Settings):
    # _reset_stuck_jobs flips stuck EXTRACTING back to INGESTED but never re-enqueues.
    db_session.add(
        _receipt(
            "reset",
            status=ReceiptStatus.INGESTED.value,
            age_seconds=7200,
            extraction_started_at=utcnow() - timedelta(minutes=40),
            status_reason="Reset from stuck EXTRACTING state on startup",
        )
    )
    db_session.commit()

    recovered = recover_orphaned_ingested(db_session, settings, enqueue=lambda rid: "job")
    assert recovered == ["reset"]


def test_deleted_and_non_ingested_are_ignored(db_session: Session, settings: Settings):
    db_session.add(_receipt("del", status=ReceiptStatus.INGESTED.value, age_seconds=3600, deleted_at=utcnow()))
    db_session.add(_receipt("review", status=ReceiptStatus.NEEDS_REVIEW.value, age_seconds=3600))
    db_session.add(_receipt("synced", status=ReceiptStatus.SYNCED.value, age_seconds=3600))
    db_session.commit()

    calls: list[str] = []
    recovered = recover_orphaned_ingested(db_session, settings, enqueue=lambda rid: calls.append(rid) or "job")

    assert recovered == []
    assert calls == []


def test_enqueue_failure_leaves_receipt_for_next_sweep(db_session: Session, settings: Settings):
    db_session.add(_receipt("flaky", status=ReceiptStatus.INGESTED.value, age_seconds=3600))
    db_session.commit()

    def boom(_rid: str) -> str:
        raise RuntimeError("redis down")

    recovered = recover_orphaned_ingested(db_session, settings, enqueue=boom)

    assert recovered == []
    receipt = db_session.get(Receipt, "flaky")
    assert receipt.status == ReceiptStatus.INGESTED.value  # untouched, retried next sweep
    assert receipt.extraction_started_at is None

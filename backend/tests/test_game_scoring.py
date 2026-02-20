from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameReceiptState, ReceiptStatus
from app.models import Base, GameReceiptStateModel, GameToken, Receipt, Validation
from app.services.game import _build_weekly_slots, apply_sync_gamification, spend_shred_token


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_receipt(db: Session, receipt_id: str) -> Receipt:
    receipt = Receipt(
        id=receipt_id,
        storage_key=f"receipts/{receipt_id}.jpg",
        original_filename=f"{receipt_id}.jpg",
        file_hash=f"hash-{receipt_id}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.SYNCED.value,
        latest_validation_version=1,
    )
    db.add(receipt)
    return receipt


def test_explicit_transaction_time_can_score_green():
    settings = Settings(_env_file=None, game_timezone="UTC", game_green_hours_threshold=24.0)
    synced_at = datetime(2026, 2, 11, 13, 0, tzinfo=timezone.utc)

    with _memory_session() as db:
        receipt = _add_receipt(db, "r-green-explicit")
        validation = Validation(
            receipt_id=receipt.id,
            version=1,
            source="user",
            payload={
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-02-10",
                "transaction_time": "15:30",
                "memo": "memo",
                "total_amount": 14.5,
                "category_id": "cat-1",
                "splits": [],
            },
            is_valid=True,
            errors=None,
        )
        db.add(validation)
        db.flush()

        state_row = apply_sync_gamification(db, receipt, validation, synced_at=synced_at, settings=settings)

        assert state_row.state == GameReceiptState.GREEN.value
        assert state_row.age_hours_at_validation < 24.0


def test_missing_transaction_time_uses_end_of_day_grace():
    settings = Settings(_env_file=None, game_timezone="UTC", game_green_hours_threshold=24.0)
    synced_at = datetime(2026, 2, 11, 21, 0, tzinfo=timezone.utc)

    with _memory_session() as db:
        receipt = _add_receipt(db, "r-green-grace")
        validation = Validation(
            receipt_id=receipt.id,
            version=1,
            source="user",
            payload={
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-02-10",
                "transaction_time": None,
                "memo": "memo",
                "total_amount": 14.5,
                "category_id": "cat-1",
                "splits": [],
            },
            is_valid=True,
            errors=None,
        )
        db.add(validation)
        db.flush()

        state_row = apply_sync_gamification(db, receipt, validation, synced_at=synced_at, settings=settings)

        assert state_row.state == GameReceiptState.GREEN.value
        assert state_row.age_hours_at_validation < 24.0


def test_weekly_slots_ignore_shredded_receipts_for_score():
    settings = Settings(_env_file=None, game_timezone="UTC")
    now = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

    rows = [
        GameReceiptStateModel(
            receipt_id="r-green",
            state=GameReceiptState.GREEN.value,
            validated_at=datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc),
            age_hours_at_validation=10.0,
            streak_group_id=1,
        ),
        GameReceiptStateModel(
            receipt_id="r-brown-shredded",
            state=GameReceiptState.BROWN.value,
            validated_at=datetime(2026, 2, 17, 9, 0, tzinfo=timezone.utc),
            age_hours_at_validation=90.0,
            streak_group_id=1,
            shredded_at=datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc),
        ),
    ]

    slots = _build_weekly_slots(rows, now=now, settings=settings)
    assert len(slots) == 9
    assert slots[-1]["display_state"] == GameReceiptState.GREEN.value


def test_shred_is_blocked_outside_validation_week():
    settings = Settings(_env_file=None, game_timezone="UTC")
    now = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

    with _memory_session() as db:
        _add_receipt(db, "r-old")
        db.add(
            GameReceiptStateModel(
                receipt_id="r-old",
                state=GameReceiptState.YELLOW.value,
                validated_at=now - timedelta(days=10),
                age_hours_at_validation=40.0,
                streak_group_id=1,
            )
        )
        db.add(GameToken(id=1, balance=1, earned_count=1, spent_count=0))
        db.flush()

        with pytest.raises(ValueError, match="same week"):
            spend_shred_token(db, settings, "r-old", spent_at=now)

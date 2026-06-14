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


def _seed_shreddable_receipt(db: Session, receipt_id: str, validated_at: datetime) -> None:
    _add_receipt(db, receipt_id)
    db.add(
        GameReceiptStateModel(
            receipt_id=receipt_id,
            state=GameReceiptState.YELLOW.value,
            validated_at=validated_at,
            age_hours_at_validation=40.0,
            streak_group_id=1,
        )
    )


def test_shred_is_blocked_outside_window():
    # now=Wed 2026-02-18 → current week starts Sun 2026-02-15. A receipt
    # validated 21 days ago is outside even the 2-week default window.
    settings = Settings(_env_file=None, game_timezone="UTC")
    now = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

    with _memory_session() as db:
        _seed_shreddable_receipt(db, "r-old", now - timedelta(days=21))
        db.add(GameToken(id=1, balance=1, earned_count=1, spent_count=0))
        db.flush()

        with pytest.raises(ValueError, match="within 2 week"):
            spend_shred_token(db, settings, "r-old", spent_at=now)


def test_shred_window_default_two_weeks_allows_previous_week():
    # Receipt validated 8 days ago = previous week. Blocked at window=1,
    # allowed at the default window=2.
    now = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)
    validated_at = now - timedelta(days=8)

    with _memory_session() as db:
        _seed_shreddable_receipt(db, "r-prev", validated_at)
        db.add(GameToken(id=1, balance=1, earned_count=1, spent_count=0))
        db.flush()

        # window=1 (current week only) → blocked.
        with pytest.raises(ValueError, match="within 1 week"):
            spend_shred_token(
                db, Settings(_env_file=None, game_timezone="UTC", game_shred_window_weeks=1), "r-prev", spent_at=now
            )

        # window=2 (default) → allowed; token is spent and receipt shredded.
        _, was_shredded = spend_shred_token(
            db, Settings(_env_file=None, game_timezone="UTC"), "r-prev", spent_at=now
        )
        assert was_shredded is True


def test_shred_window_reads_settings_override():
    # The admin GameSettings.shred_window_weeks overrides the config default and is
    # what spend_shred_token actually enforces.
    from app.models import GameSettings

    settings = Settings(_env_file=None, game_timezone="UTC")  # config default = 2
    now = datetime(2026, 2, 18, 12, 0, tzinfo=timezone.utc)

    with _memory_session() as db:
        _seed_shreddable_receipt(db, "r-prev", now - timedelta(days=8))
        db.add(GameToken(id=1, balance=1, earned_count=1, spent_count=0))
        db.add(GameSettings(id=1, shred_window_weeks=1))  # narrow window via admin settings
        db.flush()

        with pytest.raises(ValueError, match="within 1 week"):
            spend_shred_token(db, settings, "r-prev", spent_at=now)


# ---------------------------------------------------------------------------
# Admin timeliness thresholds (green/brown hours) — GameSettings
# ---------------------------------------------------------------------------

def _sync_receipt(db, settings, receipt_id, transaction_date, transaction_time, synced_at):
    """Create a receipt + validation and run gamification; returns the state row."""
    receipt = _add_receipt(db, receipt_id)
    validation = Validation(
        receipt_id=receipt.id,
        version=1,
        source="user",
        payload={
            "payee_name": "Store",
            "account_id": "acct-1",
            "transaction_date": transaction_date,
            "transaction_time": transaction_time,
            "memo": "",
            "total_amount": 10.0,
            "category_id": "cat-1",
            "splits": [],
        },
        is_valid=True,
        errors=None,
    )
    db.add(validation)
    db.flush()
    return apply_sync_gamification(db, receipt, validation, synced_at=synced_at, settings=settings)


def test_settings_thresholds_override_classification():
    """A GameSettings green/brown override changes how new syncs are classified."""
    from app.models import GameSettings

    settings = Settings(_env_file=None, game_timezone="UTC")
    synced_at = datetime(2026, 2, 11, 9, 0, tzinfo=timezone.utc)  # 18h after a 2026-02-10 15:00 purchase
    with _memory_session() as db:
        # Tighten "on time" to 12h and "very late" to 48h via admin settings.
        db.add(GameSettings(id=1, green_hours_threshold=12.0, brown_hours_threshold=48.0))
        db.flush()

        row = _sync_receipt(db, settings, "r-thr", "2026-02-10", "15:00", synced_at)
        # 18h > 12h (green) and <= 48h (brown) → yellow. Default 24/72 would be green.
        assert round(row.age_hours_at_validation) == 18
        assert row.state == GameReceiptState.YELLOW.value


def test_reclassify_regrades_existing_receipts():
    """Re-grading existing receipts against new thresholds works off the stored
    purchase→sync age (no re-sync needed)."""
    from app.services.game import reclassify_all_receipt_states

    settings = Settings(_env_file=None, game_timezone="UTC")
    synced_at = datetime(2026, 2, 11, 9, 0, tzinfo=timezone.utc)  # 18h after purchase
    with _memory_session() as db:
        row = _sync_receipt(db, settings, "r-reclass", "2026-02-10", "15:00", synced_at)
        assert row.state == GameReceiptState.GREEN.value  # default 24h → 18h is green

        reclassify_all_receipt_states(db, green_threshold=12.0, brown_threshold=48.0)
        db.flush()
        db.refresh(row)
        assert row.state == GameReceiptState.YELLOW.value  # 18h now exceeds the 12h on-time bar


def test_dashboard_rules_reflect_settings_thresholds():
    """The rules payload (read by How-to-play) surfaces the effective thresholds."""
    from app.models import GameSettings
    from app.services.game import get_dashboard_data

    settings = Settings(_env_file=None, game_timezone="UTC")
    with _memory_session() as db:
        db.add(GameSettings(id=1, green_hours_threshold=10.0, brown_hours_threshold=40.0))
        db.flush()
        data = get_dashboard_data(db, settings, window="week")
        assert data["rules"]["green_hours_threshold"] == 10.0
        assert data["rules"]["brown_hours_threshold"] == 40.0

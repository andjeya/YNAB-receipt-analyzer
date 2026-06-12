"""Tests for Game v3 correctness economy.

Updated for week-scoped fire mechanics (Game v3):
- fire_units live on game_week_fires rows, not GameCorrectnessState
- auto-douse spends 1 water when threshold would be hit, keeps week at threshold-1
- burnt weeks are permanent
- water_capacity is now 5; fire_burn_threshold is now 3
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameEventType
from app.models import (
    Base,
    GameCorrectnessState,
    GameEvent,
    GameReceiptStateModel,
    GameWeekFire,
    Receipt,
    ReceiptCorrection,
    Validation,
    YNABReconciliationRun,
)
from app.services.correctness import (
    add_fire,
    award_water,
    get_burnt_week_count,
    get_total_active_flames,
    recompute_correctness_state_from_history,
    spend_water_to_extinguish,
)


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_receipt(db: Session, receipt_id: str) -> Receipt:
    from app.enums import ReceiptStatus
    r = Receipt(
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
    db.add(r)
    return r


def _add_receipt_state(
    db: Session,
    receipt_id: str,
    validated_at: datetime,
    state: str = "green",
) -> GameReceiptStateModel:
    row = GameReceiptStateModel(
        receipt_id=receipt_id,
        state=state,
        validated_at=validated_at,
        age_hours_at_validation=1.0,
        streak_group_id=1,
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------

def test_correctness_tables_exist_in_metadata():
    assert GameCorrectnessState.__tablename__ in Base.metadata.tables
    assert ReceiptCorrection.__tablename__ in Base.metadata.tables
    assert YNABReconciliationRun.__tablename__ in Base.metadata.tables
    assert GameWeekFire.__tablename__ in Base.metadata.tables


# ---------------------------------------------------------------------------
# Water award tests (unchanged semantics, new capacity)
# ---------------------------------------------------------------------------

def test_award_water_caps_to_new_capacity():
    """Water capacity is now 5 (was 15)."""
    settings = Settings(_env_file=None, game_water_capacity=5)
    with _memory_session() as db:
        added = award_water(
            db,
            settings,
            units=20,
            receipt_id="r-1",
            idempotency_key="water:test:r-1",
            reason="unit_test",
        )
        state = db.get(GameCorrectnessState, 1)
        assert added == 5
        assert state is not None
        assert state.water_units == 5
        assert state.water_earned_count == 5


def test_award_water_partial_fill():
    settings = Settings(_env_file=None, game_water_capacity=5)
    with _memory_session() as db:
        # Fill to 3.
        award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:1", reason="test")
        # Try to add 4 more — only 2 should stick.
        added = award_water(db, settings, units=4, receipt_id="r-1", idempotency_key="w:2", reason="test")
        state = db.get(GameCorrectnessState, 1)
        assert added == 2
        assert state.water_units == 5


# ---------------------------------------------------------------------------
# Week-scoped fire tests
# ---------------------------------------------------------------------------

def _week_start_for_now(settings: Settings) -> datetime:
    """Return the UTC week-start for right now."""
    from app.services.game import _week_bounds_for_timestamp
    from app.utils import utcnow
    ws, _ = _week_bounds_for_timestamp(utcnow(), settings)
    return ws


def test_fire_lands_on_receipts_week():
    """A flame increments the receipt's week, not a global counter."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    # Use a fixed past timestamp so the week is deterministic.
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # A Monday
    with _memory_session() as db:
        receipt = _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        result = add_fire(
            db, settings,
            units=1, receipt_id="r-1",
            idempotency_key="fire:r-1:1", reason="test",
            created_at=past,
        )
        assert result["fires_added"] == 1
        assert result["burns_triggered"] == 0

        # The week row should have flames_active == 1.
        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)
        week_row = db.query(GameWeekFire).filter(
            GameWeekFire.week_start_at == ws.replace(microsecond=0)
        ).first()
        assert week_row is not None
        assert week_row.flames_active == 1
        assert week_row.burnt is False


def test_third_flame_burns_when_water_zero():
    """When no water is available, the 3rd flame burns the week."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        receipt = _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        # No water. Add 2 flames.
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:1", reason="test", created_at=past)
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:2", reason="test", created_at=past)
        # 3rd flame: should burn.
        result = add_fire(db, settings, units=1, receipt_id="r-1",
                          idempotency_key="fire:r-1:3", reason="test", created_at=past)

        assert result["fires_added"] == 1
        assert result["burns_triggered"] == 1
        assert result["forced_waters_spent"] == 0

        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)
        week_row = db.query(GameWeekFire).filter(
            GameWeekFire.week_start_at == ws.replace(microsecond=0)
        ).first()
        assert week_row.burnt is True
        assert week_row.flames_active == 3


def test_auto_douse_holds_week_at_2_when_water_available():
    """When water > 0, the 3rd flame triggers auto-douse: week stays at 2 flames, water decremented."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        receipt = _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        # Give 2 water units.
        award_water(db, settings, units=2, receipt_id="r-1", idempotency_key="w:1", reason="test")
        state = db.get(GameCorrectnessState, 1)
        assert state.water_units == 2

        # 2 flames first.
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:1", reason="test", created_at=past)
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:2", reason="test", created_at=past)

        # 3rd flame: auto-douse.
        result = add_fire(db, settings, units=1, receipt_id="r-1",
                          idempotency_key="fire:r-1:3", reason="test", created_at=past)

        assert result["fires_added"] == 1
        assert result["burns_triggered"] == 0
        assert result["forced_waters_spent"] == 1

        # Water decremented by 1.
        db.refresh(state)
        assert state.water_units == 1

        # Week still at 2 flames (not 3), not burnt.
        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)
        week_row = db.query(GameWeekFire).filter(
            GameWeekFire.week_start_at == ws.replace(microsecond=0)
        ).first()
        assert week_row.flames_active == 2
        assert week_row.burnt is False

        # Events recorded: FIRE_ADDED, WATER_SPENT, FIRE_EXTINGUISHED.
        events = db.query(GameEvent).all()
        event_types = {e.event_type for e in events}
        assert GameEventType.WATER_SPENT.value in event_types
        assert GameEventType.FIRE_EXTINGUISHED.value in event_types


def test_burnt_week_ignores_further_fires():
    """After a week burns, additional flames change nothing on the week."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        receipt = _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        # Burn the week (3 fires, no water).
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:1", reason="test", created_at=past)
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:2", reason="test", created_at=past)
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:3", reason="test", created_at=past)

        # Verify burnt.
        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)
        week_row = db.query(GameWeekFire).filter(
            GameWeekFire.week_start_at == ws.replace(microsecond=0)
        ).first()
        assert week_row.burnt is True
        flames_after_burn = week_row.flames_active

        # Add another fire — recorded as an audit event but the week state is
        # unchanged, so it must NOT count as an added fire (incident copy).
        result = add_fire(db, settings, units=1, receipt_id="r-1",
                          idempotency_key="fire:r-1:4", reason="test", created_at=past)
        assert result["fires_added"] == 0
        assert result["burns_triggered"] == 0  # No new burn

        db.refresh(week_row)
        assert week_row.flames_active == flames_after_burn  # Unchanged
        assert week_row.burnt is True


# ---------------------------------------------------------------------------
# Manual douse (spend_water_to_extinguish) — week-scoped
# ---------------------------------------------------------------------------

def test_manual_douse_reduces_week_flames():
    """Manual water spend reduces flames on the target week."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        receipt = _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:1", reason="test")
        add_fire(db, settings, units=2, receipt_id="r-1",
                 idempotency_key="fire:r-1:1", reason="test", created_at=past)
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:2", reason="test", created_at=past)

        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)

        result = spend_water_to_extinguish(
            db,
            units=2,
            receipt_id=None,
            idempotency_key="water:spend:test",
            reason="manual",
            week_start_at=ws,
        )

        assert result["waters_spent"] == 2
        assert result["fires_extinguished"] == 2

        week_row = db.query(GameWeekFire).filter(
            GameWeekFire.week_start_at == ws.replace(microsecond=0)
        ).first()
        # Started with 2 flames (after auto-douse for 3rd was not triggered since water was present
        # for the 2nd add_fire which has units=1 → only 3rd would be threshold).
        # Actually: first add_fire units=2 → flames 1 then 2; second add_fire units=1 → threshold hit
        # → auto-douse → water spent once already.
        # After spending 2 more: flames reduced by min(2, water_left, flames_active).
        assert week_row is not None
        # Water state.
        state = db.get(GameCorrectnessState, 1)
        assert state is not None
        assert state.water_units >= 0


def test_manual_douse_no_flames_returns_zero():
    """Calling spend_water on a week with no flames returns 0."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    with _memory_session() as db:
        _add_receipt(db, "r-1")
        _add_receipt_state(db, "r-1", validated_at=past)
        db.flush()

        award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:1", reason="test")

        from app.services.game import _week_bounds_for_timestamp
        ws, _ = _week_bounds_for_timestamp(past, settings)

        # No fires yet — create the week row manually.
        from app.services.correctness import _get_or_create_week_fire
        _get_or_create_week_fire(db, ws)
        db.flush()

        result = spend_water_to_extinguish(
            db,
            units=1,
            receipt_id=None,
            idempotency_key="water:spend:nofire",
            reason="manual",
            week_start_at=ws,
        )
        assert result["waters_spent"] == 0
        assert result["fires_extinguished"] == 0


# ---------------------------------------------------------------------------
# Recompute
# ---------------------------------------------------------------------------

def test_recompute_correctness_from_history():
    """recompute_correctness_state_from_history restores water from events."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    with _memory_session() as db:
        award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:r", reason="test")

        values = recompute_correctness_state_from_history(db, settings)
        assert values["water_units"] == 3
        assert values["water_units"] >= 0


def test_total_active_flames_and_burnt_count():
    """get_total_active_flames and get_burnt_week_count aggregate correctly."""
    settings = Settings(_env_file=None, game_water_capacity=5, game_fire_burn_threshold=3)
    past1 = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)   # week 1
    past2 = datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)  # week 2
    with _memory_session() as db:
        _add_receipt(db, "r-1")
        _add_receipt(db, "r-2")
        _add_receipt_state(db, "r-1", validated_at=past1)
        _add_receipt_state(db, "r-2", validated_at=past2)
        db.flush()

        # 1 flame on week 1.
        add_fire(db, settings, units=1, receipt_id="r-1",
                 idempotency_key="fire:r-1:1", reason="test", created_at=past1)
        # Burn week 2 (3 flames, no water).
        add_fire(db, settings, units=1, receipt_id="r-2",
                 idempotency_key="fire:r-2:1", reason="test", created_at=past2)
        add_fire(db, settings, units=1, receipt_id="r-2",
                 idempotency_key="fire:r-2:2", reason="test", created_at=past2)
        add_fire(db, settings, units=1, receipt_id="r-2",
                 idempotency_key="fire:r-2:3", reason="test", created_at=past2)

        total_flames = get_total_active_flames(db)
        burnt_count = get_burnt_week_count(db)
        # Week 1 has 1 active flame; week 2 is burnt (3 flames but burnt=True counts as 0 for active).
        assert total_flames == 1
        assert burnt_count == 1

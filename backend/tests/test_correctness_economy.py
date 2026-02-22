from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base, GameCorrectnessState, ReceiptCorrection, YNABReconciliationRun
from app.services.correctness import (
    add_fire,
    award_water,
    fire_breakdown,
    recompute_correctness_state_from_history,
    spend_water_to_extinguish,
)


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_correctness_tables_exist_in_metadata():
    assert GameCorrectnessState.__tablename__ in Base.metadata.tables
    assert ReceiptCorrection.__tablename__ in Base.metadata.tables
    assert YNABReconciliationRun.__tablename__ in Base.metadata.tables


def test_award_water_caps_to_capacity():
    settings = Settings(_env_file=None, game_water_capacity=15)
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
        assert added == 15
        assert state is not None
        assert state.water_units == 15
        assert state.water_earned_count == 15


def test_fire_forces_water_only_when_threshold_reached_then_can_burn():
    settings = Settings(_env_file=None, game_water_capacity=15, game_fire_burn_threshold=3)
    with _memory_session() as db:
        award_water(
            db,
            settings,
            units=2,
            receipt_id="r-1",
            idempotency_key="water:test:r-1",
            reason="unit_test",
        )

        first = add_fire(
            db,
            settings,
            units=2,
            receipt_id="r-1",
            idempotency_key="fire:test:first",
            reason="unit_test",
        )
        state = db.get(GameCorrectnessState, 1)
        assert state is not None
        assert first["fires_added"] == 2
        assert first["waters_spent"] == 0
        assert state.water_units == 2
        assert state.fire_units == 2

        second = add_fire(
            db,
            settings,
            units=1,
            receipt_id="r-1",
            idempotency_key="fire:test:second",
            reason="unit_test",
        )
        assert second["fires_added"] == 1
        assert second["waters_spent"] == 1
        assert second["burns_triggered"] == 0
        assert state.water_units == 1
        assert state.fire_units == 2

        third = add_fire(
            db,
            settings,
            units=3,
            receipt_id="r-1",
            idempotency_key="fire:test:third",
            reason="unit_test",
        )
        assert third["fires_added"] == 3
        assert third["waters_spent"] == 1
        assert third["burns_triggered"] == 1
        assert state.water_units == 0
        assert state.fire_units == 0
        assert state.burn_count == 1


def test_manual_water_spend_extinguishes_requested_amount():
    settings = Settings(_env_file=None, game_water_capacity=15, game_fire_burn_threshold=15)
    with _memory_session() as db:
        award_water(
            db,
            settings,
            units=5,
            receipt_id="r-1",
            idempotency_key="water:test:manual",
            reason="unit_test",
        )
        add_fire(
            db,
            settings,
            units=4,
            receipt_id="r-1",
            idempotency_key="fire:test:manual",
            reason="unit_test",
        )
        result = spend_water_to_extinguish(
            db,
            units=3,
            receipt_id="r-1",
            idempotency_key="water:spend:test",
            reason="manual_extinguish",
        )
        state = db.get(GameCorrectnessState, 1)
        assert state is not None
        assert result["waters_spent"] == 3
        assert result["fires_extinguished"] == 3
        assert state.water_units == 2
        assert state.fire_units == 1


def test_recompute_correctness_from_history_and_breakdown():
    settings = Settings(_env_file=None, game_water_capacity=15, game_fire_burn_threshold=15)
    with _memory_session() as db:
        award_water(
            db,
            settings,
            units=4,
            receipt_id="r-1",
            idempotency_key="water:test:recompute",
            reason="unit_test",
        )
        add_fire(
            db,
            settings,
            units=5,
            receipt_id="r-1",
            idempotency_key="fire:test:recompute",
            reason="unit_test",
        )

        values = recompute_correctness_state_from_history(db, settings)
        assert values["water_units"] >= 0
        assert values["fire_units"] >= 0

        small, medium, large = fire_breakdown(values["fire_units"])
        assert small >= 0
        assert medium >= 0
        assert large >= 0

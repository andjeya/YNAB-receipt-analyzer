"""Game v3 week-scoped fire mechanics — comprehensive test suite.

Covers:
- Flame lands on the corrected receipt's week (not global)
- 3rd flame burns when water=0
- Auto-douse holds week at 2 when water>0 (water decremented, events recorded)
- Burnt week ignores further fires and rejects douse
- Manual douse endpoint happy path + validation errors
- Derived weekly streak:
  - basic consecutive run
  - empty-week skip
  - flame pauses streak
  - douse repairs streak
  - retroactive flame on mid-run week cuts streak
  - burnt week breaks permanently
- Pass awarded at run multiples of 4 with idempotent re-evaluation (no double award)
- Pass kept after later burn
- Migration idempotency (run upgrade twice)
- Rebuild parity (rebuild produces same state as live accrual)
- Water cap clamp
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameEventType, GameReceiptState, ReceiptStatus
from app.models import (
    Base,
    GameCorrectnessState,
    GameEvent,
    GameReceiptStateModel,
    GameToken,
    GameWeekFire,
    Receipt,
    Validation,
    YNABSync,
)
from app.services.correctness import (
    add_fire,
    award_water,
    get_burnt_week_count,
    get_total_active_flames,
    spend_water_to_extinguish,
)
from app.services.game import (
    _derive_weekly_streak,
    _evaluate_passes,
    _get_or_create_tokens,
    _load_week_fires_by_start,
    _week_bounds_for_timestamp,
    apply_sync_gamification,
    get_dashboard_data,
    rebuild_gamification_state,
)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine():
    return create_engine("sqlite+pysqlite:///:memory:", future=True)


def _session() -> Session:
    engine = _engine()
    Base.metadata.create_all(engine)
    return Session(engine)


def _settings(**kwargs) -> Settings:
    defaults = dict(
        game_water_capacity=5,
        game_fire_burn_threshold=3,
        game_pass_every_green_weeks=4,
        game_timezone="UTC",
        game_green_hours_threshold=24.0,
        game_brown_hours_threshold=72.0,
    )
    defaults.update(kwargs)
    return Settings(_env_file=None, **defaults)


def _add_receipt(db: Session, receipt_id: str) -> Receipt:
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
    shredded: bool = False,
) -> GameReceiptStateModel:
    row = GameReceiptStateModel(
        receipt_id=receipt_id,
        state=state,
        validated_at=validated_at,
        age_hours_at_validation=1.0,
        streak_group_id=1,
        shredded_at=validated_at + timedelta(hours=1) if shredded else None,
    )
    db.add(row)
    return row


def _add_receipt_and_state(
    db: Session,
    receipt_id: str,
    validated_at: datetime,
    state: str = "green",
) -> tuple[Receipt, GameReceiptStateModel]:
    r = _add_receipt(db, receipt_id)
    s = _add_receipt_state(db, receipt_id, validated_at, state)
    return r, s


def _week_start(dt: datetime, settings: Settings) -> datetime:
    ws, _ = _week_bounds_for_timestamp(dt, settings)
    return ws


# Monday dates (within different weeks; week starts Sunday in UTC timezone)
WEEK1 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)   # week starting 2026-01-04
WEEK2 = datetime(2026, 1, 12, 12, 0, tzinfo=UTC)  # week starting 2026-01-11
WEEK3 = datetime(2026, 1, 19, 12, 0, tzinfo=UTC)  # week starting 2026-01-18
WEEK4 = datetime(2026, 1, 26, 12, 0, tzinfo=UTC)  # week starting 2026-01-25
WEEK5 = datetime(2026, 2, 2, 12, 0, tzinfo=UTC)   # week starting 2026-02-01
WEEK6 = datetime(2026, 2, 9, 12, 0, tzinfo=UTC)   # week starting 2026-02-08
NOW   = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)  # "now" — current incomplete week


# ---------------------------------------------------------------------------
# 1. Flame lands on receipt's week, not global
# ---------------------------------------------------------------------------

class TestFlameLandsOnReceiptWeek:
    def test_flame_increments_correct_week(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-w1", WEEK1)
            db.flush()
            # Droplets cover the fire so it lands (rather than burning the board).
            award_water(db, settings, units=1, receipt_id="r-w1", idempotency_key="w:fl:1", reason="test")

            add_fire(db, settings, units=1, receipt_id="r-w1",
                     idempotency_key="fire:1", reason="test", created_at=WEEK1)

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            week_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert week_row is not None
            assert week_row.flames_active == 1
            assert week_row.burnt is False

    def test_flame_does_not_affect_other_weeks(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-w1", WEEK1)
            _add_receipt_and_state(db, "r-w2", WEEK2)
            db.flush()
            # Droplets cover both fires so they land on their own weeks.
            award_water(db, settings, units=2, receipt_id="r-w1", idempotency_key="w:fl:2", reason="test")

            add_fire(db, settings, units=1, receipt_id="r-w1",
                     idempotency_key="fire:w1:1", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-w2",
                     idempotency_key="fire:w2:1", reason="test", created_at=WEEK2)

            ws1 = _week_start(WEEK1, settings).replace(microsecond=0)
            ws2 = _week_start(WEEK2, settings).replace(microsecond=0)
            row1 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws1).first()
            row2 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws2).first()
            assert row1.flames_active == 1
            assert row2.flames_active == 1

    def test_flame_falls_back_to_detection_time_when_no_state(self):
        """When receipt has no GameReceiptStateModel, flame falls back to created_at week."""
        settings = _settings()
        with _session() as db:
            award_water(db, settings, units=1, receipt_id=None, idempotency_key="w:fl:3", reason="test")
            # No GameReceiptStateModel for this receipt.
            result = add_fire(db, settings, units=1, receipt_id="no-state-receipt",
                              idempotency_key="fire:nostate:1", reason="test",
                              created_at=WEEK1)
            assert result["fires_added"] == 1
            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            week_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert week_row is not None
            assert week_row.flames_active == 1


# ---------------------------------------------------------------------------
# 2. Board pressure: a week burns when fires exceed droplets (water == 0)
# ---------------------------------------------------------------------------

class TestBurnsWhenUncovered:
    def test_single_fire_burns_when_no_water(self):
        """With zero droplets, even one incoming fire burns the worst week to ash."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()

            result = add_fire(db, settings, units=1, receipt_id="r-1",
                              idempotency_key="fire:1:0", reason="test", created_at=WEEK1)

            assert result["fires_added"] == 1
            assert result["burns_triggered"] == 1
            assert result["forced_waters_spent"] == 0

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert row.burnt is True
            # The victim's flames are cleared (it can no longer be doused).
            assert row.flames_active == 0

            # WEEK_BURNED event recorded for the victim week.
            burned_events = db.query(GameEvent).filter(
                GameEvent.event_type == GameEventType.WEEK_BURNED.value
            ).all()
            assert len(burned_events) == 1
            assert burned_events[0].payload_json["week_start_at"] == ws.isoformat()

    def test_worst_week_burns_not_the_landing_week(self):
        """When fires are uncovered, the week with the MOST flames burns — even if a
        new fire lands on a different week."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-w1", WEEK1)
            _add_receipt_and_state(db, "r-w2", WEEK2)
            db.flush()
            # Cover two fires on WEEK1 so it sits at 2 flames, then empty the stash.
            award_water(db, settings, units=2, receipt_id="r-w1", idempotency_key="w:cross:1", reason="test")
            add_fire(db, settings, units=1, receipt_id="r-w1",
                     idempotency_key="fire:w1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-w1",
                     idempotency_key="fire:w1:1", reason="test", created_at=WEEK1)
            db.get(GameCorrectnessState, 1).water_units = 0
            db.flush()

            ws1 = _week_start(WEEK1, settings).replace(microsecond=0)
            ws2 = _week_start(WEEK2, settings).replace(microsecond=0)
            row1 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws1).first()
            assert row1.flames_active == 2 and row1.burnt is False

            # A fresh fire lands on WEEK2 with no droplets → the worst week (WEEK1 at 2)
            # burns, not the landing week.
            result = add_fire(db, settings, units=1, receipt_id="r-w2",
                              idempotency_key="fire:w2:burn", reason="test", created_at=WEEK2)
            assert result["burns_triggered"] == 1

            row1 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws1).first()
            row2 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws2).first()
            assert row1.burnt is True and row1.flames_active == 0  # worst week burned
            assert row2.burnt is False and row2.flames_active == 1  # landing week kept its flame

    def test_tie_break_earliest_week_burns(self):
        """When tied on flames and the board is uncovered, the earliest week burns."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-w1", WEEK1)
            _add_receipt_and_state(db, "r-w2", WEEK2)
            _add_receipt_and_state(db, "r-w3", WEEK3)
            db.flush()
            # One flame each on WEEK1 and WEEK2 (covered), then empty the stash.
            award_water(db, settings, units=2, receipt_id="r-w1", idempotency_key="w:tie:1", reason="test")
            add_fire(db, settings, units=1, receipt_id="r-w1",
                     idempotency_key="fire:tie:w1", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-w2",
                     idempotency_key="fire:tie:w2", reason="test", created_at=WEEK2)
            db.get(GameCorrectnessState, 1).water_units = 0
            db.flush()

            ws1 = _week_start(WEEK1, settings).replace(microsecond=0)
            ws2 = _week_start(WEEK2, settings).replace(microsecond=0)

            # Land the triggering fire on a THIRD week so WEEK1 & WEEK2 stay tied at 1.
            # All three are now tied at 1 → the earliest (WEEK1) burns.
            add_fire(db, settings, units=1, receipt_id="r-w3",
                     idempotency_key="fire:tie:w3", reason="test", created_at=WEEK3)

            row1 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws1).first()
            row2 = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws2).first()
            assert row1.burnt is True   # earliest tied week burns
            assert row2.burnt is False

    def test_burn_count_reflected_in_dashboard(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            # No water → the first fire burns the week; the rest hit the burnt week.
            for i in range(3):
                add_fire(db, settings, units=1, receipt_id="r-1",
                         idempotency_key=f"fire:burn:{i}", reason="test", created_at=WEEK1)

            # Also need a receipt in current week for dashboard to work sensibly.
            _add_receipt_and_state(db, "r-now", NOW)
            db.flush()

            data = get_dashboard_data(db, settings, window="week")
            assert data["correctness"]["burnt_week_count"] == 1


# ---------------------------------------------------------------------------
# 3. Auto-douse holds week at 2 when water>0
# ---------------------------------------------------------------------------

class TestAutoDouse:
    def test_auto_douse_basic(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()

            award_water(db, settings, units=2, receipt_id="r-1", idempotency_key="w:1", reason="test")

            # 2 flames without issue.
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:1", reason="test", created_at=WEEK1)

            result = add_fire(db, settings, units=1, receipt_id="r-1",
                              idempotency_key="fire:1:2", reason="test", created_at=WEEK1)

            assert result["burns_triggered"] == 0
            assert result["forced_waters_spent"] == 1

            state = db.get(GameCorrectnessState, 1)
            assert state.water_units == 1  # Spent 1.

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert row.flames_active == 2
            assert row.burnt is False

    def test_auto_douse_events_recorded(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            # water=2 covers the first two fires; the third exceeds and auto-douses.
            award_water(db, settings, units=2, receipt_id="r-1", idempotency_key="w:1", reason="test")
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:1", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:2", reason="test", created_at=WEEK1)

            event_types = {e.event_type for e in db.query(GameEvent).all()}
            assert GameEventType.WATER_SPENT.value in event_types
            assert GameEventType.FIRE_EXTINGUISHED.value in event_types

    def test_auto_douse_uses_last_droplet_then_burns(self):
        """Auto-douse spends the last droplet to smother a fire; the next fire burns."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            # 1 droplet.
            award_water(db, settings, units=1, receipt_id="r-1", idempotency_key="w:1", reason="test")

            # First fire is covered (board 1 <= water 1): lands, flames=1.
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:1:0", reason="test", created_at=WEEK1)
            # Second fire would make board 2 > water 1 → auto-douse, water 1→0, flames stay 1.
            result1 = add_fire(db, settings, units=1, receipt_id="r-1",
                               idempotency_key="fire:1:1", reason="test", created_at=WEEK1)
            assert result1["forced_waters_spent"] == 1
            assert result1["burns_triggered"] == 0
            assert db.get(GameCorrectnessState, 1).water_units == 0

            # Now out of droplets: the next fire burns the worst week.
            result2 = add_fire(db, settings, units=1, receipt_id="r-1",
                               idempotency_key="fire:1:2", reason="test", created_at=WEEK1)
            assert result2["burns_triggered"] == 1


# ---------------------------------------------------------------------------
# 4. Burnt week ignores further fires and rejects douse
# ---------------------------------------------------------------------------

class TestBurntWeek:
    def _burn_week(self, db: Session, settings: Settings, receipt_id: str, ref_time: datetime):
        for i in range(3):
            add_fire(db, settings, units=1, receipt_id=receipt_id,
                     idempotency_key=f"fire:burn:{receipt_id}:{i}", reason="test", created_at=ref_time)

    def test_burnt_week_ignores_further_fires(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            self._burn_week(db, settings, "r-1", WEEK1)

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert row.burnt is True
            flames_after = row.flames_active

            result = add_fire(db, settings, units=1, receipt_id="r-1",
                              idempotency_key="fire:after:1", reason="test", created_at=WEEK1)
            assert result["burns_triggered"] == 0
            assert result["fires_added"] == 0  # Nothing changed — incident copy must not claim a fire.
            db.refresh(row)
            assert row.flames_active == flames_after  # Unchanged.
            assert row.burnt is True

    def test_burnt_week_rejects_manual_douse(self):
        """Burnt week API gate: API should reject douse on a burnt week.
        At the service level, verify that after burn the week stays burnt.
        The API gate (burnt=True → 400) is enforced in api/game.py.
        """
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            # No water — burn directly.
            self._burn_week(db, settings, "r-1", WEEK1)

            ws = _week_start(WEEK1, settings)
            ws_row = db.query(GameWeekFire).filter(
                GameWeekFire.week_start_at == ws.replace(microsecond=0)
            ).first()
            assert ws_row is not None
            assert ws_row.burnt is True

            # Now give water and attempt manual douse at service level.
            # Award water separately (after the burn) via direct state manipulation.
            state = db.get(GameCorrectnessState, 1)
            if state is None:
                from app.services.correctness import get_or_create_correctness_state
                state = get_or_create_correctness_state(db)
            state.water_units = 3
            db.flush()

            # API gate check (burnt=True) is in api/game.py; service-level call still
            # reduces flames even when burnt (the API prevents this). Verify week remains burnt.
            result = spend_water_to_extinguish(
                db, units=1, receipt_id=None,
                idempotency_key=f"manual:burnt:week:{uuid4()}",
                reason="test",
                week_start_at=ws,
            )
            db.refresh(ws_row)
            # The week stays burnt regardless (burnt is a one-way flag).
            assert ws_row.burnt is True


# ---------------------------------------------------------------------------
# 4b. add_fire idempotency regression (F2)
# ---------------------------------------------------------------------------

class TestAddFireIdempotency:
    def test_same_key_double_call_no_extra_flame(self):
        """Calling add_fire twice with the same idempotency_key must not double-increment flames."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-idem-1", WEEK1)
            db.flush()
            # Droplet so the fire lands (covered) rather than burning the board.
            award_water(db, settings, units=1, receipt_id="r-idem-1", idempotency_key="w:idem:0", reason="test")

            result1 = add_fire(db, settings, units=1, receipt_id="r-idem-1",
                               idempotency_key="fire:idem:1", reason="test", created_at=WEEK1)
            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            flames_after_first = row.flames_active

            # Second call with identical idempotency_key.
            result2 = add_fire(db, settings, units=1, receipt_id="r-idem-1",
                               idempotency_key="fire:idem:1", reason="test", created_at=WEEK1)
            db.refresh(row)

            assert row.flames_active == flames_after_first, (
                f"Double add_fire incremented flames: {flames_after_first} → {row.flames_active}"
            )
            assert result2["fires_added"] == 0, "Second call should add 0 fires"

    def test_same_key_double_call_no_extra_water_spent(self):
        """Idempotent add_fire with auto-douse path must not spend extra water on replay."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-idem-2", WEEK1)
            db.flush()

            # water=2 covers two fires; the third exceeds and auto-douses.
            award_water(db, settings, units=2, receipt_id="r-idem-2",
                        idempotency_key="w:idem:1", reason="test")
            # 2 covered flames.
            add_fire(db, settings, units=1, receipt_id="r-idem-2",
                     idempotency_key="fire:idem:2:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id="r-idem-2",
                     idempotency_key="fire:idem:2:1", reason="test", created_at=WEEK1)

            state = db.get(GameCorrectnessState, 1)
            water_before = state.water_units

            # 3rd flame triggers auto-douse.
            add_fire(db, settings, units=1, receipt_id="r-idem-2",
                     idempotency_key="fire:idem:2:2", reason="test", created_at=WEEK1)
            db.refresh(state)
            water_after_first = state.water_units

            # Replay same key — should be a no-op.
            add_fire(db, settings, units=1, receipt_id="r-idem-2",
                     idempotency_key="fire:idem:2:2", reason="test", created_at=WEEK1)
            db.refresh(state)

            assert state.water_units == water_after_first, (
                f"Double add_fire spent extra water: {water_after_first} → {state.water_units}"
            )

    def test_same_key_double_call_no_extra_burn(self):
        """Idempotent add_fire must not trigger a second burn on replay."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-idem-3", WEEK1)
            db.flush()

            # With no droplets the first fire burns the worst week.
            result1 = add_fire(db, settings, units=1, receipt_id="r-idem-3",
                               idempotency_key="fire:idem:3:0", reason="test", created_at=WEEK1)
            assert result1["burns_triggered"] == 1

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            flames_after_burn = row.flames_active

            # Replay same key.
            result2 = add_fire(db, settings, units=1, receipt_id="r-idem-3",
                               idempotency_key="fire:idem:3:0", reason="test", created_at=WEEK1)
            db.refresh(row)

            assert result2["burns_triggered"] == 0, "Second call must not trigger another burn"
            assert row.flames_active == flames_after_burn, "flames_active must not change on replay"
            assert row.burnt is True


# ---------------------------------------------------------------------------
# 5. Derived weekly streak
# ---------------------------------------------------------------------------

class TestDerivedWeeklyStreak:
    def test_basic_consecutive_run(self):
        """3 clean green weeks → streak=3."""
        settings = _settings()
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, max_s = _derive_weekly_streak(all_rows, wf, NOW, settings)
            assert streak == 3
            assert max_s == 3

    def test_empty_week_is_skipped(self):
        """WEEK2 has no receipts — skipped, streak of 2+2 still connects."""
        settings = _settings()
        with _session() as db:
            # WEEK1 green, WEEK3 green (WEEK2 empty).
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            _add_receipt_and_state(db, "r-3", WEEK3, state="green")
            db.flush()
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            # Empty WEEK2 is skipped; streak continues: 2 consecutive.
            assert streak == 2

    def test_yellow_week_breaks_streak(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            _add_receipt_and_state(db, "r-2", WEEK2, state="yellow")
            _add_receipt_and_state(db, "r-3", WEEK3, state="green")
            db.flush()
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, max_s = _derive_weekly_streak(all_rows, wf, NOW, settings)
            # After yellow WEEK2, only WEEK3 is clean-green.
            assert streak == 1
            assert max_s == 1

    def test_flame_pauses_streak(self):
        """A flame on WEEK2 breaks the run (not clean)."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            r2, s2 = _add_receipt_and_state(db, "r-2", WEEK2, state="green")
            _add_receipt_and_state(db, "r-3", WEEK3, state="green")
            db.flush()
            # Droplet covers the fire so it lands (pauses) instead of burning the week.
            award_water(db, settings, units=1, receipt_id="r-2", idempotency_key="w:pause:1", reason="test")
            # Add 1 flame to WEEK2.
            add_fire(db, settings, units=1, receipt_id="r-2",
                     idempotency_key="fire:w2:1", reason="test", created_at=WEEK2)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            # WEEK2 has flames → not clean green. Only WEEK3 is in the run.
            assert streak == 1

    def test_douse_repairs_streak(self):
        """Dousing the flame on WEEK2 restores the streak to 3."""
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            _add_receipt_and_state(db, "r-2", WEEK2, state="green")
            _add_receipt_and_state(db, "r-3", WEEK3, state="green")
            db.flush()
            award_water(db, settings, units=3, receipt_id="r-2", idempotency_key="w:1", reason="test")
            add_fire(db, settings, units=1, receipt_id="r-2",
                     idempotency_key="fire:w2:1", reason="test", created_at=WEEK2)

            ws2 = _week_start(WEEK2, settings)
            spend_water_to_extinguish(
                db, units=1, receipt_id=None,
                idempotency_key="water:douse:w2",
                reason="manual",
                week_start_at=ws2,
            )

            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            assert streak == 3

    def test_retroactive_flame_on_mid_run_week_cuts_streak(self):
        """Retroactive flame on WEEK2 cuts the streak from 4 to 2 (WEEK3+WEEK4 survive)."""
        settings = _settings()
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3, WEEK4]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            # Streak should be 4 without flames.
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak_before, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            assert streak_before == 4

            # Droplet covers the fire so WEEK2 pauses (gains a flame) rather than burning.
            award_water(db, settings, units=1, receipt_id="r-1", idempotency_key="w:retro:1", reason="test")
            # Retroactive flame on WEEK2.
            add_fire(db, settings, units=1, receipt_id="r-1",
                     idempotency_key="fire:retro:w2", reason="test", created_at=WEEK2)

            wf_after = _load_week_fires_by_start(db)
            streak_after, _ = _derive_weekly_streak(all_rows, wf_after, NOW, settings)
            # WEEK1 and WEEK2 are broken; only WEEK3+WEEK4 = run of 2.
            assert streak_after == 2

    def test_burnt_week_breaks_permanently(self):
        """A burnt week permanently breaks the streak, even after dousing (burnt can't be undone)."""
        settings = _settings()
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()
            # No water — burn WEEK2.
            for j in range(3):
                add_fire(db, settings, units=1, receipt_id="r-1",
                         idempotency_key=f"fire:burn:w2:{j}", reason="test", created_at=WEEK2)

            ws2 = _week_start(WEEK2, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws2).first()
            assert row.burnt is True

            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            # WEEK1 is clean green, WEEK2 is burnt, WEEK3 is clean green.
            # After burnt WEEK2, run resets; WEEK3 = streak 1.
            assert streak == 1

    def test_in_progress_week_not_counted(self):
        """The current incomplete week does not count toward or against the streak."""
        settings = _settings()
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            # Add a receipt in the current week.
            _add_receipt_and_state(db, "r-now", NOW, state="yellow")
            db.flush()
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            streak, _ = _derive_weekly_streak(all_rows, wf, NOW, settings)
            # The current week with yellow doesn't break the 3-week streak.
            assert streak == 3

    def test_empty_history_returns_zero(self):
        settings = _settings()
        with _session() as db:
            wf = _load_week_fires_by_start(db)
            streak, max_s = _derive_weekly_streak([], wf, NOW, settings)
            assert streak == 0
            assert max_s == 0


# ---------------------------------------------------------------------------
# 6. Skip passes
# ---------------------------------------------------------------------------

class TestSkipPasses:
    def test_pass_awarded_at_multiple_of_4(self):
        """4 consecutive clean-green completed weeks → 1 pass earned."""
        settings = _settings(game_pass_every_green_weeks=4)
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3, WEEK4]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            tokens = _get_or_create_tokens(db)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, NOW, settings)
            db.flush()

            assert tokens.earned_count == 1
            assert tokens.balance == 1

    def test_pass_idempotent_no_double_award(self):
        """Calling _evaluate_passes twice does not double-award passes."""
        settings = _settings(game_pass_every_green_weeks=4)
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3, WEEK4]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            tokens = _get_or_create_tokens(db)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()

            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, NOW, settings)
            db.flush()
            first_earned = tokens.earned_count

            # Second evaluation — same rows.
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, NOW, settings)
            db.flush()
            assert tokens.earned_count == first_earned  # No double award.

    def test_pass_not_awarded_after_3_weeks(self):
        """3 clean-green weeks at pass_every=4 → no pass yet."""
        settings = _settings(game_pass_every_green_weeks=4)
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            tokens = _get_or_create_tokens(db)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, NOW, settings)
            db.flush()
            assert tokens.earned_count == 0

    def test_pass_awarded_twice_at_8_weeks(self):
        """8 consecutive clean-green weeks at pass_every=4 → 2 passes."""
        settings = _settings(game_pass_every_green_weeks=4)
        # Build 8 consecutive weeks.
        refs = [WEEK1 + timedelta(weeks=k) for k in range(8)]
        with _session() as db:
            for i, ref in enumerate(refs):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            now_future = WEEK1 + timedelta(weeks=9)  # After all 8 weeks.
            tokens = _get_or_create_tokens(db)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, now_future, settings)
            db.flush()
            assert tokens.earned_count == 2

    def test_pass_kept_after_later_burn(self):
        """Pass earned for weeks 1-4 is NOT clawed back even if a flame later burns WEEK2."""
        settings = _settings(game_pass_every_green_weeks=4)
        with _session() as db:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3, WEEK4]):
                _add_receipt_and_state(db, f"r-{i}", ref, state="green")
            db.flush()

            tokens = _get_or_create_tokens(db)
            wf = _load_week_fires_by_start(db)
            all_rows = db.query(GameReceiptStateModel).all()
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf, NOW, settings)
            db.flush()
            assert tokens.earned_count == 1

            # Now burn WEEK2 retroactively.
            for j in range(3):
                add_fire(db, settings, units=1, receipt_id="r-1",
                         idempotency_key=f"fire:post:w2:{j}", reason="test", created_at=WEEK2)

            # Re-evaluate: pass should NOT be re-awarded, balance should be unchanged.
            wf2 = _load_week_fires_by_start(db)
            _evaluate_passes(db, tokens, 0, 0, all_rows, wf2, NOW, settings)
            db.flush()
            # earned_count stays at 1; no new pass for the now-broken run.
            assert tokens.earned_count == 1

    def test_pass_award_persists_across_session_boundary(self):
        """Pass awarded in one committed session is visible in a fresh session.

        This is the F3 regression: _evaluate_passes was only called from the
        GET dashboard handler which never commits, so awards appeared in-memory
        but rolled back on session close. The fix persists awards via the
        bookkeeping path (and rebuild). This test simulates that by calling
        _evaluate_passes in a committed session and asserting the award is visible
        in a subsequent independent session.
        """
        settings = _settings(game_pass_every_green_weeks=4)

        engine = _engine()
        Base.metadata.create_all(engine)

        # Session 1: award pass and COMMIT.
        with Session(engine) as db1:
            for i, ref in enumerate([WEEK1, WEEK2, WEEK3, WEEK4]):
                _add_receipt_and_state(db1, f"r-persist-{i}", ref, state="green")
            db1.flush()

            tokens1 = _get_or_create_tokens(db1)
            wf1 = _load_week_fires_by_start(db1)
            all_rows1 = db1.query(GameReceiptStateModel).all()
            _evaluate_passes(db1, tokens1, 0, 0, all_rows1, wf1, NOW, settings)
            db1.flush()
            assert tokens1.earned_count == 1
            db1.commit()  # Persist the award.

        # Session 2 (completely fresh): verify award is visible.
        with Session(engine) as db2:
            tokens2 = db2.get(GameToken, 1)
            assert tokens2 is not None, "GameToken row must exist after commit"
            assert tokens2.earned_count == 1, (
                f"Award not persisted: earned_count={tokens2.earned_count} in fresh session"
            )
            assert tokens2.balance >= 1, "Balance must reflect the committed award"

        # Session 3: re-evaluate (simulating a second load) — must NOT double-award.
        with Session(engine) as db3:
            tokens3 = _get_or_create_tokens(db3)
            all_rows3 = db3.query(GameReceiptStateModel).all()
            wf3 = _load_week_fires_by_start(db3)
            _evaluate_passes(db3, tokens3, 0, 0, all_rows3, wf3, NOW, settings)
            db3.flush()
            assert tokens3.earned_count == 1, (
                f"Double-award detected: earned_count={tokens3.earned_count} after second evaluate"
            )


# ---------------------------------------------------------------------------
# 7. Dashboard payload shape
# ---------------------------------------------------------------------------

class TestDashboardPayload:
    def test_dashboard_has_required_keys(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            db.flush()
            data = get_dashboard_data(db, settings, window="week")

        # momentum keys
        m = data["momentum"]
        assert "current_streak" in m
        assert "max_streak" in m
        assert "token_balance" in m
        assert "token_earned_count" in m
        assert "token_spent_count" in m
        assert "pass_every_green_weeks" in m
        assert "next_pass_in_weeks" in m
        assert "spendable_now" in m
        # Removed keys must NOT be present.
        assert "token_threshold" not in m
        assert "token_progress_current" not in m
        assert "next_token_in" not in m
        assert "last_green_at" not in m
        assert "break_reason" not in m

        # correctness keys
        c = data["correctness"]
        assert "water_units" in c
        assert "water_capacity" in c
        assert "last_reconciled_at" in c
        assert "total_active_flames" in c
        assert "burnt_week_count" in c
        # Removed keys.
        assert "fire_units" not in c
        assert "fires_to_burn" not in c
        assert "small_fires" not in c
        assert "medium_fires" not in c
        assert "large_fires" not in c
        assert "burn_count" not in c
        assert "last_burned_at" not in c
        assert "buckets_filled" not in c
        assert "bucket_capacity" not in c

        # rules keys
        r = data["rules"]
        assert "green_hours_threshold" in r
        assert "brown_hours_threshold" in r
        assert "water_capacity" in r
        assert "fire_burn_threshold" in r
        assert "pass_every_green_weeks" in r
        assert "token_earn_every_greens" not in r
        assert "bucket_capacity" not in r

    def test_weekly_slots_have_flames_and_burnt(self):
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1, state="green")
            db.flush()
            data = get_dashboard_data(db, settings, window="week")

        slots = data["forest"]["weekly_slots"]
        assert len(slots) == 9
        for slot in slots:
            assert "flames" in slot
            assert "burnt" in slot

    def test_burnt_slot_has_burnt_display_state(self):
        """A week with burnt=True should have display_state='burnt' in the slot.

        We place the receipt in the current week (relative to actual utcnow)
        so it falls within the 9 weekly slots window.
        """
        from app.utils import utcnow as _utcnow
        from app.services.game import _week_bounds_for_timestamp as _wbft
        settings = _settings()
        with _session() as db:
            now = _utcnow()
            # Use the current week so it's in the 9-slot window.
            current_week_start, _ = _wbft(now, settings)
            # Place receipt in the start of the current week.
            ref = current_week_start + timedelta(hours=12)
            _add_receipt_and_state(db, "r-slot-now", ref, state="green")
            db.flush()

            # Burn the current week (no water).
            for j in range(3):
                add_fire(db, settings, units=1, receipt_id="r-slot-now",
                         idempotency_key=f"fire:slot:now:{j}", reason="test", created_at=ref)

            data = get_dashboard_data(db, settings, window="week")

        slots = data["forest"]["weekly_slots"]
        burnt_slots = [s for s in slots if s["burnt"] is True]
        assert len(burnt_slots) >= 1
        assert burnt_slots[0]["display_state"] == "burnt"


# ---------------------------------------------------------------------------
# 8. Migration idempotency
# ---------------------------------------------------------------------------

class TestMigrationIdempotency:
    def test_migration_0011_is_idempotent(self):
        """Running migration 0011 upgrade() twice against a real SQLite bind must
        not raise and must leave the expected table/columns in place."""
        import importlib.util
        import sqlalchemy as sa
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations
        from pathlib import Path
        from sqlalchemy import create_engine as _ce, inspect, text

        # Load the migration module.
        migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/0011_game_week_fires.py"
        spec = importlib.util.spec_from_file_location("migration_0011", migration_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        engine = _ce("sqlite+pysqlite:///:memory:", future=True)

        # Create all OTHER tables so foreign-key-like references don't fail.
        # We create them via raw DDL matching the migration's expectations.
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS game_debug_seed ("
                "id INTEGER PRIMARY KEY, "
                "enabled INTEGER NOT NULL DEFAULT 0, "
                "water_units INTEGER NOT NULL DEFAULT 0, "
                "water_earned_count INTEGER NOT NULL DEFAULT 0, "
                "water_spent_count INTEGER NOT NULL DEFAULT 0, "
                "token_balance INTEGER NOT NULL DEFAULT 0, "
                "token_earned_count INTEGER NOT NULL DEFAULT 0, "
                "token_spent_count INTEGER NOT NULL DEFAULT 0, "
                "current_week_flames INTEGER NOT NULL DEFAULT 0, "
                "correctness_event_floor_id INTEGER NOT NULL DEFAULT 0, "
                "sync_floor_unix_ms INTEGER NOT NULL DEFAULT 0"
                ")"
            ))
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS game_correctness_state ("
                "id INTEGER PRIMARY KEY, "
                "water_units INTEGER NOT NULL DEFAULT 0"
                ")"
            ))
            # Seed a row with water_units > 5 to verify the clamp.
            conn.execute(text("INSERT INTO game_correctness_state (id, water_units) VALUES (1, 10)"))

        def _run_upgrade(conn: sa.engine.Connection) -> None:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()

        # First upgrade — creates table.
        with engine.begin() as conn:
            _run_upgrade(conn)

        # Second upgrade — must be idempotent (no exception).
        with engine.begin() as conn:
            _run_upgrade(conn)

        # Verify table and columns exist.
        with engine.connect() as conn:
            insp = inspect(conn)
            tables = insp.get_table_names()
            assert "game_week_fires" in tables, "game_week_fires table must exist after upgrade"

            gw_cols = {c["name"] for c in insp.get_columns("game_week_fires")}
            for col in ("id", "week_start_at", "flames_active", "burnt", "last_flame_at"):
                assert col in gw_cols, f"column {col!r} missing from game_week_fires"

            seed_cols = {c["name"] for c in insp.get_columns("game_debug_seed")}
            assert "current_week_flames" in seed_cols, "current_week_flames column must exist in game_debug_seed"

            # Verify water_units clamp was applied (10 → 5).
            row = conn.execute(text("SELECT water_units FROM game_correctness_state WHERE id=1")).fetchone()
            assert row is not None and row[0] <= 5, f"water_units should be clamped to <=5, got {row}"


# ---------------------------------------------------------------------------
# 9. Rebuild parity
# ---------------------------------------------------------------------------

class TestRebuildParity:
    """Parity tests: rebuild_gamification_state must produce the same
    flames_active / burnt values as live accrual for all significant scenarios."""

    def _make_synced_receipt(
        self,
        db: Session,
        receipt_id: str,
        ref_time: datetime,
    ) -> tuple[Receipt, Validation]:
        """Create a Receipt + Validation + YNABSync and apply gamification."""
        from app.enums import YNABSyncStatus
        receipt = _add_receipt(db, receipt_id)
        validation = Validation(
            receipt_id=receipt.id,
            version=1,
            source="user",
            payload={
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": ref_time.strftime("%Y-%m-%d"),
                "transaction_time": "12:00",
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
        sync = YNABSync(
            receipt_id=receipt.id,
            validation_id=validation.id,
            idempotency_key=f"sync-{receipt_id}",
            status=YNABSyncStatus.CREATED.value,
            match_mode="exact",
            started_at=ref_time,
            completed_at=ref_time,
        )
        db.add(sync)
        db.flush()
        apply_sync_gamification(db, receipt, validation, ref_time, settings=_settings())
        return receipt, validation

    def _week_row(self, db: Session, ref_time: datetime) -> GameWeekFire | None:
        ws = _week_start(ref_time, _settings()).replace(microsecond=0)
        return db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()

    # ------------------------------------------------------------------
    # Original single-flame case
    # ------------------------------------------------------------------

    def test_rebuild_produces_same_week_fires(self):
        """After rebuild, game_week_fires matches the state produced by live accrual."""
        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            receipt, _ = self._make_synced_receipt(db, "r-sync-1", WEEK1)

            # Droplet covers the fire so it lands (single-flame parity, not a burn).
            award_water(db, settings, units=1, receipt_id=receipt.id,
                        idempotency_key="w:sync:1", reason="test")
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:r-sync-1:1", reason="test", created_at=WEEK1)

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row_before = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            flames_before = row_before.flames_active if row_before else 0
            assert flames_before == 1

            rebuild_gamification_state(db, settings)
            db.flush()

            row_after = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            flames_after = row_after.flames_active if row_after else 0

            assert flames_after == flames_before, (
                f"Rebuild parity failed: before={flames_before} after={flames_after}"
            )

    # ------------------------------------------------------------------
    # F1 regression: auto-douse case (was losing 1 flame on rebuild)
    # ------------------------------------------------------------------

    def test_rebuild_parity_auto_douse(self):
        """Week with an auto-douse must have flames_active==2 both live and after rebuild.

        Before the F1 fix, the WATER_SPENT replay branch unconditionally decremented
        flames_active even for forced-douse events, producing flames==1 post-rebuild
        while live state was 2.
        """
        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            receipt, _ = self._make_synced_receipt(db, "r-ad-1", WEEK1)

            # water=2 covers two fires; the 3rd exceeds and auto-douses (flames stay 2).
            award_water(db, settings, units=2, receipt_id=receipt.id,
                        idempotency_key="w:ad:1", reason="test")
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:ad:1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:ad:1:1", reason="test", created_at=WEEK1)
            # 3rd flame → auto-douse; flames stays at 2.
            result = add_fire(db, settings, units=1, receipt_id=receipt.id,
                              idempotency_key="fire:ad:1:2", reason="test", created_at=WEEK1)
            assert result["forced_waters_spent"] == 1

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            live_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert live_row is not None
            flames_live = live_row.flames_active
            burnt_live = live_row.burnt
            assert flames_live == 2, f"live flames should be 2, got {flames_live}"
            assert burnt_live is False

            rebuild_gamification_state(db, settings)
            db.flush()

            rebuilt_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert rebuilt_row is not None
            assert rebuilt_row.flames_active == flames_live, (
                f"Rebuild parity (auto-douse): live={flames_live} rebuilt={rebuilt_row.flames_active}"
            )
            assert rebuilt_row.burnt == burnt_live

    # ------------------------------------------------------------------
    # Manually-doused week parity
    # ------------------------------------------------------------------

    def test_rebuild_parity_manual_douse(self):
        """Week with 2 flames manually doused to 1 must round-trip through rebuild."""
        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            receipt, _ = self._make_synced_receipt(db, "r-md-1", WEEK1)

            award_water(db, settings, units=3, receipt_id=receipt.id,
                        idempotency_key="w:md:1", reason="test")
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:md:1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:md:1:1", reason="test", created_at=WEEK1)

            # Manual douse: extinguish 1 flame.
            ws_dt = _week_start(WEEK1, settings)
            spend_water_to_extinguish(
                db,
                units=1,
                week_start_at=ws_dt,
                receipt_id=receipt.id,
                idempotency_key="douse:md:1",
                reason="test",
            )

            ws = ws_dt.replace(microsecond=0)
            live_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert live_row is not None
            flames_live = live_row.flames_active
            burnt_live = live_row.burnt

            rebuild_gamification_state(db, settings)
            db.flush()

            rebuilt_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert rebuilt_row is not None
            assert rebuilt_row.flames_active == flames_live, (
                f"Rebuild parity (manual douse): live={flames_live} rebuilt={rebuilt_row.flames_active}"
            )
            assert rebuilt_row.burnt == burnt_live

    # ------------------------------------------------------------------
    # Burnt week parity
    # ------------------------------------------------------------------

    def test_rebuild_parity_burnt_week(self):
        """A burned week must remain burnt=True with flames_active==0 (victim cleared)
        after rebuild."""
        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            receipt, _ = self._make_synced_receipt(db, "r-bw-1", WEEK1)

            # No water → the first fire burns the week (and clears its flames).
            add_fire(db, settings, units=1, receipt_id=receipt.id,
                     idempotency_key="fire:bw:1:0", reason="test", created_at=WEEK1)

            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            live_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert live_row is not None
            assert live_row.burnt is True
            assert live_row.flames_active == 0
            flames_live = live_row.flames_active
            burnt_live = live_row.burnt

            rebuild_gamification_state(db, settings)
            db.flush()

            rebuilt_row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert rebuilt_row is not None
            assert rebuilt_row.flames_active == flames_live, (
                f"Rebuild parity (burnt week): live={flames_live} rebuilt={rebuilt_row.flames_active}"
            )
            assert rebuilt_row.burnt == burnt_live

    def test_rebuild_parity_cross_week_victim(self):
        """The hardest case: a fire lands on WEEK2 but burns WEEK1 (more flames). After
        rebuild, WEEK1 must be burnt/flames=0 and WEEK2 must keep its landed flame —
        exercising the FIRE_ADDED(landing) vs WEEK_BURNED(victim) event split."""
        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            r1, _ = self._make_synced_receipt(db, "r-cw-1", WEEK1)
            r2, _ = self._make_synced_receipt(db, "r-cw-2", WEEK2)

            # WEEK1 to 2 flames (covered), then empty the stash.
            award_water(db, settings, units=2, receipt_id=r1.id,
                        idempotency_key="w:cw:1", reason="test")
            add_fire(db, settings, units=1, receipt_id=r1.id,
                     idempotency_key="fire:cw:1:0", reason="test", created_at=WEEK1)
            add_fire(db, settings, units=1, receipt_id=r1.id,
                     idempotency_key="fire:cw:1:1", reason="test", created_at=WEEK1)
            db.get(GameCorrectnessState, 1).water_units = 0
            db.flush()

            # Fire lands on WEEK2 → worst week WEEK1 burns.
            add_fire(db, settings, units=1, receipt_id=r2.id,
                     idempotency_key="fire:cw:2:0", reason="test", created_at=WEEK2)

            ws1 = _week_start(WEEK1, settings).replace(microsecond=0)
            ws2 = _week_start(WEEK2, settings).replace(microsecond=0)

            def _state():
                a = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws1).first()
                b = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws2).first()
                return (a.burnt, a.flames_active, b.burnt, b.flames_active)

            live = _state()
            assert live == (True, 0, False, 1)

            rebuild_gamification_state(db, settings)
            db.flush()

            assert _state() == live, f"Cross-week burn parity failed: {_state()} != {live}"

    # ------------------------------------------------------------------
    # Seeded flames must survive replay when the event floor masks history
    # ------------------------------------------------------------------

    def test_seeded_flames_respect_event_floor(self):
        """An enabled seed's current_week_flames must not be cancelled by
        pre-seed douse events during rebuild — the correctness_event_floor_id
        masks them, mirroring recompute_correctness_state_from_history."""
        from sqlalchemy import func, select

        from app.services.debug_seed import get_or_create_debug_seed

        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            now = datetime.now(timezone.utc)
            receipt, _ = self._make_synced_receipt(db, "r-seed-1", now)

            # Real history on the CURRENT week: 2 flames, both doused.
            award_water(db, settings, units=2, receipt_id=receipt.id,
                        idempotency_key="w:seed:1", reason="test")
            for j in range(2):
                add_fire(db, settings, units=1, receipt_id=receipt.id,
                         idempotency_key=f"fire:seed:1:{j}", reason="test", created_at=now)
            ws_dt = _week_start(now, settings)
            spend_water_to_extinguish(
                db, units=2, week_start_at=ws_dt, receipt_id=receipt.id,
                idempotency_key="douse:seed:1", reason="test",
            )
            db.flush()

            # Enable a seed with demo flames, floor above all existing events.
            seed = get_or_create_debug_seed(db)
            seed.enabled = True
            seed.current_week_flames = 2
            seed.correctness_event_floor_id = int(db.scalar(select(func.max(GameEvent.id))) or 0)
            db.flush()

            rebuild_gamification_state(db, settings)
            db.flush()

            ws = ws_dt.replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert row is not None
            assert row.flames_active == 2, (
                f"Seeded flames cancelled by pre-floor events: {row.flames_active}"
            )
            assert row.burnt is False

    def test_apply_debug_seed_injects_current_week_flames_without_rebuild(self):
        """The debug panel save (apply-to-live) must surface demo flames
        immediately — not only after a separate rebuild."""
        from app.services.debug_seed import apply_debug_seed_to_live_state, get_or_create_debug_seed

        settings = _settings()
        engine = _engine()
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            seed = get_or_create_debug_seed(db)
            seed.enabled = True
            seed.current_week_flames = 2
            seed.water_units = 3

            apply_debug_seed_to_live_state(db, seed, settings)
            db.flush()

            now = datetime.now(timezone.utc)
            ws, _ = _week_bounds_for_timestamp(now, settings)
            row = db.query(GameWeekFire).filter(
                GameWeekFire.week_start_at == ws.replace(microsecond=0)
            ).first()
            assert row is not None, "apply-to-live must create the current-week fire row"
            assert row.flames_active == 2
            # Clamped to the water cap, like the rebuild injection.
            seed.current_week_flames = 99
            apply_debug_seed_to_live_state(db, seed, settings)
            db.flush()
            db.refresh(row)
            assert row.flames_active == settings.game_water_capacity


# ---------------------------------------------------------------------------
# 10. Water cap clamp (migration + award)
# ---------------------------------------------------------------------------

class TestWaterCapClamp:
    def test_award_water_capped_at_5(self):
        settings = _settings()
        with _session() as db:
            # Award more than capacity.
            added = award_water(db, settings, units=10, receipt_id="r-1",
                                idempotency_key="w:cap:1", reason="test")
            state = db.get(GameCorrectnessState, 1)
            assert added == 5
            assert state.water_units == 5

    def test_water_units_cannot_exceed_5_via_sequential_awards(self):
        settings = _settings()
        with _session() as db:
            award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:1", reason="test")
            award_water(db, settings, units=3, receipt_id="r-1", idempotency_key="w:2", reason="test")
            state = db.get(GameCorrectnessState, 1)
            assert state.water_units == 5  # Capped.


# ---------------------------------------------------------------------------
# 11. Schema validation tests
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_dashboard_schema_validates(self):
        """GameDashboardOut validates against the new payload shape."""
        from app.schemas import GameDashboardOut
        settings = _settings()
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            data = get_dashboard_data(db, settings)
        # Should not raise.
        out = GameDashboardOut.model_validate(data)
        assert out.momentum.current_streak >= 0
        assert out.correctness.total_active_flames >= 0
        assert out.correctness.burnt_week_count >= 0
        assert out.momentum.pass_every_green_weeks == 4
        assert out.momentum.next_pass_in_weeks >= 1

    def test_weekly_slot_schema_has_flames_and_burnt(self):
        from app.schemas import GameWeeklySlotOut
        slot = GameWeeklySlotOut(
            index=0,
            start_at=WEEK1,
            end_at=WEEK1 + timedelta(days=7),
            is_empty=False,
            display_state="green",
            receipt_count=1,
            flames=2,
            burnt=False,
        )
        assert slot.flames == 2
        assert slot.burnt is False

    def test_water_spend_request_requires_week_start_at(self):
        from app.schemas import GameWaterSpendRequest
        import pydantic
        with pytest.raises((pydantic.ValidationError, TypeError)):
            GameWaterSpendRequest(units=1)  # Missing week_start_at.

    def test_water_spend_response_has_week_flames_active(self):
        from app.schemas import GameWaterSpendResponse
        resp = GameWaterSpendResponse(
            waters_spent=1, fires_extinguished=1, water_units=4, week_flames_active=1
        )
        assert resp.week_flames_active == 1
        assert not hasattr(resp, "fire_units")

    def test_rules_schema(self):
        from app.schemas import GameRulesOut
        rules = GameRulesOut(
            green_hours_threshold=24.0,
            brown_hours_threshold=72.0,
            shred_daily_spend_cap=0,
            water_capacity=5,
            fire_burn_threshold=3,
            pass_every_green_weeks=4,
            timezone="UTC",
        )
        assert rules.pass_every_green_weeks == 4
        assert rules.timezone == "UTC"
        assert not hasattr(rules, "token_earn_every_greens")
        assert not hasattr(rules, "bucket_capacity")


# ---------------------------------------------------------------------------
# 12. Config validation
# ---------------------------------------------------------------------------

class TestConfig:
    def test_new_defaults(self):
        s = Settings(_env_file=None)
        assert s.game_fire_burn_threshold == 3
        assert s.game_water_capacity == 5
        assert s.game_pass_every_green_weeks == 4
        assert not hasattr(s, "game_token_earn_every_greens")
        assert not hasattr(s, "game_bucket_capacity")

    def test_pass_every_validator_rejects_zero(self):
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            Settings(_env_file=None, game_pass_every_green_weeks=0)

    def test_fire_burn_threshold_no_longer_gates_burns(self):
        """The deprecated threshold must not gate burns: with a huge threshold, a
        single fire on an empty stash still burns the worst week."""
        settings = _settings(game_fire_burn_threshold=99)
        with _session() as db:
            _add_receipt_and_state(db, "r-1", WEEK1)
            db.flush()
            result = add_fire(db, settings, units=1, receipt_id="r-1",
                              idempotency_key="fire:nogate:1", reason="test", created_at=WEEK1)
            assert result["burns_triggered"] == 1
            ws = _week_start(WEEK1, settings).replace(microsecond=0)
            row = db.query(GameWeekFire).filter(GameWeekFire.week_start_at == ws).first()
            assert row.burnt is True

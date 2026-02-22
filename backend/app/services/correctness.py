from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameEventType
from app.models import GameCorrectnessState, GameEvent
from app.services.debug_seed import get_or_create_debug_seed


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_exists(db: Session, idempotency_key: str) -> bool:
    return db.scalar(select(GameEvent.id).where(GameEvent.idempotency_key == idempotency_key)) is not None


def _record_event(
    db: Session,
    *,
    event_type: GameEventType,
    idempotency_key: str,
    receipt_id: str | None,
    payload: dict[str, Any] | None,
    created_at: datetime | None = None,
) -> bool:
    if _event_exists(db, idempotency_key):
        return False
    db.add(
        GameEvent(
            event_type=event_type.value,
            receipt_id=receipt_id,
            payload_json=payload,
            idempotency_key=idempotency_key,
            created_at=_as_utc(created_at or utcnow()),
        )
    )
    return True


def get_or_create_correctness_state(db: Session) -> GameCorrectnessState:
    row = db.get(GameCorrectnessState, 1)
    if row is not None:
        return row

    row = GameCorrectnessState(id=1)
    db.add(row)
    db.flush()
    return row


def _apply_board_burn_if_needed(
    db: Session,
    *,
    state: GameCorrectnessState,
    settings: Settings,
    receipt_id: str | None,
    event_suffix: str,
    now: datetime,
) -> bool:
    if state.fire_units < settings.game_fire_burn_threshold:
        return False

    if state.water_units > 0:
        # Waters should already auto-extinguish before burn; keep this guard.
        return False

    state.burn_count += 1
    state.fire_units = 0
    state.last_burned_at = now
    _record_event(
        db,
        event_type=GameEventType.BOARD_BURNED,
        idempotency_key=f"board_burned:{event_suffix}",
        receipt_id=receipt_id,
        payload={"burn_count": state.burn_count},
        created_at=now,
    )
    return True


def _spend_water_to_extinguish(
    db: Session,
    *,
    state: GameCorrectnessState,
    units: int,
    receipt_id: str | None,
    idempotency_key: str,
    reason: str,
    created_at: datetime,
) -> int:
    if units <= 0:
        return 0

    state.water_units = max(state.water_units - units, 0)
    state.fire_units = max(state.fire_units - units, 0)
    state.water_spent_count += units
    state.fire_extinguished_count += units

    _record_event(
        db,
        event_type=GameEventType.WATER_SPENT,
        idempotency_key=f"{idempotency_key}:water",
        receipt_id=receipt_id,
        payload={"units": units, "reason": reason, "water_units": state.water_units},
        created_at=created_at,
    )
    _record_event(
        db,
        event_type=GameEventType.FIRE_EXTINGUISHED,
        idempotency_key=f"{idempotency_key}:extinguished",
        receipt_id=receipt_id,
        payload={"units": units, "reason": reason, "fire_units": state.fire_units},
        created_at=created_at,
    )
    return units


def award_water(
    db: Session,
    settings: Settings,
    *,
    units: int,
    receipt_id: str | None,
    idempotency_key: str,
    reason: str,
) -> int:
    if units <= 0:
        return 0

    if _event_exists(db, idempotency_key):
        return 0

    state = get_or_create_correctness_state(db)
    before = state.water_units
    state.water_units = min(state.water_units + units, settings.game_water_capacity)
    added = max(state.water_units - before, 0)
    if added <= 0:
        return 0

    state.water_earned_count += added
    _record_event(
        db,
        event_type=GameEventType.WATER_EARNED,
        idempotency_key=idempotency_key,
        receipt_id=receipt_id,
        payload={"units": added, "reason": reason, "water_units": state.water_units},
    )
    return added


def add_fire(
    db: Session,
    settings: Settings,
    *,
    units: int,
    receipt_id: str | None,
    idempotency_key: str,
    reason: str,
    created_at: datetime | None = None,
) -> dict[str, int]:
    if units <= 0:
        return {
            "fires_added": 0,
            "fires_extinguished": 0,
            "waters_spent": 0,
            "burns_triggered": 0,
            "forced_waters_spent": 0,
        }

    if _event_exists(db, idempotency_key):
        return {
            "fires_added": 0,
            "fires_extinguished": 0,
            "waters_spent": 0,
            "burns_triggered": 0,
            "forced_waters_spent": 0,
        }

    now = _as_utc(created_at or utcnow())
    state = get_or_create_correctness_state(db)

    state.fire_units += units
    state.fire_added_count += units
    _record_event(
        db,
        event_type=GameEventType.FIRE_ADDED,
        idempotency_key=idempotency_key,
        receipt_id=receipt_id,
        payload={"units": units, "reason": reason, "fire_units": state.fire_units},
        created_at=now,
    )

    forced_waters_spent = 0
    burns_triggered = 0

    if state.fire_units >= settings.game_fire_burn_threshold and state.water_units > 0:
        minimum_fire_to_survive = settings.game_fire_burn_threshold - 1
        waters_needed = max(state.fire_units - minimum_fire_to_survive, 0)
        forced_waters_spent = min(waters_needed, state.water_units)
        _spend_water_to_extinguish(
            db,
            state=state,
            units=forced_waters_spent,
            receipt_id=receipt_id,
            idempotency_key=f"{idempotency_key}:forced_spend",
            reason="forced_prevent_board_burn",
            created_at=now,
        )

    did_burn = _apply_board_burn_if_needed(
        db,
        state=state,
        settings=settings,
        receipt_id=receipt_id,
        event_suffix=idempotency_key,
        now=now,
    )
    if did_burn:
        burns_triggered += 1

    return {
        "fires_added": units,
        "fires_extinguished": forced_waters_spent,
        "waters_spent": forced_waters_spent,
        "burns_triggered": burns_triggered,
        "forced_waters_spent": forced_waters_spent,
    }


def spend_water_to_extinguish(
    db: Session,
    *,
    units: int,
    receipt_id: str | None,
    idempotency_key: str,
    reason: str = "manual_extinguish",
    created_at: datetime | None = None,
) -> dict[str, int]:
    if units <= 0:
        return {"waters_spent": 0, "fires_extinguished": 0}

    if _event_exists(db, f"{idempotency_key}:water"):
        return {"waters_spent": 0, "fires_extinguished": 0}

    now = _as_utc(created_at or utcnow())
    state = get_or_create_correctness_state(db)
    spendable = min(units, state.water_units, state.fire_units)
    if spendable <= 0:
        return {"waters_spent": 0, "fires_extinguished": 0}

    spent = _spend_water_to_extinguish(
        db,
        state=state,
        units=spendable,
        receipt_id=receipt_id,
        idempotency_key=idempotency_key,
        reason=reason,
        created_at=now,
    )
    return {"waters_spent": spent, "fires_extinguished": spent}


def fire_breakdown(fire_units: int) -> tuple[int, int, int]:
    if fire_units <= 0:
        return 0, 0, 0
    large_fires = fire_units // 3
    remainder = fire_units % 3
    small_fires = 1 if remainder == 1 else 0
    medium_fires = 1 if remainder == 2 else 0
    return small_fires, medium_fires, large_fires


def recompute_correctness_state_from_history(db: Session, settings: Settings) -> dict[str, int]:
    state = get_or_create_correctness_state(db)
    seed = get_or_create_debug_seed(db)

    if seed.enabled:
        water_units = seed.water_units
        water_earned_count = seed.water_earned_count
        water_spent_count = seed.water_spent_count
        fire_units = seed.fire_units
        fire_added_count = seed.fire_added_count
        fire_extinguished_count = seed.fire_extinguished_count
        burn_count = seed.burn_count
        event_floor = seed.correctness_event_floor_id
    else:
        water_units = 0
        water_earned_count = 0
        water_spent_count = 0
        fire_units = 0
        fire_added_count = 0
        fire_extinguished_count = 0
        burn_count = 0
        event_floor = 0

    last_burned_at: datetime | None = None

    stmt = (
        select(GameEvent)
        .where(
            GameEvent.event_type.in_(
                [
                    GameEventType.WATER_EARNED.value,
                    GameEventType.WATER_SPENT.value,
                    GameEventType.FIRE_ADDED.value,
                    GameEventType.FIRE_EXTINGUISHED.value,
                    GameEventType.BOARD_BURNED.value,
                ]
            )
        )
        .order_by(GameEvent.created_at.asc(), GameEvent.id.asc())
    )
    if event_floor > 0:
        stmt = stmt.where(GameEvent.id > event_floor)
    events = list(db.scalars(stmt))

    for event in events:
        payload = event.payload_json or {}
        units = int(payload.get("units", 1))

        if event.event_type == GameEventType.WATER_EARNED.value:
            water_earned_count += units
            water_units = min(water_units + units, settings.game_water_capacity)
        elif event.event_type == GameEventType.WATER_SPENT.value:
            water_spent_count += units
            water_units = max(water_units - units, 0)
        elif event.event_type == GameEventType.FIRE_ADDED.value:
            fire_added_count += units
            fire_units += units
        elif event.event_type == GameEventType.FIRE_EXTINGUISHED.value:
            fire_extinguished_count += units
            fire_units = max(fire_units - units, 0)
        elif event.event_type == GameEventType.BOARD_BURNED.value:
            burn_count += max(units, 1)
            fire_units = 0
            last_burned_at = event.created_at

    state.water_units = water_units
    state.water_earned_count = water_earned_count
    state.water_spent_count = water_spent_count
    state.fire_units = fire_units
    state.fire_added_count = fire_added_count
    state.fire_extinguished_count = fire_extinguished_count
    state.burn_count = burn_count
    state.last_burned_at = last_burned_at

    return {
        "water_units": state.water_units,
        "fire_units": state.fire_units,
        "burn_count": state.burn_count,
    }

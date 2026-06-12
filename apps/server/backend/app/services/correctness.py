from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameEventType
from app.models import GameCorrectnessState, GameEvent, GameReceiptStateModel, GameWeekFire
from app.services.debug_seed import get_or_create_debug_seed
from app.utils import utcnow


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


def _get_or_create_week_fire(db: Session, week_start_at: datetime) -> GameWeekFire:
    """Get or create a GameWeekFire row for the given week_start_at (UTC, truncated to second)."""
    week_start_utc = _as_utc(week_start_at).replace(microsecond=0)
    row = db.scalar(select(GameWeekFire).where(GameWeekFire.week_start_at == week_start_utc))
    if row is not None:
        return row
    row = GameWeekFire(week_start_at=week_start_utc)
    db.add(row)
    db.flush()
    return row


def _week_start_for_receipt(
    db: Session,
    receipt_id: str | None,
    settings: Settings,
    fallback_time: datetime,
) -> datetime:
    """Resolve the week_start_at for a given receipt using its GameReceiptStateModel.validated_at.

    Falls back to fallback_time if no state row exists.
    Uses the same _week_bounds_for_timestamp helper from game.py via import.
    """
    from app.services.game import _week_bounds_for_timestamp  # avoid circular at module level

    if receipt_id is not None:
        state_row = db.get(GameReceiptStateModel, receipt_id)
        if state_row is not None:
            week_start, _ = _week_bounds_for_timestamp(_as_utc(state_row.validated_at), settings)
            return week_start

    week_start, _ = _week_bounds_for_timestamp(_as_utc(fallback_time), settings)
    return week_start


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
    """Add flame(s) to the week that the corrected receipt belongs to.

    Week-scoped fire mechanics (Game v3):
    - Flames land on the receipt's week (derived from GameReceiptStateModel.validated_at).
    - When flames_active would reach game_fire_burn_threshold (3):
        - Auto-douse: if water_units > 0, spend 1 water, keep week at threshold-1 flames.
        - Otherwise: mark week as burnt, record incident.
    - If week is already burnt, record the event but change nothing on the week.
    """
    if units <= 0:
        return {"fires_added": 0, "fires_extinguished": 0, "waters_spent": 0, "burns_triggered": 0, "forced_waters_spent": 0}

    # NOTE: the top-level idempotency_key is intentionally NOT checked here.
    # add_fire uses suffixed per-unit keys ({idempotency_key}:unit:{i}…) so that
    # each unit is individually idempotent via _record_event's return value.
    # A bare top-level guard would never fire because no event is written to that key.

    now = _as_utc(created_at or utcnow())
    state = get_or_create_correctness_state(db)

    # Resolve which week this fire belongs to.
    week_start = _week_start_for_receipt(db, receipt_id, settings, fallback_time=now)
    week_row = _get_or_create_week_fire(db, week_start)

    fires_added = 0
    forced_waters_spent = 0
    burns_triggered = 0

    for i in range(units):
        # If already burnt, record the fire event but don't change week state.
        if week_row.burnt:
            recorded = _record_event(
                db,
                event_type=GameEventType.FIRE_ADDED,
                idempotency_key=f"{idempotency_key}:unit:{i}:burnt_week",
                receipt_id=receipt_id,
                payload={
                    "units": 1,
                    "reason": reason,
                    "week_start_at": week_start.isoformat(),
                    "week_already_burnt": True,
                },
                created_at=now,
            )
            if recorded:
                fires_added += 1
            continue

        # Would this flame reach the threshold?
        new_flames = week_row.flames_active + 1
        threshold = settings.game_fire_burn_threshold

        if new_flames >= threshold:
            # Auto-douse: spend 1 water to hold week at threshold-1.
            if state.water_units > 0:
                recorded = _record_event(
                    db,
                    event_type=GameEventType.FIRE_ADDED,
                    idempotency_key=f"{idempotency_key}:unit:{i}",
                    receipt_id=receipt_id,
                    payload={
                        "units": 1,
                        "reason": reason,
                        "week_start_at": week_start.isoformat(),
                        "flames_active": week_row.flames_active,
                    },
                    created_at=now,
                )
                if recorded:
                    state.water_units -= 1
                    state.water_spent_count += 1
                    state.fire_extinguished_count += 1
                    # Keep week at threshold-1 flames (don't increment).
                    week_row.last_flame_at = now
                    forced_waters_spent += 1
                    fires_added += 1

                    _record_event(
                        db,
                        event_type=GameEventType.WATER_SPENT,
                        idempotency_key=f"{idempotency_key}:unit:{i}:forced_douse:water",
                        receipt_id=receipt_id,
                        payload={
                            "units": 1,
                            "reason": "forced_prevent_week_burn",
                            "water_units": state.water_units,
                            "week_start_at": week_start.isoformat(),
                        },
                        created_at=now,
                    )
                    _record_event(
                        db,
                        event_type=GameEventType.FIRE_EXTINGUISHED,
                        idempotency_key=f"{idempotency_key}:unit:{i}:forced_douse:extinguished",
                        receipt_id=receipt_id,
                        payload={
                            "units": 1,
                            "reason": "forced_prevent_week_burn",
                            "week_start_at": week_start.isoformat(),
                        },
                        created_at=now,
                    )

                    # Record non-blocking incident for frontend narration.
                    from app.services.incidents import record_incident
                    record_incident(
                        db,
                        incident_type="forced_week_douse",
                        severity="warning",
                        title="Auto-Douse Triggered",
                        message="A flame would have burned a week — water was automatically spent to prevent it.",
                        details={
                            "week_start_at": week_start.isoformat(),
                            "flames_active": week_row.flames_active,
                            "water_units_remaining": state.water_units,
                        },
                        idempotency_key=f"forced_week_douse:{idempotency_key}:unit:{i}",
                        created_at=now,
                    )
            else:
                # Burn the week.
                recorded = _record_event(
                    db,
                    event_type=GameEventType.FIRE_ADDED,
                    idempotency_key=f"{idempotency_key}:unit:{i}",
                    receipt_id=receipt_id,
                    payload={
                        "units": 1,
                        "reason": reason,
                        "week_start_at": week_start.isoformat(),
                        # flames_active set after mutation below
                        "flames_active": new_flames,
                    },
                    created_at=now,
                )
                if recorded:
                    week_row.flames_active = new_flames
                    week_row.burnt = True
                    week_row.last_flame_at = now
                    fires_added += 1
                    burns_triggered += 1

                    _record_event(
                        db,
                        event_type=GameEventType.WEEK_BURNED,
                        idempotency_key=f"week_burned:{week_start.isoformat()}:{idempotency_key}:unit:{i}",
                        receipt_id=receipt_id,
                        payload={
                            "week_start_at": week_start.isoformat(),
                            "flames_active": week_row.flames_active,
                        },
                        created_at=now,
                    )

                    from app.services.incidents import record_incident
                    record_incident(
                        db,
                        incident_type="week_burned",
                        severity="warning",
                        title="Week Burned",
                        message="A week has been burned due to too many YNAB corrections.",
                        details={
                            "week_start_at": week_start.isoformat(),
                            "flames_active": week_row.flames_active,
                        },
                        idempotency_key=f"week_burned_incident:{week_start.isoformat()}:{idempotency_key}:unit:{i}",
                        created_at=now,
                    )
        else:
            # Normal flame increment. Gate mutation on event being newly recorded
            # so that calling add_fire twice with the same idempotency_key is safe.
            recorded = _record_event(
                db,
                event_type=GameEventType.FIRE_ADDED,
                idempotency_key=f"{idempotency_key}:unit:{i}",
                receipt_id=receipt_id,
                payload={
                    "units": 1,
                    "reason": reason,
                    "week_start_at": week_start.isoformat(),
                    "flames_active": new_flames,
                },
                created_at=now,
            )
            if recorded:
                week_row.flames_active = new_flames
                week_row.last_flame_at = now
                fires_added += 1

    return {
        "fires_added": fires_added,
        "fires_extinguished": forced_waters_spent,
        "waters_spent": forced_waters_spent,
        "burns_triggered": burns_triggered,
        "forced_waters_spent": forced_waters_spent,
    }


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


def spend_water_to_extinguish(
    db: Session,
    *,
    units: int,
    receipt_id: str | None,
    idempotency_key: str,
    reason: str = "manual_extinguish",
    week_start_at: datetime | None = None,
    created_at: datetime | None = None,
) -> dict[str, int]:
    """Manually spend water to douse flames on a specific week.

    week_start_at is required for week-scoped operation.
    Returns: waters_spent, fires_extinguished (flames removed from week), water_units.
    """
    if units <= 0:
        return {"waters_spent": 0, "fires_extinguished": 0, "water_units": 0}

    idem_key = f"{idempotency_key}:water"
    if _event_exists(db, idem_key):
        state = get_or_create_correctness_state(db)
        return {"waters_spent": 0, "fires_extinguished": 0, "water_units": state.water_units}

    now = _as_utc(created_at or utcnow())
    state = get_or_create_correctness_state(db)

    if week_start_at is not None:
        # Week-scoped douse.
        week_start_utc = _as_utc(week_start_at).replace(microsecond=0)
        week_row = db.scalar(select(GameWeekFire).where(GameWeekFire.week_start_at == week_start_utc))
        if week_row is None:
            return {"waters_spent": 0, "fires_extinguished": 0, "water_units": state.water_units}

        spendable = min(units, state.water_units, week_row.flames_active)
        if spendable <= 0:
            return {"waters_spent": 0, "fires_extinguished": 0, "water_units": state.water_units}

        state.water_units = max(state.water_units - spendable, 0)
        state.water_spent_count += spendable
        state.fire_extinguished_count += spendable
        week_row.flames_active = max(week_row.flames_active - spendable, 0)

        _record_event(
            db,
            event_type=GameEventType.WATER_SPENT,
            idempotency_key=idem_key,
            receipt_id=receipt_id,
            payload={
                "units": spendable,
                "reason": reason,
                "water_units": state.water_units,
                "week_start_at": week_start_utc.isoformat(),
            },
            created_at=now,
        )
        _record_event(
            db,
            event_type=GameEventType.FIRE_EXTINGUISHED,
            idempotency_key=f"{idempotency_key}:extinguished",
            receipt_id=receipt_id,
            payload={
                "units": spendable,
                "reason": reason,
                "flames_active": week_row.flames_active,
                "week_start_at": week_start_utc.isoformat(),
            },
            created_at=now,
        )
        return {"waters_spent": spendable, "fires_extinguished": spendable, "water_units": state.water_units}
    else:
        # Legacy path (no week_start_at): global fire reduction for backwards compat.
        spendable = min(units, state.water_units, state.fire_units)
        if spendable <= 0:
            return {"waters_spent": 0, "fires_extinguished": 0, "water_units": state.water_units}

        state.water_units = max(state.water_units - spendable, 0)
        state.water_spent_count += spendable
        state.fire_extinguished_count += spendable
        state.fire_units = max(state.fire_units - spendable, 0)

        _record_event(
            db,
            event_type=GameEventType.WATER_SPENT,
            idempotency_key=idem_key,
            receipt_id=receipt_id,
            payload={"units": spendable, "reason": reason, "water_units": state.water_units},
            created_at=now,
        )
        _record_event(
            db,
            event_type=GameEventType.FIRE_EXTINGUISHED,
            idempotency_key=f"{idempotency_key}:extinguished",
            receipt_id=receipt_id,
            payload={"units": spendable, "reason": reason, "fire_units": state.fire_units},
            created_at=now,
        )
        return {"waters_spent": spendable, "fires_extinguished": spendable, "water_units": state.water_units}


def get_total_active_flames(db: Session) -> int:
    """Sum of flames_active across all non-burnt weeks."""
    from sqlalchemy import func as sqlfunc
    result = db.scalar(
        select(sqlfunc.sum(GameWeekFire.flames_active)).where(GameWeekFire.burnt.is_(False))
    )
    return int(result or 0)


def get_burnt_week_count(db: Session) -> int:
    """Count of weeks that have been burnt."""
    from sqlalchemy import func as sqlfunc
    count = db.scalar(
        select(sqlfunc.count(GameWeekFire.id)).where(GameWeekFire.burnt.is_(True))
    )
    return int(count or 0)


def recompute_correctness_state_from_history(db: Session, settings: Settings) -> dict[str, int]:
    """Recompute GameCorrectnessState water counters from event history.

    In Game v3, fire/burn state lives on game_week_fires rows (rebuilt by
    rebuild_gamification_state). This function only recomputes water counters
    from WATER_EARNED/WATER_SPENT events.
    """
    state = get_or_create_correctness_state(db)
    seed = get_or_create_debug_seed(db)

    if seed.enabled:
        water_units = seed.water_units
        water_earned_count = seed.water_earned_count
        water_spent_count = seed.water_spent_count
        event_floor = seed.correctness_event_floor_id
    else:
        water_units = 0
        water_earned_count = 0
        water_spent_count = 0
        event_floor = 0

    stmt = (
        select(GameEvent)
        .where(
            GameEvent.event_type.in_(
                [
                    GameEventType.WATER_EARNED.value,
                    GameEventType.WATER_SPENT.value,
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

    state.water_units = water_units
    state.water_earned_count = water_earned_count
    state.water_spent_count = water_spent_count

    return {
        "water_units": state.water_units,
        "fire_units": get_total_active_flames(db),
        "burn_count": get_burnt_week_count(db),
    }


def fire_breakdown(fire_units: int) -> tuple[int, int, int]:
    """Legacy helper kept for backwards compatibility. Returns (small, medium, large)."""
    if fire_units <= 0:
        return 0, 0, 0
    large_fires = fire_units // 3
    remainder = fire_units % 3
    small_fires = 1 if remainder == 1 else 0
    medium_fires = 1 if remainder == 2 else 0
    return small_fires, medium_fires, large_fires

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import YNABSyncStatus
from app.models import GameCorrectnessState, GameDebugSeed, GameStreak, GameToken, YNABSync
from app.utils import utcnow


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def datetime_to_unix_ms(value: datetime) -> int:
    return int(_as_utc(value).timestamp() * 1000)


def unix_ms_to_datetime(value: int) -> datetime | None:
    if value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def get_or_create_debug_seed(db: Session) -> GameDebugSeed:
    row = db.get(GameDebugSeed, 1)
    if row is not None:
        return row

    row = GameDebugSeed(id=1)
    db.add(row)
    db.flush()
    return row


def mark_debug_seed_floor_now(db: Session, seed: GameDebugSeed) -> None:
    from app.models import GameEvent

    seed.correctness_event_floor_id = int(db.scalar(select(func.max(GameEvent.id))) or 0)
    latest_sync = db.scalar(
        select(func.max(YNABSync.completed_at)).where(
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
            YNABSync.completed_at.is_not(None),
        )
    )
    seed.sync_floor_unix_ms = datetime_to_unix_ms(_as_utc(latest_sync)) if latest_sync else 0


def _get_or_create_correctness_state(db: Session) -> GameCorrectnessState:
    row = db.get(GameCorrectnessState, 1)
    if row is not None:
        return row
    row = GameCorrectnessState(id=1)
    db.add(row)
    db.flush()
    return row


def _get_or_create_tokens(db: Session) -> GameToken:
    row = db.get(GameToken, 1)
    if row is not None:
        return row
    row = GameToken(id=1)
    db.add(row)
    db.flush()
    return row


def _get_or_create_streak(db: Session) -> GameStreak:
    row = db.get(GameStreak, 1)
    if row is not None:
        return row
    row = GameStreak(id=1)
    db.add(row)
    db.flush()
    return row


def apply_debug_seed_to_live_state(db: Session, seed: GameDebugSeed) -> None:
    correctness = _get_or_create_correctness_state(db)
    # Clamp water_units to the new capacity (5).
    correctness.water_units = min(max(seed.water_units, 0), 5)
    correctness.water_earned_count = max(seed.water_earned_count, 0)
    correctness.water_spent_count = max(seed.water_spent_count, 0)
    # fire_units / burn_count are legacy in the correctness state; week-scoped fire
    # lives in game_week_fires. Keep writing for audit trail compatibility.
    correctness.fire_units = max(seed.fire_units, 0)
    correctness.fire_added_count = max(seed.fire_added_count, 0)
    correctness.fire_extinguished_count = max(seed.fire_extinguished_count, 0)
    correctness.burn_count = max(seed.burn_count, 0)

    tokens = _get_or_create_tokens(db)
    tokens.balance = max(seed.token_balance, 0)
    tokens.earned_count = max(seed.token_earned_count, 0)
    tokens.spent_count = max(seed.token_spent_count, 0)

    # Streak is now derived from GameReceiptStateModel history; do not write GameStreak.
    # Keep GameStreak row but don't overwrite it — the legacy streak fields are read-only
    # in the seed context.

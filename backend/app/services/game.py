from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameChallengeStatus, GameEventType, GameReceiptState, YNABSyncStatus
from app.models import GameEvent, GameReceiptStateModel, GameStreak, GameToken, Receipt, Validation, YNABSync
from app.services.correctness import fire_breakdown, get_or_create_correctness_state

ALLOWED_WINDOWS = {"week", "month"}
WEEK_SLOT_COUNT = 9


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _game_tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.game_timezone)


def _week_start_sunday(value: datetime) -> datetime:
    days_since_sunday = (value.weekday() + 1) % 7
    return (value - timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)


def _day_bounds(now: datetime, settings: Settings) -> tuple[datetime, datetime]:
    tz = _game_tz(settings)
    local_now = _as_utc(now).astimezone(tz)
    day_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timedelta(days=1)
    return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)


def _week_bounds_for_timestamp(value: datetime, settings: Settings) -> tuple[datetime, datetime]:
    tz = _game_tz(settings)
    local_value = _as_utc(value).astimezone(tz)
    week_start_local = _week_start_sunday(local_value)
    week_end_local = week_start_local + timedelta(days=7)
    return week_start_local.astimezone(timezone.utc), week_end_local.astimezone(timezone.utc)


def classify_receipt_state(
    transaction_at: datetime,
    synced_at: datetime,
    settings: Settings,
) -> tuple[GameReceiptState, float]:
    age_hours = max((_as_utc(synced_at) - _as_utc(transaction_at)).total_seconds() / 3600, 0.0)

    if age_hours <= settings.game_green_hours_threshold:
        return GameReceiptState.GREEN, age_hours
    if age_hours > settings.game_brown_hours_threshold:
        return GameReceiptState.BROWN, age_hours
    return GameReceiptState.YELLOW, age_hours


def _get_or_create_streak(db: Session) -> GameStreak:
    streak = db.get(GameStreak, 1)
    if streak is not None:
        return streak

    streak = GameStreak(id=1)
    db.add(streak)
    db.flush()
    return streak


def _get_or_create_tokens(db: Session) -> GameToken:
    tokens = db.get(GameToken, 1)
    if tokens is not None:
        return tokens

    tokens = GameToken(id=1)
    db.add(tokens)
    db.flush()
    return tokens


def _record_event(
    db: Session,
    event_type: GameEventType,
    idempotency_key: str,
    receipt_id: str | None,
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> bool:
    existing = db.scalar(select(GameEvent.id).where(GameEvent.idempotency_key == idempotency_key))
    if existing is not None:
        return False

    db.add(
        GameEvent(
            event_type=event_type.value,
            receipt_id=receipt_id,
            payload_json=payload,
            idempotency_key=idempotency_key,
            created_at=created_at or utcnow(),
        )
    )
    return True


def _parse_transaction_date(raw_value: Any) -> date | None:
    if raw_value is None:
        return None
    try:
        return date.fromisoformat(str(raw_value))
    except ValueError:
        return None


def _parse_transaction_time(raw_value: Any) -> tuple[time | None, bool]:
    if raw_value is None:
        return None, False

    text = str(raw_value).strip()
    if not text:
        return None, False

    try:
        parsed = time.fromisoformat(text)
    except ValueError:
        return None, False

    # Receipt times are interpreted in the configured game timezone.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed, True


def _transaction_datetime_from_validation(
    validation: Validation,
    settings: Settings,
) -> tuple[datetime | None, bool]:
    parsed_date = _parse_transaction_date(validation.payload.get("transaction_date"))
    if parsed_date is None:
        return None, False

    parsed_time, has_explicit_time = _parse_transaction_time(validation.payload.get("transaction_time"))
    if parsed_time is None:
        # Unknown receipt time: allow grace through the end of the following day.
        parsed_time = time.max

    transaction_at_local = datetime.combine(parsed_date, parsed_time, tzinfo=_game_tz(settings))
    return transaction_at_local.astimezone(timezone.utc), has_explicit_time


def apply_sync_gamification(
    db: Session,
    receipt: Receipt,
    validation: Validation,
    synced_at: datetime,
    settings: Settings,
) -> GameReceiptStateModel:
    existing = db.get(GameReceiptStateModel, receipt.id)
    if existing is not None:
        return existing

    transaction_at, has_explicit_time = _transaction_datetime_from_validation(validation, settings)
    if transaction_at is None:
        raise ValueError("Validation payload is missing a valid transaction_date for gamification")

    synced_at_utc = _as_utc(synced_at)
    state, age_hours = classify_receipt_state(transaction_at, synced_at_utc, settings)
    streak = _get_or_create_streak(db)
    tokens = _get_or_create_tokens(db)

    prior_streak = streak.current_streak
    streak_group_id = streak.active_streak_group_id

    if state == GameReceiptState.GREEN:
        streak.current_streak += 1
        streak.max_streak = max(streak.max_streak, streak.current_streak)
        streak.last_green_at = synced_at_utc
        streak.break_reason = None

        _record_event(
            db,
            GameEventType.STREAK_INCREMENTED,
            idempotency_key=f"streak_incremented:{receipt.id}",
            receipt_id=receipt.id,
            payload={
                "current_streak": streak.current_streak,
                "streak_group_id": streak_group_id,
            },
            created_at=synced_at_utc,
        )

        threshold = settings.game_token_earn_every_greens
        if streak.current_streak % threshold == 0:
            tokens.earned_count += 1
            tokens.balance += 1
            _record_event(
                db,
                GameEventType.TOKEN_EARNED,
                idempotency_key=f"token_earned:{receipt.id}",
                receipt_id=receipt.id,
                payload={
                    "earned_count": tokens.earned_count,
                    "balance": tokens.balance,
                    "threshold": threshold,
                    "streak": streak.current_streak,
                },
                created_at=synced_at_utc,
            )
    else:
        streak.current_streak = 0
        streak.break_reason = state.value
        streak.active_streak_group_id += 1

        if prior_streak > 0:
            _record_event(
                db,
                GameEventType.STREAK_BROKEN,
                idempotency_key=f"streak_broken:{receipt.id}",
                receipt_id=receipt.id,
                payload={
                    "reason": state.value,
                    "prior_streak": prior_streak,
                },
                created_at=synced_at_utc,
            )

    state_row = GameReceiptStateModel(
        receipt_id=receipt.id,
        state=state.value,
        validated_at=synced_at_utc,
        age_hours_at_validation=age_hours,
        streak_group_id=streak_group_id,
    )
    db.add(state_row)

    _record_event(
        db,
        GameEventType.RECEIPT_CLASSIFIED,
        idempotency_key=f"receipt_classified:{receipt.id}",
        receipt_id=receipt.id,
        payload={
            "state": state.value,
            "age_hours": age_hours,
            "streak_group_id": streak_group_id,
            "transaction_date": validation.payload.get("transaction_date"),
            "transaction_time": validation.payload.get("transaction_time"),
            "has_explicit_time": has_explicit_time,
        },
        created_at=synced_at_utc,
    )

    return state_row


def _window_bounds(window: str, now: datetime, settings: Settings) -> tuple[datetime, datetime]:
    tz = _game_tz(settings)
    anchor_local = _as_utc(now).astimezone(tz)

    if window == "month":
        start_local = anchor_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_local.month == 12:
            end_local = start_local.replace(year=start_local.year + 1, month=1)
        else:
            end_local = start_local.replace(month=start_local.month + 1)
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    start_local = _week_start_sunday(anchor_local)
    end_local = start_local + timedelta(days=7)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _state_for_display(row: GameReceiptStateModel) -> GameReceiptState:
    if row.shredded_at is not None and row.state in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}:
        return GameReceiptState.SHREDDED

    return GameReceiptState(row.state)


def _slot_state(rows: list[GameReceiptStateModel]) -> str | None:
    active_rows = [row for row in rows if row.shredded_at is None]
    if not active_rows:
        return None
    rank = {
        GameReceiptState.GREEN.value: 0,
        GameReceiptState.YELLOW.value: 1,
        GameReceiptState.BROWN.value: 2,
    }
    worst = max(active_rows, key=lambda item: rank.get(item.state, 0))
    return worst.state


def _build_weekly_slots(rows: list[GameReceiptStateModel], now: datetime, settings: Settings) -> list[dict[str, Any]]:
    tz = _game_tz(settings)
    current_week_start_local = _week_start_sunday(_as_utc(now).astimezone(tz))
    slots: list[dict[str, Any]] = []
    for index in range(WEEK_SLOT_COUNT):
        end_local = current_week_start_local + timedelta(days=7) - timedelta(days=index * 7)
        start_local = end_local - timedelta(days=7)
        start_at = start_local.astimezone(timezone.utc)
        end_at = end_local.astimezone(timezone.utc)
        slot_rows = [row for row in rows if start_at <= _as_utc(row.validated_at) < end_at]
        state = _slot_state(slot_rows)
        slots.append(
            {
                "index": index,
                "start_at": start_at,
                "end_at": end_at,
                "is_empty": state is None,
                "display_state": state,
                "receipt_count": len(slot_rows),
            }
        )
    # Oldest first for left-to-right visual flow.
    slots.reverse()
    return slots


def _spent_in_window(db: Session, start: datetime, end: datetime) -> int:
    count = db.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.event_type == GameEventType.TOKEN_SPENT.value,
            GameEvent.created_at >= start,
            GameEvent.created_at < end,
        )
    )
    return int(count or 0)


def _first_successful_sync_rows(db: Session) -> list[tuple[Receipt, Validation, datetime]]:
    rows = db.execute(
        select(Receipt, Validation, YNABSync.completed_at)
        .join(YNABSync, YNABSync.receipt_id == Receipt.id)
        .join(Validation, Validation.id == YNABSync.validation_id)
        .where(
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
            YNABSync.completed_at.is_not(None),
        )
        .order_by(YNABSync.completed_at.asc(), Receipt.id.asc())
    ).all()

    seen_receipts: set[str] = set()
    first_rows: list[tuple[Receipt, Validation, datetime]] = []
    for receipt, validation, completed_at in rows:
        if receipt.id in seen_receipts:
            continue
        seen_receipts.add(receipt.id)
        first_rows.append((receipt, validation, _as_utc(completed_at)))
    return first_rows


def rebuild_gamification_state(db: Session, settings: Settings) -> dict[str, int]:
    preserved_shreds = {
        receipt_id: _as_utc(shredded_at)
        for receipt_id, shredded_at in db.execute(
            select(GameReceiptStateModel.receipt_id, GameReceiptStateModel.shredded_at).where(
                GameReceiptStateModel.shredded_at.is_not(None)
            )
        ).all()
    }

    preserved_spent = int(
        db.scalar(
            select(func.count(GameEvent.id)).where(GameEvent.event_type == GameEventType.TOKEN_SPENT.value)
        )
        or 0
    )

    db.execute(delete(GameReceiptStateModel))
    db.execute(delete(GameStreak))
    db.execute(delete(GameToken))
    db.flush()

    tokens = _get_or_create_tokens(db)
    tokens.spent_count = preserved_spent
    tokens.balance = 0
    tokens.earned_count = 0

    processed_count = 0
    restored_shreds = 0

    for receipt, validation, completed_at in _first_successful_sync_rows(db):
        state_row = apply_sync_gamification(db, receipt, validation, completed_at, settings)
        processed_count += 1

        preserved_shredded_at = preserved_shreds.get(receipt.id)
        if preserved_shredded_at is None:
            continue
        if state_row.state not in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}:
            continue

        state_row.shredded_at = preserved_shredded_at
        restored_shreds += 1

    tokens.balance = max(tokens.earned_count - tokens.spent_count, 0)

    return {
        "processed_receipts": processed_count,
        "restored_shreds": restored_shreds,
    }


def spend_shred_token(
    db: Session,
    settings: Settings,
    receipt_id: str,
    spent_at: datetime | None = None,
) -> tuple[GameReceiptStateModel, bool]:
    state_row = db.get(GameReceiptStateModel, receipt_id)
    if state_row is None:
        raise ValueError("Receipt has not entered the gamification forest yet")

    if state_row.shredded_at is not None:
        return state_row, False

    if state_row.state not in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}:
        raise ValueError("Only yellow or brown receipts can be shredded")

    now = _as_utc(spent_at or utcnow())

    shred_week_start, shred_week_end = _week_bounds_for_timestamp(now, settings)
    if not (shred_week_start <= _as_utc(state_row.validated_at) < shred_week_end):
        raise ValueError("Receipts can only be shredded in the same week they were validated")

    if settings.game_shred_daily_spend_cap > 0:
        day_start, day_end = _day_bounds(now, settings)
        spent_today = _spent_in_window(db, day_start, day_end)
        if spent_today >= settings.game_shred_daily_spend_cap:
            raise ValueError("Daily shred cap reached")

    tokens = _get_or_create_tokens(db)
    if tokens.balance <= 0:
        raise ValueError("No shred tokens available")

    idempotency_key = f"token_spent:{receipt_id}"
    if db.scalar(select(GameEvent.id).where(GameEvent.idempotency_key == idempotency_key)) is not None:
        state_row.shredded_at = now
        return state_row, False

    tokens.balance -= 1
    tokens.spent_count += 1
    state_row.shredded_at = now

    _record_event(
        db,
        GameEventType.TOKEN_SPENT,
        idempotency_key=idempotency_key,
        receipt_id=receipt_id,
        payload={
            "balance": tokens.balance,
            "spent_count": tokens.spent_count,
            "state": state_row.state,
        },
        created_at=now,
    )

    return state_row, True


def get_dashboard_data(
    db: Session,
    settings: Settings,
    window: str = "week",
    forest_limit: int = 140,
) -> dict[str, Any]:
    if window not in ALLOWED_WINDOWS:
        raise ValueError(f"Unsupported game window: {window}")

    now = utcnow()

    streak = db.get(GameStreak, 1)
    tokens = db.get(GameToken, 1)

    current_streak = streak.current_streak if streak else 0
    max_streak = streak.max_streak if streak else 0
    last_green_at = streak.last_green_at if streak else None
    break_reason = streak.break_reason if streak else None

    token_balance = tokens.balance if tokens else 0
    token_earned_count = tokens.earned_count if tokens else 0
    token_spent_count = tokens.spent_count if tokens else 0
    correctness = get_or_create_correctness_state(db)
    small_fires, medium_fires, large_fires = fire_breakdown(correctness.fire_units)

    token_threshold = settings.game_token_earn_every_greens
    token_progress_current = current_streak % token_threshold
    next_token_in = token_threshold if token_progress_current == 0 else token_threshold - token_progress_current

    forest_rows = list(
        db.scalars(
            select(GameReceiptStateModel)
            .order_by(GameReceiptStateModel.validated_at.desc(), GameReceiptStateModel.receipt_id.desc())
            .limit(forest_limit)
        )
    )

    latest_receipt_id = forest_rows[0].receipt_id if forest_rows else None

    display_counts: dict[str, int] = {
        GameReceiptState.GREEN.value: 0,
        GameReceiptState.YELLOW.value: 0,
        GameReceiptState.BROWN.value: 0,
        GameReceiptState.SHREDDED.value: 0,
    }

    forest_tiles: list[dict[str, Any]] = []
    for row in forest_rows:
        display_state = _state_for_display(row)
        display_counts[display_state.value] += 1
        forest_tiles.append(
            {
                "receipt_id": row.receipt_id,
                "state": row.state,
                "display_state": display_state.value,
                "validated_at": row.validated_at,
                "age_hours_at_validation": row.age_hours_at_validation,
                "streak_group_id": row.streak_group_id,
                "shredded_at": row.shredded_at,
                "is_latest": row.receipt_id == latest_receipt_id,
            }
        )

    week_start_utc, _ = _window_bounds("week", now, settings)
    weekly_slots_window_end = week_start_utc + timedelta(days=7)
    weekly_slots_rows = list(
        db.scalars(
            select(GameReceiptStateModel).where(
                GameReceiptStateModel.validated_at >= week_start_utc - timedelta(days=(WEEK_SLOT_COUNT - 1) * 7),
                GameReceiptStateModel.validated_at < weekly_slots_window_end,
            )
        )
    )
    weekly_slots = _build_weekly_slots(weekly_slots_rows, now, settings)

    window_start, window_end = _window_bounds(window, now, settings)
    window_rows = list(
        db.scalars(
            select(GameReceiptStateModel).where(
                GameReceiptStateModel.validated_at >= window_start,
                GameReceiptStateModel.validated_at < window_end,
            )
        )
    )

    total_validated = len(window_rows)
    scoring_rows = [row for row in window_rows if row.shredded_at is None]
    green_count = sum(1 for row in scoring_rows if row.state == GameReceiptState.GREEN.value)
    yellow_count = sum(1 for row in scoring_rows if row.state == GameReceiptState.YELLOW.value)
    brown_count = sum(1 for row in scoring_rows if row.state == GameReceiptState.BROWN.value)
    shredded_count = sum(
        1
        for row in window_rows
        if row.shredded_at is not None and row.state in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}
    )

    avg_validation_age_hours = None
    if scoring_rows:
        avg_validation_age_hours = sum(row.age_hours_at_validation for row in scoring_rows) / len(scoring_rows)

    green_percent = (green_count / len(scoring_rows) * 100) if scoring_rows else 0.0

    ratio_target = settings.game_green_ratio_target_percent
    streak_target = settings.game_streak_challenge_target
    shred_target = settings.game_shred_challenge_target

    challenges = [
        {
            "key": "green_ratio",
            "title": f"Keep Forest >= {ratio_target}% Green",
            "description": f"Hold at least {ratio_target}% green receipts this {window}",
            "status": (
                GameChallengeStatus.COMPLETED.value
                if green_percent >= ratio_target
                else GameChallengeStatus.IN_PROGRESS.value
            ),
            "target": float(ratio_target),
            "current": round(green_percent, 2),
            "unit": "%",
            "progress": min(green_percent / ratio_target, 1.0),
        },
        {
            "key": "green_streak",
            "title": f"Build a {streak_target}-Green Streak",
            "description": "Consecutive greens mint shred tokens and compound momentum",
            "status": (
                GameChallengeStatus.COMPLETED.value
                if current_streak >= streak_target
                else GameChallengeStatus.IN_PROGRESS.value
            ),
            "target": float(streak_target),
            "current": float(current_streak),
            "unit": "greens",
            "progress": min(current_streak / streak_target, 1.0),
        },
        {
            "key": "shred_decay",
            "title": f"Shred {shred_target} Decay Receipts",
            "description": f"Spend tokens to clear yellow/brown receipts this {window}",
            "status": (
                GameChallengeStatus.COMPLETED.value
                if shredded_count >= shred_target
                else GameChallengeStatus.IN_PROGRESS.value
            ),
            "target": float(shred_target),
            "current": float(shredded_count),
            "unit": "receipts",
            "progress": min(shredded_count / shred_target, 1.0),
        },
    ]

    spendable_now = token_balance > 0
    if settings.game_shred_daily_spend_cap > 0:
        day_start, day_end = _day_bounds(now, settings)
        spendable_now = spendable_now and _spent_in_window(db, day_start, day_end) < settings.game_shred_daily_spend_cap

    return {
        "generated_at": now,
        "window": window,
        "rules": {
            "green_hours_threshold": settings.game_green_hours_threshold,
            "brown_hours_threshold": settings.game_brown_hours_threshold,
            "token_earn_every_greens": settings.game_token_earn_every_greens,
            "shred_daily_spend_cap": settings.game_shred_daily_spend_cap,
            "water_capacity": settings.game_water_capacity,
            "bucket_capacity": settings.game_bucket_capacity,
            "fire_burn_threshold": settings.game_fire_burn_threshold,
        },
        "momentum": {
            "current_streak": current_streak,
            "max_streak": max_streak,
            "last_green_at": last_green_at,
            "break_reason": break_reason,
            "token_balance": token_balance,
            "token_earned_count": token_earned_count,
            "token_spent_count": token_spent_count,
            "token_threshold": token_threshold,
            "token_progress_current": token_progress_current,
            "next_token_in": next_token_in,
            "spendable_now": spendable_now,
        },
        "forest": {
            "latest_receipt_id": latest_receipt_id,
            "counts": display_counts,
            "receipts": forest_tiles,
            "weekly_slots": weekly_slots,
        },
        "correctness": {
            "water_units": correctness.water_units,
            "water_capacity": settings.game_water_capacity,
            "bucket_capacity": settings.game_bucket_capacity,
            "buckets_filled": int(math.ceil(correctness.water_units / settings.game_bucket_capacity))
            if correctness.water_units
            else 0,
            "fire_units": correctness.fire_units,
            "small_fires": small_fires,
            "medium_fires": medium_fires,
            "large_fires": large_fires,
            "burn_count": correctness.burn_count,
            "last_burned_at": correctness.last_burned_at,
            "last_reconciled_at": correctness.last_reconciled_at,
        },
        "summary": {
            "window": window,
            "window_start": window_start,
            "window_end": window_end,
            "total_validated": total_validated,
            "green_count": green_count,
            "yellow_count": yellow_count,
            "brown_count": brown_count,
            "shredded_count": shredded_count,
            "green_percent": round(green_percent, 2),
            "avg_validation_age_hours": round(avg_validation_age_hours, 2) if avg_validation_age_hours is not None else None,
        },
        "challenges": challenges,
    }

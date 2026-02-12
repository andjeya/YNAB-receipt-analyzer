from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameChallengeStatus, GameEventType, GameReceiptState
from app.models import GameEvent, GameReceiptStateModel, GameStreak, GameToken, Receipt, Validation

ALLOWED_WINDOWS = {"week", "month"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def classify_receipt_state(
    ingested_at: datetime,
    validated_at: datetime,
    settings: Settings,
) -> tuple[GameReceiptState, float]:
    age_hours = max((_as_utc(validated_at) - _as_utc(ingested_at)).total_seconds() / 3600, 0.0)

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


def apply_user_validation_gamification(
    db: Session,
    receipt: Receipt,
    validated_at: datetime,
    settings: Settings,
) -> GameReceiptStateModel:
    existing = db.get(GameReceiptStateModel, receipt.id)
    if existing is not None:
        return existing

    state, age_hours = classify_receipt_state(receipt.ingested_at, validated_at, settings)
    streak = _get_or_create_streak(db)
    tokens = _get_or_create_tokens(db)

    prior_streak = streak.current_streak
    streak_group_id = streak.active_streak_group_id

    if state == GameReceiptState.GREEN:
        streak.current_streak += 1
        streak.max_streak = max(streak.max_streak, streak.current_streak)
        streak.last_green_at = validated_at
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
            created_at=validated_at,
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
                created_at=validated_at,
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
                created_at=validated_at,
            )

    state_row = GameReceiptStateModel(
        receipt_id=receipt.id,
        state=state.value,
        validated_at=validated_at,
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
        },
        created_at=validated_at,
    )

    return state_row


def _window_bounds(window: str, now: datetime) -> tuple[datetime, datetime]:
    anchor = _as_utc(now)

    if window == "month":
        start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end

    # week starts on Monday
    start = (anchor - timedelta(days=anchor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


def _state_for_display(row: GameReceiptStateModel) -> GameReceiptState:
    if row.shredded_at is not None and row.state in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}:
        return GameReceiptState.SHREDDED

    return GameReceiptState(row.state)


def _spent_in_window(db: Session, start: datetime, end: datetime) -> int:
    count = db.scalar(
        select(func.count(GameEvent.id)).where(
            GameEvent.event_type == GameEventType.TOKEN_SPENT.value,
            GameEvent.created_at >= start,
            GameEvent.created_at < end,
        )
    )
    return int(count or 0)


def _first_valid_user_validation_rows(db: Session) -> list[tuple[Receipt, datetime]]:
    first_valid_subquery = (
        select(
            Validation.receipt_id.label("receipt_id"),
            func.min(Validation.created_at).label("validated_at"),
        )
        .where(
            Validation.source == "user",
            Validation.is_valid.is_(True),
        )
        .group_by(Validation.receipt_id)
        .subquery()
    )

    rows = db.execute(
        select(Receipt, first_valid_subquery.c.validated_at)
        .join(first_valid_subquery, first_valid_subquery.c.receipt_id == Receipt.id)
        .order_by(first_valid_subquery.c.validated_at.asc(), Receipt.id.asc())
    ).all()

    return [(receipt, _as_utc(validated_at)) for receipt, validated_at in rows]


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

    for receipt, validated_at in _first_valid_user_validation_rows(db):
        state_row = apply_user_validation_gamification(db, receipt, validated_at, settings)
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

    if settings.game_shred_daily_spend_cap > 0:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
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

    window_start, window_end = _window_bounds(window, now)
    window_rows = list(
        db.scalars(
            select(GameReceiptStateModel).where(
                GameReceiptStateModel.validated_at >= window_start,
                GameReceiptStateModel.validated_at < window_end,
            )
        )
    )

    total_validated = len(window_rows)
    green_count = sum(1 for row in window_rows if row.state == GameReceiptState.GREEN.value)
    yellow_count = sum(1 for row in window_rows if row.state == GameReceiptState.YELLOW.value)
    brown_count = sum(1 for row in window_rows if row.state == GameReceiptState.BROWN.value)
    shredded_count = sum(
        1
        for row in window_rows
        if row.shredded_at is not None and row.state in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}
    )

    avg_validation_age_hours = None
    if total_validated:
        avg_validation_age_hours = sum(row.age_hours_at_validation for row in window_rows) / total_validated

    green_percent = (green_count / total_validated * 100) if total_validated else 0.0

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
        day_start = _as_utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        spendable_now = spendable_now and _spent_in_window(db, day_start, day_end) < settings.game_shred_daily_spend_cap

    return {
        "generated_at": now,
        "window": window,
        "rules": {
            "green_hours_threshold": settings.game_green_hours_threshold,
            "brown_hours_threshold": settings.game_brown_hours_threshold,
            "token_earn_every_greens": settings.game_token_earn_every_greens,
            "shred_daily_spend_cap": settings.game_shred_daily_spend_cap,
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

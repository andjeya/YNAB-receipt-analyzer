from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import GameChallengeStatus, GameEventType, GameReceiptState, YNABSyncStatus
from app.models import (
    GameEvent,
    GameReceiptStateModel,
    GameStreak,
    GameToken,
    GameWeekFire,
    Receipt,
    Validation,
    YNABSync,
)
from app.services.correctness import (
    get_or_create_correctness_state,
    get_burnt_week_count,
    get_total_active_flames,
)
from app.services.debug_seed import get_or_create_debug_seed, unix_ms_to_datetime
from app.services.debug_tools import is_debug_tools_enabled
from app.utils import utcnow

ALLOWED_WINDOWS = {"week", "month"}
WEEK_SLOT_COUNT = 9


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
    """Create a GameReceiptStateModel row for a newly synced receipt.

    In Game v3, streak increments/breaks and per-receipt token earning are
    removed. The GameReceiptStateModel row is still created (unchanged) and
    the RECEIPT_CLASSIFIED event is recorded. Streak is now derived from
    completed-week analysis in get_dashboard_data.
    """
    existing = db.get(GameReceiptStateModel, receipt.id)
    if existing is not None:
        return existing

    transaction_at, has_explicit_time = _transaction_datetime_from_validation(validation, settings)
    if transaction_at is None:
        raise ValueError("Validation payload is missing a valid transaction_date for gamification")

    synced_at_utc = _as_utc(synced_at)
    state, age_hours = classify_receipt_state(transaction_at, synced_at_utc, settings)
    streak = _get_or_create_streak(db)
    streak_group_id = streak.active_streak_group_id

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


# ---------------------------------------------------------------------------
# Derived weekly streak computation
# ---------------------------------------------------------------------------

def _derive_weekly_streak(
    all_rows: list[GameReceiptStateModel],
    week_fires_by_start: dict[str, GameWeekFire],
    now: datetime,
    settings: Settings,
) -> tuple[int, int]:
    """Compute (current_streak, max_streak) from completed-week history.

    A completed week (end_at <= now) is "clean green" iff:
    - >= 1 scored non-shredded receipt
    - worst state is green
    - flames_active == 0
    - not burnt

    Empty weeks (no scored receipts) are skipped — neither break nor count.

    Returns (current_streak, max_streak).
    """
    if not all_rows:
        return 0, 0

    # Build the list of completed weeks in chronological order.
    # Collect all week_start values from receipt history.
    week_starts: set[datetime] = set()
    for row in all_rows:
        ws, _ = _week_bounds_for_timestamp(_as_utc(row.validated_at), settings)
        week_starts.add(ws)

    # Sort chronologically.
    sorted_weeks = sorted(week_starts)

    # Determine current week boundary — only completed weeks count.
    current_week_start, _ = _week_bounds_for_timestamp(now, settings)

    # Build week summaries.
    # Map week_start -> list of rows.
    rows_by_week: dict[datetime, list[GameReceiptStateModel]] = {}
    for row in all_rows:
        ws, _ = _week_bounds_for_timestamp(_as_utc(row.validated_at), settings)
        rows_by_week.setdefault(ws, []).append(row)

    current_streak = 0
    max_streak = 0
    run = 0

    for ws in sorted_weeks:
        # Skip in-progress current week.
        if ws >= current_week_start:
            continue

        week_rows = rows_by_week.get(ws, [])
        scored = [r for r in week_rows if r.shredded_at is None]

        if not scored:
            # Empty week: skip (neither breaks nor counts).
            continue

        # Check fire state for this week.
        ws_key = _as_utc(ws).replace(microsecond=0).isoformat()
        fire_row = week_fires_by_start.get(ws_key)
        has_flames = fire_row is not None and (fire_row.flames_active > 0 or fire_row.burnt)
        is_burnt = fire_row is not None and fire_row.burnt

        worst_state_rank = max(
            {"green": 0, "yellow": 1, "brown": 2}.get(r.state, 0) for r in scored
        )
        is_clean_green = worst_state_rank == 0 and not has_flames and not is_burnt

        if is_clean_green:
            run += 1
            if run > max_streak:
                max_streak = run
        else:
            run = 0

    current_streak = run
    return current_streak, max_streak


def _evaluate_passes(
    db: Session,
    tokens: GameToken,
    current_streak: int,
    max_streak: int,
    all_rows: list[GameReceiptStateModel],
    week_fires_by_start: dict[str, GameWeekFire],
    now: datetime,
    settings: Settings,
) -> None:
    """Idempotently award skip passes for completed clean-green week run multiples.

    A pass is awarded when a completed week's position in a consecutive
    clean-green run is a multiple of game_pass_every_green_weeks (4).
    Award is idempotent via _record_event; never clawed back.

    Accepted asymmetry: a retroactive flame can re-segment a run AFTER a pass
    was awarded; the re-grown run may then mint an extra pass at a new week.
    Combined with no-clawback this mildly over-awards in a rare edge case —
    deliberately accepted (player-favorable, low stakes) over complicating the
    award keying. Reviewed 2026-06-12.
    """
    if not all_rows:
        return

    threshold = settings.game_pass_every_green_weeks

    week_starts: set[datetime] = set()
    for row in all_rows:
        ws, _ = _week_bounds_for_timestamp(_as_utc(row.validated_at), settings)
        week_starts.add(ws)

    sorted_weeks = sorted(week_starts)
    current_week_start, _ = _week_bounds_for_timestamp(now, settings)

    rows_by_week: dict[datetime, list[GameReceiptStateModel]] = {}
    for row in all_rows:
        ws, _ = _week_bounds_for_timestamp(_as_utc(row.validated_at), settings)
        rows_by_week.setdefault(ws, []).append(row)

    run = 0
    for ws in sorted_weeks:
        if ws >= current_week_start:
            continue

        week_rows = rows_by_week.get(ws, [])
        scored = [r for r in week_rows if r.shredded_at is None]

        if not scored:
            continue

        ws_key = _as_utc(ws).replace(microsecond=0).isoformat()
        fire_row = week_fires_by_start.get(ws_key)
        has_flames = fire_row is not None and (fire_row.flames_active > 0 or fire_row.burnt)
        is_burnt = fire_row is not None and fire_row.burnt

        worst_state_rank = max(
            {"green": 0, "yellow": 1, "brown": 2}.get(r.state, 0) for r in scored
        )
        is_clean_green = worst_state_rank == 0 and not has_flames and not is_burnt

        if is_clean_green:
            run += 1
            if run % threshold == 0:
                week_start_iso = _as_utc(ws).replace(microsecond=0).isoformat()
                idem_key = f"pass_earned:week:{week_start_iso}"
                awarded = _record_event(
                    db,
                    GameEventType.PASS_EARNED,
                    idempotency_key=idem_key,
                    receipt_id=None,
                    payload={
                        "run_position": run,
                        "week_start_at": week_start_iso,
                        "threshold": threshold,
                    },
                )
                if awarded:
                    tokens.earned_count += 1
                    tokens.balance += 1
        else:
            run = 0


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


def _build_weekly_slots(
    rows: list[GameReceiptStateModel],
    now: datetime,
    settings: Settings,
    week_fires_by_start: dict[str, GameWeekFire] | None = None,
) -> list[dict[str, Any]]:
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

        # Look up fire state for this week.
        week_start_key = start_at.replace(microsecond=0).isoformat()
        fire_row = (week_fires_by_start or {}).get(week_start_key)
        flames = fire_row.flames_active if fire_row else 0
        burnt = fire_row.burnt if fire_row else False

        # If burnt, display_state becomes "burnt" regardless of receipt states.
        display_state = "burnt" if burnt else state

        slots.append(
            {
                "index": index,
                "start_at": start_at,
                "end_at": end_at,
                "is_empty": state is None,
                "display_state": display_state,
                "receipt_count": len(slot_rows),
                "flames": flames,
                "burnt": burnt,
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


def _first_successful_sync_rows(db: Session, *, sync_floor: datetime | None = None) -> list[tuple[Receipt, Validation, datetime]]:
    rows = db.execute(
        select(Receipt, Validation, YNABSync.completed_at)
        .join(YNABSync, YNABSync.receipt_id == Receipt.id)
        .join(Validation, Validation.id == YNABSync.validation_id)
        .where(
            YNABSync.status.in_([YNABSyncStatus.MATCHED_UPDATED.value, YNABSyncStatus.CREATED.value]),
            YNABSync.completed_at.is_not(None),
            *([YNABSync.completed_at > sync_floor] if sync_floor is not None else []),
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


def _load_week_fires_by_start(db: Session) -> dict[str, GameWeekFire]:
    """Load all GameWeekFire rows keyed by their week_start_at ISO string."""
    rows = list(db.scalars(select(GameWeekFire)))
    return {_as_utc(row.week_start_at).replace(microsecond=0).isoformat(): row for row in rows}


def rebuild_gamification_state(db: Session, settings: Settings) -> dict[str, int]:
    """Rebuild gamification state from scratch.

    In Game v3 the week-fire state must also be reconstructed. We replay
    FIRE_ADDED / WEEK_BURNED / WATER_SPENT events (those associated with
    week-scoped fire) after clearing game_week_fires, so that a rebuild
    produces the same state as live accrual.
    """
    seed = get_or_create_debug_seed(db)
    sync_floor = unix_ms_to_datetime(seed.sync_floor_unix_ms) if seed.enabled else None
    preserved_shreds = {
        receipt_id: _as_utc(shredded_at)
        for receipt_id, shredded_at in db.execute(
            select(GameReceiptStateModel.receipt_id, GameReceiptStateModel.shredded_at).where(
                GameReceiptStateModel.shredded_at.is_not(None)
            )
        ).all()
    }

    preserved_spent = 0
    if not seed.enabled:
        preserved_spent = int(
            db.scalar(
                select(func.count(GameEvent.id)).where(GameEvent.event_type == GameEventType.TOKEN_SPENT.value)
            )
            or 0
        )

    # Clear game state tables (keep GameEvents for replay).
    db.execute(delete(GameReceiptStateModel))
    db.execute(delete(GameStreak))
    db.execute(delete(GameToken))
    db.execute(delete(GameWeekFire))
    db.flush()

    streak = _get_or_create_streak(db)
    tokens = _get_or_create_tokens(db)
    if seed.enabled:
        tokens.balance = seed.token_balance
        tokens.earned_count = seed.token_earned_count
        tokens.spent_count = seed.token_spent_count
        # Seed current_week_flames if set: inject a flame on the current week.
        if seed.current_week_flames > 0:
            now = utcnow()
            from app.services.correctness import _get_or_create_week_fire
            ws, _ = _week_bounds_for_timestamp(now, settings)
            week_row = _get_or_create_week_fire(db, ws)
            week_row.flames_active = min(seed.current_week_flames, settings.game_fire_burn_threshold - 1)
    else:
        tokens.spent_count = preserved_spent
        tokens.balance = 0
        tokens.earned_count = 0

    processed_count = 0
    restored_shreds = 0

    for receipt, validation, completed_at in _first_successful_sync_rows(db, sync_floor=sync_floor):
        state_row = apply_sync_gamification(db, receipt, validation, completed_at, settings)
        processed_count += 1

        preserved_shredded_at = preserved_shreds.get(receipt.id)
        if preserved_shredded_at is None:
            continue
        if state_row.state not in {GameReceiptState.YELLOW.value, GameReceiptState.BROWN.value}:
            continue

        state_row.shredded_at = preserved_shredded_at
        restored_shreds += 1

    # Replay week-fire events from game_events log.
    # FIRE_ADDED events with week_start_at in payload → reconstruct game_week_fires.
    # WEEK_BURNED events → mark week as burnt.
    # WATER_SPENT events with week_start_at → reduce flames on that week.
    _replay_week_fire_events(
        db,
        settings,
        event_floor_id=seed.correctness_event_floor_id if seed.enabled else 0,
    )

    # Re-evaluate skip-pass awards against the replayed state so that awarded
    # passes survive a rebuild (idempotent: uses pass_earned:week:… keys).
    db.flush()
    all_rows_for_passes = list(
        db.scalars(
            select(GameReceiptStateModel).order_by(
                GameReceiptStateModel.validated_at.asc(),
                GameReceiptStateModel.receipt_id.asc(),
            )
        )
    )
    week_fires_for_passes = _load_week_fires_by_start(db)
    _evaluate_passes(
        db,
        tokens,
        current_streak=0,
        max_streak=0,
        all_rows=all_rows_for_passes,
        week_fires_by_start=week_fires_for_passes,
        now=utcnow(),
        settings=settings,
    )

    if not seed.enabled:
        tokens.balance = max(tokens.earned_count - tokens.spent_count, 0)
    else:
        tokens.balance = max(tokens.balance, 0)

    return {
        "processed_receipts": processed_count,
        "restored_shreds": restored_shreds,
    }


def _replay_week_fire_events(db: Session, settings: Settings, event_floor_id: int = 0) -> None:
    """Replay FIRE_ADDED / WEEK_BURNED / WATER_SPENT events to reconstruct game_week_fires.

    `event_floor_id` mirrors the debug seed's `correctness_event_floor_id`:
    when a seed is active, events at or below the floor are masked so seeded
    demo state (e.g. current_week_flames) isn't cancelled by pre-seed history.
    """
    from app.services.correctness import _get_or_create_week_fire

    fire_events = list(
        db.scalars(
            select(GameEvent)
            .where(
                GameEvent.event_type.in_([
                    GameEventType.FIRE_ADDED.value,
                    GameEventType.WEEK_BURNED.value,
                    GameEventType.WATER_SPENT.value,
                ]),
                GameEvent.id > event_floor_id,
            )
            .order_by(GameEvent.created_at.asc(), GameEvent.id.asc())
        )
    )

    from datetime import datetime as dt
    week_rows: dict[str, GameWeekFire] = {}

    def _get_week_row(ws_iso: str) -> GameWeekFire | None:
        if ws_iso in week_rows:
            return week_rows[ws_iso]
        try:
            ws_dt = dt.fromisoformat(ws_iso)
        except ValueError:
            return None
        row = _get_or_create_week_fire(db, ws_dt)
        week_rows[ws_iso] = row
        return row

    for event in fire_events:
        payload = event.payload_json or {}
        ws_iso = payload.get("week_start_at")
        if not ws_iso:
            continue

        if event.event_type == GameEventType.FIRE_ADDED.value:
            already_burnt = payload.get("week_already_burnt", False)
            if already_burnt:
                continue
            row = _get_week_row(ws_iso)
            if row is None or row.burnt:
                continue
            # Determine if this was a forced-douse fire (water was spent, flame not added to count).
            # Forced-douse fires have a companion WATER_SPENT event; they don't increment flames_active.
            # We skip them if there's a forced_douse water event for the same idempotency_key.
            forced_douse_key = event.idempotency_key + ":forced_douse:water"
            if db.scalar(select(GameEvent.id).where(GameEvent.idempotency_key == forced_douse_key)) is not None:
                # This was a forced-douse: no flame increment.
                row.last_flame_at = event.created_at
                continue
            row.flames_active += 1
            row.last_flame_at = event.created_at

        elif event.event_type == GameEventType.WEEK_BURNED.value:
            row = _get_week_row(ws_iso)
            if row is None:
                continue
            row.burnt = True

        elif event.event_type == GameEventType.WATER_SPENT.value:
            # Only week-scoped water spends have week_start_at in payload.
            # Skip forced-douse events: these prevented a flame from being added
            # (the companion FIRE_ADDED was already skipped above), so subtracting
            # here would remove a flame that was never counted.
            if payload.get("reason") == "forced_prevent_week_burn":
                continue
            row = _get_week_row(ws_iso)
            if row is None:
                continue
            units = int(payload.get("units", 1))
            row.flames_active = max(row.flames_active - units, 0)


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

    tokens = db.get(GameToken, 1)

    token_balance = tokens.balance if tokens else 0
    token_earned_count = tokens.earned_count if tokens else 0
    token_spent_count = tokens.spent_count if tokens else 0

    correctness = get_or_create_correctness_state(db)

    # Load all week fire rows.
    week_fires_by_start = _load_week_fires_by_start(db)

    # Load full receipt state history for streak derivation.
    all_receipt_rows = list(
        db.scalars(
            select(GameReceiptStateModel).order_by(
                GameReceiptStateModel.validated_at.asc(),
                GameReceiptStateModel.receipt_id.asc(),
            )
        )
    )

    # Ensure tokens row exists.
    tokens_row = _get_or_create_tokens(db)

    # Pass awards are persisted by the post-sync bookkeeping path (ynab.py)
    # and by rebuild_gamification_state — both sessions commit. This GET path
    # never commits (get_db closes → rollback), so calling _evaluate_passes here
    # would appear to award passes in-memory but roll them back on session close,
    # allowing the same pass to be "awarded" on every GET without ever persisting.
    # Dashboard reads the already-committed balance from tokens_row.
    token_balance = tokens_row.balance
    token_earned_count = tokens_row.earned_count
    token_spent_count = tokens_row.spent_count

    # Derive weekly streak.
    current_streak, max_streak = _derive_weekly_streak(
        all_receipt_rows, week_fires_by_start, now, settings
    )

    # Compute next_pass_in_weeks.
    pass_every = settings.game_pass_every_green_weeks
    streak_mod = current_streak % pass_every
    next_pass_in_weeks = pass_every if streak_mod == 0 and current_streak > 0 else (pass_every - streak_mod)

    # Forest tiles.
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
    weekly_slots = _build_weekly_slots(weekly_slots_rows, now, settings, week_fires_by_start=week_fires_by_start)

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
            "title": f"Build a {streak_target}-Week Streak",
            "description": "Consecutive clean-green weeks earn skip passes",
            "status": (
                GameChallengeStatus.COMPLETED.value
                if current_streak >= streak_target
                else GameChallengeStatus.IN_PROGRESS.value
            ),
            "target": float(streak_target),
            "current": float(current_streak),
            "unit": "weeks",
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

    total_active_flames = get_total_active_flames(db)
    burnt_week_count = get_burnt_week_count(db)

    return {
        "generated_at": now,
        "window": window,
        "debug_tools_enabled": is_debug_tools_enabled(settings),
        "rules": {
            "green_hours_threshold": settings.game_green_hours_threshold,
            "brown_hours_threshold": settings.game_brown_hours_threshold,
            "shred_daily_spend_cap": settings.game_shred_daily_spend_cap,
            "water_capacity": settings.game_water_capacity,
            "fire_burn_threshold": settings.game_fire_burn_threshold,
            "pass_every_green_weeks": settings.game_pass_every_green_weeks,
            # Week slots are bounded in this timezone; the frontend must format
            # week-range labels in it (not the browser tz) or days shift.
            "timezone": settings.game_timezone,
        },
        "momentum": {
            "current_streak": current_streak,
            "max_streak": max_streak,
            "token_balance": token_balance,
            "token_earned_count": token_earned_count,
            "token_spent_count": token_spent_count,
            "pass_every_green_weeks": pass_every,
            "next_pass_in_weeks": next_pass_in_weeks,
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
            "last_reconciled_at": correctness.last_reconciled_at,
            "total_active_flames": total_active_flames,
            "burnt_week_count": burnt_week_count,
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

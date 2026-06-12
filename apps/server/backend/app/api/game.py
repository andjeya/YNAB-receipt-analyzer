from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session, require_debug_tools_enabled
from app.config import Settings
from app.models import GameDebugSeed, GameIncident, GameToken, ReceiptCorrection
from app.schemas import (
    GameDebugSeedOut,
    GameDebugSeedUpdateRequest,
    GameCorrectnessRecomputeResponse,
    GameDashboardOut,
    GameIncidentOut,
    GameRebuildResponse,
    GameReconcileResponse,
    GameShredResponse,
    GameWaterSpendRequest,
    GameWaterSpendResponse,
)
from app.services.correctness import get_or_create_correctness_state, recompute_correctness_state_from_history, spend_water_to_extinguish
from app.models import GameWeekFire
from app.services.debug_seed import apply_debug_seed_to_live_state, get_or_create_debug_seed, mark_debug_seed_floor_now
from app.services.game import ALLOWED_WINDOWS, get_dashboard_data, rebuild_gamification_state, spend_shred_token
from app.services.incidents import acknowledge_incident, list_incidents
from app.services.reconciliation import run_ynab_reconciliation

router = APIRouter(prefix="/game", tags=["game"])
logger = logging.getLogger(__name__)


@router.get("/dashboard", response_model=GameDashboardOut)
def get_game_dashboard(
    window: str = Query(default="week"),
    forest_limit: int = Query(default=140, ge=20, le=400),
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> GameDashboardOut:
    if window not in ALLOWED_WINDOWS:
        allowed = ", ".join(sorted(ALLOWED_WINDOWS))
        raise HTTPException(status_code=400, detail=f"window must be one of: {allowed}")

    return GameDashboardOut.model_validate(get_dashboard_data(db, settings, window=window, forest_limit=forest_limit))


@router.post("/receipts/{receipt_id}/shred", response_model=GameShredResponse)
def shred_receipt(
    receipt_id: str,
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> GameShredResponse:
    try:
        state_row, was_shredded = spend_shred_token(db, settings, receipt_id)
    except ValueError as exc:
        logger.warning("Shred token spend rejected for receipt %s: %s", receipt_id, exc)
        raise HTTPException(status_code=400, detail="Shred not allowed") from exc

    token_row = db.get(GameToken, 1)
    db.commit()

    return GameShredResponse(
        receipt_id=receipt_id,
        was_shredded=was_shredded,
        state="shredded" if state_row.shredded_at is not None else state_row.state,
        token_balance=token_row.balance if token_row else 0,
        token_spent_count=token_row.spent_count if token_row else 0,
    )


@router.post("/rebuild", response_model=GameRebuildResponse)
def rebuild_game(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> GameRebuildResponse:
    result = rebuild_gamification_state(db, settings)
    db.commit()
    return GameRebuildResponse.model_validate(result)


@router.post("/reconcile", response_model=GameReconcileResponse)
def reconcile_game(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> GameReconcileResponse:
    try:
        result = run_ynab_reconciliation(db, settings)
    except ValueError as exc:
        logger.exception("Game reconciliation failed")
        raise HTTPException(status_code=400, detail="Reconciliation failed") from exc
    db.commit()
    return GameReconcileResponse.model_validate(result)


@router.post("/correctness/recompute", response_model=GameCorrectnessRecomputeResponse)
def recompute_correctness(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> GameCorrectnessRecomputeResponse:
    values = recompute_correctness_state_from_history(db, settings)
    correction_count = int(db.scalar(select(func.count(ReceiptCorrection.id))) or 0)
    db.commit()
    return GameCorrectnessRecomputeResponse(
        correction_count=correction_count,
        water_units=values["water_units"],
        fire_units=values["fire_units"],
        burn_count=values["burn_count"],
    )


def _to_incident_schema(row: GameIncident) -> GameIncidentOut:
    return GameIncidentOut(
        id=row.id,
        incident_type=row.incident_type,
        severity=row.severity,
        title=row.title,
        message=row.message,
        details_json=row.details_json,
        created_at=row.created_at,
        acknowledged_at=row.acknowledged_at,
    )


@router.get("/incidents", response_model=list[GameIncidentOut])
def get_incidents(
    pending_only: bool = Query(default=True),
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(db_session),
) -> list[GameIncidentOut]:
    rows = list_incidents(db, pending_only=pending_only, limit=limit)
    return [_to_incident_schema(row) for row in rows]


@router.post("/incidents/{incident_id}/ack", response_model=GameIncidentOut)
def ack_incident(
    incident_id: int,
    db: Session = Depends(db_session),
) -> GameIncidentOut:
    row = acknowledge_incident(db, incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    db.commit()
    db.refresh(row)
    return _to_incident_schema(row)


@router.post("/water/spend", response_model=GameWaterSpendResponse)
def spend_water(
    request: GameWaterSpendRequest,
    db: Session = Depends(db_session),
) -> GameWaterSpendResponse:
    from datetime import timezone as _tz
    from sqlalchemy import select as _select

    # Validate week_start_at: must match an existing game_week_fires row.
    week_start_utc = request.week_start_at.replace(microsecond=0)
    if week_start_utc.tzinfo is None:
        week_start_utc = week_start_utc.replace(tzinfo=_tz.utc)
    else:
        week_start_utc = week_start_utc.astimezone(_tz.utc)

    week_row = db.scalar(_select(GameWeekFire).where(GameWeekFire.week_start_at == week_start_utc))
    if week_row is None:
        raise HTTPException(status_code=400, detail="Week not found")
    if week_row.burnt:
        raise HTTPException(status_code=400, detail="Cannot douse a burnt week")
    if week_row.flames_active <= 0:
        raise HTTPException(status_code=400, detail="No active flames on this week")

    result = spend_water_to_extinguish(
        db,
        units=request.units,
        receipt_id=None,
        idempotency_key=f"manual_water_spend:{request.week_start_at.isoformat()}:{uuid4()}",
        week_start_at=week_start_utc,
    )
    state = get_or_create_correctness_state(db)
    # Refresh week_row after mutation.
    db.flush()
    db.commit()
    db.refresh(week_row)
    return GameWaterSpendResponse(
        waters_spent=int(result.get("waters_spent", 0)),
        fires_extinguished=int(result.get("fires_extinguished", 0)),
        water_units=state.water_units,
        week_flames_active=week_row.flames_active,
    )


def _to_debug_seed_schema(row: GameDebugSeed) -> GameDebugSeedOut:
    return GameDebugSeedOut(
        enabled=row.enabled,
        water_units=row.water_units,
        water_earned_count=row.water_earned_count,
        water_spent_count=row.water_spent_count,
        fire_units=row.fire_units,
        fire_added_count=row.fire_added_count,
        fire_extinguished_count=row.fire_extinguished_count,
        burn_count=row.burn_count,
        token_balance=row.token_balance,
        token_earned_count=row.token_earned_count,
        token_spent_count=row.token_spent_count,
        current_week_flames=row.current_week_flames,
        current_streak=row.current_streak,
        max_streak=row.max_streak,
        active_streak_group_id=row.active_streak_group_id,
        break_reason=row.break_reason,
        correctness_event_floor_id=row.correctness_event_floor_id,
        sync_floor_unix_ms=row.sync_floor_unix_ms,
    )


@router.get("/debug-seed", response_model=GameDebugSeedOut)
def get_debug_seed(
    _: None = Depends(require_debug_tools_enabled),
    db: Session = Depends(db_session),
) -> GameDebugSeedOut:
    seed = get_or_create_debug_seed(db)
    return _to_debug_seed_schema(seed)


@router.post("/debug-seed", response_model=GameDebugSeedOut)
def update_debug_seed(
    request: GameDebugSeedUpdateRequest,
    _: None = Depends(require_debug_tools_enabled),
    db: Session = Depends(db_session),
) -> GameDebugSeedOut:
    seed = get_or_create_debug_seed(db)

    if request.enabled is not None:
        seed.enabled = request.enabled
    if request.water_units is not None:
        seed.water_units = request.water_units
    if request.water_earned_count is not None:
        seed.water_earned_count = request.water_earned_count
    if request.water_spent_count is not None:
        seed.water_spent_count = request.water_spent_count
    if request.fire_units is not None:
        seed.fire_units = request.fire_units
    if request.fire_added_count is not None:
        seed.fire_added_count = request.fire_added_count
    if request.fire_extinguished_count is not None:
        seed.fire_extinguished_count = request.fire_extinguished_count
    if request.burn_count is not None:
        seed.burn_count = request.burn_count
    if request.token_balance is not None:
        seed.token_balance = request.token_balance
    if request.token_earned_count is not None:
        seed.token_earned_count = request.token_earned_count
    if request.token_spent_count is not None:
        seed.token_spent_count = request.token_spent_count
    if request.current_week_flames is not None:
        seed.current_week_flames = max(request.current_week_flames, 0)
    if request.current_streak is not None:
        seed.current_streak = request.current_streak
    if request.max_streak is not None:
        seed.max_streak = request.max_streak
    if request.active_streak_group_id is not None:
        seed.active_streak_group_id = max(request.active_streak_group_id, 1)
    if request.break_reason is not None:
        seed.break_reason = request.break_reason or None

    if request.reset_floors_to_now:
        mark_debug_seed_floor_now(db, seed)

    if request.apply_to_live_state:
        apply_debug_seed_to_live_state(db, seed)

    db.commit()
    db.refresh(seed)
    return _to_debug_seed_schema(seed)

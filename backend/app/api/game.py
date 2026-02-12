from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.models import GameToken
from app.schemas import GameDashboardOut, GameRebuildResponse, GameShredResponse
from app.services.game import ALLOWED_WINDOWS, get_dashboard_data, rebuild_gamification_state, spend_shred_token

router = APIRouter(prefix="/game", tags=["game"])


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
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.models import YNABCache
from app.schemas import AppConfigOut

router = APIRouter(prefix="/config", tags=["config"])
logger = logging.getLogger(__name__)


def _resolve_budget_name(db: Session, budget_id: str | None) -> str | None:
    """Best-effort budget name from cache rows, WITHOUT making any network calls.

    YNAB does not expose a budget-level entity in the cache table.  We fall
    back to None if the name cannot be derived locally.
    """
    if not budget_id:
        return None
    # Check if any cache rows exist for this budget (proves the cache was populated)
    has_rows = db.scalar(
        select(YNABCache.id).where(YNABCache.budget_id == budget_id).limit(1)
    )
    if has_rows is None:
        return None
    # We don't store the budget name in the cache — return None gracefully.
    return None


@router.get("", response_model=AppConfigOut)
def get_app_config(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> AppConfigOut:
    budget_name = _resolve_budget_name(db, settings.ynab_budget_id)
    return AppConfigOut(
        ynab_sync_enabled=settings.ynab_sync_enabled,
        ynab_dry_run=settings.ynab_dry_run,
        ynab_budget_id=settings.ynab_budget_id,
        ynab_budget_name=budget_name,
        new_transaction_flag_color=settings.ynab_new_transaction_flag_color,
        updated_transaction_flag_color=settings.ynab_updated_transaction_flag_color,
    )

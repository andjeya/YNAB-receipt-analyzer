from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.schemas import AppConfigOut
from receipt_shared.ynab_client import YNABClient

router = APIRouter(prefix="/config", tags=["config"])
logger = logging.getLogger(__name__)

# Module-level cache: (access_token, budget_id) → budget name string.
# Populated at most once per process per unique (token, budget) pair.
_budget_name_cache: dict[tuple[str, str], str] = {}


def _fetch_budget_name(settings: Settings) -> str | None:
    """Return the YNAB budget name for the configured budget_id.

    - Returns None (never raises) if sync is disabled, no token/budget_id, or on any error.
    - Result is memoised in _budget_name_cache so the YNAB API is called at most once per process.
    """
    if not settings.ynab_sync_enabled:
        return None
    token = settings.ynab_access_token
    budget_id = settings.ynab_budget_id
    if not token or not budget_id:
        return None

    cache_key = (token, budget_id)
    if cache_key in _budget_name_cache:
        return _budget_name_cache[cache_key]

    try:
        client = YNABClient(token)
        budgets: list[dict[str, Any]] = client.list_budgets()
        for b in budgets:
            if b.get("id") == budget_id:
                name: str | None = b.get("name")
                if name:
                    _budget_name_cache[cache_key] = name
                    return name
        # Budget not found in list (unusual — just return None).
        return None
    except Exception:
        logger.warning("Failed to fetch YNAB budget name for budget_id=%s", budget_id, exc_info=True)
        return None


@router.get("", response_model=AppConfigOut)
def get_app_config(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> AppConfigOut:
    budget_name = _fetch_budget_name(settings)
    return AppConfigOut(
        ynab_sync_enabled=settings.ynab_sync_enabled,
        ynab_dry_run=settings.ynab_dry_run,
        ynab_budget_id=settings.ynab_budget_id,
        ynab_budget_name=budget_name,
        new_transaction_flag_color=settings.ynab_new_transaction_flag_color,
        updated_transaction_flag_color=settings.ynab_updated_transaction_flag_color,
    )

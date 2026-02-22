from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session
from app.config import Settings
from app.schemas import CacheEntityOut, CacheRefreshResponse, FetchYnabUpdatesResponse
from app.services.reconciliation import run_ynab_reconciliation
from app.services.ynab import list_cached_entities, refresh_ynab_cache

router = APIRouter(prefix="/ynab", tags=["ynab"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/cache", response_model=list[CacheEntityOut])
def get_cache(
    entity_type: str | None = Query(default=None),
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> list[CacheEntityOut]:
    entities = list_cached_entities(
        db,
        entity_type=entity_type,
        budget_id=settings.ynab_budget_id,
    )
    return [
        CacheEntityOut(
            entity_type=entity.entity_type,
            entity_id=entity.entity_id,
            name=entity.name,
            group_name=entity.group_name,
            raw_json=entity.raw_json,
            fetched_at=entity.fetched_at,
        )
        for entity in entities
    ]


@router.post("/cache/refresh", response_model=CacheRefreshResponse)
def refresh_cache(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> CacheRefreshResponse:
    try:
        counts = refresh_ynab_cache(db, settings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CacheRefreshResponse(
        refreshed_at=utcnow(),
        category_count=counts["category_count"],
        account_count=counts["account_count"],
        payee_count=counts["payee_count"],
    )


@router.post("/updates/fetch", response_model=FetchYnabUpdatesResponse)
def fetch_ynab_updates(
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> FetchYnabUpdatesResponse:
    try:
        counts = refresh_ynab_cache(db, settings)
        reconciliation = run_ynab_reconciliation(db, settings)
        db.commit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FetchYnabUpdatesResponse(
        refreshed_at=utcnow(),
        category_count=counts["category_count"],
        account_count=counts["account_count"],
        payee_count=counts["payee_count"],
        run_id=int(reconciliation.get("run_id", 0)),
        scanned_receipts=int(reconciliation.get("scanned_receipts", 0)),
        detected_mistakes=int(reconciliation.get("detected_mistakes", 0)),
        applied_penalties=int(reconciliation.get("applied_penalties", 0)),
        fires_added=int(reconciliation.get("fires_added", 0)),
        waters_spent=int(reconciliation.get("waters_spent", 0)),
        burns_triggered=int(reconciliation.get("burns_triggered", 0)),
    )

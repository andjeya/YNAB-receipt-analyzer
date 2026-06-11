"""Debug-only API for managing learned card→account mappings.

All endpoints require debug tools to be enabled (404 when disabled).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import app_settings, db_session, require_debug_tools_enabled
from app.config import Settings
from app.schemas import CardMappingListOut, CardMappingOut, CardMappingUpsertRequest
from app.services.card_mapping import (
    delete_card_mapping,
    get_card_mapping,
    list_card_mappings,
    upsert_card_mapping,
)
from receipt_shared.contracts import normalize_card_last_four

router = APIRouter(prefix="/debug/card-mappings", tags=["debug-card-mappings"])
logger = logging.getLogger(__name__)


@router.get("", response_model=CardMappingListOut)
def list_mappings(
    _: None = Depends(require_debug_tools_enabled),
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> CardMappingListOut:
    budget_id = settings.ynab_budget_id or ""
    pairs = list_card_mappings(db, budget_id)
    items = [
        CardMappingOut(
            id=mapping.id,
            card_last_four=mapping.card_last_four,
            account_id=mapping.account_id,
            account_name=account_name,
        )
        for mapping, account_name in pairs
    ]
    return CardMappingListOut(items=items)


@router.put("", response_model=CardMappingOut)
def upsert_mapping(
    body: CardMappingUpsertRequest,
    _: None = Depends(require_debug_tools_enabled),
    db: Session = Depends(db_session),
    settings: Settings = Depends(app_settings),
) -> CardMappingOut:
    # Validate card_last_four
    normalized = normalize_card_last_four(body.card_last_four)
    if normalized is None:
        raise HTTPException(status_code=422, detail="card_last_four must contain at least 4 digits")

    budget_id = settings.ynab_budget_id or ""
    mapping = upsert_card_mapping(db, budget_id=budget_id, card_last_four=body.card_last_four, account_id=body.account_id)

    if mapping is None:
        raise HTTPException(status_code=422, detail="account_id not found in YNAB cache")

    db.commit()
    db.refresh(mapping)

    # Fetch account name for response
    from app.services.card_mapping import list_card_mappings
    pairs = list_card_mappings(db, budget_id)
    account_name: str | None = None
    for m, name in pairs:
        if m.id == mapping.id:
            account_name = name
            break

    return CardMappingOut(
        id=mapping.id,
        card_last_four=mapping.card_last_four,
        account_id=mapping.account_id,
        account_name=account_name,
    )


@router.delete("/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: int,
    _: None = Depends(require_debug_tools_enabled),
    db: Session = Depends(db_session),
) -> Response:
    deleted = delete_card_mapping(db, mapping_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Card mapping not found")
    db.commit()
    return Response(status_code=204)

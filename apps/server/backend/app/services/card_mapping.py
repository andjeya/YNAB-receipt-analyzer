"""Service for learned card→account mapping.

Key rules:
- key = normalized trailing 4 digits (None for cash / non-numeric)
- unique (budget_id, card_last_four), last-write-wins
- lookup only returns accounts present in YNABCache (mapped account ∈ cache ⊆ allowed_account_ids)
- upsert does NOT commit (caller must commit)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.enums import YNABCacheEntityType
from app.models import CardAccountMapping, YNABCache
from app.services.validation import UNKNOWN_ACCOUNT_ID
from receipt_shared.contracts import normalize_card_last_four

logger = logging.getLogger(__name__)


def _account_exists(db: Any, budget_id: str, account_id: str) -> bool:
    """Return True if the account_id exists in the YNAB cache for this budget.

    Returns False for blank or __unknown__ values.
    """
    if not account_id or not account_id.strip():
        return False
    if account_id == UNKNOWN_ACCOUNT_ID:
        return False
    row = db.scalar(
        select(YNABCache).where(
            YNABCache.budget_id == budget_id,
            YNABCache.entity_type == YNABCacheEntityType.ACCOUNT.value,
            YNABCache.entity_id == account_id,
        )
    )
    return row is not None


def lookup_account_for_card(db: Any, budget_id: str, card_last_four: str | None) -> str | None:
    """Return the mapped account_id for this card, or None.

    Returns None when:
    - card_last_four normalizes to None (cash, non-numeric, < 4 digits)
    - budget_id is blank
    - no mapping row exists
    - the mapped account is no longer in the YNAB cache (stale)
    """
    normalized = normalize_card_last_four(card_last_four)
    if not normalized:
        return None
    if not budget_id or not budget_id.strip():
        return None

    mapping = db.scalar(
        select(CardAccountMapping).where(
            CardAccountMapping.budget_id == budget_id,
            CardAccountMapping.card_last_four == normalized,
        )
    )
    if mapping is None:
        return None

    # Guard: only return the account if it still exists in the cache.
    if not _account_exists(db, budget_id, mapping.account_id):
        logger.debug(
            "Card mapping for card_last_four=%s budget_id=%s points to stale account %s — ignoring",
            normalized,
            budget_id,
            mapping.account_id,
        )
        return None

    return mapping.account_id


def upsert_card_mapping(
    db: Any,
    budget_id: str,
    card_last_four: str | None,
    account_id: str,
) -> CardAccountMapping | None:
    """Create or update a card→account mapping. Does NOT commit.

    Returns None (no-op) when:
    - card_last_four normalizes to None
    - account_id is blank, __unknown__, or not in the YNAB cache
    """
    normalized = normalize_card_last_four(card_last_four)
    if not normalized:
        return None
    if not budget_id or not budget_id.strip():
        return None
    if not _account_exists(db, budget_id, account_id):
        return None

    # Try to find an existing row and update it (last-write-wins).
    existing = db.scalar(
        select(CardAccountMapping).where(
            CardAccountMapping.budget_id == budget_id,
            CardAccountMapping.card_last_four == normalized,
        )
    )
    if existing is not None:
        existing.account_id = account_id
        db.add(existing)
        return existing

    # No existing row — try to create one (guard against concurrent insert race).
    def _create() -> CardAccountMapping:
        mapping = CardAccountMapping(
            budget_id=budget_id,
            card_last_four=normalized,
            account_id=account_id,
        )
        db.add(mapping)
        db.flush()
        return mapping

    try:
        with db.begin_nested():
            return _create()
    except IntegrityError:
        # Another writer raced us. The begin_nested() context manager has ALREADY
        # rolled back to its savepoint, leaving the outer transaction (and any
        # in-flight bookkeeping writes from the caller) intact. Do NOT call
        # db.rollback() here — a full-session rollback would discard the caller's
        # pending gamification/corrections writes. Just re-fetch and update.
        existing = db.scalar(
            select(CardAccountMapping).where(
                CardAccountMapping.budget_id == budget_id,
                CardAccountMapping.card_last_four == normalized,
            )
        )
        if existing is not None:
            existing.account_id = account_id
            db.add(existing)
            return existing
        # Extremely unlikely; try one more time without a savepoint.
        return _create()


def list_card_mappings(
    db: Any,
    budget_id: str,
) -> list[tuple[CardAccountMapping, str | None]]:
    """Return all mappings for a budget, each paired with the account name (or None if stale)."""
    mappings = list(
        db.scalars(
            select(CardAccountMapping).where(
                CardAccountMapping.budget_id == budget_id,
            ).order_by(CardAccountMapping.card_last_four)
        )
    )
    result: list[tuple[CardAccountMapping, str | None]] = []
    for mapping in mappings:
        cache_row = db.scalar(
            select(YNABCache).where(
                YNABCache.budget_id == budget_id,
                YNABCache.entity_type == YNABCacheEntityType.ACCOUNT.value,
                YNABCache.entity_id == mapping.account_id,
            )
        )
        account_name = cache_row.name if cache_row is not None else None
        result.append((mapping, account_name))
    return result


def get_card_mapping(db: Any, mapping_id: int) -> CardAccountMapping | None:
    """Fetch a single mapping by primary key."""
    return db.get(CardAccountMapping, mapping_id)


def delete_card_mapping(db: Any, mapping_id: int) -> bool:
    """Delete a mapping by primary key. Returns True if deleted, False if not found."""
    mapping = db.get(CardAccountMapping, mapping_id)
    if mapping is None:
        return False
    db.delete(mapping)
    db.flush()
    return True

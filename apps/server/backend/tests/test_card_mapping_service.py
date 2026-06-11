"""Tests for the card_mapping service layer."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.enums import YNABCacheEntityType
from app.models import CardAccountMapping, YNABCache
from app.services.card_mapping import (
    lookup_account_for_card,
    upsert_card_mapping,
    list_card_mappings,
    get_card_mapping,
    delete_card_mapping,
)


BUDGET_ID = "budget-test"


def _add_account(db: Session, account_id: str, name: str = "Test Account") -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.ACCOUNT.value,
            entity_id=account_id,
            name=name,
            group_name=None,
            raw_json={"id": account_id, "name": name},
        )
    )
    db.commit()


def _add_mapping(db: Session, card_last_four: str, account_id: str) -> CardAccountMapping:
    mapping = CardAccountMapping(
        budget_id=BUDGET_ID,
        card_last_four=card_last_four,
        account_id=account_id,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


class TestLookupAccountForCard:
    def test_lookup_hit_returns_account_id(self, db_session: Session):
        _add_account(db_session, "acct-1")
        _add_mapping(db_session, "5830", "acct-1")
        result = lookup_account_for_card(db_session, BUDGET_ID, "5830")
        assert result == "acct-1"

    def test_lookup_miss_unseen_card_returns_none(self, db_session: Session):
        _add_account(db_session, "acct-1")
        result = lookup_account_for_card(db_session, BUDGET_ID, "9999")
        assert result is None

    def test_lookup_returns_none_when_mapped_account_deleted_from_cache(self, db_session: Session):
        # Map the card but don't put the account in the cache (simulates deleted/stale account).
        _add_mapping(db_session, "5830", "acct-stale")
        result = lookup_account_for_card(db_session, BUDGET_ID, "5830")
        assert result is None

    def test_lookup_returns_none_for_null_card(self, db_session: Session):
        _add_account(db_session, "acct-1")
        _add_mapping(db_session, "5830", "acct-1")
        result = lookup_account_for_card(db_session, BUDGET_ID, None)
        assert result is None

    def test_lookup_returns_none_for_cash_card(self, db_session: Session):
        _add_account(db_session, "acct-1")
        result = lookup_account_for_card(db_session, BUDGET_ID, "cash")
        assert result is None

    def test_lookup_normalizes_masked_pan(self, db_session: Session):
        _add_account(db_session, "acct-1")
        _add_mapping(db_session, "5830", "acct-1")
        result = lookup_account_for_card(db_session, BUDGET_ID, "**** **** **** 5830")
        assert result == "acct-1"

    def test_lookup_blank_budget_id_returns_none(self, db_session: Session):
        _add_account(db_session, "acct-1")
        _add_mapping(db_session, "5830", "acct-1")
        result = lookup_account_for_card(db_session, "", "5830")
        assert result is None


class TestUpsertCardMapping:
    def test_upsert_creates_new_row(self, db_session: Session):
        _add_account(db_session, "acct-1")
        mapping = upsert_card_mapping(db_session, BUDGET_ID, "5830", "acct-1")
        db_session.commit()
        assert mapping is not None
        assert mapping.card_last_four == "5830"
        assert mapping.account_id == "acct-1"
        assert mapping.budget_id == BUDGET_ID

    def test_upsert_updates_existing_row_last_write_wins(self, db_session: Session):
        _add_account(db_session, "acct-1")
        _add_account(db_session, "acct-2", "Second Account")
        # Create initial mapping.
        _add_mapping(db_session, "5830", "acct-1")
        # Upsert with new account should update.
        mapping = upsert_card_mapping(db_session, BUDGET_ID, "5830", "acct-2")
        db_session.commit()
        assert mapping is not None
        assert mapping.account_id == "acct-2"
        # Confirm single row in db.
        rows = list(db_session.scalars(
            __import__("sqlalchemy", fromlist=["select"]).select(CardAccountMapping).where(
                CardAccountMapping.budget_id == BUDGET_ID,
                CardAccountMapping.card_last_four == "5830",
            )
        ))
        assert len(rows) == 1

    def test_upsert_noop_null_card(self, db_session: Session):
        _add_account(db_session, "acct-1")
        result = upsert_card_mapping(db_session, BUDGET_ID, None, "acct-1")
        assert result is None

    def test_upsert_noop_unknown_account(self, db_session: Session):
        result = upsert_card_mapping(db_session, BUDGET_ID, "5830", "__unknown__")
        assert result is None

    def test_upsert_noop_blank_account(self, db_session: Session):
        result = upsert_card_mapping(db_session, BUDGET_ID, "5830", "")
        assert result is None

    def test_upsert_noop_account_not_in_cache(self, db_session: Session):
        # Account ID provided but not in the YNAB cache.
        result = upsert_card_mapping(db_session, BUDGET_ID, "5830", "acct-nonexistent")
        assert result is None

    def test_upsert_normalizes_masked_pan(self, db_session: Session):
        _add_account(db_session, "acct-1")
        mapping = upsert_card_mapping(db_session, BUDGET_ID, "**** **** **** 5830", "acct-1")
        db_session.commit()
        assert mapping is not None
        assert mapping.card_last_four == "5830"

    def test_upsert_cash_is_noop(self, db_session: Session):
        _add_account(db_session, "acct-1")
        result = upsert_card_mapping(db_session, BUDGET_ID, "cash", "acct-1")
        assert result is None


class TestManyCardsOneAccount:
    def test_many_cards_map_to_one_account(self, db_session: Session):
        _add_account(db_session, "acct-1")
        for card in ("1111", "2222", "3333"):
            upsert_card_mapping(db_session, BUDGET_ID, card, "acct-1")
        db_session.commit()

        for card in ("1111", "2222", "3333"):
            result = lookup_account_for_card(db_session, BUDGET_ID, card)
            assert result == "acct-1"


class TestListGetDelete:
    def test_list_returns_account_name(self, db_session: Session):
        _add_account(db_session, "acct-1", "Checking")
        _add_mapping(db_session, "5830", "acct-1")
        pairs = list_card_mappings(db_session, BUDGET_ID)
        assert len(pairs) == 1
        mapping, name = pairs[0]
        assert mapping.card_last_four == "5830"
        assert name == "Checking"

    def test_list_stale_account_has_none_name(self, db_session: Session):
        _add_mapping(db_session, "9999", "acct-stale")
        pairs = list_card_mappings(db_session, BUDGET_ID)
        assert len(pairs) == 1
        _, name = pairs[0]
        assert name is None

    def test_get_card_mapping_returns_row(self, db_session: Session):
        _add_account(db_session, "acct-1")
        mapping = _add_mapping(db_session, "5830", "acct-1")
        fetched = get_card_mapping(db_session, mapping.id)
        assert fetched is not None
        assert fetched.id == mapping.id

    def test_get_card_mapping_returns_none_for_missing(self, db_session: Session):
        result = get_card_mapping(db_session, 99999)
        assert result is None

    def test_delete_card_mapping_removes_row(self, db_session: Session):
        _add_account(db_session, "acct-1")
        mapping = _add_mapping(db_session, "5830", "acct-1")
        deleted = delete_card_mapping(db_session, mapping.id)
        db_session.commit()
        assert deleted is True
        assert get_card_mapping(db_session, mapping.id) is None

    def test_delete_missing_returns_false(self, db_session: Session):
        result = delete_card_mapping(db_session, 99999)
        assert result is False


def test_upsert_never_full_rollbacks_callers_transaction(db_session: Session, monkeypatch):
    """Regression for the checker's MAJOR finding: upsert_card_mapping must NEVER
    issue a full db.rollback(), which would discard the caller's in-flight
    bookkeeping writes (gamification/corrections) pending in the same transaction
    inside _apply_post_sync. The IntegrityError race branch relies on
    begin_nested()'s savepoint rollback only.

    (A genuine cross-connection unique race can't be reproduced in single-session
    in-memory SQLite, so we guard the actual defect directly: no full rollback,
    and an unrelated pending write survives an upsert.)"""
    from sqlalchemy import select

    _add_account(db_session, "acct-1")
    db_session.add(
        CardAccountMapping(budget_id=BUDGET_ID, card_last_four="9999", account_id="acct-1")
    )

    rollback_calls = {"n": 0}
    real_rollback = db_session.rollback

    def spy_rollback(*args, **kwargs):
        rollback_calls["n"] += 1
        return real_rollback(*args, **kwargs)

    monkeypatch.setattr(db_session, "rollback", spy_rollback)
    result = upsert_card_mapping(db_session, BUDGET_ID, "1234", "acct-1")
    monkeypatch.setattr(db_session, "rollback", real_rollback)
    db_session.commit()

    assert rollback_calls["n"] == 0
    cards = {m.card_last_four for m in db_session.scalars(select(CardAccountMapping))}
    assert "9999" in cards
    assert "1234" in cards
    assert result is not None

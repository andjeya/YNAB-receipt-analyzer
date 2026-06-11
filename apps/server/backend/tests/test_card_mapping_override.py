"""Tests for the card→account mapping override in _validate_ynab_payload."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.enums import YNABCacheEntityType
from app.jobs.tasks import _validate_ynab_payload
from app.models import CardAccountMapping, YNABCache


BUDGET_ID = "test-budget"


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


def _add_category(db: Session, cat_id: str = "cat-1", name: str = "Groceries") -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id=cat_id,
            name=name,
            group_name="Everyday",
            raw_json={"id": cat_id, "name": name},
        )
    )
    db.commit()


def _add_mapping(db: Session, card_last_four: str, account_id: str) -> None:
    db.add(CardAccountMapping(
        budget_id=BUDGET_ID,
        card_last_four=card_last_four,
        account_id=account_id,
    ))
    db.commit()


BASE_EXTRACTION = {
    "payee_name": "Test Store",
    "account_id": "acct-ai",
    "transaction_date": "2026-01-15",
    "total_amount": 25.00,
    "category_id": "cat-1",
    "splits": [],
    "card_last_four": "5830",
}


class TestMappingOverridesAIGuess:
    def test_mapping_overrides_confident_ai_account(self, db_session: Session):
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Guess Account")
        _add_account(db_session, "acct-mapped", "Mapped Card Account")
        _add_mapping(db_session, "5830", "acct-mapped")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai", "acct-mapped"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert payload["account_id"] == "acct-mapped"

    def test_stale_mapping_ignored_ai_stands(self, db_session: Session):
        """Mapped account removed from cache — lookup returns None, AI guess used."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Guess Account")
        # Mapping points to a stale account (not in cache).
        _add_mapping(db_session, "5830", "acct-stale")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert payload["account_id"] == "acct-ai"

    def test_no_mapping_ai_stands(self, db_session: Session):
        """No mapping for this card — AI guess is used unchanged."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert payload["account_id"] == "acct-ai"

    def test_override_beats_default_account_id(self, db_session: Session):
        """Mapping overrides the default_account_id fallback."""
        _add_category(db_session)
        _add_account(db_session, "acct-default", "Default Account")
        _add_account(db_session, "acct-mapped", "Mapped Account")
        _add_mapping(db_session, "5830", "acct-mapped")

        extraction = {**BASE_EXTRACTION, "account_id": ""}
        payload, is_valid, errors = _validate_ynab_payload(
            extraction,
            default_account_id="acct-default",
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-default", "acct-mapped"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert payload["account_id"] == "acct-mapped"

    def test_mapped_account_keeps_is_valid_true(self, db_session: Session):
        """Override account is ∈ cache ⊆ allowed_account_ids → is_valid stays True."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai")
        _add_account(db_session, "acct-mapped")
        _add_mapping(db_session, "5830", "acct-mapped")

        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai", "acct-mapped"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert errors == []

    def test_no_db_no_override(self):
        """Without a db session no lookup is attempted; AI guess passes through."""
        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai"},
            db=None,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert payload["account_id"] == "acct-ai"

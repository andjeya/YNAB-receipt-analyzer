"""Tests for account_source='card_mapping' flag on validation payloads.

Covers:
(a) mapping fires → payload contains account_source='card_mapping'
(b) no mapping → account_source absent (None)
(c) round-trip through detail endpoint shows the field
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.enums import YNABCacheEntityType
from app.jobs.tasks import _validate_ynab_payload
from app.models import CardAccountMapping, YNABCache


BUDGET_ID = "budget-source-test"


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


def _add_category(db: Session, cat_id: str = "cat-1") -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id=cat_id,
            name="Groceries",
            group_name="Everyday",
            raw_json={"id": cat_id, "name": "Groceries"},
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
    "card_last_four": "1234",
}


class TestAccountSourceField:
    def test_mapping_fires_sets_account_source(self, db_session: Session):
        """When a card mapping overrides the account, account_source='card_mapping' is in the payload."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Account")
        _add_account(db_session, "acct-mapped", "Card Account")
        _add_mapping(db_session, "1234", "acct-mapped")

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
        assert payload.get("account_source") == "card_mapping"

    def test_no_mapping_account_source_absent(self, db_session: Session):
        """When no mapping fires, account_source is absent or None in the payload."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Account")
        # No mapping row for card 1234

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
        # account_source must be absent or falsy
        assert not payload.get("account_source")

    def test_stale_mapping_no_account_source(self, db_session: Session):
        """When mapping points to a stale account (not in cache), no override fires, no account_source."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Account")
        _add_mapping(db_session, "1234", "acct-stale")  # stale — not in cache

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
        assert not payload.get("account_source")

    def test_no_db_no_account_source(self):
        """Without a db session no mapping fires and account_source is absent."""
        payload, is_valid, errors = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai"},
            db=None,
            budget_id=BUDGET_ID,
        )

        assert is_valid is True
        assert not payload.get("account_source")


class TestAccountSourceRoundTrip:
    """Verify that account_source survives a JSON round-trip (as it would through DB storage)."""

    def test_account_source_survives_json_roundtrip(self, db_session: Session):
        """account_source stored as part of the payload JSON is returned as-is."""
        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Account")
        _add_account(db_session, "acct-mapped", "Card Account")
        _add_mapping(db_session, "1234", "acct-mapped")

        payload, is_valid, _ = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai", "acct-mapped"},
            db=db_session,
            budget_id=BUDGET_ID,
        )

        # Simulate DB round-trip: JSON encode → decode
        serialized = json.loads(json.dumps(payload))
        assert serialized.get("account_source") == "card_mapping"

    def test_detail_endpoint_schema_passes_account_source_through(self, db_session: Session):
        """ValidationOut.payload is dict[str, Any] — account_source is preserved as-is.

        This verifies the serialization path used by _to_validation_schema in
        the detail endpoint: the payload dict is assigned to ValidationOut.payload
        without any filtering, so account_source flows through to the API response.
        """
        from app.schemas import ValidationOut
        from app.utils import utcnow

        _add_category(db_session)
        _add_account(db_session, "acct-ai", "AI Account")
        _add_account(db_session, "acct-mapped", "Card Account")
        _add_mapping(db_session, "1234", "acct-mapped")

        # Build a validation payload as the worker would
        payload, is_valid, _ = _validate_ynab_payload(
            BASE_EXTRACTION,
            default_account_id=None,
            allowed_category_ids={"cat-1"},
            allowed_account_ids={"acct-ai", "acct-mapped"},
            db=db_session,
            budget_id=BUDGET_ID,
        )
        assert payload.get("account_source") == "card_mapping"

        # Serialize via ValidationOut (mimics _to_validation_schema in the detail endpoint)
        out = ValidationOut(
            id=1,
            version=1,
            source="model",
            payload=payload,
            allocation_workspace=None,
            is_valid=True,
            errors=[],
            created_at=utcnow(),
        )
        assert out.payload.get("account_source") == "card_mapping"

        # Confirm it survives model_dump (the JSON serialization path)
        dumped = out.model_dump()
        assert dumped["payload"].get("account_source") == "card_mapping"

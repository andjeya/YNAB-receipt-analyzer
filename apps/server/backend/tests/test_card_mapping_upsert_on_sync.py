"""Tests for card→account mapping upsert triggered on successful sync.

Covers:
- After successful sync w/ twin card + cached account → mapping row created.
- Card None in twin → no row, sync SYNCED.
- Account __unknown__ in validation → no row (service-level no-op verified).
- NON-FATAL: upsert_card_mapping raises → sync still SYNCED.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.models import CardAccountMapping, Receipt, ReceiptTwin, Validation, YNABSync
from app.services.card_mapping import upsert_card_mapping
from app.services.ynab import sync_receipt_to_ynab
from receipt_shared.ynab_client import YNABClient


BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"
RECEIPT_ID = "cc111111-2222-4333-8444-555566667777"
TXN_ID = "txn-upsert-test"


def _live_settings(**overrides: Any) -> Settings:
    base = dict(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        ynab_access_token="test-token",
        ynab_budget_id=BUDGET_ID,
        ynab_default_account_id=ACCOUNT_ID,
        ynab_sync_enabled=True,
        ynab_dry_run=False,
        object_store_root="./data",
        ingest_dir="./data/ingest",
    )
    base.update(overrides)
    return Settings(**base)


def _mock_ynab_client() -> MagicMock:
    client = MagicMock(spec=YNABClient)
    client.create_transaction.return_value = {"id": TXN_ID}
    client.update_transaction.return_value = {}
    client.list_transactions_since.return_value = []
    client.get_transaction.return_value = {}
    client.delete_transaction.return_value = {}
    return client


def _seed_full(
    db: Any,
    *,
    account_id: str = ACCOUNT_ID,
    card_last_four: str | None = "5830",
    validation_account_id: str | None = None,
) -> tuple[Receipt, Validation, ReceiptTwin]:
    receipt = Receipt(
        id=RECEIPT_ID,
        storage_key=f"receipts/{RECEIPT_ID}.jpg",
        original_filename="test.jpg",
        file_hash=f"hash-upsert-{RECEIPT_ID}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()

    v_account = validation_account_id or account_id
    validation = Validation(
        receipt_id=receipt.id,
        version=1,
        source="user",
        payload={
            "payee_name": "Test Store",
            "account_id": v_account,
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "test",
            "total_amount": 25.0,
            "category_id": CATEGORY_ID,
            "splits": [],
            "transaction_kind": "purchase",
        },
        allocation_workspace=None,
        is_valid=True,
        errors=[],
    )
    db.add(validation)
    receipt.latest_validation_version = 1

    twin_payload: dict[str, Any] = {
        "store_name": "Test Store",
        "total_amount": 25.0,
        "payment_method": "Visa",
        "card_last_four": card_last_four,
        "currency": "USD",
        "receipt_language": "en",
        "line_items": [],
    }
    twin = ReceiptTwin(
        receipt_id=receipt.id,
        version=1,
        source="model",
        payload=twin_payload,
        confirmed_sections={"date_time": True, "total": True},
    )
    db.add(twin)
    receipt.latest_twin_version = 1

    db.commit()
    db.refresh(receipt)
    db.refresh(validation)
    db.refresh(twin)
    return receipt, validation, twin


def test_sync_creates_card_mapping_row(db_with_cache: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """After successful sync with twin card + cached account → mapping row exists."""
    _seed_full(db_with_cache)
    mock_client = _mock_ynab_client()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.CREATED.value

    rows = list(db_with_cache.scalars(
        sa_select(CardAccountMapping).where(
            CardAccountMapping.budget_id == BUDGET_ID,
            CardAccountMapping.card_last_four == "5830",
        )
    ))
    assert len(rows) == 1
    assert rows[0].account_id == ACCOUNT_ID


def test_sync_with_null_card_does_not_create_mapping(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Card None in twin → no mapping row, but sync still SYNCED."""
    _seed_full(db_with_cache, card_last_four=None)
    mock_client = _mock_ynab_client()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    assert result["status"] == YNABSyncStatus.CREATED.value

    db_with_cache.expire_all()
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt.status == ReceiptStatus.SYNCED.value

    rows = list(db_with_cache.scalars(sa_select(CardAccountMapping)))
    assert len(rows) == 0


def test_unknown_account_does_not_create_mapping(db_with_cache: Any) -> None:
    """upsert_card_mapping is a no-op when account_id == __unknown__.

    This tests the service-level guard: even if __unknown__ were passed in
    (e.g. from a validation payload with an unresolved account), no row is created.
    """
    result = upsert_card_mapping(db_with_cache, BUDGET_ID, "5830", "__unknown__")
    assert result is None

    rows = list(db_with_cache.scalars(sa_select(CardAccountMapping)))
    assert len(rows) == 0


def test_upsert_card_mapping_raises_is_non_fatal(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """upsert_card_mapping raises → sync still SYNCED (bookkeeping error non-fatal)."""
    _seed_full(db_with_cache, card_last_four="5830")
    mock_client = _mock_ynab_client()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("upsert_card_mapping exploded")

    monkeypatch.setattr("app.services.ynab.upsert_card_mapping", _raise)

    result = sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=RECEIPT_ID,
        force_create=False,
        allow_update_match=True,
    )

    # Sync must still succeed
    assert result["status"] == YNABSyncStatus.CREATED.value

    db_with_cache.expire_all()
    receipt = db_with_cache.get(Receipt, RECEIPT_ID)
    assert receipt.status == ReceiptStatus.SYNCED.value

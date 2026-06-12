"""Tests for payee→category memory upsert triggered on successful sync.

Covers:
- Single-category sync → memory row with category_id created.
- Split sync → memory row with template_json created.
- upsert_payee_memory raises → sync still SYNCED (non-fatal).
- Blank payee → no row (service-level no-op).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.models import PayeeCategoryMemory, Receipt, ReceiptTwin, Validation, YNABSync
from app.services.ynab import sync_receipt_to_ynab
from receipt_shared.ynab_client import YNABClient


BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"
RECEIPT_ID = "pm111111-2222-4333-8444-555566667777"
TXN_ID = "txn-pm-test"


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


def _seed_single_category(db: Any, *, payee_name: str = "Test Store") -> tuple[Receipt, Validation, ReceiptTwin]:
    receipt = Receipt(
        id=RECEIPT_ID,
        storage_key=f"receipts/{RECEIPT_ID}.jpg",
        original_filename="test.jpg",
        file_hash=f"hash-pm-single-{RECEIPT_ID}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()

    validation = Validation(
        receipt_id=receipt.id,
        version=1,
        source="user",
        payload={
            "payee_name": payee_name,
            "account_id": ACCOUNT_ID,
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

    twin = ReceiptTwin(
        receipt_id=receipt.id,
        version=1,
        source="model",
        payload={
            "store_name": payee_name,
            "total_amount": 25.0,
            "payment_method": "Visa",
            "card_last_four": None,
            "currency": "USD",
            "receipt_language": "en",
            "line_items": [],
        },
        confirmed_sections={"date_time": True, "total": True},
    )
    db.add(twin)
    receipt.latest_twin_version = 1

    db.commit()
    db.refresh(receipt)
    return receipt, validation, twin


def _seed_split(db: Any, *, payee_name: str = "Grocery Store") -> tuple[Receipt, Validation, ReceiptTwin]:
    receipt = Receipt(
        id=RECEIPT_ID,
        storage_key=f"receipts/{RECEIPT_ID}.jpg",
        original_filename="test.jpg",
        file_hash=f"hash-pm-split-{RECEIPT_ID}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db.add(receipt)
    db.flush()

    validation = Validation(
        receipt_id=receipt.id,
        version=1,
        source="user",
        payload={
            "payee_name": payee_name,
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "test",
            "total_amount": 25.0,
            "category_id": None,
            "splits": [
                {"category_id": CATEGORY_ID, "amount": 15.0, "memo": ""},
                {"category_id": "cat-2", "amount": 10.0, "memo": ""},
            ],
            "transaction_kind": "purchase",
        },
        allocation_workspace=None,
        is_valid=True,
        errors=[],
    )
    db.add(validation)
    receipt.latest_validation_version = 1

    twin = ReceiptTwin(
        receipt_id=receipt.id,
        version=1,
        source="model",
        payload={
            "store_name": payee_name,
            "total_amount": 25.0,
            "payment_method": "Visa",
            "card_last_four": None,
            "currency": "USD",
            "receipt_language": "en",
            "line_items": [],
        },
        confirmed_sections={"date_time": True, "total": True},
    )
    db.add(twin)
    receipt.latest_twin_version = 1

    db.commit()
    db.refresh(receipt)
    return receipt, validation, twin


def test_sync_single_category_creates_memory_row(db_with_cache: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """After successful sync with single-category → memory row with category_id."""
    # Add cat-2 to cache so split test fixture works; here just single.
    from app.models import YNABCache
    db_with_cache.add(YNABCache(
        budget_id=BUDGET_ID,
        entity_type=YNABCacheEntityType.CATEGORY.value,
        entity_id="cat-2",
        name="Dining",
        group_name="Everyday",
        raw_json={"id": "cat-2", "name": "Dining"},
    ))
    db_with_cache.commit()

    _seed_single_category(db_with_cache)
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
        sa_select(PayeeCategoryMemory).where(
            PayeeCategoryMemory.budget_id == BUDGET_ID,
        )
    ))
    assert len(rows) == 1
    assert rows[0].category_id == CATEGORY_ID
    assert rows[0].template_json is None


def test_sync_split_creates_template_memory_row(db_with_cache: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """After successful sync with split → memory row with template_json."""
    from app.models import YNABCache
    db_with_cache.add(YNABCache(
        budget_id=BUDGET_ID,
        entity_type=YNABCacheEntityType.CATEGORY.value,
        entity_id="cat-2",
        name="Dining",
        group_name="Everyday",
        raw_json={"id": "cat-2", "name": "Dining"},
    ))
    db_with_cache.commit()

    _seed_split(db_with_cache)
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
        sa_select(PayeeCategoryMemory).where(
            PayeeCategoryMemory.budget_id == BUDGET_ID,
        )
    ))
    assert len(rows) == 1
    assert rows[0].template_json is not None
    assert rows[0].category_id is None


def test_upsert_payee_memory_raises_is_non_fatal(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """upsert_payee_memory raises → sync still SYNCED (non-fatal)."""
    _seed_single_category(db_with_cache)
    mock_client = _mock_ynab_client()
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: mock_client)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("upsert_payee_memory exploded")

    monkeypatch.setattr("app.services.payee_memory.upsert_payee_memory", _raise)

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


def test_blank_payee_does_not_create_memory_row(db_with_cache: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank payee → no memory row (service no-op)."""
    _seed_single_category(db_with_cache, payee_name="")
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

    rows = list(db_with_cache.scalars(sa_select(PayeeCategoryMemory)))
    assert len(rows) == 0


def _apply_post_sync_with_status(db: Any, status: str) -> None:
    """Drive _apply_post_sync directly with a given sync-row status."""
    import time

    from app.services.ynab import _apply_post_sync
    from app.utils import utcnow

    receipt = db.get(Receipt, RECEIPT_ID)
    validation = db.scalar(
        sa_select(Validation).where(Validation.receipt_id == RECEIPT_ID).order_by(Validation.version.desc())
    )
    sync_row = YNABSync(
        receipt_id=RECEIPT_ID,
        validation_id=validation.id,
        status=status,
        match_mode="match_or_create",
        idempotency_key=f"test-{status}",
        created_transaction_id=TXN_ID,
        started_at=utcnow(),
    )
    db.add(sync_row)
    db.flush()
    _apply_post_sync(
        db,
        receipt,
        validation,
        sync_row,
        _live_settings(),
        idempotency_key=f"test-{status}",
        started_perf=time.perf_counter(),
    )


def test_adopted_sync_does_not_learn_memory(db_with_cache: Any) -> None:
    """MATCHED_UPDATED (adopt path) must not learn: the payload splits were
    rewritten from YNAB but the workspace was not, so a template built from the
    pair would map items to the wrong lanes."""
    _seed_split(db_with_cache)

    _apply_post_sync_with_status(db_with_cache, YNABSyncStatus.MATCHED_UPDATED.value)
    rows = list(db_with_cache.scalars(sa_select(PayeeCategoryMemory)))
    assert rows == []

    # Control: same receipt through the same path with CREATED learns a row,
    # proving the learn block is reachable in this harness.
    _apply_post_sync_with_status(db_with_cache, YNABSyncStatus.CREATED.value)
    rows = list(db_with_cache.scalars(sa_select(PayeeCategoryMemory)))
    assert len(rows) == 1

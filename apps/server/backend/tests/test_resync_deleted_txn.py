"""B-01: re-sync against a deleted YNAB transaction flags review, does not recreate.

When a previously-synced receipt is re-synced but its YNAB transaction was deleted
in YNAB (and no exact match is found), the worker must NOT create a fresh
transaction — that would silently duplicate or resurrect a deliberately-removed
entry. Instead it flags the receipt NEEDS_REVIEW with a clear reason and records
the sync attempt as FAILED. All YNAB interaction is mocked.

(Decision 2026-06-16; resync-path only — reconciliation's deleted-txn handling is
intentionally unchanged.)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select

from app.config import Settings
from app.enums import ReceiptStatus, YNABSyncStatus
from app.models import Receipt, Validation, YNABSync
from app.services.ynab import (
    _PRIOR_TRANSACTION_DELETED_REASON,
    make_idempotency_key,
    sync_receipt_to_ynab,
)
from receipt_shared.ynab_client import YNABClient

BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"


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


def test_resync_against_deleted_txn_flags_review_not_recreate(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = str(uuid.uuid4())
    receipt = Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="b01.jpg",
        file_hash=f"hash-b01-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.NEEDS_REVIEW.value,
    )
    db_with_cache.add(receipt)
    db_with_cache.flush()

    validation = Validation(
        receipt_id=rid,
        version=1,
        source="user",
        payload={
            "payee_name": "Test Payee",
            "account_id": ACCOUNT_ID,
            "transaction_date": "2026-06-01",
            "transaction_time": None,
            "memo": "",
            "total_amount": 50.0,
            "category_id": CATEGORY_ID,
            "splits": [],
            "transaction_kind": "purchase",
        },
        is_valid=True,
        errors=[],
    )
    db_with_cache.add(validation)
    db_with_cache.flush()
    receipt.latest_validation_version = 1

    prior_row = YNABSync(
        receipt_id=rid,
        validation_id=validation.id,
        idempotency_key=make_idempotency_key(rid, validation.id, False, True) + "-prior",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        completed_at=datetime.now(timezone.utc) - timedelta(minutes=9),
        created_transaction_id="txn-gone",
        raw_request={
            "transaction": {
                "account_id": ACCOUNT_ID,
                "date": "2026-06-01",
                "amount": -50000,
                "payee_name": "Test Payee",
                "memo": "",
                "category_id": CATEGORY_ID,
            }
        },
    )
    db_with_cache.add(prior_row)
    db_with_cache.commit()

    # YNAB reports the prior transaction as deleted, and no exact match exists.
    client = MagicMock(spec=YNABClient)
    client.get_transaction.return_value = {"id": "txn-gone", "deleted": True}
    client.list_transactions_since.return_value = []
    client.create_transaction.return_value = {"id": "txn-recreated"}
    monkeypatch.setattr("app.services.ynab.get_ynab_client", lambda s: client)
    monkeypatch.setattr("app.services.ynab.apply_sync_gamification", MagicMock())

    sync_receipt_to_ynab(
        db=db_with_cache,
        settings=_live_settings(),
        receipt_id=rid,
        force_create=False,
        allow_update_match=True,
    )

    # No recreate (and certainly no delete) — the receipt is flagged for human review.
    client.create_transaction.assert_not_called()
    client.delete_transaction.assert_not_called()

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, rid)
    assert after is not None
    assert after.status == ReceiptStatus.NEEDS_REVIEW.value
    assert after.status_reason == _PRIOR_TRANSACTION_DELETED_REASON

    # The new sync attempt is recorded as FAILED (no successful write occurred).
    new_row = db_with_cache.scalar(
        sa_select(YNABSync).where(
            YNABSync.receipt_id == rid,
            YNABSync.idempotency_key != prior_row.idempotency_key,
        )
    )
    assert new_row is not None
    assert new_row.status == YNABSyncStatus.FAILED.value
    assert new_row.created_transaction_id is None

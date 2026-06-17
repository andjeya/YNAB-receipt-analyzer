"""Reconciliation scope regression tests (plan T1-03..T1-07).

These lock the INTENTIONAL reconciliation scope. `run_ynab_reconciliation`
detects only:

  (a) top-level amount drift, and
  (b) category / split-structure changes.

YNAB-side edits to date, account, payee, or memo MUST NOT create a correction
or flip a SYNCED receipt to NEEDS_REVIEW (product decision 2026-06-16: keep the
current scope; broader detection is explicitly out of scope to avoid review
churn). A deleted YNAB transaction is currently skipped — that current behavior
is asserted here; the desired "flag for review, don't recreate" upgrade is
tracked separately as backlog B-01 (see test_backlog_desired_behavior.py).

Amount drift and category-only-stays-SYNCED already have coverage in
test_m2_write_safety.py (tests 18/19); this file adds the non-detection locks,
the split-only correction case, and the deleted-transaction case.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select as sa_select

from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.models import Receipt, ReceiptCorrection, Validation, YNABCache, YNABSync
from app.services.reconciliation import run_ynab_reconciliation
from receipt_shared.ynab_client import YNABClient

BUDGET_ID = "test-budget-id"
ACCOUNT_ID = "acct-1"
CATEGORY_ID = "cat-1"


def _settings(**overrides: Any) -> Settings:
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


def _add_category(db: Any, entity_id: str, name: str) -> None:
    db.add(
        YNABCache(
            budget_id=BUDGET_ID,
            entity_type=YNABCacheEntityType.CATEGORY.value,
            entity_id=entity_id,
            name=name,
            group_name="Everyday",
            raw_json={"id": entity_id, "name": name},
        )
    )
    db.commit()


def _seed_synced_receipt(
    db: Any,
    *,
    raw_transaction: dict[str, Any],
    payload: dict[str, Any],
    txn_id: str,
    total: float = 30.0,
) -> tuple[Receipt, YNABSync]:
    """Seed a SYNCED receipt + validation + completed CREATED sync row.

    `raw_transaction` is the dict stored at raw_request["transaction"] (the
    payload that was actually synced); reconciliation compares it against the
    current YNAB transaction.
    """
    rid = str(uuid.uuid4())
    receipt = Receipt(
        id=rid,
        storage_key=f"receipts/{rid}.jpg",
        original_filename="recon.jpg",
        file_hash=f"hash-recon-{rid}",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1024,
        status=ReceiptStatus.SYNCED.value,
    )
    db.add(receipt)
    db.flush()

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
            "total_amount": total,
            "category_id": payload.get("category_id"),
            "splits": payload.get("splits", []),
            "transaction_kind": "purchase",
        },
        is_valid=True,
        errors=[],
    )
    db.add(validation)
    db.flush()
    receipt.latest_validation_version = 1

    now = datetime.now(timezone.utc)
    sync_row = YNABSync(
        receipt_id=rid,
        validation_id=validation.id,
        idempotency_key=f"ikey-recon-{rid}",
        status=YNABSyncStatus.CREATED.value,
        match_mode="match_or_create",
        started_at=now - timedelta(minutes=5),
        completed_at=now - timedelta(minutes=4),
        created_transaction_id=txn_id,
        raw_request={"transaction": raw_transaction},
    )
    db.add(sync_row)
    db.commit()
    return receipt, sync_row


def _run_with_ynab(
    db: Any, settings: Settings, monkeypatch: pytest.MonkeyPatch, ynab_txn: dict[str, Any]
) -> MagicMock:
    client = MagicMock(spec=YNABClient)
    client.get_transaction.return_value = ynab_txn
    monkeypatch.setattr("app.services.reconciliation.get_ynab_client", lambda s: client)
    run_ynab_reconciliation(db=db, settings=settings)
    return client


def _corrections(db: Any, receipt_id: str) -> list[ReceiptCorrection]:
    return list(
        db.scalars(sa_select(ReceiptCorrection).where(ReceiptCorrection.receipt_id == receipt_id))
    )


# ---------------------------------------------------------------------------
# T1-03 / T1-04 / T1-05: non-signature field changes are NOT detected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("changed_field", ["date", "account", "payee", "memo"])
def test_recon_ignores_non_signature_field_changes(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch, changed_field: str
) -> None:
    """YNAB-side date/account/payee/memo-only edits create no correction and keep SYNCED.

    Signature compares category + split structure only, and amount is compared
    separately; none of these fields participate. Changing exactly one of them
    must be a no-op for reconciliation (intentional scope, decision 2026-06-16).
    """
    raw_transaction = {
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,
    }
    receipt, _ = _seed_synced_receipt(
        db_with_cache,
        raw_transaction=raw_transaction,
        payload={"category_id": CATEGORY_ID, "splits": []},
        txn_id="txn-nonsig",
    )

    ynab_txn = {
        "id": "txn-nonsig",
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,  # unchanged
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,  # unchanged
        "deleted": False,
        "subtransactions": [],
    }
    # Mutate exactly one non-signature field on the YNAB side.
    if changed_field == "date":
        ynab_txn["date"] = "2026-06-15"
    elif changed_field == "account":
        ynab_txn["account_id"] = "acct-moved"
    elif changed_field == "payee":
        ynab_txn["payee_name"] = "Renamed Payee"
    elif changed_field == "memo":
        ynab_txn["memo"] = "user added a note"

    client = _run_with_ynab(db_with_cache, _settings(), monkeypatch, ynab_txn)

    # No write-back, no correction, stays SYNCED.
    client.update_transaction.assert_not_called()
    client.create_transaction.assert_not_called()
    assert _corrections(db_with_cache, receipt.id) == []

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, receipt.id)
    assert after is not None
    assert after.status == ReceiptStatus.SYNCED.value
    assert after.status_reason is None


# ---------------------------------------------------------------------------
# T1-06: category-only and split-only changes DO record a correction (stay SYNCED)
# ---------------------------------------------------------------------------


def test_recon_category_only_records_correction_keeps_synced(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YNAB category swap (no amount drift) records exactly one correction and
    keeps the receipt SYNCED (pulls the YNAB category locally, no write-back)."""
    _add_category(db_with_cache, "cat-changed", "Changed Category")

    raw_transaction = {
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,
    }
    receipt, _ = _seed_synced_receipt(
        db_with_cache,
        raw_transaction=raw_transaction,
        payload={"category_id": CATEGORY_ID, "splits": []},
        txn_id="txn-cat",
    )

    ynab_txn = {
        "id": "txn-cat",
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": "cat-changed",
        "deleted": False,
        "subtransactions": [],
    }

    client = _run_with_ynab(db_with_cache, _settings(), monkeypatch, ynab_txn)

    client.update_transaction.assert_not_called()
    client.create_transaction.assert_not_called()
    assert len(_corrections(db_with_cache, receipt.id)) == 1

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, receipt.id)
    assert after is not None
    assert after.status == ReceiptStatus.SYNCED.value


def test_recon_split_only_change_records_correction_keeps_synced(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YNAB split-category change (same total) records a correction and stays SYNCED."""
    _add_category(db_with_cache, "cat-changed", "Changed Category")

    raw_transaction = {
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": None,
        "subtransactions": [
            {"amount": -20000, "category_id": CATEGORY_ID, "memo": "a"},
            {"amount": -10000, "category_id": CATEGORY_ID, "memo": "b"},
        ],
    }
    receipt, _ = _seed_synced_receipt(
        db_with_cache,
        raw_transaction=raw_transaction,
        payload={
            "category_id": None,
            "splits": [
                {"amount": 20.0, "category_id": CATEGORY_ID, "memo": "a"},
                {"amount": 10.0, "category_id": CATEGORY_ID, "memo": "b"},
            ],
        },
        txn_id="txn-split",
    )

    ynab_txn = {
        "id": "txn-split",
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": None,
        "deleted": False,
        "subtransactions": [
            {"id": "s1", "amount": -20000, "category_id": CATEGORY_ID, "memo": "a"},
            {"id": "s2", "amount": -10000, "category_id": "cat-changed", "memo": "b"},
        ],
    }

    client = _run_with_ynab(db_with_cache, _settings(), monkeypatch, ynab_txn)

    client.update_transaction.assert_not_called()
    client.create_transaction.assert_not_called()
    assert len(_corrections(db_with_cache, receipt.id)) == 1

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, receipt.id)
    assert after is not None
    assert after.status == ReceiptStatus.SYNCED.value


# ---------------------------------------------------------------------------
# T1-08: amount drift in the INCREASE direction (e.g. tip added in YNAB)
# ---------------------------------------------------------------------------


def test_recon_amount_drift_increase_flags_needs_review(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tip scenario: receipt synced pre-tip (-30.00) but YNAB posts the tip-inclusive
    amount (-34.50). Amount drift is direction-agnostic: reconciliation flags
    NEEDS_REVIEW, pulls the local validation to the higher YNAB amount, and never
    pushes the pre-tip amount back. (test_m2 covers the decrease direction.)"""
    raw_transaction = {
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Diner",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,
    }
    receipt, _ = _seed_synced_receipt(
        db_with_cache,
        raw_transaction=raw_transaction,
        payload={"category_id": CATEGORY_ID, "splits": []},
        txn_id="txn-tip",
        total=30.0,
    )

    ynab_txn = {
        "id": "txn-tip",
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -34500,  # tip added on the YNAB side
        "payee_name": "Diner",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,
        "deleted": False,
        "subtransactions": [],
    }

    client = _run_with_ynab(db_with_cache, _settings(), monkeypatch, ynab_txn)

    client.update_transaction.assert_not_called()
    client.create_transaction.assert_not_called()
    assert len(_corrections(db_with_cache, receipt.id)) == 1

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, receipt.id)
    assert after is not None
    assert after.status == ReceiptStatus.NEEDS_REVIEW.value
    assert after.status_reason is not None and "YNAB amount" in after.status_reason

    latest = db_with_cache.scalar(
        sa_select(Validation)
        .where(Validation.receipt_id == receipt.id)
        .order_by(Validation.version.desc())
    )
    assert latest is not None
    assert abs(float(latest.payload.get("total_amount", 0)) - 34.5) < 0.01


# ---------------------------------------------------------------------------
# T1-07: deleted YNAB transaction is skipped (current behavior; see backlog B-01)
# ---------------------------------------------------------------------------


def test_recon_deleted_ynab_transaction_is_skipped(
    db_with_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the YNAB transaction is deleted, reconciliation skips it: no correction,
    no local mutation, no YNAB write. Documents CURRENT behavior; the desired
    flag-for-review upgrade is tracked as backlog B-01."""
    raw_transaction = {
        "account_id": ACCOUNT_ID,
        "date": "2026-06-01",
        "amount": -30000,
        "payee_name": "Test Payee",
        "memo": "[receipt_id:r]",
        "category_id": CATEGORY_ID,
    }
    receipt, _ = _seed_synced_receipt(
        db_with_cache,
        raw_transaction=raw_transaction,
        payload={"category_id": CATEGORY_ID, "splits": []},
        txn_id="txn-deleted",
    )

    ynab_txn = {"id": "txn-deleted", "deleted": True}

    client = _run_with_ynab(db_with_cache, _settings(), monkeypatch, ynab_txn)

    client.update_transaction.assert_not_called()
    client.create_transaction.assert_not_called()
    client.delete_transaction.assert_not_called()
    assert _corrections(db_with_cache, receipt.id) == []

    db_with_cache.expire_all()
    after = db_with_cache.get(Receipt, receipt.id)
    assert after is not None
    assert after.status == ReceiptStatus.SYNCED.value
    assert after.status_reason is None

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.receipts import get_receipt_detail, reset_to_synced
from app.enums import ReceiptStatus, YNABCacheEntityType, YNABSyncStatus
from app.models import Base, Receipt, Validation, YNABCache, YNABSync


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_cache_entities(db: Session) -> None:
    db.add_all(
        [
            YNABCache(
                budget_id="budget-1",
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-1",
                name="Groceries",
                group_name="Everyday",
                raw_json={"id": "cat-1", "name": "Groceries"},
            ),
            YNABCache(
                budget_id="budget-1",
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-2",
                name="Household",
                group_name="Everyday",
                raw_json={"id": "cat-2", "name": "Household"},
            ),
            YNABCache(
                budget_id="budget-1",
                entity_type=YNABCacheEntityType.ACCOUNT.value,
                entity_id="acct-1",
                name="Checking",
                group_name=None,
                raw_json={"id": "acct-1", "name": "Checking"},
            ),
        ]
    )


def _payload(*, payee: str, category_id: str, total: float) -> dict[str, object]:
    return {
        "payee_name": payee,
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "Imported",
        "total_amount": total,
        "category_id": category_id,
        "splits": [],
    }


def _new_receipt(status: str, version: int) -> Receipt:
    return Receipt(
        id="11111111-2222-4333-8444-555555555555",
        storage_key="receipts/11111111-2222-4333-8444-555555555555.jpg",
        original_filename="receipt.jpg",
        file_hash="hash-reset-1",
        file_ext=".jpg",
        mime_type="image/jpeg",
        file_size_bytes=1234,
        status=status,
        latest_validation_version=version,
        extraction_completed_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
    )


def test_reset_to_synced_restores_payload_and_synced_status():
    """A synced receipt accidentally edited (flipped to needs_review) is fully
    realigned to the last state pushed to YNAB and returns to the Synced tab."""
    with _memory_session() as db:
        _add_cache_entities(db)

        # Post-edit state: status flipped to NEEDS_REVIEW, latest validation v3 is the
        # accidental edit; v2 (cat-1) is what was actually synced to YNAB.
        receipt = _new_receipt(ReceiptStatus.NEEDS_REVIEW.value, version=3)
        receipt.display_payee_name = "Wrong Payee"
        receipt.display_total_milliunits = 99_990
        db.add(receipt)

        synced_payload = _payload(payee="Test Merchant", category_id="cat-1", total=12.5)
        db.add(Validation(receipt_id=receipt.id, version=1, source="model",
                          payload=_payload(payee="Test Merchant", category_id="cat-1", total=12.5),
                          is_valid=True, errors=None))
        synced_validation = Validation(receipt_id=receipt.id, version=2, source="user",
                                       payload=synced_payload, is_valid=True, errors=None)
        db.add(synced_validation)
        db.add(Validation(receipt_id=receipt.id, version=3, source="user",
                          payload=_payload(payee="Wrong Payee", category_id="cat-2", total=99.99),
                          is_valid=True, errors=None))
        db.flush()

        db.add(
            YNABSync(
                receipt_id=receipt.id,
                validation_id=synced_validation.id,
                idempotency_key="idem-reset-1",
                status=YNABSyncStatus.CREATED.value,
                match_mode="match_or_create",
                created_transaction_id="txn-1",
                started_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        # Detail exposes the synced validation (cat-1) even though latest is the edit (cat-2).
        before = get_receipt_detail(receipt.id, db)
        assert before.latest_validation is not None and before.latest_validation.payload["category_id"] == "cat-2"
        assert before.synced_validation is not None and before.synced_validation.payload["category_id"] == "cat-1"

        result = reset_to_synced(receipt.id, db)

        assert result.status == ReceiptStatus.SYNCED.value
        assert result.latest_validation is not None
        assert result.latest_validation.version == 4
        assert result.latest_validation.payload["category_id"] == "cat-1"
        assert result.latest_validation.payload["payee_name"] == "Test Merchant"
        assert result.display_payee_name == "Test Merchant"
        assert result.display_total_milliunits == 12_500

        db.refresh(receipt)
        assert receipt.status == ReceiptStatus.SYNCED.value
        assert receipt.status_reason is None
        latest = db.scalar(
            select(Validation).where(Validation.receipt_id == receipt.id).order_by(Validation.version.desc()).limit(1)
        )
        assert latest is not None and latest.version == 4 and latest.payload["category_id"] == "cat-1"


def test_reset_to_synced_without_successful_sync_rejected():
    """No successful sync to restore from → 400, no validation created."""
    with _memory_session() as db:
        _add_cache_entities(db)

        receipt = _new_receipt(ReceiptStatus.NEEDS_REVIEW.value, version=1)
        db.add(receipt)
        db.add(Validation(receipt_id=receipt.id, version=1, source="user",
                          payload=_payload(payee="Test Merchant", category_id="cat-1", total=12.5),
                          is_valid=True, errors=None))
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            reset_to_synced(receipt.id, db)

        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "no_successful_sync"

        db.refresh(receipt)
        assert receipt.latest_validation_version == 1
        assert receipt.status == ReceiptStatus.NEEDS_REVIEW.value


def test_reset_to_synced_ignores_structure_ignored_updates():
    """A matched_updated sync whose structure YNAB ignored does NOT hold this payload,
    so it must not be a restore source (else we'd mark SYNCED a state YNAB never took)."""
    with _memory_session() as db:
        _add_cache_entities(db)

        receipt = _new_receipt(ReceiptStatus.NEEDS_REVIEW.value, version=1)
        db.add(receipt)
        validation = Validation(receipt_id=receipt.id, version=1, source="user",
                                payload=_payload(payee="Test Merchant", category_id="cat-1", total=12.5),
                                is_valid=True, errors=None)
        db.add(validation)
        db.flush()
        db.add(
            YNABSync(
                receipt_id=receipt.id,
                validation_id=validation.id,
                idempotency_key="idem-reset-structure",
                status=YNABSyncStatus.MATCHED_UPDATED.value,
                match_mode="match_or_create",
                structure_applied=False,  # YNAB ignored the split/category change
                started_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            reset_to_synced(receipt.id, db)
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "no_successful_sync"


def test_reset_to_synced_refuses_while_sync_in_progress():
    """An in-flight sync owns the SYNCING→SYNCED transition; restoring would race it."""
    with _memory_session() as db:
        _add_cache_entities(db)

        receipt = _new_receipt(ReceiptStatus.SYNCING.value, version=2)
        db.add(receipt)
        validation = Validation(receipt_id=receipt.id, version=2, source="user",
                                payload=_payload(payee="Test Merchant", category_id="cat-1", total=12.5),
                                is_valid=True, errors=None)
        db.add(validation)
        db.flush()
        db.add(
            YNABSync(
                receipt_id=receipt.id,
                validation_id=validation.id,
                idempotency_key="idem-reset-syncing",
                status=YNABSyncStatus.CREATED.value,
                match_mode="match_or_create",
                started_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            reset_to_synced(receipt.id, db)
        assert excinfo.value.status_code == 409
        assert excinfo.value.detail == "sync_in_progress"

        db.refresh(receipt)
        assert receipt.latest_validation_version == 2  # no version created


def test_reset_to_synced_realigns_semantic_state_and_clears_duplicate_pointer():
    """Restore recomputes the semantic signature from the synced payload and drops any
    stale duplicate pointer left by the accidental edit."""
    with _memory_session() as db:
        _add_cache_entities(db)

        receipt = _new_receipt(ReceiptStatus.NEEDS_REVIEW.value, version=3)
        # Stale duplicate state left over from the edited payload.
        receipt.duplicate_of_receipt_id = "00000000-0000-4000-8000-000000000000"
        receipt.semantic_signature = "stale-edited-signature"
        db.add(receipt)

        synced_payload = _payload(payee="Test Merchant", category_id="cat-1", total=12.5)
        db.add(Validation(receipt_id=receipt.id, version=1, source="model", payload=synced_payload,
                          is_valid=True, errors=None))
        synced_validation = Validation(receipt_id=receipt.id, version=2, source="user",
                                       payload=synced_payload, is_valid=True, errors=None)
        db.add(synced_validation)
        db.add(Validation(receipt_id=receipt.id, version=3, source="user",
                          payload=_payload(payee="Edited", category_id="cat-2", total=88.0),
                          is_valid=True, errors=None))
        db.flush()
        db.add(
            YNABSync(
                receipt_id=receipt.id,
                validation_id=synced_validation.id,
                idempotency_key="idem-reset-semantic",
                status=YNABSyncStatus.MATCHED_UPDATED.value,
                match_mode="match_or_create",
                structure_applied=True,
                started_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        reset_to_synced(receipt.id, db)

        db.refresh(receipt)
        assert receipt.status == ReceiptStatus.SYNCED.value
        assert receipt.duplicate_of_receipt_id is None
        assert receipt.semantic_signature != "stale-edited-signature"
        assert receipt.semantic_payee_key  # recomputed from the synced payload


def test_reset_to_synced_ignores_failed_sync_rows():
    """A FAILED sync row must not count as a restore source."""
    with _memory_session() as db:
        _add_cache_entities(db)

        receipt = _new_receipt(ReceiptStatus.ERROR_SYNC.value, version=1)
        db.add(receipt)
        validation = Validation(receipt_id=receipt.id, version=1, source="user",
                                payload=_payload(payee="Test Merchant", category_id="cat-1", total=12.5),
                                is_valid=True, errors=None)
        db.add(validation)
        db.flush()
        db.add(
            YNABSync(
                receipt_id=receipt.id,
                validation_id=validation.id,
                idempotency_key="idem-reset-failed",
                status=YNABSyncStatus.FAILED.value,
                match_mode="match_or_create",
                started_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
                completed_at=datetime(2026, 2, 15, 13, 0, tzinfo=timezone.utc),
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as excinfo:
            reset_to_synced(receipt.id, db)
        assert excinfo.value.status_code == 400

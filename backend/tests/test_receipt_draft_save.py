from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.receipts import _is_manual_category_correction, save_draft
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import Base, Receipt, Validation, YNABCache
from app.schemas import SaveDraftRequest


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _add_cache_entities(db: Session, budget_id: str) -> None:
    db.add_all(
        [
            YNABCache(
                budget_id=budget_id,
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-1",
                name="Groceries",
                group_name="Everyday",
                raw_json={"id": "cat-1", "name": "Groceries"},
            ),
            YNABCache(
                budget_id=budget_id,
                entity_type=YNABCacheEntityType.CATEGORY.value,
                entity_id="cat-2",
                name="Household",
                group_name="Everyday",
                raw_json={"id": "cat-2", "name": "Household"},
            ),
            YNABCache(
                budget_id=budget_id,
                entity_type=YNABCacheEntityType.ACCOUNT.value,
                entity_id="acct-1",
                name="Checking",
                group_name=None,
                raw_json={"id": "acct-1", "name": "Checking"},
            ),
        ]
    )


def _payload(category_id: str) -> dict[str, object]:
    return {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "Imported",
        "total_amount": 12.5,
        "category_id": category_id,
        "splits": [],
    }


def test_save_draft_manual_correction_with_prior_user_validation_succeeds():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")

        receipt = Receipt(
            id="11111111-2222-4333-8444-555555555555",
            storage_key="receipts/11111111-2222-4333-8444-555555555555.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-1",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=1234,
            status=ReceiptStatus.NEEDS_REVIEW.value,
            latest_validation_version=2,
            extraction_completed_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
        )
        db.add(receipt)

        db.add(
            Validation(
                receipt_id=receipt.id,
                version=1,
                source="model",
                payload=_payload("cat-1"),
                is_valid=True,
                errors=None,
            )
        )
        db.add(
            Validation(
                receipt_id=receipt.id,
                version=2,
                source="user",
                payload=_payload("cat-1"),
                is_valid=True,
                errors=None,
            )
        )
        db.commit()

        response = save_draft(
            receipt_id=receipt.id,
            request=SaveDraftRequest(payload=_payload("cat-2"), source="user"),
            db=db,
            settings=settings,
        )

        latest = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )

        assert response.can_sync is True
        assert latest is not None
        assert latest.version == 3
        assert latest.payload["category_id"] == "cat-2"


def test_manual_correction_ignores_split_parent_category_and_memo_only_changes():
    model_payload = {
        "category_id": "parent-cat-model",
        "splits": [
            {"category_id": "cat-1", "amount": 85.21, "memo": "model memo a"},
            {"category_id": "cat-2", "amount": 12.71, "memo": "model memo b"},
        ],
    }
    user_payload = {
        "category_id": "",
        "splits": [
            {"category_id": "cat-2", "amount": 12.7100, "memo": "user memo b"},
            {"category_id": "cat-1", "amount": 85.2100, "memo": "user memo a"},
        ],
    }

    assert _is_manual_category_correction(model_payload, user_payload) is False


def test_manual_correction_detects_split_amount_or_category_changes():
    model_payload = {
        "category_id": None,
        "splits": [
            {"category_id": "cat-1", "amount": 85.21},
            {"category_id": "cat-2", "amount": 12.71},
        ],
    }
    changed_amount_payload = {
        "category_id": "",
        "splits": [
            {"category_id": "cat-1", "amount": 85.21},
            {"category_id": "cat-2", "amount": 12.72},
        ],
    }
    changed_category_payload = {
        "category_id": "",
        "splits": [
            {"category_id": "cat-1", "amount": 85.21},
            {"category_id": "cat-3", "amount": 12.71},
        ],
    }

    assert _is_manual_category_correction(model_payload, changed_amount_payload) is True
    assert _is_manual_category_correction(model_payload, changed_category_payload) is True


def test_manual_correction_ignores_single_category_vs_equivalent_one_split():
    model_payload = {
        "category_id": "cat-1",
        "total_amount": 23.04,
        "splits": [],
    }
    user_payload = {
        "category_id": None,
        "total_amount": 23.04,
        "splits": [
            {"category_id": "cat-1", "amount": 23.04, "memo": ""},
        ],
    }

    assert _is_manual_category_correction(model_payload, user_payload) is False

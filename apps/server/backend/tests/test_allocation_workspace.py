from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.receipts import save_draft
from app.config import Settings
from app.enums import ReceiptStatus, YNABCacheEntityType
from app.models import Base, Receipt, Validation, YNABCache
from app.schemas import SaveDraftRequest
from app.services.allocation_workspace import build_initial_allocation_workspace, recompute_payload_from_workspace


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


def _split_payload() -> dict[str, object]:
    return {
        "payee_name": "Test Merchant",
        "account_id": "acct-1",
        "transaction_date": "2026-02-15",
        "transaction_time": None,
        "memo": "Imported",
        "total_amount": 30.0,
        "category_id": None,
        "splits": [
            {"category_id": "cat-1", "amount": 20.0, "memo": "a"},
            {"category_id": "cat-2", "amount": 10.0, "memo": "b"},
        ],
    }


def _twin_payload() -> dict[str, object]:
    return {
        "line_items": [
            {"index": 0, "raw_text": "A", "translated_text": "", "line_total": 20.0, "tax_code": None, "item_type": "product"},
            {"index": 1, "raw_text": "B", "translated_text": "", "line_total": 10.0, "tax_code": None, "item_type": "product"},
            {"index": 2, "raw_text": "TOTAL", "translated_text": "", "line_total": 30.0, "tax_code": None, "item_type": "total"},
        ]
    }


def test_workspace_recompute_keep_and_discard_modes():
    payload = _split_payload()
    workspace = build_initial_allocation_workspace(
        payload,
        twin_payload=_twin_payload(),
        twin_version=3,
    )

    # Pin only split-0 to force "keep manual" behavior.
    for lane in workspace["lanes"]:
        if lane["lane_id"] == "split-0":
            lane["pinned_amount"] = 25.0
        elif lane["lane_id"] == "split-1":
            lane["pinned_amount"] = None

    keep_payload, _, _ = recompute_payload_from_workspace(
        payload,
        workspace,
        mode="keep_manual_amounts",
    )
    assert keep_payload["splits"][0]["amount"] == 25.0
    assert keep_payload["splits"][1]["amount"] == 5.0

    discard_payload, _, _ = recompute_payload_from_workspace(
        payload,
        workspace,
        mode="discard_manual_amounts",
    )
    assert discard_payload["splits"][0]["amount"] == 20.0
    assert discard_payload["splits"][1]["amount"] == 10.0


def test_save_draft_persists_allocation_workspace():
    settings = Settings(_env_file=None, ynab_budget_id="budget-1")

    with _memory_session() as db:
        _add_cache_entities(db, settings.ynab_budget_id or "")
        receipt = Receipt(
            id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            storage_key="receipts/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.jpg",
            original_filename="receipt.jpg",
            file_hash="hash-receipt-workspace",
            file_ext=".jpg",
            mime_type="image/jpeg",
            file_size_bytes=1234,
            status=ReceiptStatus.NEEDS_REVIEW.value,
            latest_validation_version=0,
            extraction_completed_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
        )
        db.add(receipt)
        db.commit()

        payload = _split_payload()
        workspace = build_initial_allocation_workspace(
            payload,
            twin_payload=_twin_payload(),
            twin_version=2,
        )
        response = save_draft(
            receipt_id=receipt.id,
            request=SaveDraftRequest(payload=payload, source="user", allocation_workspace=workspace),
            db=db,
            settings=settings,
        )

        latest = db.scalar(
            select(Validation)
            .where(Validation.receipt_id == receipt.id)
            .order_by(Validation.version.desc())
            .limit(1)
        )
        assert response.validation.allocation_workspace is not None
        assert latest is not None
        assert latest.allocation_workspace is not None
        assert latest.allocation_workspace.get("version") == 1
        assert len(latest.allocation_workspace.get("lanes", [])) >= 2
        assert len(latest.allocation_workspace.get("assignments", [])) >= 1
